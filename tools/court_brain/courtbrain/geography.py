"""The map, by name: which locations a place means, and what a realm holds.

EU5's map_data/definitions.txt nests the world as
continent > subcontinent > region > area > province > locations, and the
names players see live in four localisation files. When land changes hands in
a conversation ("Cagliari passes to the Order"), the court speaks of places;
the game needs location keys. This module turns one into the other, and says
which provinces a realm holds (from a save's owners), so the referee can name
them exactly. Read once from the game folder, cached in Court Brain's folder.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

DEFINITIONS = Path("game") / "in_game" / "map_data" / "definitions.txt"
LOC_DIR = Path("game") / "main_menu" / "localization" / "{lang}"
NAME_FILES = ("location_names/location_names_l_{lang}.yml", "province_names_l_{lang}.yml",
              "area_l_{lang}.yml", "region_names_l_{lang}.yml")
LEVELS = ("region", "area", "province", "location")


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(the|of|province|county|duchy|area|region|land|lands|isle|island)\b", " ", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _near(a: str, b: str) -> bool:
    """One name inside the other, and close in length: 'palermo' ~ 'palermo città',
    never 'siracusa' ~ 'sira'."""
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    return short in long_ and (len(short) / len(long_) >= 0.6 or f" {short} " in f" {long_} ")


class Geography:
    def __init__(self, game_dir: str, cache_dir: Path, language: str = "english") -> None:
        # key -> (level, parent key); for provinces, the location keys they hold
        self.level: dict[str, str] = {}
        self.parent: dict[str, str] = {}
        self.children: dict[str, list[str]] = {}
        self.names: dict[str, str] = {}
        self.index: list[str] = []                  # location keys by save number (1-based)
        cache = Path(cache_dir) / "geography.json"
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            self.level, self.parent, self.children = data["level"], data["parent"], data["children"]
            self.names, self.index = data["names"], data["index"]
            return
        except (OSError, ValueError, KeyError):
            pass
        if not game_dir:
            return
        try:
            self._read(Path(game_dir), language)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"level": self.level, "parent": self.parent, "children": self.children,
                                         "names": self.names, "index": self.index}), encoding="utf-8")
        except OSError:
            pass

    def _read(self, game: Path, language: str) -> None:
        text = (game / DEFINITIONS).read_text(encoding="utf-8-sig", errors="replace")
        stack: list[str] = []
        for token in re.findall(r"[\w.-]+\s*=\s*\{|\}|[\w.-]+", text):
            if token.endswith("{"):
                key = token.split("=")[0].strip()
                depth = len(stack)                 # 0 continent, 1 subcontinent, 2 region, 3 area, 4 province
                if depth in (2, 3, 4):
                    lvl = LEVELS[depth - 2]
                    self.level[key] = lvl
                    if depth > 2:
                        self.parent[key] = stack[-1]
                        self.children.setdefault(stack[-1], []).append(key)
                stack.append(key)
            elif token == "}":
                if stack:
                    stack.pop()
            else:
                # a location, inside a province
                if len(stack) == 5:
                    self.level[token] = "location"
                    self.parent[token] = stack[-1]
                    self.children.setdefault(stack[-1], []).append(token)
        # The save numbers locations by their order in the innermost lists (places.py does the same).
        for m in re.finditer(r"=\s*\{([^{}]*)\}", text):
            self.index += m.group(1).split()
        base = game / str(LOC_DIR).format(lang=language)
        for rel in NAME_FILES:
            try:
                loc = (base / rel.format(lang=language)).read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for m in re.finditer(r'^ ([\w.-]+):\d* "(.*)"$', loc, re.M):
                if m.group(1) in self.level:
                    self.names[m.group(1)] = m.group(2)
        # Some names only point at another ("$barbastro$"): follow them.
        for key, value in list(self.names.items()):
            m = re.fullmatch(r"\$([\w.-]+)\$", value.strip())
            if m:
                self.names[key] = self.names.get(m.group(1)) or m.group(1).replace("_", " ").title()

    # ------------------------------------------------------------------
    def name(self, key: str) -> str:
        return self.names.get(key) or key.replace("_", " ").title()

    def locations_of(self, key: str) -> list[str]:
        """Every location under a region, area, province, or the location itself."""
        if self.level.get(key) == "location":
            return [key]
        out: list[str] = []
        for child in self.children.get(key, []):
            out += self.locations_of(child)
        return out

    def resolve(self, place: str, scope: str = "") -> tuple[str, list[str]]:
        """(the key it matched, its location keys) for a place named by the court.
        scope ("location", "province", "area", "region") decides between a city and
        the province named after it; without one, the province is preferred."""
        raw = (place or "").strip()
        if raw in self.level:
            return raw, self.locations_of(raw)
        want = _plain(raw)
        if not want:
            return "", []
        order = ([scope] if scope in LEVELS else []) + [l for l in ("province", "area", "location", "region")
                                                         if l != scope]
        for lvl in order:
            exact = [k for k, l in self.level.items() if l == lvl
                     and (want == _plain(self.names.get(k, "")) or want == _plain(k))]
            if exact:
                return exact[0], self.locations_of(exact[0])
        for lvl in order:
            close = [k for k, l in self.level.items() if l == lvl and _plain(self.names.get(k, ""))
                     and _near(want, _plain(self.names[k]))]
            if len(close) == 1:
                return close[0], self.locations_of(close[0])
        return "", []

    def holdings(self, owners: dict[int, int], cid: int, limit: int = 40) -> list[str]:
        """The provinces a realm holds, by name (with their area): 'Cagliari (Sardinia, 8 of 8)'."""
        count: dict[str, int] = {}
        for loc, owner in owners.items():
            if owner != cid or not 0 < loc <= len(self.index):
                continue
            prov = self.parent.get(self.index[loc - 1], "")
            if prov:
                count[prov] = count.get(prov, 0) + 1
        out = []
        for prov, n in sorted(count.items(), key=lambda kv: -kv[1]):
            total = len(self.children.get(prov, []))
            area = self.name(self.parent.get(prov, ""))
            out.append(f"{self.name(prov)} ({area}, {n} of {total})")
        return out[:limit]
