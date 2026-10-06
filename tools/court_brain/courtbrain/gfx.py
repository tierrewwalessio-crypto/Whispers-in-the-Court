"""The game's own artwork for the side panel.

EU5 draws its windows with textures - a dark lacquered background, gilded
dividers, carved corner ornaments - kept as DDS files in the game folder.
Tk cannot read DDS, and Court Brain installs nothing, so this module decodes
the few it needs (block-compressed DXT1/DXT5, the format of the game's
interface pieces) in plain Python, once, and keeps them as PNG files in Court
Brain's own folder. Nothing of the game is copied anywhere else.

If a file is missing or in another format, the panel simply draws that piece
itself.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

VERSION = 3
UI = Path("game") / "main_menu" / "gfx" / "interface"

# The game's pieces the panel uses (all masks, painted gold here).
PIECES = {
    "ornament": ("component_masks/ornament_alpha_1.dds", 0),
    "divider": ("component_masks/event_divider_left.dds", 0),
    "corner": ("component_decoration/bg_corner_flavor_01.dds", 0),
}


# ----------------------------------------------------------------------
# DDS
# ----------------------------------------------------------------------

def _rgb565(c: int) -> tuple[int, int, int]:
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _color_block(data: bytes, off: int, dxt1: bool) -> list[tuple[int, int, int, int]]:
    c0, c1, bits = struct.unpack_from("<HHI", data, off)
    r0, g0, b0 = _rgb565(c0)
    r1, g1, b1 = _rgb565(c1)
    if c0 > c1 or not dxt1:
        pal = [(r0, g0, b0, 255), (r1, g1, b1, 255),
               ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255),
               ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255)]
    else:
        pal = [(r0, g0, b0, 255), (r1, g1, b1, 255),
               ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255), (0, 0, 0, 0)]
    return [pal[(bits >> (2 * i)) & 3] for i in range(16)]


def _alpha_block(data: bytes, off: int) -> list[int]:
    a0, a1 = data[off], data[off + 1]
    bits = int.from_bytes(data[off + 2:off + 8], "little")
    if a0 > a1:
        pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    return [pal[(bits >> (3 * i)) & 7] for i in range(16)]


def decode_dds(path: Path) -> tuple[int, int, bytearray]:
    """(width, height, RGBA bytes) of the top level of a DXT1/DXT3/DXT5 DDS."""
    data = path.read_bytes()
    if data[:4] != b"DDS ":
        raise ValueError("not a DDS file")
    h, w = struct.unpack_from("<II", data, 12)
    four = data[84:88]
    if four not in (b"DXT1", b"DXT3", b"DXT5"):
        raise ValueError(f"unsupported format {four!r}")
    dxt1 = four == b"DXT1"
    size = 8 if dxt1 else 16
    out = bytearray(w * h * 4)
    off = 128
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            if dxt1:
                px = _color_block(data, off, True)
                alpha = None
            else:
                px = _color_block(data, off + 8, False)
                if four == b"DXT5":
                    alpha = _alpha_block(data, off)
                else:
                    raw = int.from_bytes(data[off:off + 8], "little")
                    alpha = [((raw >> (4 * i)) & 15) * 17 for i in range(16)]
            off += size
            for i in range(16):
                x, y = bx + (i & 3), by + (i >> 2)
                if x >= w or y >= h:
                    continue
                r, g, b, a = px[i]
                p = (y * w + x) * 4
                out[p:p + 4] = bytes((r, g, b, a if alpha is None else alpha[i]))
    return w, h, out


# ----------------------------------------------------------------------
# PNG
# ----------------------------------------------------------------------

def write_png(path: Path, w: int, h: int, rgba: bytes) -> None:
    raw = b"".join(b"\x00" + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(png)
    tmp.replace(path)


# ----------------------------------------------------------------------
# Small image operations (RGBA bytearrays)
# ----------------------------------------------------------------------

Image = tuple[int, int, bytearray]


def shrink(img: Image, f: int) -> Image:
    """Box filter by an integer factor, with premultiplied alpha (no dark fringes)."""
    w, h, px = img
    if f <= 1:
        return img
    nw, nh = w // f, h // f
    out = bytearray(nw * nh * 4)
    area = f * f
    for y in range(nh):
        for x in range(nw):
            r = g = b = a = 0
            for yy in range(y * f, y * f + f):
                p = (yy * w + x * f) * 4
                for _ in range(f):
                    al = px[p + 3]
                    r += px[p] * al
                    g += px[p + 1] * al
                    b += px[p + 2] * al
                    a += al
                    p += 4
            q = (y * nw + x) * 4
            if a:
                out[q:q + 4] = bytes((r // a, g // a, b // a, a // area))
    return nw, nh, out


def mirror(img: Image) -> Image:
    w, h, px = img
    out = bytearray(len(px))
    for y in range(h):
        for x in range(w):
            p, q = (y * w + x) * 4, (y * w + (w - 1 - x)) * 4
            out[q:q + 4] = px[p:p + 4]
    return w, h, out


def flip(img: Image) -> Image:
    w, h, px = img
    row = w * 4
    return w, h, bytearray(b"".join(bytes(px[(h - 1 - y) * row:(h - y) * row]) for y in range(h)))


def join(left: Image, right: Image) -> Image:
    (w1, h, a), (w2, _h2, b) = left, right
    out = bytearray()
    for y in range(h):
        out += a[y * w1 * 4:(y + 1) * w1 * 4] + b[y * w2 * 4:(y + 1) * w2 * 4]
    return w1 + w2, h, out


def crop(img: Image, x0: int, y0: int, cw: int, ch: int) -> Image:
    w, _h, px = img
    out = bytearray()
    for y in range(y0, y0 + ch):
        out += px[(y * w + x0) * 4:(y * w + x0 + cw) * 4]
    return cw, ch, out


def tint(img: Image, color: str, *, relief: bool = False, opacity: float = 1.0) -> Image:
    """Paint a mask in a colour. relief=True keeps the mask's light and shade."""
    w, h, px = img
    cr, cg, cb = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    out = bytearray(len(px))
    for p in range(0, len(px), 4):
        a = int(px[p + 3] * opacity)
        if not a:
            continue
        if relief:
            lum = (px[p] * 3 + px[p + 1] * 5 + px[p + 2] * 2) / 10 / 255 * 1.3
            out[p:p + 4] = bytes((min(255, int(cr * lum)), min(255, int(cg * lum)), min(255, int(cb * lum)), a))
        else:
            out[p:p + 4] = bytes((cr, cg, cb, a))
    return w, h, out


