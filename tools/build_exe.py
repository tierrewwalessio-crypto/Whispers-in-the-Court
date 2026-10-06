"""Build WhispersInTheCourt.exe: one file with Python, Court Brain and the mod.

Run from the repository root:  py -3 tools/build_exe.py

It checks the mod, installs PyInstaller for the build if it is missing (the
players never need it), draws the program's icon, and writes
dist/WhispersInTheCourt.exe. That file is all a player downloads: started, it
installs or updates the mod in EU5's mod folder and opens the Court Brain
window. Player2 remains a separate app, as it always was.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "tools" / "court_brain"
MOD = ROOT / "mod" / "WhispersInTheCourt"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "WhispersInTheCourt"


# ----------------------------------------------------------------------
# The icon: drawn here (a gilt diamond on a navy seal), no game artwork
# ----------------------------------------------------------------------

def _png(size: int, pixels: bytearray) -> bytes:
    raw = b"".join(b"\x00" + bytes(pixels[y * size * 4:(y + 1) * size * 4]) for y in range(size))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _draw(size: int) -> bytearray:
    px = bytearray(size * size * 4)
    c = (size - 1) / 2
    r_out, r_ring = size * 0.48, size * 0.43
    navy_hi, navy_lo = (0x2a, 0x44, 0x63), (0x0e, 0x17, 0x23)
    gold, gold_hi = (0xc9, 0xa4, 0x5c), (0xf0, 0xd9, 0x9c)
    ss = 3                                   # supersampling for smooth edges
    for y in range(size):
        for x in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    fx, fy = x + (sx + 0.5) / ss - 0.5, y + (sy + 0.5) / ss - 0.5
                    dx, dy = fx - c, fy - c
                    d = math.hypot(dx, dy)
                    if d > r_out:
                        continue
                    t = min(1.0, max(0.0, (fy / size)))
                    col = tuple(navy_hi[i] + (navy_lo[i] - navy_hi[i]) * t for i in range(3))
                    if d > r_ring:                                  # the gilt rim
                        col = gold if d < r_out - size * 0.012 else tuple(v * 0.6 for v in gold)
                    else:
                        # a four-pointed diamond star, and a small one inside
                        star = abs(dx) / (size * 0.30) + abs(dy) / (size * 0.36)
                        cross = min(abs(dx), abs(dy)) < size * 0.018 and max(abs(dx), abs(dy)) < size * 0.40
                        if star <= 1.0:
                            shade = 0.75 + 0.25 * (1 - (dx + dy) / (size * 0.6))
                            col = tuple(min(255, gold[i] * shade) for i in range(3))
                            if star <= 0.42:
                                col = navy_lo if star > 0.30 else gold_hi
                        elif cross:
                            col = gold
                        elif abs(d - r_ring * 0.93) < size * 0.008:
                            col = tuple(v * 0.7 for v in gold)
                    for i in range(3):
                        acc[i] += col[i]
                    acc[3] += 255
            n = ss * ss
            p = (y * size + x) * 4
            if acc[3]:
                k = acc[3] / 255
                px[p:p + 4] = bytes((int(acc[0] / k), int(acc[1] / k), int(acc[2] / k), int(acc[3] / n)))
    return px


def make_icon(path: Path) -> None:
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [_png(s, _draw(s)) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, data = b"", b""
    for s, img in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset + len(data))
        data += img
    path.write_bytes(head + entries + data)


# ----------------------------------------------------------------------

def main() -> int:
    check = subprocess.run([sys.executable, str(ROOT / "tools" / "validate_mod.py")])
    if check.returncode != 0:
        print("The mod has problems: fix them before building the exe.")
        return 1
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller (only needed to build the exe)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"], check=True)
    BUILD.mkdir(exist_ok=True)
    icon = BUILD / f"{NAME}.ico"
    make_icon(icon)
    shutil.copyfile(icon, BRAIN / "courtbrain" / "icon.ico")      # also the windows' own icon
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME, "--icon", str(icon),
        "--paths", str(BRAIN),
        "--add-data", f"{MOD}{';' if sys.platform == 'win32' else ':'}mod/WhispersInTheCourt",
        "--add-data", f"{BRAIN / 'courtbrain' / 'icon.ico'}{';' if sys.platform == 'win32' else ':'}courtbrain",
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
        str(BRAIN / f"{NAME}.py"),
    ]
    subprocess.run(args, check=True)
    exe = DIST / f"{NAME}.exe"
    print(f"\nReady: {exe} ({exe.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
