"""A "Command the battle" button in the game's own battle window.

The battle window (in_game/gui/battle_lateralview.gui) is the game's, and it
changes with the game's updates; so, like the debug boxes (hidedebug.py), it
is copied from the installed game at every install and one button is added,
beside the game's own retreat button. If the game's file no longer has the
retreat button where it used to be, nothing is copied: the army's own
"Command the Battle" ability (in_game/common/unit_abilities) still works.
"""

from __future__ import annotations

from pathlib import Path

FILE = "in_game/gui/battle_lateralview.gui"
ANCHOR = "#RETREAT BUTTON"
MARK = "# Whispers in the Court: copied from the game by courtbrain/battleview.py, with a \"Command\" button added.\n"

BUTTON = """
		# WHISPERS IN THE COURT: take command of this battle (courtbrain/battleview.py)
		header_button_left = {
			ignore_layout = yes
			position = { 34 -11 }
			parentanchor = center
			widgetanchor = bottom|hcenter
			size = { 28 28 }
			blockoverride "button_icon_stretch_sprite" {}
			blockoverride "header_button_frame_inner" {visible = no}
			blockoverride "header_button_icon_size" { size = { 22 22 } }
			blockoverride "icon_overlay" {}
			blockoverride "header_button_text_layout" {}
			visible = "[And(Or(Combat.GetDefender.IsPlayerInCombat, Combat.GetAttacker.IsPlayerInCombat), Not(Combat.IsNavalCombat))]"

			blockoverride "icon_texture" {
				texture = "gfx/interface/icons/unit_ability/votc_take_command.dds"
			}

			onclick = "[GetScriptedGui('votc_command_battle').Execute(GuiScope.SetRoot(GetPlayer.MakeScope).AddScope('votc_bloc', Combat.GetLocation.MakeScope).End)]"
			tooltip = "VOTC_BATTLE_BUTTON_TT"
		}
"""


def patch_text(text: str) -> str | None:
    """The game's file with the button after its retreat button; None if the file changed shape."""
    at = text.find(ANCHOR)
    if at < 0:
        return None
    start = text.find("{", at)
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = text.find("\n", i)
                end = len(text) if end < 0 else end + 1
                return text[:end] + BUTTON + text[end:]
    return None


def install(game_dir: Path, mod_dir: Path) -> bool:
    src = Path(game_dir) / "game" / FILE
    if not src.is_file():
        return False
    raw = src.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    patched = patch_text(raw.decode("utf-8-sig", errors="replace"))
    if patched is None:
        return False
    dst = Path(mod_dir) / FILE
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes((b"\xef\xbb\xbf" if bom else b"") + (MARK + patched).encode("utf-8"))
    return True
