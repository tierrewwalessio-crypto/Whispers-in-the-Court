"""Hide the purple debug boxes (and the error platypus) that -debug_mode adds.

The mod needs EU5 started with -debug_mode (the bridge talks through the
console), and debug mode fills tooltips with "DEBUG INFO" boxes. They are
drawn by a handful of vanilla interface files, always behind the condition
IsBuildDebug. This module copies exactly those files from the installed game
into the mod and adds one more condition: the global variable
votc_show_debug. So in debug mode the boxes stay hidden until the player
asks for them from the console:

    effect set_global_variable = votc_show_debug      (show)
    effect remove_global_variable = votc_show_debug   (hide again)

The copies are made from the game the player actually has, at every
`--install`, so a game update never leaves the mod with stale interface
files. Debug menus, the console and the debug map mode are left alone.
"""

from __future__ import annotations

import re
from pathlib import Path

# Interface files whose debug boxes appear in ordinary play (tooltips).
FILES = (
    "in_game/gui/cooltip_types.gui",
    "in_game/gui/shared/country_tooltips.gui",
    "in_game/gui/shared/location_tooltips.gui",
    "in_game/gui/attribute_columns/cabinet.gui",
    "in_game/gui/alertmanager.gui",
    "main_menu/gui/shared/building_tooltips.gui",
    # the "error platypus": the error counter in the bottom right corner
    "in_game/gui/ingame_topbar.gui",
)

# Other conditions to gate, per file: (text in the game file, replacement).
EXTRA = {
    "in_game/gui/ingame_topbar.gui": [
        ('visible = "[And3(HasErrors,', 'visible = "[And3(And(HasErrors, ' + "GetGlobalVariable('votc_show_debug').IsSet),"),
    ],
}

GATE = "GetGlobalVariable('votc_show_debug').IsSet"
MARK = "# Whispers in the Court: copied from the game by courtbrain/hidedebug.py; debug boxes hidden unless votc_show_debug is set.\n"


def patch_text(text: str) -> tuple[str, int]:
    """Every IsBuildDebug inside a [...] expression also needs the player's permission."""
    count = 0

    def fix(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f"And(IsBuildDebug, {GATE})"

    # Only whole-word IsBuildDebug, never inside another identifier.
    return re.sub(r"(?<![\w.])IsBuildDebug(?![\w(])", fix, text), count


def install(game_dir: Path, mod_dir: Path) -> list[tuple[str, int]]:
    """Copy and patch the files; returns (file, conditions patched)."""
    done: list[tuple[str, int]] = []
    for rel in FILES:
        src = Path(game_dir) / "game" / rel
        if not src.is_file():
            continue
        raw = src.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig", errors="replace")
        patched, n = patch_text(text)
        for find, repl in EXTRA.get(rel, []):
            if find in patched:
                patched = patched.replace(find, repl)
                n += 1
        if not n:
            continue
        dst = Path(mod_dir) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        out = MARK + patched
        dst.write_bytes((b"\xef\xbb\xbf" if bom else b"") + out.encode("utf-8"))
        done.append((rel, n))
    return done
