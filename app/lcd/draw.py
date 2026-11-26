"""Drawing helpers for the LCD: simple vector icons, big status, QR codes.

Icons are drawn with Pillow primitives (no image files), at any size, in the
mFruit theme colours. They are deliberately simple and large: the screen is
1.69 inches across.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

from mfruit_sdk.ui.theme import CONTENT_BOTTOM, CONTENT_TOP, MARGIN, SCREEN_W

from . import qr


def _rounded(d, box, radius: float, fill) -> None:
    """Pillow refuses a radius of half the box or more: clamp it."""
    (x0, y0), (x1, y1) = box
    limit = int(min(x1 - x0, y1 - y0) / 2) - 1
    d.rounded_rectangle([(x0, y0), (x1, y1)], radius=max(0, min(int(radius), limit)), fill=fill)


def icon(c, name: str, cx: int, cy: int, size: int, color, bg=None) -> None:
    """Draw icon ``name`` centred on (cx, cy) in a ``size`` box."""
    d = c.draw
    s = size / 24.0
    bg = bg or c.theme.bg

    def p(x: float, y: float) -> tuple:
        return (cx + (x - 12) * s, cy + (y - 12) * s)

    w = max(2, int(round(2 * s)))
    if name == "mic":
        _rounded(d, [p(8.5, 2.5), p(15.5, 15)], 3.5 * s, color)
        d.arc([p(5, 6), p(19, 18.5)], 0, 180, fill=color, width=w)
        d.line([p(12, 18.5), p(12, 21.5)], fill=color, width=w)
        d.line([p(8, 21.5), p(16, 21.5)], fill=color, width=w)
    elif name == "folder":
        d.polygon([p(2.5, 6), p(9, 6), p(11, 8), p(21.5, 8), p(21.5, 19.5), p(2.5, 19.5)], fill=color)
        d.rectangle([p(2.5, 10), p(21.5, 10.8)], fill=bg)
    elif name == "chip":
        _rounded(d, [p(6, 6), p(18, 18)], 2 * s, color)
        d.rectangle([p(9.5, 9.5), p(14.5, 14.5)], fill=bg)
        for k in (8.5, 12, 15.5):
            d.line([p(k, 2.5), p(k, 6)], fill=color, width=w)
            d.line([p(k, 18), p(k, 21.5)], fill=color, width=w)
            d.line([p(2.5, k), p(6, k)], fill=color, width=w)
            d.line([p(18, k), p(21.5, k)], fill=color, width=w)
    elif name == "gear":
        for k in range(8):
            a = k * math.pi / 4
            x0, y0 = 12 + 7.2 * math.cos(a), 12 + 7.2 * math.sin(a)
            x1, y1 = 12 + 10.5 * math.cos(a), 12 + 10.5 * math.sin(a)
            d.line([p(x0, y0), p(x1, y1)], fill=color, width=max(3, int(3.2 * s)))
        d.ellipse([p(4.5, 4.5), p(19.5, 19.5)], fill=color)
        d.ellipse([p(9, 9), p(15, 15)], fill=bg)
    elif name == "check":
        d.ellipse([p(1.5, 1.5), p(22.5, 22.5)], fill=color)
        d.line([p(7, 12.5), p(10.5, 16), p(17, 8.5)], fill=bg, width=max(2, int(2.6 * s)),
               joint="curve")
    elif name == "cross":
        d.ellipse([p(1.5, 1.5), p(22.5, 22.5)], fill=color)
        d.line([p(8, 8), p(16, 16)], fill=bg, width=max(2, int(2.6 * s)))
        d.line([p(16, 8), p(8, 16)], fill=bg, width=max(2, int(2.6 * s)))
    elif name == "warning":
        d.polygon([p(12, 2), p(22.5, 21), p(1.5, 21)], fill=color)
        d.line([p(12, 8.5), p(12, 14.5)], fill=bg, width=max(2, int(2.4 * s)))
        d.ellipse([p(10.8, 16.5), p(13.2, 18.9)], fill=bg)
    elif name == "spark":
        pts = []
        for k in range(8):
            r = 10 if k % 2 == 0 else 3.2
            a = k * math.pi / 4 - math.pi / 2
            pts.append(p(12 + r * math.cos(a), 12 + r * math.sin(a)))
        d.polygon(pts, fill=color)
    elif name == "plug":
        _rounded(d, [p(6, 8), p(18, 17)], 2 * s, color)
        d.line([p(9.5, 3), p(9.5, 8)], fill=color, width=w)
        d.line([p(14.5, 3), p(14.5, 8)], fill=color, width=w)
        d.line([p(12, 17), p(12, 22)], fill=color, width=w)
    elif name == "bolt":
        d.polygon([p(13.5, 1.5), p(5, 13.5), p(11, 13.5), p(10, 22.5), p(19, 10), p(13, 10)],
                  fill=color)
    elif name == "hammer":
        d.polygon([p(5, 4), p(15, 4), p(17, 7), p(15, 10), p(5, 10)], fill=color)
        d.line([p(10, 10), p(10, 21.5)], fill=color, width=max(3, int(3 * s)))
    elif name == "plus":
        d.line([p(12, 4), p(12, 20)], fill=color, width=max(3, int(3 * s)))
        d.line([p(4, 12), p(20, 12)], fill=color, width=max(3, int(3 * s)))
    else:
        d.ellipse([p(4, 4), p(20, 20)], outline=color, width=w)


def level_bars(c, cx: int, cy: int, level_db: float, phase: int, color) -> None:
    """A voice level meter: bars that follow the microphone level."""
    norm = max(0.0, min(1.0, (level_db + 60.0) / 50.0))
    bars = 9
    for i in range(bars):
        wave = 0.55 + 0.45 * math.sin(phase * 0.9 + i * 1.3)
        height = 6 + int(46 * norm * wave * (1 - abs(i - bars // 2) / (bars + 1)))
        x = cx - (bars * 10) // 2 + i * 10
        c.rounded((x, cy - height // 2, x + 6, cy + height // 2), 3, fill=color)


def qr_code(c, text: str, cx: int, top: int, max_size: int) -> int:
    """Draw a QR code (white quiet zone included) centred at cx; returns its height."""
    code = qr.QrCode(text.encode("utf-8"), "M")
    quiet = 2
    modules = code.size + 2 * quiet
    scale = max(1, max_size // modules)
    size = modules * scale
    x0 = cx - size // 2
    c.rect((x0, top, x0 + size - 1, top + size - 1), fill=(255, 255, 255))
    for y, row in enumerate(code.modules):
        for x, dark in enumerate(row):
            if dark:
                px = x0 + (x + quiet) * scale
                py = top + (y + quiet) * scale
                c.rect((px, py, px + scale - 1, py + scale - 1), fill=(0, 0, 0))
    return size


def wrapped(c, text: str, x: int, y: int, width: int, size: int = 14, weight: str = "regular",
            color=None, max_lines: int = 6, line_gap: int = 4, anchor_center: bool = False) -> int:
    """Wrapped text; returns the y below it."""
    lines = c.wrap(text, size, weight, width, max_lines=max_lines) if text else []
    for line in lines:
        if anchor_center:
            c.text(x + width // 2, y, line, size, weight, color or c.theme.text, anchor="ma")
        else:
            c.text(x, y, line, size, weight, color or c.theme.text)
        y += size + line_gap
    return y


def big_status(c, kind: str, title: str, body: str = "", tone_color=None,
               top: int = CONTENT_TOP + 6, body_lines: int = 4, compact: bool = False) -> int:
    """An icon, a large title and a short body, centred. Returns the y below.
    ``compact`` fits above a list (about 104 px tall)."""
    t = c.theme
    color = tone_color or {"check": t.success, "cross": t.error, "warning": t.warning}.get(kind,
                                                                                         t.accent)
    size, title_size, body_size = (30, 19, 13) if compact else (40, 20, 14)
    icon(c, kind, SCREEN_W // 2, top + size // 2 + 2, size, color)
    y = top + size + 8
    c.text(SCREEN_W // 2, y, title, title_size, "bold", t.text, anchor="ma",
           max_width=SCREEN_W - 2 * MARGIN)
    y += title_size + 8
    if body:
        y = wrapped(c, body, MARGIN + 4, y, SCREEN_W - 2 * MARGIN - 8, body_size, "regular",
                    t.text_muted, max_lines=2 if compact else body_lines, anchor_center=True,
                    line_gap=3)
    return y


def spinner_title(c, title: str, subtitle: str, phase: int, top: int = CONTENT_TOP + 8) -> int:
    t = c.theme
    c.spinner(SCREEN_W // 2, top + 22, 18, phase)
    y = top + 52
    c.text(SCREEN_W // 2, y, title, 20, "bold", t.text, anchor="ma", max_width=SCREEN_W - 2 * MARGIN)
    y += 28
    if subtitle:
        y = wrapped(c, subtitle, MARGIN + 4, y, SCREEN_W - 2 * MARGIN - 8, 14, "medium",
                    t.text_muted, max_lines=2, anchor_center=True)
    return y


def facts(c, rows: Sequence[tuple], top: int, label_w: int = 70, size: int = 14,
          bottom: int = CONTENT_BOTTOM) -> int:
    """Label/value rows ("Board", "ESP32-S3"); returns the y below."""
    t = c.theme
    y = top
    for row in rows:
        label, value = row[0], row[1]
        tone = row[2] if len(row) > 2 else None
        if y + size > bottom:
            break
        c.text(MARGIN + 2, y + 1, label, size - 2, "medium", t.text_faint)
        c.text(MARGIN + 2 + label_w, y, str(value), size, "semibold", tone or t.text,
               max_width=SCREEN_W - 2 * MARGIN - label_w - 4)
        y += size + 9
    return y


def short_lines(text: str, limit: int = 400) -> List[str]:
    return [l for l in (text or "").splitlines() if l.strip()][:limit]
