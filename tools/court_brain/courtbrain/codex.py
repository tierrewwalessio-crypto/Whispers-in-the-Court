"""A codex of the game's own content, built from the installed files.

The mod tells Court Brain what the realm looks like right now. It does not
tell it what a "Feudal De Jure Law" is, which advances belong to which age,
or what the Cossacks are called in a Polish game. That knowledge already
exists on disk, in the game's own script and localisation, so the codex
reads it once and caches a digest.

Why bother: without it the model invents plausible-sounding institutions
that do not exist, and the narration stops being about *this* game. With it,
a chancellor can argue for a law the player can actually go and pass.

Nothing here is ever fed to the model wholesale - `digest()` returns a few
hundred lines, and `lookup()` answers specific questions.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CODEX_VERSION = 6

_LOC_LINE = re.compile(r'^\s*([A-Za-z0-9_.\-]+):\s*\d*\s*"(.*)"\s*$')
_TOP_KEY = re.compile(r"^([a-z][a-z0-9_]*)\s*=\s*\{")
_FIELD = re.compile(r"^\s*([a-z_]+)\s*=\s*([A-Za-z0-9_:\.]+)\s*$")

# Loc markup the game renders but the model should not see: #T ... #!,
# icons like @gold!, and data functions.
_MARKUP = re.compile(r"#[A-Za-z_!]+|@[A-Za-z_]+!|\[[^\]]*\]|\\n")


def clean_loc(text: str) -> str:
    return _MARKUP.sub(" ", text).replace("  ", " ").strip()


@dataclass
class Law:
    key: str
    name: str = ""
    category: str = ""
    gov_group: str = ""
    options: list[str] = field(default_factory=list)
    option_names: list[str] = field(default_factory=list)


@dataclass
class Codex:
    game_dir: str = ""
    built_at: float = 0.0
    loc: dict[str, str] = field(default_factory=dict)
    laws: list[Law] = field(default_factory=list)
    privileges: list[str] = field(default_factory=list)
    government_reforms: list[str] = field(default_factory=list)
    advances: list[str] = field(default_factory=list)
    advance_ages: dict[str, str] = field(default_factory=dict)     # advance -> the age it belongs to
    goods_info: dict[str, dict[str, Any]] = field(default_factory=dict)  # good -> price, category, method
    building_goods: dict[str, list[str]] = field(default_factory=dict)  # building -> the goods it makes
    religions: list[str] = field(default_factory=list)
    culture_groups: list[str] = field(default_factory=list)
    goods: list[str] = field(default_factory=list)
    societal_values: list[str] = field(default_factory=list)
    estates: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    traits: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    def name_of(self, key: str, default: str = "") -> str:
        raw = self.loc.get(key)
        if raw is None:
            return default or key.replace("_", " ")
        return clean_loc(raw) or (default or key)

    def word(self, key: str) -> str:
        """The in-game name of an engine key, or the key made readable."""
        if not key:
            return ""
        raw = self.loc.get(key)
        if not raw:
            return key.replace("_", " ")
        # Localisation strings nest other keys as $key$.
        raw = re.sub(r"\$(\w+)\$", lambda m: self.word(m.group(1)) if m.group(1) != key else "", raw)
        return clean_loc(raw)

    def country(self, tag: str) -> str:
        return self.tags.get(tag, tag)

    def event_title(self, key: str) -> str:
        raw = self.loc.get(key + ".title")
        return clean_loc(raw) if raw else ""

    def lookup(self, needle: str, limit: int = 8) -> list[tuple[str, str]]:
        needle = needle.lower()
        out: list[tuple[str, str]] = []
        for key, value in self.loc.items():
            if needle in key.lower():
                out.append((key, clean_loc(value)))
                if len(out) >= limit:
                    break
        return out

    # ------------------------------------------------------------------
    def digest(self, *, government_group: str = "", max_laws: int = 40,
               own_laws: dict[str, str] | None = None) -> str:
        """A compact briefing on what exists in this game.

        Laws are filtered by the realm's government group where the game
        itself says which group they belong to, because a republic's
        chancellor has no business proposing a tribal law. When the save
        tells which laws the realm actually has (own_laws: law -> option in
        force), only those are listed, with the option in force: the others
        (harem laws for a republic, a Japanese isolation law in Tuscany) cannot
        be touched by this realm and only cost words.
        """
        lines: list[str] = []

        relevant = [
            law for law in self.laws
            if not law.gov_group or not government_group or law.gov_group == government_group
        ]
        if own_laws:
            mine = [law for law in relevant if law.key in own_laws]
            if mine:
                relevant = mine
        if relevant:
            lines.append("LAWS OF THE REALM (the player passes these in the government screen;")
            lines.append("you may argue for one, you cannot enact it yourself):")
            by_cat: dict[str, list[Law]] = {}
            for law in relevant[:max_laws]:
                by_cat.setdefault(law.category or "other", []).append(law)
            for cat, laws in sorted(by_cat.items()):
                lines.append(f"  [{cat}]")
                for law in laws:
                    names = law.option_names or law.options
                    # Some laws carry two dozen culture-specific options; the
                    # model needs to know the law exists and roughly what the
                    # choice is about, not to memorise every variant.
                    shown = names[:6]
                    opts = ", ".join(shown)
                    if len(names) > len(shown):
                        opts += f", +{len(names) - len(shown)} more"
                    now = (own_laws or {}).get(law.key, "")
                    if now:
                        pairs = dict(zip(law.options, law.option_names or law.options))
                        opts = f"now {pairs.get(now, self.word(now))}; " + opts
                    lines.append(f"    {law.name}: {opts}" if opts else f"    {law.name}")

        if self.estates:
            lines.append("")
            lines.append("ESTATES: " + ", ".join(self.estates))
        if self.societal_values:
            lines.append("SOCIETAL VALUES: " + ", ".join(self.societal_values))
        if self.privileges:
            sample = ", ".join(self.name_of(p) for p in self.privileges[:25])
            lines.append("")
            lines.append(f"ESTATE PRIVILEGES ({len(self.privileges)} in total), for example: {sample}")
        if self.government_reforms:
            sample = ", ".join(self.name_of(r) for r in self.government_reforms[:20])
            lines.append("")
            lines.append(f"GOVERNMENT REFORMS ({len(self.government_reforms)}), for example: {sample}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "version": CODEX_VERSION,
            "game_dir": self.game_dir,
            "built_at": self.built_at,
            "loc": self.loc,
            "laws": [law.__dict__ for law in self.laws],
            "privileges": self.privileges,
            "government_reforms": self.government_reforms,
            "advances": self.advances,
            "advance_ages": self.advance_ages,
            "goods_info": self.goods_info,
            "building_goods": self.building_goods,
            "religions": self.religions,
            "culture_groups": self.culture_groups,
            "goods": self.goods,
            "societal_values": self.societal_values,
            "estates": self.estates,
            "tags": self.tags,
            "traits": self.traits,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Codex":
        c = cls()
        c.game_dir = data.get("game_dir", "")
        c.built_at = data.get("built_at", 0.0)
        c.loc = data.get("loc", {})
        c.laws = [Law(**d) for d in data.get("laws", [])]
        c.privileges = data.get("privileges", [])
        c.government_reforms = data.get("government_reforms", [])
        c.advances = data.get("advances", [])
        c.advance_ages = data.get("advance_ages", {})
        c.goods_info = data.get("goods_info", {})
        c.building_goods = data.get("building_goods", {})
        c.religions = data.get("religions", [])
        c.culture_groups = data.get("culture_groups", [])
        c.goods = data.get("goods", [])
        c.societal_values = data.get("societal_values", [])
        c.estates = data.get("estates", [])
        c.tags = data.get("tags", {})
        c.traits = data.get("traits", [])
        return c


# ----------------------------------------------------------------------
# Building
# ----------------------------------------------------------------------

def _top_level_keys(path: Path) -> list[str]:
    out: list[str] = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        m = _TOP_KEY.match(line)
        if m:
            out.append(m.group(1))
    return out


def _keys_in_dir(root: Path) -> list[str]:
    out: list[str] = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.txt")):
        if path.name.startswith("_") or path.name in ("readme.txt",):
            continue
        out.extend(_top_level_keys(path))
    return out


_ADVANCE = re.compile(r"^(\w+)\s*=\s*\{(.*?)^\}", re.S | re.M)
_ADVANCE_AGE = re.compile(r"^\s*age\s*=\s*(\w+)", re.M)


def _advance_ages(root: Path) -> dict[str, str]:
    """Which age each advance belongs to (its "age = ..."), for what a realm has lately learnt."""
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for m in _ADVANCE.finditer(text):
            age = _ADVANCE_AGE.search(m.group(2))
            if age:
                out[m.group(1)] = age.group(1)
    return out


def _goods_info(root: Path) -> dict[str, dict[str, Any]]:
    """What each good is worth and how it is got (for what a realm has to trade)."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.txt")) if root.is_dir() else []:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for m in _ADVANCE.finditer(text):
            body = m.group(2)
            price = re.search(r"default_market_price\s*=\s*([0-9.]+)", body)
            cat = re.search(r"\bcategory\s*=\s*(\w+)", body)
            method = re.search(r"\bmethod\s*=\s*(\w+)", body)
            out[m.group(1)] = {"price": float(price.group(1)) if price else 1.0,
                               "category": cat.group(1) if cat else "", "method": method.group(1) if method else "",
                               "file": path.stem}
    return out


