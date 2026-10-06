"""New names for states, chosen in play.

EU5 can only show a country name that is a localisation key, so the mod keeps
a pool of keys - votc_cname_1 ... votc_cname_100 and their _ADJ adjectives -
in main_menu/localization/english/votc_names_l_english.yml. When a realm is
renamed, Court Brain gives the new name a free key, writes the file, and only
then sends the change to the game (the same mail reloads the localisation).

The registry lives in the user's court_brain folder, not in the mod, so a
reinstall of the mod or a restart of Court Brain never loses a name: both
rewrite the file from the registry. A key, once given, is never reused - old
saves that use it keep their name.
"""

from __future__ import annotations

import json
from pathlib import Path

SLOTS = 100
LOC_REL = Path("main_menu") / "localization" / "english" / "votc_names_l_english.yml"


class NameRegistry:
    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / "country_names.json"
        try:
            self.items: dict[str, dict] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = {}

    def assign(self, name: str, adjective: str, *, campaign: int, tag: str) -> int:
        """The key number for this name (the same one if it was already given)."""
        for k, v in self.items.items():
            if v.get("name") == name and v.get("adj") == adjective and v.get("campaign") == campaign:
                return int(k)
        free = next((n for n in range(1, SLOTS + 1) if str(n) not in self.items), 0)
        if not free:
            return 0
        self.items[str(free)] = {"name": name, "adj": adjective, "campaign": campaign, "tag": tag}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
        return free

    def write_loc(self, mod_dir: Path) -> None:
        from .mailbox import escape_loc
        lines = [" # Written by Court Brain (courtbrain/names.py). Names chosen in the game."]
        for n in range(1, SLOTS + 1):
            item = self.items.get(str(n)) or {}
            lines.append(f' votc_cname_{n}:0 "{escape_loc(item.get("name") or "Whispers in the Court")}"')
            lines.append(f' votc_cname_{n}_ADJ:0 "{escape_loc(item.get("adj") or item.get("name") or "Whispers in the Court")}"')
        _write_everywhere(mod_dir, "votc_names", lines)


def _write_everywhere(mod_dir: Path, stem: str, lines: list[str]) -> None:
    """In every language the mod is installed for: the game reads only its own."""
    root = Path(mod_dir) / LOC_REL.parent.parent
    folders = [d for d in root.iterdir() if d.is_dir()] if root.is_dir() else []
    for folder in folders or [root / "english"]:
        lang = folder.name
        target = folder / f"{stem}_l_{lang}.yml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xef\xbb\xbf" + "\n".join([f"l_{lang}:", ""] + lines + [""]).encode("utf-8"))


class PlaceNames:
    """New names for places (rename_location), the same way: a pool of keys
    votc_lname_1 ... votc_lname_<SLOTS>, given out once and never reused, kept in the
    user's court_brain folder and written into the mod at every install and change."""

    SLOTS = 400

    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / "location_names.json"
        try:
            self.items: dict[str, dict] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = {}

    def assign(self, name: str, *, location: str, campaign: int) -> int:
        """The key number for this place's new name (the same one if already given)."""
        for k, v in self.items.items():
            if v.get("name") == name and v.get("location") == location and v.get("campaign") == campaign:
                return int(k)
        free = next((n for n in range(1, self.SLOTS + 1) if str(n) not in self.items), 0)
        if not free:
            return 0
        self.items[str(free)] = {"name": name, "location": location, "campaign": campaign}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
        return free

    def write_loc(self, mod_dir: Path) -> None:
        from .mailbox import escape_loc
        lines = [" # Written by Court Brain (courtbrain/names.py). Places renamed in the game."]
        for n in range(1, self.SLOTS + 1):
            item = self.items.get(str(n)) or {}
            lines.append(f' votc_lname_{n}:0 "{escape_loc(item.get("name") or "Whispers in the Court")}"')
        _write_everywhere(mod_dir, "votc_lnames", lines)
