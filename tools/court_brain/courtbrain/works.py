"""Works of the realm: what a ruler can order done to the land and to the state.

Build, pull down, raise or lower a building (a fortress, a university, a
market...), lay a road, grant a town its charter, convert a province's faith or
people, accept or tolerate a culture, grant or revoke an estate privilege,
enact or repeal a law, adopt or drop a government reform, move the capital,
invest in a place's development, order or prosperity.

The AI names the work in words ("pull down every castle in the kingdom"); this
module finds it in the game's own catalogue (read from the installed game),
finds the places, and writes the game's own effects - never anything the AI
wrote. Every work carries its price in the game: what the game itself charges
(a building under construction is paid for and takes its time), plus what it
costs the realm (treasury, stability, the estates who lose by it), sized by
how much of the realm it touches.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from . import simulated as SIM
from typing import Any

KINDS = {
    "build": "build a building",
    "demolish": "pull down a building",
    "upgrade": "enlarge a building",
    "downgrade": "reduce a building",
    "road": "lay a road",
    "charter": "grant a town or city charter",
    "convert_religion": "begin converting a place's people to a faith (some at once, the rest over years)",
    "convert_culture": "begin making a place's people adopt a culture (some at once, the rest over years)",
    "accept_culture": "accept a culture as the realm's own",
    "tolerate_culture": "tolerate a culture",
    "grant_privilege": "grant an estate privilege",
    "revoke_privilege": "revoke an estate privilege",
    "enact_policy": "enact a law or policy",
    "repeal_policy": "repeal a law or policy",
    "adopt_reform": "adopt a government reform",
    "drop_reform": "drop a government reform",
    "move_capital": "move the capital",
    "develop": "invest in a place's development",
    "integrate": "bind a place closer to the realm - integrated, then a core (officials, oaths, marriages, charters)",
    "control": "tighten the Crown's hold on a place",
    "prosperity": "invest in a place's prosperity",
    "depopulate": "people die, are killed or driven out",
    "repopulate": "settle people in a place",
    "rename": "give places new names",
    "trait_add": "a person gains a trait",
    "trait_remove": "a person loses a trait",
    "skill_up": "a person's skill grows",
    "skill_down": "a person's skill falls",
    "pay": "money handed over now, an exact sum",
    "state_religion": "make a faith the religion of the state",
}
# Who may receive a payment, as the game knows them: an estate's coffers (the rest -
# a person, a town, a granary, a church - is money out of the treasury and into their hands).
PAY_ESTATES = {"nobles": "nobles_estate", "nobility": "nobles_estate", "barons": "nobles_estate",
               "clergy": "clergy_estate", "church": "clergy_estate", "bishops": "clergy_estate",
               "burghers": "burghers_estate", "merchants": "burghers_estate", "guilds": "burghers_estate",
               "peasants": "peasants_estate", "commoners": "peasants_estate"}
PERSON_KINDS = {"trait_add", "trait_remove", "skill_up", "skill_down"}
SKILLS = {"adm": "administration", "dip": "diplomacy", "mil": "military skill"}
SKILL_POINTS = {1: 3, 2: 5, 3: 10}             # as the game's own events give them
MAX_RENAMES = 12
# Traits that harm the one who has them: gained by a wound, a vice, a disgrace - never
# bought. (The others are virtues, learning, health: tutors, masters and physicians cost.)
BAD_TRAITS = frozenset("""babbling_buffoon craven cruel drunkard embezzler greedy loose_lips malevolent naive
    sinner unsuited_for_country_ruling child_idiot child_slow child_rowdy careless lazy idler
    unsuited_for_army_command unsuited_for_naval_command corrupt deceitful fickle unmotivated arrogant abrasive
    stubborn blind castrated disfigured hunchback maimed one_eyed pockmarked_trait scarred sickly
    bubonic_plague_trait smallpox_trait eunuch""".split())
POP_TYPES = ("peasants", "laborers", "burghers", "nobles", "clergy", "soldiers", "slaves", "tribesmen")
POP_SHARE = {"depopulate": {1: 0.05, 2: 0.15, 3: 0.35}, "repopulate": {1: 0.03, 2: 0.07, 3: 0.12}}
PLACE_KINDS = {"build", "demolish", "upgrade", "downgrade", "road", "charter", "convert_religion",
               "convert_culture", "move_capital", "develop", "control", "prosperity", "depopulate", "repopulate",
               "integrate"}
REALM_WIDE_OK = {"demolish", "depopulate"}
MAX_PLACES = {"build": 3, "upgrade": 3, "downgrade": 6, "charter": 2, "move_capital": 1, "road": 1,
              "convert_religion": 12, "convert_culture": 12, "develop": 8, "control": 12, "prosperity": 8,
              "demolish": 40, "depopulate": 40, "repopulate": 8, "integrate": 8}
TIER = {1: "weak", 2: "mild", 3: "severe"}

# Words the court uses for the defences of the realm: every kind of fort.
_FORT_WORDS = ("fortification", "fortifications", "fort", "forts", "castle", "castles", "wall", "walls",
               "fortress", "fortresses", "bastion", "bastions", "defence", "defences", "defense", "defenses",
               "fortificazioni", "fortificazione", "castelli", "castello", "mura", "fortezze", "fortezza",
               "fortalezas", "castillos", "murallas", "forteresses", "chateaux", "burgen", "festungen")
_COASTAL = ("coastal", "coast", "naval battery", "costiere", "costiera", "batterie")
_SCHEMA_WHERE = ("location", "province", "area", "region", "realm")


def schema_item() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "what", "where", "scope", "to", "size", "who"],
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "what": {"type": "string", "description": ("What: the building (or 'fortifications' for every kind "
                                                        "of fort), the privilege, law, reform, culture or faith - "
                                                        "by its name in the game; the trait; the skill "
                                                        "(administration, diplomacy, military); for rename, the "
                                                        "places and their new names: 'Old -> New; Old2 -> New2'. "
                                                        "'' when the kind needs nothing.")},
            "who": {"type": "string", "description": ("For a trait or a skill: the person, by exact name from the "
                                                       "court list (the ruler, the heir, someone of the court, the "
                                                       "person spoken to). Otherwise ''.")},
            "where": {"type": "string", "description": ("Where: a place by its exact name (a town, a province, an "
                                                         "area, a region), or 'realm' for the whole realm. '' for "
                                                         "works on the state itself (laws, privileges...).")},
            "scope": {"type": "string", "enum": list(_SCHEMA_WHERE)},
            "to": {"type": "string", "description": "For a road: the place it leads to. Otherwise ''."},
            "size": {"type": "integer", "minimum": 1, "maximum": 3,
                     "description": "How much: 1 a little, 2 a good deal, 3 a great work (levels, investment)."},
        },
    }


# --------------------------------------------------------------------------
# The game's catalogue
# --------------------------------------------------------------------------
_TOP = re.compile(r"^([a-z][a-z0-9_]*)\s*=\s*\{", re.M)


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


_STOP = frozenset("""the of and for to in on a an with from by its their our privilege privileges right rights
    law laws policy estate estates reform del della dei degli delle di il la lo le gli per con da""".split())


def _near(a: str, b: str) -> bool:
    """One name inside the other and close in length ('university' ~ 'the university'),
    never a short word caught inside a long request ('rights' in 'nobles judicial rights')."""
    short, long_ = sorted((a, b), key=len)
    return bool(short) and short in long_ and len(short) / len(long_) >= 0.6


def _overlap(a: str, b: str) -> float:
    """Shared meaningful words, counting a shared stem of five letters as half a word."""
    wa = {w for w in a.split() if len(w) >= 4 and w not in _STOP}
    wb = {w for w in b.split() if len(w) >= 4 and w not in _STOP}
    whole = len(wa & wb)
    stems = len({w[:5] for w in wa - wb} & {w[:5] for w in wb - wa})
    return whole + 0.5 * stems


class Catalog:
    """Buildings, cultures, faiths, privileges, laws and reforms, with their names."""

    VERSION = 3

    def __init__(self, data: dict[str, Any]) -> None:
        self.buildings: dict[str, dict[str, str]] = data.get("buildings", {})
        self.cultures: dict[str, str] = data.get("cultures", {})
        self.religions: dict[str, str] = data.get("religions", {})
        self.privileges: dict[str, str] = data.get("privileges", {})
        self.policies: dict[str, str] = data.get("policies", {})
        self.reforms: dict[str, str] = data.get("reforms", {})
        # the reforms that ARE a form of government ("major = yes"): never dropped by a work
        self.major_reforms: set[str] = set(data.get("major_reforms", []))
        # trait key -> {"name", "category" (ruler, child, cabinet, health, general...), "conflicts"}
        self.traits: dict[str, dict[str, Any]] = data.get("traits", {})

    @classmethod
    def load(cls, game_dir: str, cache_dir: Path, codex: Any, language: str = "english") -> "Catalog":
        path = Path(cache_dir) / "works_catalog.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if (data.get("version") == cls.VERSION and data.get("game_dir") == str(game_dir)
                    and data.get("language") == language):
                return cls(data)
        except (OSError, ValueError):
            pass
        data = cls._read(Path(game_dir), codex, language)
        data.update(version=cls.VERSION, game_dir=str(game_dir), language=language)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return cls(data)

    @staticmethod
    def _read(game: Path, codex: Any, language: str) -> dict[str, Any]:
        from .codex import _load_localisation, clean_loc
        loc = _load_localisation(game, language) or _load_localisation(game, "english")

        def name(key: str) -> str:
            return clean_loc(loc.get(key, "")) or key.replace("_", " ")

        def keys_in(folder: str) -> list[tuple[str, str]]:
            out = []
            root = game / "game" / "in_game" / "common" / folder
            for f in sorted(root.glob("*.txt")) if root.is_dir() else []:
                if f.name.startswith("_") or f.name == "readme.txt":
                    continue
                try:
                    text = f.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                out += [(k, f.stem) for k in _TOP.findall(text)]
            return out

        buildings = {k: {"name": name(k), "group": group} for k, group in keys_in("building_types")}
        cultures = {k: name(k) for k, _g in keys_in("cultures")}
        religions = {k: name(k) for k in (codex.religions or [k for k, _g in keys_in("religions")])}
        privileges = {k: name(k) for k in codex.privileges}
        policies = {}
        for law in codex.laws:
            for key, shown in zip(law.options, law.option_names):
                policies[key] = f"{shown} ({law.name})"
        reforms = {k: name(k) for k in codex.government_reforms}
        major = []
        root = game / "game" / "in_game" / "common" / "government_reforms"
        for f in sorted(root.glob("*.txt")) if root.is_dir() else []:
            try:
                text = f.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for m in re.finditer(r"^([a-z][a-z0-9_]*)\s*=\s*\{(.*?)^\}", text, re.S | re.M):
                if re.search(r"^\s*major\s*=\s*yes", m.group(2), re.M):
                    major.append(m.group(1))
        traits: dict[str, dict[str, Any]] = {}
        root = game / "game" / "in_game" / "common" / "traits"
        for f in sorted(root.glob("*.txt")) if root.is_dir() else []:
            try:
                text = f.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for m in re.finditer(r"^([a-z][a-z0-9_]*)\s*=\s*\{(.*?)^\}", text, re.S | re.M):
                body = m.group(2)
                cat = re.search(r"\bcategory\s*=\s*(\w+)", body)
                allow = re.search(r"\ballow\s*=\s*\{(.*?)\n\t\}", body, re.S)
                conflicts = re.findall(r"NOT\s*=\s*\{\s*has_trait\s*=\s*(?:trait:)?(\w+)", allow.group(1)) if allow else []
                traits[m.group(1)] = {"name": name(m.group(1)), "category": cat.group(1) if cat else "ruler",
                                      "conflicts": sorted(set(conflicts))}
        # A trait that forbids another forbids it both ways.
        for key, t in traits.items():
            for other in t["conflicts"]:
                if other in traits and key not in traits[other]["conflicts"]:
                    traits[other]["conflicts"].append(key)
        return {"buildings": buildings, "cultures": cultures, "religions": religions,
                "privileges": privileges, "policies": policies, "reforms": reforms, "traits": traits,
                "major_reforms": sorted(major)}

    # ------------------------------------------------------------------
    @staticmethod
    def _find(table: dict[str, Any], what: str) -> list[str]:
        """Keys whose key or name is what was asked: exact first, then a unique close match."""
        want = _plain(what)
        if not want:
            return []
        if what.strip() in table:
            return [what.strip()]
        named = {k: _plain(v["name"] if isinstance(v, dict) else v) for k, v in table.items()}
        exact = [k for k, n in named.items() if want == n or want == _plain(k)]
        if exact:
            return exact[:1]
        close = [k for k, n in named.items() if n and _near(want, n)]
        if len(close) == 1:
            return close
        singular = want[:-1] if want.endswith("s") else want
        close = [k for k, n in named.items() if n and singular and _near(singular, n)]
        if len(close) == 1:
            return close
        # The court names a thing in its own words ("the barons' right to judge"): the
        # entry that shares the most meaningful words with it, if one clearly does.
        scored = sorted(((_overlap(want, n + " " + _plain(k)), k) for k, n in named.items()), reverse=True)
        if scored and scored[0][0] >= 1 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return [scored[0][1]]
        return []

    @staticmethod
    def closest(table: dict[str, Any], what: str, n: int = 5) -> list[str]:
        """The names nearest to what was asked, to tell the court what exists."""
        want = _plain(what)
        named = {k: (v["name"] if isinstance(v, dict) else str(v)) for k, v in table.items()}
        scored = sorted(((_overlap(want, _plain(v) + " " + _plain(k)), v) for k, v in named.items()), reverse=True)
        return [v for s, v in scored[:n] if s > 0]

    def buildings_for(self, what: str, *, build: bool = False) -> list[str]:
        """The building types meant. Pulling down 'the fortifications' means every kind of
        fort; building one means the fort that was named, or a castle."""
        low = _plain(what)
        every = re.search(r"\b(all|every|ogni|tutte|tutti|todas|toutes)\b", low)
        strict = [k for k, b in self.buildings.items() if low and (low == _plain(b["name"]) or low == _plain(k))]
        if strict and not every:
            return strict[:1]
        exact = self._find(self.buildings, what)
        fort = any(re.search(rf"\b{re.escape(w)}\b", low) for w in _FORT_WORDS)
        if build and fort:
            for words, key in ((("bastion", "bastione"), "bastion"), (("star", "stella"), "star_fort"),
                               (("fortress", "fortezza", "fortezze", "fortaleza"), "fortress"),
                               (("wall", "walls", "mura", "murallas"), "city_walls"),
                               (("coastal", "costiera", "battery", "batteria"), "naval_battery")):
                if any(x in low for x in words) and key in self.buildings:
                    return [key]
            return ["castle"] if "castle" in self.buildings else exact
        if fort:
            forts = [k for k, b in self.buildings.items() if b.get("group") == "forts"]
            if any(w in low for w in _COASTAL):
                forts = [k for k, b in self.buildings.items() if b.get("group") == "coastal_forts"]
            elif re.search(r"\b(all|every|ogni|tutte|tutti|todas|toutes)\b", low):
                forts += [k for k, b in self.buildings.items() if b.get("group") == "coastal_forts"]
            if forts:
                return forts
        return exact

    def summary(self) -> str:
        """For the prompt: the kinds of buildings the game has, by group, with a few names each."""
        groups: dict[str, list[str]] = {}
        for key, b in self.buildings.items():
            groups.setdefault(b.get("group", ""), []).append(b["name"])
        wanted = ("forts", "coastal_forts", "town_buildings", "rural_buildings", "religion_buildings",
                  "culture_buildings", "market_buildings", "port_buildings", "manpower_buildings",
                  "capital_buildings", "trade_buildings", "estate_buildings")
        lines = []
        for g in wanted:
            if groups.get(g):
                names = sorted(set(groups[g]))
                lines.append(f"- {g.replace('_', ' ')}: " + ", ".join(names[:14]) + ("..." if len(names) > 14 else ""))
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Validation: the AI's words -> a work the game can do
# --------------------------------------------------------------------------
def _person(people: list[Any], who: str) -> Any:
    who = " ".join((who or "").lower().split())
    if not who:
        return None
    return (next((p for p in people if p.name.lower() == who), None)
            or next((p for p in people if who in p.name.lower() or p.name.lower() in who), None))


def _trait_fits(trait: dict[str, Any], person: Any) -> str:
    """Why this kind of trait cannot belong to this person ("" if it can)."""
    cat, age = trait.get("category", "ruler"), int(getattr(person, "age", 0) or 0)
    child = 0 < age < 16
    if cat == "child":
        return "" if child else "a trait of childhood, and they are grown"
    if child and cat != "health":
        return "they are still a child (a child's traits are those of childhood)"
    if cat == "ruler" and not (person.is_ruler or person.is_heir):
        return "a trait of rulers, and they neither rule nor are heir"
    if cat == "cabinet" and not person.in_cabinet:
        return "a trait of ministers, and they are not in the cabinet"
    if cat == "general" and not person.is_general:
        return "a trait of generals, and they command no army"
    return ""


def _renames(what: str, geo: Any, owns: Any) -> tuple[list[dict[str, str]], str]:
    """'Old -> New; Old2 -> New2' -> the places and their new names."""
    out: list[dict[str, str]] = []
    for pair in re.split(r"[;\n]+", what):
        if not pair.strip():
            continue
        m = re.match(r"\s*(.+?)\s*(?:->|→|=>)\s*(.*?)\s*$", pair)
        old = m.group(1) if m else pair.strip()
        new = re.sub(r"[\"\[\]$#{}]", "", m.group(2) if m else "").strip()[:40]
        if len(new) < 2:
            return [], f"no new name given for {old!r} (write 'Old -> New')"
        key, locs = geo.resolve(old.strip(), "location")
        if not locs:
            return [], f"no place called {old.strip()!r}"
        if len(locs) > 1:
            # a province: its town of the same name, else its first
            same = [k for k in locs if geo.name(k).strip().lower() == geo.name(key).strip().lower()]
            locs = same[:1] or locs[:1]
        if owns is not None and not owns(locs[0]):
            return [], f"{geo.name(locs[0])} is not held by the realm"
        if new.lower() == geo.name(locs[0]).strip().lower():
            continue
        if all(r["location"] != locs[0] for r in out):
            out.append({"location": locs[0], "old": geo.name(locs[0]), "new": new})
    if not out:
        return [], "no place to rename"
    if len(out) > MAX_RENAMES:
        return [], f"too many places at once ({len(out)}; at most {MAX_RENAMES} in one order)"
    return out, ""


def validate(raw: Any, catalog: Catalog, geo: Any, state_religion: str = "", state_culture: str = "", *,
             people: list[Any] | None = None, owns: Any = None,
             treasury: float | None = None) -> tuple[dict[str, Any] | None, str]:
    """people: the persons the game reported (for traits and skills); owns(location key):
    whether the realm holds a place, when a save says so (for renaming); treasury: the
    gold the realm has now (for a payment)."""
    if not isinstance(raw, dict):
        return None, "not a work"
    kind = str(raw.get("kind") or "")
    if kind not in KINDS:
        return None, f"unknown work {kind!r}"
    what = str(raw.get("what") or "").strip()
    where = str(raw.get("where") or "").strip()
    scope = str(raw.get("scope") or "")
    size = max(1, min(3, int(raw.get("size") or 1)))
    work: dict[str, Any] = {"kind": kind, "size": size, "what": [], "places": [], "realm": False, "to": ""}

    if kind == "pay":
        m = re.search(r"\d[\d.,]*", what)
        amount = int(float(m.group().replace(",", "").rstrip("."))) if m else 0
        if amount < 1:
            return None, "a payment needs its sum in gold"
        if treasury is not None and treasury < amount:
            return None, (f"the treasury holds only {max(0, int(treasury))} gold and cannot pay {amount} - "
                          f"nothing is paid")
        who = " ".join(str(raw.get("who") or "").split())[:80]
        low = who.lower()
        estate = next((v for k, v in PAY_ESTATES.items() if re.search(rf"\b{k}\b", low)), "")
        work.update(amount=amount, to=who, estate=estate, where_name=where[:60])
        work["label"] = label(work, catalog)
        return work, ""
    if kind == "rename":
        if geo is None:
            return None, "the map is not known yet"
        renames, why = _renames(what, geo, owns)
        if not renames:
            return None, why
        work["renames"] = renames
        work["places"] = [r["location"] for r in renames]
        work["label"] = label(work, catalog)
        return work, ""
    if kind in PERSON_KINDS:
        person = _person([p for p in (people or []) if getattr(p, "slot", "")], str(raw.get("who") or ""))
        if person is None:
            return None, f"nobody called {str(raw.get('who') or '')!r} is at court"
        work.update(who=person.name, who_slot=person.slot)
        if kind in ("skill_up", "skill_down"):
            low = what.lower()
            skill = ("adm" if re.search(r"adm|govern|justice|state|amministr", low) else
                     "dip" if re.search(r"dip|negoti|speech|eloqu|court", low) else
                     "mil" if re.search(r"mil|war|arms|soldier|guerr|armi|command", low) else "")
            if not skill:
                return None, "a skill is administration, diplomacy or military"
            work["what"] = [skill]
        else:
            found = catalog._find({k: t["name"] for k, t in catalog.traits.items()}, what)
            if not found:
                return None, f"no trait called {what!r}" + _hint({k: t["name"] for k, t in catalog.traits.items()},
                                                                 what)
            trait = catalog.traits[found[0]]
            if kind == "trait_add":
                why = _trait_fits(trait, person)
                if why:
                    return None, f"{trait['name']} for {person.name}: {why}"
            work["what"] = found[:1]
            work["conflicts"] = [c for c in trait.get("conflicts", []) if c in catalog.traits]
        work["label"] = label(work, catalog)
        return work, ""

    # what
    if kind in ("build", "demolish", "upgrade", "downgrade"):
        work["what"] = catalog.buildings_for(what, build=kind in ("build", "upgrade"))
        if not work["what"]:
            return None, f"no building called {what!r} in this game" + _hint(catalog.buildings, what)
    elif kind in ("convert_religion",):
        work["what"] = catalog._find(catalog.religions, what) if what else []
    elif kind == "state_religion":
        work["what"] = catalog._find(catalog.religions, what)
        if not work["what"]:
            return None, f"no faith called {what!r}" + _hint(catalog.religions, what)
    elif kind in ("convert_culture",):
        work["what"] = catalog._find(catalog.cultures, what) if what else []
    elif kind in ("accept_culture", "tolerate_culture"):
        work["what"] = catalog._find(catalog.cultures, what)
        if not work["what"]:
            return None, f"no culture called {what!r}" + _hint(catalog.cultures, what)
    elif kind in ("grant_privilege", "revoke_privilege"):
        work["what"] = catalog._find(catalog.privileges, what)
        if not work["what"]:
            return None, f"no privilege called {what!r}" + _hint(catalog.privileges, what)
    elif kind in ("enact_policy", "repeal_policy"):
        work["what"] = catalog._find(catalog.policies, what)
        if not work["what"]:
            return None, f"no law or policy called {what!r}" + _hint(catalog.policies, what)
    elif kind in ("adopt_reform", "drop_reform"):
        work["what"] = catalog._find(catalog.reforms, what)
        if not work["what"]:
            return None, f"no government reform called {what!r}" + _hint(catalog.reforms, what)
        if kind == "drop_reform" and work["what"][0] in catalog.major_reforms:
            if work["what"][0] not in SIM.REFORMS:
                return None, ("that reform is the form of government itself - it is changed as a change of the "
                              "state (change_government), never dropped")
            work["major"] = True        # only the one held by decree (the mod's own) can be dropped
    elif kind in ("depopulate", "repopulate"):
        low = _plain(what)
        pop = next((p for p in POP_TYPES if p in low or p[:-1] in low), "")
        if pop:
            work["what"], work["filter"] = [pop], "pop_type"
        elif low and catalog._find(catalog.religions, what):
            work["what"], work["filter"] = catalog._find(catalog.religions, what), "religion"
        elif low and catalog._find(catalog.cultures, what):
            work["what"], work["filter"] = catalog._find(catalog.cultures, what), "culture"
        else:
            work["what"], work["filter"] = [], ""
    elif kind == "charter":
        work["what"] = ["town"] if re.search(r"\b(town|borgo|villa)\b", what, re.I) else ["city"]
    elif kind == "road":
        low = what.lower()
        work["what"] = ["modern_road" if "modern" in low else "paved_road" if re.search(r"pav|lastric|stone|pietra", low)
                        else "gravel_road"]

    # where
    if kind in PLACE_KINDS:
        if kind in REALM_WIDE_OK and (scope == "realm" or where.lower() in ("realm", "kingdom", "the realm", "")):
            work["realm"] = True
        else:
            if geo is None:
                return None, "the map is not known yet"
            key, locs = geo.resolve(where, scope if scope != "realm" else "")
            if not locs:
                return None, f"no place called {where!r}"
            cap = MAX_PLACES.get(kind, 6)
            if kind in ("build", "upgrade", "charter", "move_capital") and len(locs) > 1:
                locs = locs[:1] if kind == "move_capital" or scope != "location" else locs[:cap]
            if len(locs) > cap:
                return None, f"{where} is too large for this work ({len(locs)} places, at most {cap})"
            work["places"] = locs
            work["where_name"] = geo.name(key)
            if kind == "road":
                tkey, tlocs = geo.resolve(str(raw.get("to") or ""), "")
                if not tlocs:
                    return None, "a road needs the place it leads to"
                work["to"] = tlocs[0]
                work["to_name"] = geo.name(tkey)
                work["places"] = locs[:1]
    work["label"] = label(work, catalog)
    return work, ""


def _hint(table: dict[str, Any], what: str) -> str:
    near = Catalog.closest(table, what)
    return f" - the nearest in this game: {', '.join(near)}" if near else ""


def _names(work: dict[str, Any], catalog: Catalog) -> str:
    table = {"build": catalog.buildings, "demolish": catalog.buildings, "upgrade": catalog.buildings,
             "downgrade": catalog.buildings, "convert_religion": catalog.religions, "convert_culture": catalog.cultures,
             "state_religion": catalog.religions,
             "accept_culture": catalog.cultures, "tolerate_culture": catalog.cultures,
             "grant_privilege": catalog.privileges, "revoke_privilege": catalog.privileges,
             "enact_policy": catalog.policies, "repeal_policy": catalog.policies,
             "adopt_reform": catalog.reforms, "drop_reform": catalog.reforms}.get(work["kind"], {})
    out = []
    for k in work["what"]:
        v = table.get(k, k)
        out.append(v["name"] if isinstance(v, dict) else str(v))
    if work["kind"] in ("demolish", "build") and len(out) > 3:
        return "every kind of fortification"
    return ", ".join(dict.fromkeys(out))


def label(work: dict[str, Any], catalog: Catalog) -> str:
    kind = work["kind"]
    if kind == "pay":
        to, where = work.get("to") or "", work.get("where_name") or ""
        if not to:
            return f"{work['amount']} gold paid from the treasury" + (f" to {where}" if where else "")
        return f"{work['amount']} gold paid from the treasury to {to}" + (f" ({where})" if where else "")
    if kind == "rename":
        pairs = [f"{r['old']} becomes {r['new']}" for r in work["renames"]]
        return "New names: " + "; ".join(pairs[:4]) + (f" and {len(pairs) - 4} more" if len(pairs) > 4 else "")
    if kind in PERSON_KINDS:
        who = work.get("who", "")
        if kind in ("skill_up", "skill_down"):
            pts = SKILL_POINTS[work["size"]]
            return f"{who}: {SKILLS[work['what'][0]]} {'+' if kind == 'skill_up' else '-'}{pts}"
        trait = catalog.traits.get(work["what"][0], {}).get("name", work["what"][0])
        return f"{who} {'gains' if kind == 'trait_add' else 'loses'} the trait: {trait}"
    what = _names(work, catalog)
    where = "across the whole realm" if work.get("realm") else f"in {work.get('where_name', '')}" if work.get("places") else ""
    size = {1: "", 2: " (a good deal)", 3: " (a great work)"}[work["size"]]
    texts = {
        "build": f"Build {what} {where}", "demolish": f"Pull down {what} {where}",
        "upgrade": f"Enlarge {what} {where}", "downgrade": f"Reduce {what} {where}",
        "road": f"A road from {work.get('where_name', '')} to {work.get('to_name', '')}",
        "charter": f"Grant {work.get('where_name', '')} a {what or 'city'} charter",
        "convert_religion": f"Begin converting the people {where} to {what or 'the state faith'}",
        "convert_culture": f"Begin making the people {where} {what or 'of the state culture'}",
        "accept_culture": f"Accept the {what} as the realm's own", "tolerate_culture": f"Tolerate the {what}",
        "state_religion": f"Make {what} the religion of the state",
        "grant_privilege": f"Grant the privilege: {what}", "revoke_privilege": f"Revoke the privilege: {what}",
        "enact_policy": f"Enact: {what}", "repeal_policy": f"Repeal: {what}",
        "adopt_reform": f"Adopt the reform: {what}", "drop_reform": f"Drop the reform: {what}",
        "move_capital": f"Move the capital to {work.get('where_name', '')}",
        "develop": f"Invest in the development of {work.get('where_name', '')}{size}",
        "integrate": (f"Bind {work.get('where_name', '')} closer to the realm"
                      + {1: " (the effort begins)", 2: " (one step closer)", 3: " (to make it part of the realm's heartland)"}
                      .get(work.get("size", 1), "")),
        "control": f"Tighten the Crown's hold on {work.get('where_name', '')}{size}",
        "prosperity": f"Invest in the prosperity of {work.get('where_name', '')}{size}",
        "depopulate": f"{what or 'People'} die or are driven out {where} (about "
                      f"{int(POP_SHARE['depopulate'][work['size']] * 100)} in 100)",
        "repopulate": f"Settle {what or 'people'} {where}",
    }
    return re.sub(r"\s+", " ", texts[kind]).strip()


# --------------------------------------------------------------------------
# The game's effects - and the price
# --------------------------------------------------------------------------
def _scale(work: dict[str, Any]) -> int:
    """How much of the realm the work touches: 1 a place, 2 a province, 3 a region or the realm."""
    if work.get("realm"):
        return 3
    n = len(work.get("places") or [])
    return 1 if n <= 2 else 2 if n <= 8 else 3


def _price(work: dict[str, Any]) -> list[str]:
    kind, size, scale = work["kind"], work["size"], _scale(work)
    t = TIER[max(size, scale)]
    lines = []

    def gold(tier: str) -> None:
        lines.append(f"add_gold = {{ value = votc_gold_{tier} multiply = -1 }}")

    def stability(tier: str) -> None:
        lines.append(f"add_stability = stability_{tier}_penalty")

    def estate(e: str, tier: str, sign: str = "penalty") -> None:
        lines.append(f"if = {{ limit = {{ country_has_estate = estate_type:{e} }} add_estate_satisfaction = "
                     f"{{ type = estate_type:{e} value = estate_satisfaction_{tier}_{sign} }} }}")

    forts = any("fort" in k or k in ("castle", "bastion", "city_walls", "naval_battery") for k in work["what"])
    if kind == "demolish":
        gold(TIER[scale] if scale > 1 else "weak")
        if forts:
            estate("nobles_estate", TIER[scale])          # their castles, their power
            if scale >= 2:
                stability("weak")
    elif kind == "integrate":
        gold(TIER[max(size, scale)])
        if size >= 3:
            stability("weak")
    elif kind in ("upgrade", "develop", "prosperity"):
        gold(t)
    elif kind == "control":
        gold(TIER[scale]); estate("nobles_estate", "weak")
    elif kind in ("road", "charter"):
        gold("mild" if kind == "road" else t)
        if kind == "charter":
            estate("burghers_estate", "weak", "bonus")
    elif kind == "convert_religion":
        stability(TIER[scale]); estate("clergy_estate", "weak", "bonus")
    elif kind == "convert_culture":
        stability(TIER[scale])
    elif kind == "state_religion":
        stability("severe"); gold("mild"); estate("clergy_estate", "severe")
    elif kind == "revoke_privilege":
        key = " ".join(work["what"])
        e = next((x for x in ("nobles", "clergy", "burghers", "peasants", "tribes", "cossacks", "dhimmi") if x in key), "")
        if e:
            estate(f"{e}_estate", "severe")
        stability("weak")
    elif kind in ("enact_policy", "repeal_policy"):
        stability("mild")
    elif kind in ("adopt_reform", "drop_reform"):
        stability("mild")
    elif kind == "move_capital":
        stability("mild"); gold("mild")
    elif kind == "depopulate":
        stability(TIER[max(size, scale)])
    elif kind == "repopulate":
        gold(t)
    elif kind == "rename":
        # new charters, maps and seals; the Crown's word on the names
        gold("weak" if len(work.get("renames") or []) <= 4 else "mild")
    elif kind in ("trait_add", "trait_remove"):
        bad = (work.get("what") or [""])[0] in BAD_TRAITS
        # tutors, masters, physicians and confessors are paid; a wound or a vice is not bought
        if (kind == "trait_add") != bad:
            gold("mild")
    elif kind == "skill_up":
        gold(TIER[size])
    return lines


def _condition(work: dict[str, Any]) -> str:
    """When the work really does something - only then is it done and paid for."""
    kind, what, places = work["kind"], work["what"], work.get("places") or []

    def owned(extra: str = "") -> str:
        return "OR = { " + " ".join(f"location:{p} ?= {{ owner = root {extra} }}" for p in places) + " }"

    if kind == "depopulate" and work.get("realm"):
        return "always = yes"
    if kind == "pay":
        return f"gold >= {work['amount']}"
    if kind in PERSON_KINDS:
        slot = work["who_slot"]
        if kind == "trait_add":
            return f"exists = var:votc_{slot} var:votc_{slot} = {{ NOT = {{ has_trait = trait:{what[0]} }} }}"
        if kind == "trait_remove":
            return f"exists = var:votc_{slot} var:votc_{slot} = {{ has_trait = trait:{what[0]} }}"
        return f"exists = var:votc_{slot}"
    if kind == "rename":
        return owned()
    if kind == "demolish":
        if work.get("realm"):
            return "OR = { " + " ".join(f"any_owned_location = {{ has_building = building_type:{b} }}"
                                        for b in what) + " }"
        return "OR = { " + " ".join(f"location:{p} ?= {{ owner = root has_building = building_type:{b} }}"
                                    for p in places for b in what) + " }"
    if kind in ("upgrade", "downgrade"):
        return owned(f"has_building = building_type:{what[0]}")
    if kind == "integrate":
        return owned("NOT = { integration_level = core }")
    if kind == "build" and what:
        return owned() + f" building_type:{what[0]} = {{ is_available_for = root }}"
    if kind == "state_religion":
        return f"NOT = {{ religion = religion:{what[0]} }}"
    # What the game itself would not allow this realm (another government's reform, the Pope's,
    # an estate it does not have, a law it cannot hold) is never forced on it: that leaves the
    # state in a shape the game does not expect.
    if kind == "drop_reform" and work.get("major"):
        return f"has_country_modifier = votc_simr_{what[0]}"
    if kind in INSTITUTION_KINDS:
        return _institution(kind, what[0])[0]
    if places:
        return owned()
    return "always = yes"


# Laws, reforms and privileges: the game's own when the game allows this realm to have it;
# else the mod's simulated one (tools/gen_simulated.py) - the same effects, by decree, until
# revoked - since forcing on a realm what the game does not allow it leaves the state in a
# shape the game does not expect. (Buildings are not simulated: one not allowed is not built.)
INSTITUTION_KINDS = ("enact_policy", "repeal_policy", "adopt_reform", "drop_reform",
                     "grant_privilege", "revoke_privilege")
_INSTITUTION = {  # kind -> (has, allowed, add, remove, simulated set, prefix of the simulated modifier)
    "policy": ("has_policy = {k}", "policy:{k} = {{ is_available_for = root }}", "add_policy = policy:{k}",
               "remove_policy = policy:{k}", SIM.POLICIES, "votc_simp_"),
    "reform": ("has_reform = government_reform:{k}", "government_reform:{k} = {{ is_available_for = root }}",
               "add_reform = government_reform:{k}", "remove_reform = government_reform:{k}", SIM.REFORMS,
               "votc_simr_"),
    "privilege": ("has_estate_privilege = estate_privilege:{k}",
                  "estate_privilege:{k} = {{ is_available_for = root }}",
                  "grant_estate_privilege = estate_privilege:{k}", "revoke_estate_privilege = estate_privilege:{k}",
                  SIM.PRIVILEGES, "votc_simv_"),
}
_INSTITUTION_OF = {"enact_policy": ("policy", True), "repeal_policy": ("policy", False),
                   "adopt_reform": ("reform", True), "drop_reform": ("reform", False),
                   "grant_privilege": ("privilege", True), "revoke_privilege": ("privilege", False)}


def _institution(kind: str, key: str) -> tuple[str, str]:
    """(condition, effect) for a law, reform or privilege given or taken away."""
    what, give = _INSTITUTION_OF[kind]
    has, allowed, add, remove, simulated, prefix = (s.format(k=key) if isinstance(s, str) else s
                                                   for s in _INSTITUTION[what])
    sim = prefix + key if key in simulated else ""
    has_sim = f"has_country_modifier = {sim}" if sim else ""
    if give:
        if sim:
            cond = f"NOT = {{ {has} }} NOT = {{ {has_sim} }}"
            effect = (f"if = {{ limit = {{ {allowed} }} {add} }} "
                      f"else = {{ add_country_modifier = {{ modifier = {sim} years = -1 }} }}")
        else:
            cond, effect = f"NOT = {{ {has} }} {allowed}", add
        return cond, effect
    if sim:
        return (f"OR = {{ {has} {has_sim} }}",
                f"if = {{ limit = {{ {has} }} {remove} }} if = {{ limit = {{ {has_sim} }} "
                f"remove_country_modifier = {sim} }}")
    return has, remove


ROAD_TYPES = ("modern_road", "paved_road", "gravel_road")      # best first

CONVERSION_KINDS = ("convert_religion", "convert_culture", "state_religion")


def conversion_plan(work: dict[str, Any], skill: float, rng: Any = random) -> dict[str, Any]:
    """How converting people goes, decided when the work is written: the share that turns at
    once, how many years the effort goes on, and how hard the people resist. `skill` (-1..1:
    the ruler's record and how the realm is kept) moves all three - a skilful ruler gets there
    in time, a careless one meets more resistance, and it is never impossible."""
    skill = max(-1.0, min(1.0, skill))
    scale = _scale(work) if work.get("kind") != "state_religion" else 3
    lo, hi = {1: (0.10, 0.25), 2: (0.06, 0.18), 3: (0.03, 0.12)}[scale]
    lo, hi = max(0.01, lo + 0.05 * skill), max(0.03, hi + 0.05 * skill)
    years = max(5, round(10 + 5 * skill))
    weights = {"none": max(5.0, 30 + 25 * skill), "mild": 50.0,
               "severe": max(3.0, 20 - 15 * skill + (10 if scale == 3 else 0))}
    resistance = rng.choices(list(weights), weights=list(weights.values()))[0]
    plan = {"lo": f"{lo:.2f}", "hi": f"{hi:.2f}", "years": years, "resistance": resistance}
    work["plan"] = plan
    return plan


def script(work: dict[str, Any]) -> list[str]:
    """The game's effects for this work and its price, in the player's country scope -
    all inside one condition, so that nothing is paid for a work that cannot happen."""
    kind, size, what = work["kind"], work["size"], work["what"]
    body: list[str] = []

    def at(loc: str, inner: str) -> None:
        body.append(f"location:{loc} ?= {{ if = {{ limit = {{ owner = root }} {inner} }} }}")

    if kind == "build":
        for loc in work["places"]:
            for b in what[:1]:
                at(loc, f"if = {{ limit = {{ NOT = {{ has_building = building_type:{b} }} }} "
                        f"construct_building = {{ building_type = building_type:{b} }} }}")
    elif kind in ("upgrade", "downgrade"):
        value = size if kind == "upgrade" else -size
        for loc in work["places"]:
            at(loc, f"if = {{ limit = {{ has_building = building_type:{what[0]} }} change_building_level_in_location = "
                    f"{{ building = building_type:{what[0]} value = {value} }} }}")
    elif kind == "demolish":
        for b in what:
            inner = f"destroy_all_buildings_of_type = building_type:{b}"
            if work.get("realm"):
                body.append(f"every_owned_location = {{ limit = {{ has_building = building_type:{b} }} {inner} }}")
            else:
                for loc in work["places"]:
                    at(loc, f"if = {{ limit = {{ has_building = building_type:{b} }} {inner} }}")
    elif kind == "road":
        # the road asked for if the realm can build it, else the best it can (a paved road
        # ordered in 1340 is laid as a gravel one)
        road = lambda t: f"add_road_to = {{ target = location:{work['to']} type = {t} }}"  # noqa: E731
        kinds = ROAD_TYPES[ROAD_TYPES.index(what[0]):] if what[0] in ROAD_TYPES else [what[0]]
        chain = ""
        for i, t in enumerate(kinds):
            if i == len(kinds) - 1:
                chain += f"else = {{ {road(t)} }}" if i else road(t)
            else:
                chain += (f"{'if' if i == 0 else 'else_if'} = {{ limit = {{ road_type:{t} = {{ is_available_for = root }} }} "
                          f"{road(t)} }} ")
        at(work["places"][0], chain)
    elif kind == "charter":
        for loc in work["places"]:
            at(loc, f"change_location_rank = location_rank:{what[0]}")
    elif kind in ("convert_religion", "convert_culture"):
        # People are not converted by a decree: a first share turns at once, the rest over the
        # years - where the realm's own pull goes their way - and some may resist.
        plan = work.get("plan") or conversion_plan(work, 0.0)
        faith = kind == "convert_religion"
        field = "religion" if faith else "culture"
        target = f"{field}:{what[0]}" if what else f"root.{field}"
        if faith and what:
            body.append(f"religion:{what[0]} ?= {{ enable_religion = yes }}")
        home = (f"religion = {target}" if faith else f"has_primary_or_accepted_culture = {target}")
        drive = "votc_conversion_drive" if faith else "votc_assimilation_drive"
        for loc in work["places"]:
            inner = (f"every_pop = {{ limit = {{ NOT = {{ {field} = {target} }} }} split_pop = {{ "
                     f"fraction = {{ {plan['lo']} {plan['hi']} }} {field} = {target} }} }} "
                     f"if = {{ limit = {{ root = {{ {home} }} }} add_location_modifier = {{ modifier = {drive} "
                     f"years = {plan['years']} mode = add_and_extend }} }}")
            if plan["resistance"] != "none":
                inner += (f" add_location_modifier = {{ modifier = votc_conversion_unrest_{plan['resistance']} "
                          f"years = 5 mode = add_and_extend }}")
            at(loc, inner)
        if faith and plan["resistance"] == "severe":
            body.append("add_country_modifier = { modifier = votc_faith_resistance_mild years = 5 mode = add_and_extend }")
    elif kind == "state_religion":
        # The state changes its faith at once; its people follow over the years, and those who
        # keep the old faith may resist.
        plan = work.get("plan") or conversion_plan(work, 0.0)
        body.append(f"religion:{what[0]} ?= {{ enable_religion = yes }}")
        body.append(f"change_religion = religion:{what[0]}")
        body.append(f"add_country_modifier = {{ modifier = votc_new_faith_zeal years = {plan['years']} "
                    f"mode = add_and_extend }}")
        if plan["resistance"] != "none":
            body.append(f"add_country_modifier = {{ modifier = votc_faith_resistance_{plan['resistance']} "
                        f"years = {5 if plan['resistance'] == 'mild' else 10} mode = add_and_extend }}")
            body.append(f"every_owned_location = {{ limit = {{ NOT = {{ dominant_religion = religion:{what[0]} }} }} "
                        f"add_location_modifier = {{ modifier = votc_conversion_unrest_{plan['resistance']} "
                        f"years = 5 mode = add_and_extend }} }}")
    elif kind == "accept_culture":
        body.append(f"add_accepted_culture = culture:{what[0]}")
    elif kind == "tolerate_culture":
        body.append(f"add_tolerated_culture = culture:{what[0]}")
    elif kind == "drop_reform" and work.get("major"):
        body.append(f"remove_country_modifier = votc_simr_{what[0]}")
    elif kind in INSTITUTION_KINDS:
        body.append(_institution(kind, what[0])[1])
    elif kind == "move_capital":
        body.append(f"set_capital = location:{work['places'][0]}")
    elif kind == "integrate":
        # the effort (ten years of faster integration), and the steps it earns (votc_integration_effects.txt)
        steps = ("add_location_modifier = { modifier = votc_integration_drive years = 10 mode = add_and_extend }")
        if size >= 3:
            steps += (" if = { limit = { votc_integ_own_people = yes } change_integration_level = core add_core = root }"
                      " else_if = { limit = { integration_level = conquered } change_integration_level = integrated }"
                      " else_if = { limit = { integration_level = integrated } change_integration_level = core "
                      "add_core = root }")
        elif size == 2:
            steps += (" if = { limit = { integration_level = conquered } change_integration_level = integrated }"
                      " else_if = { limit = { integration_level = integrated } change_integration_level = core "
                      "add_core = root }")
        for loc in work["places"]:
            at(loc, f"if = {{ limit = {{ NOT = {{ integration_level = core }} }} {steps} }}")
    elif kind in ("develop", "control", "prosperity"):
        effect = {"develop": "change_development = development", "control": "change_control = control",
                  "prosperity": "change_prosperity = prosperity"}[kind]
        for loc in work["places"]:
            at(loc, f"{effect}_{TIER[size]}_bonus")
    elif kind == "pay":
        # the exact sum leaves the treasury; to an estate, it goes into its coffers
        body.append(f"add_gold = -{work['amount']}")
        if work.get("estate"):
            body.append(f"if = {{ limit = {{ country_has_estate = estate_type:{work['estate']} }} add_gold_to_estate "
                        f"= {{ estate_type = estate_type:{work['estate']} value = {work['amount']} }} }}")
    elif kind == "rename":
        # each new name is a key of the mod's own text (names.PlaceNames), numbered by Court Brain
        for r in work["renames"]:
            if r.get("n"):
                at(r["location"], f"rename_location = votc_lname_{r['n']}")
        if not body:
            return []
    elif kind in PERSON_KINDS:
        slot, key = work["who_slot"], what[0]
        if kind == "trait_add":
            drop = " ".join(f"if = {{ limit = {{ has_trait = trait:{c} }} remove_trait = trait:{c} }}"
                            for c in work.get("conflicts") or [])
            body.append(f"var:votc_{slot} = {{ {drop} add_trait = trait:{key} }}")
        elif kind == "trait_remove":
            body.append(f"var:votc_{slot} = {{ remove_trait = trait:{key} }}")
        else:
            pts = SKILL_POINTS[size] * (1 if kind == "skill_up" else -1)
            body.append(f"var:votc_{slot} = {{ add_{key} = {pts} }}")
    elif kind in ("depopulate", "repopulate"):
        share = POP_SHARE[kind][size] * (-1 if kind == "depopulate" else 1)
        flt = work.get("filter")
        limit = ""
        if flt == "pop_type":
            limit = f"limit = {{ pop_type = pop_type:{what[0]} }} "
        elif flt in ("religion", "culture"):
            limit = f"limit = {{ {flt} = {flt}:{what[0]} }} "
        pops = f"every_pop = {{ {limit}add_pop_size = {{ value = pop_size multiply = {share} }} }}"
        if work.get("realm"):
            body.append(f"every_owned_location = {{ {pops} }}")
        else:
            for loc in work["places"]:
                at(loc, pops)
    inner = " ".join(body + _price(work))
    return [f"if = {{ limit = {{ {_condition(work)} }} {inner} }}"]


def price_text(work: dict[str, Any]) -> str:
    """What the work costs the realm, in words, for the panel."""
    if work["kind"] == "pay":
        return f"{work['amount']} gold"
    words = {"add_gold": "money", "add_stability": "stability", "nobles_estate": "the nobles' goodwill",
             "clergy_estate": "", "burghers_estate": "", "peasants_estate": "the peasants' goodwill"}
    parts = []
    for line in _price(work):
        for key, w in words.items():
            if key in line and w and "_bonus" not in line and w not in parts:
                parts.append(w)
    if work["kind"] == "build":
        parts.insert(0, "the building's cost and time, as the game charges them")
    return ", ".join(parts) or "little beyond the work itself"