def _building_goods(root: Path) -> dict[str, list[str]]:
    """Which goods each building makes (its production methods' "produced")."""
    out: dict[str, list[str]] = {}
    for path in sorted(root.glob("*.txt")) if root.is_dir() else []:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for m in _ADVANCE.finditer(text):
            made = sorted(set(re.findall(r"\bproduced\s*=\s*(\w+)", m.group(2))))
            if made:
                out[m.group(1)] = made
    return out


def _parse_laws(root: Path, loc: dict[str, str]) -> list[Law]:
    laws: list[Law] = []
    if not root.is_dir():
        return laws
    for path in sorted(root.glob("*.txt")):
        if path.name.startswith("_") or path.name == "readme.txt":
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        current: Law | None = None
        depth = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = _TOP_KEY.match(line)
            if m and depth == 0:
                current = Law(key=m.group(1))
                current.name = clean_loc(loc.get(current.key, "")) or current.key.replace("_", " ")
                laws.append(current)
                depth = 1
                continue
            if current is None:
                continue
            opens = line.count("{")
            closes = line.count("}")
            if depth == 1:
                f = _FIELD.match(line)
                if f and f.group(1) in ("law_category", "law_gov_group"):
                    setattr(
                        current,
                        "category" if f.group(1) == "law_category" else "gov_group",
                        f.group(2),
                    )
                # A nested block at depth 1 whose key is not a known field is
                # one of the law's options (by_tradition, by_blood, ...).
                elif opens and re.match(r"^\s*([a-z][a-z0-9_]*)\s*=\s*\{", line):
                    key = re.match(r"^\s*([a-z][a-z0-9_]*)", line).group(1)
                    if key not in ("potential", "trigger", "allow", "country_modifier",
                                   "ai_will_do", "estate_preferences", "on_enact", "effect"):
                        current.options.append(key)
                        current.option_names.append(
                            clean_loc(loc.get(key, "")) or key.replace("_", " ")
                        )
            depth += opens - closes
            if depth <= 0:
                current = None
                depth = 0
    return laws


