"""Draw the mod's cover / logo for the Steam Workshop and the launcher.

Run from the repository root:  python tools/make_cover.py
Writes workshop/cover.png (1024 x 1024) - the same seal as the program's icon
(tools/build_exe.py: a gilt four-pointed diamond in a gilt ring on navy), a
small crown, the whispers around it, and the name.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workshop" / "cover.png"
FONTS = Path("C:/Windows/Fonts")

S = 2048                                   # drawn large, then scaled down for smooth edges
NAVY_HI, NAVY_LO = (0x22, 0x3a, 0x58), (0x07, 0x0c, 0x14)
GOLD, GOLD_HI, GOLD_LO = (0xc9, 0xa4, 0x5c), (0xf0, 0xd9, 0x9c), (0x8a, 0x6a, 0x32)
PARCHMENT = (0xd8, 0xc7, 0xa0)


def font(names: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for name in names:
        try:
            return ImageFont.truetype(str(FONTS / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def background() -> Image.Image:
    """A navy field, lighter behind the seal, darker at the edges."""
    img = Image.new("RGB", (S, S))
    px = img.load()
    cx, cy = S / 2, S * 0.40
    far = math.hypot(S / 2, S * 0.62)
    for y in range(S):
        for x in range(0, S):
            t = min(1.0, math.hypot(x - cx, y - cy) / far) ** 1.2
            px[x, y] = tuple(int(NAVY_HI[i] + (NAVY_LO[i] - NAVY_HI[i]) * t) for i in range(3))
    return img


def spaced(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, y: int, fill, spacing: float) -> None:
    """Centred text with letter spacing."""
    widths = [draw.textlength(ch, font=f) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = (S - total) / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=f, fill=fill)
        x += w + spacing


def main() -> None:
    img = background()

    # the whispers: faint arcs on either side of the seal
    waves = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    wd = ImageDraw.Draw(waves)
    cx, cy, r_out = S // 2, int(S * 0.40), int(S * 0.20)
    for k in range(4):
        r = r_out + 70 + k * 62
        alpha = 150 - k * 34
        box = (cx - r, cy - r, cx + r, cy + r)
        wd.arc(box, 200, 250, fill=GOLD + (alpha,), width=10 - k)
        wd.arc(box, 290, 340, fill=GOLD + (alpha,), width=10 - k)
        wd.arc(box, 20, 55, fill=GOLD + (alpha,), width=10 - k)
        wd.arc(box, 125, 160, fill=GOLD + (alpha,), width=10 - k)
    img.paste(waves, (0, 0), waves)

    d = ImageDraw.Draw(img)
    # the seal: a gilt rim, a navy field, a thin inner ring
    r_ring = int(r_out * 0.90)
    d.ellipse((cx - r_out - 8, cy - r_out - 8, cx + r_out + 8, cy + r_out + 8), fill=GOLD_LO)
    d.ellipse((cx - r_out, cy - r_out, cx + r_out, cy + r_out), fill=GOLD)
    d.ellipse((cx - r_ring, cy - r_ring, cx + r_ring, cy + r_ring), fill=NAVY_HI)
    ri = int(r_ring * 0.93)
    d.ellipse((cx - ri, cy - ri, cx + ri, cy + ri), outline=GOLD_LO, width=7)
    # the cross behind the diamond
    arm = int(r_out * 0.83)
    d.rectangle((cx - 9, cy - arm, cx + 9, cy + arm), fill=GOLD)
    d.rectangle((cx - arm, cy - 9, cx + arm, cy + 9), fill=GOLD)
    # the four-pointed diamond, faceted
    a, b = int(r_out * 0.62), int(r_out * 0.75)
    top, right, bottom, left = (cx, cy - b), (cx + a, cy), (cx, cy + b), (cx - a, cy)
    for pts, col in (((top, right, (cx, cy)), GOLD_HI), ((right, bottom, (cx, cy)), GOLD),
                     ((bottom, left, (cx, cy)), GOLD_LO), ((left, top, (cx, cy)), GOLD)):
        d.polygon(pts, fill=col)
    ia, ib = int(a * 0.42), int(b * 0.42)
    d.polygon(((cx, cy - ib), (cx + ia, cy), (cx, cy + ib), (cx - ia, cy)), fill=NAVY_LO)
    ja, jb = int(a * 0.28), int(b * 0.28)
    d.polygon(((cx, cy - jb), (cx + ja, cy), (cx, cy + jb), (cx - ja, cy)), fill=GOLD_HI)

    # a small crown resting on the seal
    base_y = cy - r_out - 4
    w, h = int(r_out * 0.62), int(r_out * 0.40)
    band = (cx - w // 2, base_y - int(h * 0.28), cx + w // 2, base_y)
    d.rectangle(band, fill=GOLD)
    d.rectangle((band[0], band[3] - 12, band[2], band[3]), fill=GOLD_LO)
    points = [(cx - w // 2, band[1])]
    for i, px_ in enumerate((-0.5, -0.25, 0.0, 0.25, 0.5)):
        x = cx + int(w * px_)
        peak = base_y - h if i in (0, 2, 4) else base_y - int(h * 0.62)
        points += [(x, peak)]
        if i < 4:
            points += [(cx + int(w * (px_ + 0.125)), band[1] - int(h * 0.05))]
    points += [(cx + w // 2, band[1])]
    d.polygon(points, fill=GOLD)
    for px_ in (-0.5, 0.0, 0.5):
        x, y = cx + int(w * px_), base_y - h
        d.ellipse((x - 17, y - 17, x + 17, y + 17), fill=GOLD_HI)
    for px_ in (-0.25, 0.25):
        x, y = cx + int(w * px_), base_y - int(h * 0.62)
        d.ellipse((x - 12, y - 12, x + 12, y + 12), fill=GOLD_HI)
    gy = (band[1] + band[3] - 12) // 2
    d.polygon(((cx, gy - 20), (cx + 16, gy), (cx, gy + 20), (cx - 16, gy)), fill=GOLD_HI)

    # the name
    title = font(("GARABD.TTF", "BASKVILL.TTF", "georgiab.ttf"), 250)
    second = font(("GARABD.TTF", "BASKVILL.TTF", "georgiab.ttf"), 132)
    small = font(("GARAIT.TTF", "georgiai.ttf"), 78)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    spaced(gd, "WHISPERS", title, int(S * 0.635), GOLD + (255,), 26)
    spaced(gd, "IN THE COURT", second, int(S * 0.775), GOLD + (255,), 34)
    img.paste(glow.filter(ImageFilter.GaussianBlur(18)), (0, 0), glow.filter(ImageFilter.GaussianBlur(18)).split()[3]
              .point(lambda v: v * 0.35))
    d = ImageDraw.Draw(img)
    spaced(d, "WHISPERS", title, int(S * 0.635), GOLD_HI, 26)
    spaced(d, "IN THE COURT", second, int(S * 0.775), GOLD, 34)
    # a rule between the name and the line under it
    yr = int(S * 0.872)
    d.line((S * 0.30, yr, S * 0.46, yr), fill=GOLD_LO, width=5)
    d.line((S * 0.54, yr, S * 0.70, yr), fill=GOLD_LO, width=5)
    d.polygon(((S / 2, yr - 16), (S / 2 + 16, yr), (S / 2, yr + 16), (S / 2 - 16, yr)), fill=GOLD)
    spaced(d, "An AI court for Europa Universalis V", small, int(S * 0.895), PARCHMENT, 2)

    # a thin gilt frame with diamonds at the corners
    m = int(S * 0.035)
    d.rectangle((m, m, S - m, S - m), outline=GOLD_LO, width=6)
    d.rectangle((m + 18, m + 18, S - m - 18, S - m - 18), outline=GOLD_LO, width=2)
    for x, y in ((m, m), (S - m, m), (m, S - m), (S - m, S - m)):
        d.polygon(((x, y - 30), (x + 30, y), (x, y + 30), (x - 30, y)), fill=GOLD)

    out = img.resize((1024, 1024), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(OUT, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
