"""Location names: the save counts locations by number, the court by name.

A save file refers to every location by its index in the game's
map_data/definitions.txt (1-based: 3071 is Urbino), and the names players see
are in location_names_l_<language>.yml. Both are read once from the game
folder and cached in Court Brain's folder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DEFINITIONS = Path("game") / "in_game" / "map_data" / "definitions.txt"
NAMES = Path("game") / "main_menu" / "localization" / "{lang}" / "location_names" / "location_names_l_{lang}.yml"


class Places:
    def __init__(self, game_dir: str, cache_dir: Path, language: str = "english") -> None:
        self.keys: list[str] = []
        self.names: dict[str, str] = {}
        cache = Path(cache_dir) / "places.json"
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            self.keys, self.names = data["keys"], data["names"]
            return
        except (OSError, ValueError, KeyError):
            pass
        if not game_dir:
            return
        try:
            text = (Path(game_dir) / DEFINITIONS).read_text(encoding="utf-8-sig", errors="replace")
            for m in re.finditer(r"=\s*\{([^{}]*)\}", text):
                self.keys += m.group(1).split()
            loc = (Path(game_dir) / str(NAMES).format(lang=language)).read_text(encoding="utf-8-sig",
                                                                                errors="replace")
            self.names = {m.group(1): m.group(2) for m in re.finditer(r'^ ([\w.-]+):\d* "(.*)"$', loc, re.M)}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"keys": self.keys, "names": self.names}), encoding="utf-8")
        except OSError:
            self.keys, self.names = [], {}

    def name(self, location: int) -> str:
        """The name of location number `location`, as the player sees it."""
        if not 0 < location <= len(self.keys):
            return f"location {location}"
        key = self.keys[location - 1]
        return self.names.get(key) or key.replace("_", " ").title()