def _load_localisation(game_dir: Path, language: str) -> dict[str, str]:
    out: dict[str, str] = {}
    root = game_dir / "game" / "main_menu" / "localization" / language
    if not root.is_dir():
        # Fall back to anything that looks like a localisation tree.
        candidates = list((game_dir / "game").glob("**/localization/*"))
        root = next((c for c in candidates if c.is_dir() and c.name == language), root)
    if not root.is_dir():
        return out
    for path in root.rglob(f"*_l_{language}.yml"):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = _LOC_LINE.match(line)
            if m:
                out.setdefault(m.group(1), m.group(2))
    return out


def _prune_loc(c: "Codex") -> None:
    """Keep only the localisation the codex actually needs.

    The full English localisation is about 225,000 keys and 17 MB, which is
    absurd to cache and useless to carry around. What is worth keeping is the
    names of the things the codex indexes, plus country names (three capital
    letters), which is a few thousand entries.
    """
    wanted: set[str] = set()
    for law in c.laws:
        wanted.add(law.key)
        wanted.update(law.options)
    for group in (c.privileges, c.government_reforms, c.religions,
                  c.culture_groups, c.goods, c.advances):
        wanted.update(group)
    # Plus what the world picture read from a save needs in words: traits,
    # given names, event titles, and the few engine words for governments,
    # ranks, parliaments and subject bonds.
    wanted.update(c.traits)
    wanted.update(("nobles_estate", "clergy_estate", "burghers_estate", "peasants_estate",
                   "crown_estate", "tribes_estate", "dhimmi_estate", "cossacks_estate"))
    wanted.update(("monarchy", "republic", "theocracy", "tribe", "steppe_horde",
                   "estate_parliament", "rank_county", "rank_duchy", "rank_kingdom",
                   "rank_empire", "vassal", "fiefdom", "tributary", "dominion",
                   "appanage", "march", "colonial_nation"))
    kept = {k: v for k, v in c.loc.items()
            if k in wanted or k.startswith("name_") or k.endswith(".title")}
    for key, value in c.loc.items():
        if len(key) == 3 and key.isupper() and key.isalpha():
            kept[key] = value
            c.tags[key] = clean_loc(value)
    c.loc = kept


