#!/usr/bin/env python3
"""Generate assets/icon.png (128x128): a chip with a spark on a rounded tile.

Drawn with Pillow only, so the icon carries no third-party licence. Run
again after changing it.
"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make(size: int = 128) -> Image.Image:
    big = size * 4
    tile = Image.new("RGB", (big, big))
    draw = ImageDraw.Draw(tile)
    for y in range(big):                      # night blue to mFruit blue
        f = y / big
        draw.line([(0, y), (big, y)], fill=(int(12 + 20 * f), int(30 + 80 * f), int(70 + 150 * f)))
    # chip body and pins
    body = (big * 0.24, big * 0.24, big * 0.76, big * 0.76)
    pin_w, pin_l = big * 0.045, big * 0.11
    for k in range(4):
        c = big * (0.33 + k * 0.113)
        draw.rounded_rectangle((c - pin_w, body[1] - pin_l, c + pin_w, body[1] + 4), radius=8, fill=(220, 228, 240))
        draw.rounded_rectangle((c - pin_w, body[3] - 4, c + pin_w, body[3] + pin_l), radius=8, fill=(220, 228, 240))
        draw.rounded_rectangle((body[0] - pin_l, c - pin_w, body[0] + 4, c + pin_w), radius=8, fill=(220, 228, 240))
        draw.rounded_rectangle((body[2] - 4, c - pin_w, body[2] + pin_l, c + pin_w), radius=8, fill=(220, 228, 240))
    draw.rounded_rectangle(body, radius=big * 0.06, fill=(18, 22, 30))
    # spark (the AI) inside the chip
    cx, cy = big * 0.5, big * 0.5
    points = []
    for k in range(8):
        r = big * (0.17 if k % 2 == 0 else 0.05)
        a = k * math.pi / 4 - math.pi / 2
        points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    draw.polygon(points, fill=(52, 199, 110))
    # rounded tile mask
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.22, fill=255)
    out = Image.new("RGBA", (big, big))
    out.paste(tile, (0, 0), mask)
    return out.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    path = os.path.join(ROOT, "assets", "icon.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    make().save(path)
    print(path)
