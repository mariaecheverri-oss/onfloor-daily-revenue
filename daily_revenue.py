import os
import time
from datetime import datetime

import pytz
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

CST = pytz.timezone("America/Chicago")
HUBSPOT_BASE = "https://api.hubapi.com"
PIPELINE_NAME = "ONFLOOR - NEW BUSINESS"

CLOSED_STAGE_LABELS = {
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


def hs_headers():
    return {
        "Authorization": f"Bearer {os.environ['HUBSPOT_TOKEN']}",
        "Content-Type": "application/json",
    }


def get_pipeline_and_stages():
    resp = requests.get(f"{HUBSPOT_BASE}/crm/v3/pipelines/deals", headers=hs_headers())
    resp.raise_for_status()
    for pipeline in resp.json().get("results", []):
        if pipeline["label"] == PIPELINE_NAME:
            stage_map = {s["label"]: s["id"] for s in pipeline.get("stages", [])}
            return pipeline["id"], stage_map
    raise ValueError(f"Pipeline '{PIPELINE_NAME}' not found")


def get_owners():
    resp = requests.get(f"{HUBSPOT_BASE}/crm/v3/owners", headers=hs_headers())
    resp.raise_for_status()
    owners = {}
    for o in resp.json().get("results", []):
        name = f"{o.get('firstName', '')} {o.get('lastName', '')}".strip()
        owners[str(o["id"])] = name or f"Owner {o['id']}"
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
            f"{HUBSPOT_BASE}/crm/v3/objects/deals/search",
            headers=hs_headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return results


def fmt_usd(amount):
    return f"${int(round(float(amount))):,}"


def send_slack(text):
    resp = requests.post(
        os.environ["SLACK_WEBHOOK_URL"], json={"text": text}
    )
    resp.raise_for_status()


def build_closed_revenue_message(pipeline_id, stage_map, owners):
    now_cst = datetime.now(CST)
    month_start = now_cst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day_end = now_cst.replace(hour=23, minute=59, second=59, microsecond=0)

    start_ms = int(month_start.timestamp() * 1000)
    end_ms = int(day_end.timestamp() * 1000)

    stage_ids = [stage_map[s] for s in CLOSED_STAGE_LABELS if s in stage_map]

    filters = [
        {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
        {"propertyName": "dealstage", "operator": "IN", "values": stage_ids},
        {"propertyName": "closedate", "operator": "GTE", "value": str(start_ms)},
        {"propertyName": "closedate", "operator": "LTE", "value": str(end_ms)},
    ]

    deals = search_deals(filters, ["amount", "dealname", "dealstage", "hubspot_owner_id"])

    totals = {}
    counts = {}
    for deal in deals:
        props = deal.get("properties", {})
        owner_id = str(props.get("hubspot_owner_id") or "")
        try:
            val = float(props.get("amount") or 0)
        except (ValueError, TypeError):
            val = 0.0
        totals[owner_id] = totals.get(owner_id, 0.0) + val
        counts[owner_id] = counts.get(owner_id, 0) + 1

    month_label = now_cst.strftime("%B %Y")
    lines = [f"📊 *Monthly Closed Revenue — {month_label}*\n"]
    grand_total = 0.0
    grand_count = 0
    for owner_id, total in sorted(totals.items(), key=lambda x: -x[1]):
        name = owners.get(owner_id, f"Owner {owner_id}")
        n = counts[owner_id]
        label = "customer" if n == 1 else "customers"
        lines.append(f"👤 {name}: {fmt_usd(total)} — {n} {label}")
        grand_total += total
        grand_count += n
    grand_label = "customer" if grand_count == 1 else "customers"
    lines.append(f"\n💰 *Total: {fmt_usd(grand_total)} — {grand_count} {grand_label}*")
    return "\n".join(lines)


def build_open_pipeline_message(pipeline_id, stage_map, owners):
    excluded_ids = {stage_map[s] for s in EXCLUDED_STAGE_LABELS if s in stage_map}
    open_stage_ids = [sid for sid in stage_map.values() if sid not in excluded_ids]

    filters = [
        {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
        {"propertyName": "dealstage", "operator": "IN", "values": open_stage_ids},
        {"propertyName": "amount", "operator": "GT", "value": "0"},
    ]

    deals = search_deals(filters, ["amount", "dealname", "dealstage", "hubspot_owner_id"])

    totals = {}
    for deal in deals:
        props = deal.get("properties", {})
        owner_id = str(props.get("hubspot_owner_id") or "")
        try:
            val = float(props.get("amount") or 0)
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


@app.route("/trigger", methods=["POST"])
def trigger():
    secret = os.environ.get("TRIGGER_SECRET", "")
    incoming = request.headers.get("X-Trigger-Secret", "")
    if not incoming or incoming != secret:
        return jsonify({"error": "Unauthorized"}), 401

    now_cst = datetime.now(CST)
    if now_cst.weekday() >= 5:
        return jsonify({"message": "Skipping weekend"}), 200

    pipeline_id, stage_map = get_pipeline_and_stages()
    owners = get_owners()

    msg1 = build_closed_revenue_message(pipeline_id, stage_map, owners)
    send_slack(msg1)

    time.sleep(2)

    msg2 = build_open_pipeline_message(pipeline_id, stage_map, owners)
    send_slack(msg2)

    return jsonify({"message": "Reports sent"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