def build(game_dir: str | Path, language: str = "english") -> Codex:
    game_dir = Path(game_dir)
    common = game_dir / "game" / "in_game" / "common"
    c = Codex(game_dir=str(game_dir), built_at=time.time())
    c.loc = _load_localisation(game_dir, language)
    c.laws = _parse_laws(common / "laws", c.loc)
    c.privileges = _keys_in_dir(common / "estate_privileges")
    c.government_reforms = _keys_in_dir(common / "government_reforms")
    c.advances = _keys_in_dir(common / "advances")
    c.advance_ages = _advance_ages(common / "advances")
    c.goods_info = _goods_info(common / "goods")
    c.building_goods = _building_goods(common / "building_types")
    c.religions = _keys_in_dir(common / "religions")
    c.culture_groups = _keys_in_dir(common / "culture_groups")
    c.goods = _keys_in_dir(common / "goods")
    c.estates = [c.name_of(k, k) for k in _keys_in_dir(common / "estates")]
    c.traits = _keys_in_dir(common / "traits")

    # Societal values are named by the change_societal_value effect rather
    # than living in their own folder, so they come from the mirrored list.
    from .actions import SOCIETAL_VALUES
    c.societal_values = [v.replace("_vs_", " vs ").replace("_", " ") for v in SOCIETAL_VALUES]
    _prune_loc(c)
    return c


def load_or_build(game_dir: str | Path, cache_dir: str | Path, language: str = "english") -> Codex:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / "codex.json"
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("version") == CODEX_VERSION and data.get("game_dir") == str(game_dir):
                return Codex.from_json(data)
        except (OSError, json.JSONDecodeError):
            pass
    codex = build(game_dir, language)
    try:
        cache.write_text(json.dumps(codex.to_json()), encoding="utf-8")
    except OSError:
        pass
    return codex