def _load(game_dir: str, rel: str) -> Image | None:
    src = Path(game_dir) / UI / rel
    try:
        return decode_dds(src)
    except (OSError, ValueError, struct.error):
        return None


def prepare(game_dir: str, cache_dir: Path, gold: str = "#c9a45c") -> dict[str, Path]:
    """The panel's ornaments, gilded, as PNG files; name -> path of what exists."""
    cache = Path(cache_dir) / f"gfx_v{VERSION}"
    names = ("ornament", "ornament_faint", "divider", "divider_small", "corner_tl", "corner_tr",
             "corner_bl", "corner_br")
    found = {n: cache / f"{n}.png" for n in names if (cache / f"{n}.png").is_file()}
    if len(found) == len(names) or not game_dir:
        return found
    made: dict[str, Image] = {}
    orn = _load(game_dir, PIECES["ornament"][0])
    if orn:
        made["ornament"] = tint(shrink(orn, 5), gold, relief=True)
        made["ornament_faint"] = tint(shrink(orn, 2), gold, relief=True, opacity=0.13)
    div = _load(game_dir, PIECES["divider"][0])
    if div:
        # The game's divider is a line with a diamond near its left end. Cut
        # it through the diamond and mirror it: one diamond in the middle.
        w, h, px = div
        tall = [x for x in range(w) if sum(1 for y in range(h) if px[(y * w + x) * 4 + 3] > 96) > h // 4]
        if tall:
            mid = (tall[0] + tall[-1]) // 2 if tall[-1] - tall[0] < w // 3 else tall[0]
            div = crop(div, mid, 0, w - mid, h)
        whole = join(mirror(div), div)
        made["divider"] = tint(shrink(whole, 3), gold)
        made["divider_small"] = tint(shrink(whole, 5), gold, opacity=0.8)
    cor = _load(game_dir, PIECES["corner"][0])
    if cor:
        w, h, _ = cor
        n = 40
        made["corner_tl"] = tint(crop(cor, 0, 0, n, n), gold)
        made["corner_tr"] = tint(crop(cor, w - n, 0, n, n), gold)
        made["corner_bl"] = tint(crop(cor, 0, h - n, n, n), gold)
        made["corner_br"] = tint(crop(cor, w - n, h - n, n, n), gold)
    for name, img in made.items():
        try:
            write_png(cache / f"{name}.png", *img)
            found[name] = cache / f"{name}.png"
        except OSError:
            continue
    return found
