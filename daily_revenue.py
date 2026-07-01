import os
import time
import requests
import pytz
from datetime import datetime

HUBSPOT_TOKEN = os.environ["HUBSPOT_TOKEN"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

HEADERS = {
    "Authorization": f"Bearer {HUBSPOT_TOKEN}",
    "Content-Type": "application/json",
}

CST = pytz.timezone("America/Chicago")

CLOSED_STAGES_LABELS = {
    "🤝 READY FOR FULFILLMENT",
    "🎉 CLOSED WON (Successful Solution Delivery)",
}

EXCLUDED_STAGE_LABELS = {
    "🤝 READY FOR FULFILLMENT",
    "🎉 CLOSED WON (Successful Solution Delivery)",
    "❌ CLOSED LOST",
    "🧪 QC DQ CONSULT - REVIEW PILE",
    "♻️ Reactivation Potential",
}


def get_pipeline_and_stage_ids():
    resp = requests.get(
        "https://api.hubapi.com/crm/v3/pipelines/deals",
        headers=HEADERS,
    )
    resp.raise_for_status()
    for pipeline in resp.json().get("results", []):
        if pipeline["label"] == "ONFLOOR - NEW BUSINESS":
            pipeline_id = pipeline["id"]
            stage_map = {s["label"]: s["id"] for s in pipeline.get("stages", [])}
            return pipeline_id, stage_map
    raise ValueError("Pipeline 'ONFLOOR - NEW BUSINESS' not found")


def get_owners():
    resp = requests.get(
        "https://api.hubapi.com/crm/v3/owners",
        headers=HEADERS,
    )
    resp.raise_for_status()
    owners = {}
    for owner in resp.json().get("results", []):
        name = f"{owner.get('firstName', '')} {owner.get('lastName', '')}".strip()
        owners[str(owner["id"])] = name or f"Owner {owner['id']}"
    return owners


def search_deals(filters, properties):
    results = []
    after = None
    while True:
        payload = {
            "filterGroups": [{"filters": filters}],
            "properties": properties,
            "limit": 100,
        }
        if after:
            payload["after"] = after
        resp = requests.post(
            "https://api.hubapi.com/crm/v3/objects/deals/search",
            headers=HEADERS,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        paging = data.get("paging", {})
        after = paging.get("next", {}).get("after")
        if not after:
            break
    return results


def fmt_usd(amount):
    return f"${int(round(float(amount))):,}"


def send_slack(text):
    resp = requests.post(SLACK_WEBHOOK_URL, json={"text": text})
    resp.raise_for_status()


def monthly_closed_revenue(pipeline_id, stage_map, owners):
    now_cst = datetime.now(CST)
    month_start = now_cst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day_end = now_cst.replace(hour=23, minute=59, second=59, microsecond=0)

    month_start_ms = int(month_start.timestamp() * 1000)
    day_end_ms = int(day_end.timestamp() * 1000)

    closed_stage_ids = [
        stage_map[label] for label in CLOSED_STAGES_LABELS if label in stage_map
    ]

    filters = [
        {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
        {"propertyName": "dealstage", "operator": "IN", "values": closed_stage_ids},
        {"propertyName": "closedate", "operator": "GTE", "value": str(month_start_ms)},
        {"propertyName": "closedate", "operator": "LTE", "value": str(day_end_ms)},
    ]

    deals = search_deals(filters, ["amount", "dealname", "dealstage", "hubspot_owner_id"])

    totals = {}
    for deal in deals:
        props = deal.get("properties", {})
        amount = props.get("amount") or "0"
        owner_id = str(props.get("hubspot_owner_id", ""))
        try:
            val = float(amount)
        except (ValueError, TypeError):
            val = 0.0
        totals[owner_id] = totals.get(owner_id, 0.0) + val

    month_label = now_cst.strftime("%B %Y")
    lines = [f"📊 *Monthly Closed Revenue — {month_label}*\n"]
    grand_total = 0.0
    for owner_id, total in sorted(totals.items(), key=lambda x: -x[1]):
        name = owners.get(owner_id, f"Owner {owner_id}")
        lines.append(f"👤 {name}: {fmt_usd(total)}")
        grand_total += total
    lines.append(f"\n💰 *Total: {fmt_usd(grand_total)}*")
    return "\n".join(lines)


def open_pipeline_value(pipeline_id, stage_map, owners):
    excluded_stage_ids = [
        stage_map[label] for label in EXCLUDED_STAGE_LABELS if label in stage_map
    ]

    filters = [
        {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
        {"propertyName": "dealstage", "operator": "NOT_IN", "values": excluded_stage_ids},
        {"propertyName": "amount", "operator": "GT", "value": "0"},
    ]

    deals = search_deals(filters, ["amount", "dealname", "dealstage", "hubspot_owner_id"])

    totals = {}
    for deal in deals:
        props = deal.get("properties", {})
        amount = props.get("amount") or "0"
        owner_id = str(props.get("hubspot_owner_id", ""))
        try:
            val = float(amount)
        except (ValueError, TypeError):
            val = 0.0
        if val > 0:
            totals[owner_id] = totals.get(owner_id, 0.0) + val

    now_cst = datetime.now(CST)
    date_label = now_cst.strftime("%B %-d, %Y")
    lines = [f"🔭 *Open Pipeline Value — {date_label}*\n"]
    grand_total = 0.0
    for owner_id, total in sorted(totals.items(), key=lambda x: -x[1]):
        name = owners.get(owner_id, f"Owner {owner_id}")
        lines.append(f"👤 {name}: {fmt_usd(total)}")
        grand_total += total
    lines.append(f"\n📦 *Total Open: {fmt_usd(grand_total)}*")
    return "\n".join(lines)


def main():
    pipeline_id, stage_map = get_pipeline_and_stage_ids()
    owners = get_owners()

    msg1 = monthly_closed_revenue(pipeline_id, stage_map, owners)
    send_slack(msg1)

    time.sleep(2)

    msg2 = open_pipeline_value(pipeline_id, stage_map, owners)
    send_slack(msg2)


if __name__ == "__main__":
    main()
