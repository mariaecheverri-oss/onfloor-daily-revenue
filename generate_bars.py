from pathlib import Path
from PIL import Image, ImageDraw

WIDTH = 400
HEIGHT = 40
RADIUS = 8
BG_COLOR = "#E0E0E0"
FILL_COLOR = "#2EB67D"

output_dir = Path("bars")
output_dir.mkdir(exist_ok=True)

for pct in range(0, 101):
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background (full rounded rect)
    draw.rounded_rectangle([0, 0, WIDTH - 1, HEIGHT - 1], radius=RADIUS, fill=BG_COLOR)

    # Filled portion — skip entirely for 0%
    if pct > 0:
        fill_width = max(int(WIDTH * pct / 100), RADIUS * 2)
        draw.rounded_rectangle([0, 0, fill_width - 1, HEIGHT - 1], radius=RADIUS, fill=FILL_COLOR)

    img = img.convert("RGB")
    img.save(output_dir / f"bar_{pct}.png")

print(f"Generated {pct} images in bars/")
