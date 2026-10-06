"""The live picture of the realm, assembled from the mod's context records.

The mod dumps its context immediately before every request, and again on
each heartbeat, so a Snapshot is always the state of the world at the moment
the player did something - never stale.

Court Brain does not hook vanilla's on_actions (see the mod's
common/on_action/votc_on_actions.txt for why). Instead it notices what
happened by DIFFING consecutive snapshots, which also catches things no
on_action would report: stability sliding for a year, an estate quietly
turning against the crown, the treasury draining.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .protocol import Record, scrub_record

ESTATES = ("nobles", "clergy", "burghers", "peasants", "tribes", "dhimmi", "cossacks")


@dataclass
class Person:
    name: str = ""
    dynasty: str = ""
    role: str = ""
    court: str = ""
    adm: int = 0
    dip: int = 0
    mil: int = 0
    age: int = 0
    is_ruler: bool = False
    is_heir: bool = False
    in_cabinet: bool = False
    is_general: bool = False
    is_female: bool = False
    religion: str = ""
    slot: str = ""          # the mod's variable holding them: ruler, heir, p1..p6, tchar

    def describe(self) -> str:
        bits = [self.name]
        if self.dynasty:
            bits.append(f"of {self.dynasty}")
        role = self.role or self._implied_role()
        if role:
            bits.append(f"- {role}")
        # The game sometimes does not report these; 0 then means unknown,
        # not a newborn with no talent.
        facts = []
        if self.age > 0:
            facts.append(f"{self.age}y")
        if self.adm or self.dip or self.mil:
            facts.append(f"adm {self.adm}/dip {self.dip}/mil {self.mil}")
        if facts:
            bits.append("(" + ", ".join(facts) + ")")
        if self.religion:
            bits.append(f"[{self.religion}]")
        return " ".join(b for b in bits if b)

    def _implied_role(self) -> str:
        if self.is_ruler:
            return "the ruler"
        if self.is_heir:
            return "the heir"
        if self.in_cabinet:
            return "cabinet"
        if self.is_general:
            return "general"
        return ""


@dataclass
class ForeignCourt:
    tag: str = ""
    name: str = ""
    long_name: str = ""
    government: str = ""
    ruler: str = ""
    religion: str = ""
    culture: str = ""
    gold: int = 0
    army: int = 0
    locations: int = 0
    at_war: bool = False


def own_crown(snap: "Snapshot") -> bool:
    """Is the 'foreign' court the ruler's own other crown - the same ruler (a personal union: the
    King of England who is also King of France), or the same union? The game says so; an older
    bridge does not, and then the same ruler's name tells it."""
    c = snap.target_country
    if c is None:
        return False
    if snap.numbers.get("target_union"):
        return True
    return bool(snap.ruler and snap.ruler.name and c.ruler and c.ruler.strip() == snap.ruler.name.strip())


@dataclass
class LiveCountry:
    """One country of the live picture (see tools/gen_live.py)."""
    tag: str = ""
    name: str = ""
    ruler: str = ""
    foe_tag: str = ""
    foe_name: str = ""
    army: int = 0


@dataclass
class Place:
    name: str = ""
    province: str = ""
    culture: str = ""
    religion: str = ""
    owner: str = ""
    controller: str = ""
    control: float = 0.0
    development: float = 0.0
    population: int = 0
    is_capital: str = ""


@dataclass
class Snapshot:
    # HEAD
    date: str = ""
    tag: str = ""
    name: str = ""
    long_name: str = ""
    adjective: str = ""
    government: str = ""
    ruler_title: str = ""
    court_title: str = ""
    capital: str = ""
    culture: str = ""
    religion: str = ""
    court_language: str = ""
    rank: int = 0

    numbers: dict[str, float] = field(default_factory=dict)
    estates: dict[str, float] = field(default_factory=dict)
    estate_names: dict[str, str] = field(default_factory=dict)

    ruler: Person | None = None
    heir: Person | None = None
    court: list[Person] = field(default_factory=list)

    target_person: Person | None = None
    target_country: ForeignCourt | None = None
    target_place: Place | None = None

    # ID: which campaign this game is, and how far its memory reaches
    campaign: int = 0
    memhead: int = 0
    has_id: bool = False
    scenes: dict = field(default_factory=dict)      # kind -> number of the last scene the game holds

    # LIVE: the world around the realm, reported without a save
    live: dict[str, list[LiveCountry]] = field(default_factory=dict)
    has_live: bool = False

    unresolved: bool = False
    complete: bool = False

    # ------------------------------------------------------------------
    @property
    def year(self) -> int:
        # "08:00, 1 April, 1337": the first number is the hour, not the year.
        from . import gamedate
        p = gamedate.parse(self.date)
        return p[0] if p else 0

    def n(self, key: str, default: float = 0.0) -> float:
        return self.numbers.get(key, default)

    @property
    def at_war(self) -> bool:
        return self.n("atwar") >= 1

    @property
    def budget(self) -> int:
        return int(self.n("budget"))

    def is_valid(self) -> bool:
        return bool(self.tag) and bool(self.date)


def _person_from(rec: Record, *, positional_name: int = 0) -> Person:
    p = Person()
    # The game prefixes some names with the role ("Heir Federic"); the role
    # is reported separately, so the name keeps only the name.
    p.name = re.sub(r"^(?:(?:Crown\s+|Grand\s+)?(?:Prince|Princess|Duke|Duchess)|King|Queen|Emperor|Empress|Count|Countess|Doge|Sultan|Khan|Heir|Heiress|Erede|Regent|Reggente|Principe ereditario)\s+", "", rec.at(positional_name))
    p.dynasty = rec.at(positional_name + 1)
    p.adm = rec.i("adm")
    p.dip = rec.i("dip")
    p.mil = rec.i("mil")
    p.age = rec.i("age")
    p.is_ruler = rec.i("ruler") == 1
    p.is_heir = rec.i("heir") == 1
    p.in_cabinet = rec.i("cabinet") == 1
    p.is_general = rec.i("general") == 1
    p.is_female = rec.i("female") == 1
    p.religion = rec.fields.get("religion", "")
    return p


class SnapshotBuilder:
    """Accumulates records into a Snapshot.

    HEAD always opens a dump (the mod logs it through votc_log_seq, the only
    call that bumps the counter), so a HEAD means "start over".
    """

    def __init__(self) -> None:
        self.current = Snapshot()
        self._started = False

    def feed(self, rec: Record) -> None:
        rec = scrub_record(rec)
        kind = rec.kind

        if kind == "HEAD":
            snap = Snapshot()
            snap.date = rec.fields.get("date", "")
            snap.tag = rec.at(0)
            snap.name = rec.at(1)
            snap.long_name = rec.at(2)
            snap.adjective = rec.at(3)
            snap.government = rec.at(4)
            snap.ruler_title = rec.at(5)
            snap.court_title = rec.at(6)
            snap.capital = rec.at(7)
            snap.culture = rec.at(8)
            snap.religion = rec.at(9)
            snap.court_language = rec.at(10)
            snap.rank = rec.i("rank")
            snap.unresolved = rec.unresolved
            self.current = snap
            self._started = True
            self._person_n = 0
            return

        if not self._started:
            # Records before the first HEAD (a mid-file start, say) are not
            # worth guessing at.
            return

        snap = self.current
        if kind in ("STATE", "MIGHT", "REALM", "COURT"):
            snap.numbers.update({k: _as_float(v) for k, v in rec.fields.items()})
        elif kind == "ESTATES":
            for e in ESTATES:
                if e in rec.fields:
                    snap.estates[e] = _as_float(rec.fields[e])
        elif kind == "ESTATENAMES":
            snap.estate_names.update(rec.fields)
        elif kind == "RULER":
            p = _person_from(rec)
            p.role, p.court = rec.at(2), rec.at(3)
            p.is_ruler = True
            p.slot = "ruler"
            snap.ruler = p if p.name else None
            self._person_n = 0
        elif kind == "HEIR":
            p = _person_from(rec)
            p.role, p.court = rec.at(2), rec.at(3)
            # The mod parks the ruler in the heir slot when there is no heir.
            if p.name and not (snap.ruler and p.name == snap.ruler.name):
                p.is_heir = True
                p.slot = "heir"
                snap.heir = p
        elif kind == "PERSON":
            self._person_n = getattr(self, "_person_n", 0) + 1
            p = _person_from(rec)
            p.role, p.court = rec.at(2), rec.at(3)
            p.slot = f"p{self._person_n}"
            known = {q.name for q in snap.court}
            if snap.ruler:
                known.add(snap.ruler.name)
            # Empty slots are filled with the ruler; drop those and repeats.
            if p.name and p.name not in known:
                snap.court.append(p)
        elif kind == "TARGETCHAR":
            p = _person_from(rec)
            p.role = rec.at(2)
            p.court = rec.at(3)
            p.slot = "tchar"
            snap.target_person = p
        elif kind == "TARGETCOUNTRY":
            fc = ForeignCourt(
                tag=rec.at(0),
                name=rec.at(1),
                long_name=rec.at(2),
                government=rec.at(3),
                ruler=rec.at(4),
                religion=rec.at(5),
                culture=rec.at(6),
                gold=rec.i("gold"),
                army=rec.i("army"),
                locations=rec.i("locations"),
                at_war=rec.i("atwar") == 1,
            )
            snap.target_country = fc
            # Our standing with them is carried on our own COURT-side values.
            snap.numbers["target_allied"] = rec.num("allied")
            snap.numbers["target_at_war"] = rec.num("warwithus")
            snap.numbers["target_truce"] = rec.num("truce")
            snap.numbers["target_rival"] = rec.num("rival")
            snap.numbers["target_union"] = rec.num("union")
        elif kind == "ID":
            snap.campaign = rec.i("campaign")
            snap.memhead = rec.i("head")
            snap.has_id = True
            snap.scenes = {k[3:]: rec.i(k) for k in rec.fields if k.startswith("sc_")}
        elif kind == "LIVE":
            snap.has_live = True
            lc = LiveCountry(tag=rec.at(2), name=rec.at(3), ruler=rec.at(4),
                             foe_tag=rec.fields.get("foe", ""), foe_name=rec.fields.get("foename", ""),
                             army=rec.i("army"))
            group = snap.live.setdefault(rec.at(0), [])
            # Empty slots point at the player's own country; a country with no
            # enemy names itself as its foe.
            if lc.tag and lc.tag != snap.tag and all(x.tag != lc.tag for x in group):
                if lc.foe_tag == lc.tag:
                    lc.foe_tag = lc.foe_name = ""
                group.append(lc)
        elif kind == "LOCATION":
            snap.target_place = Place(
                name=rec.at(0),
                province=rec.at(1),
                culture=rec.at(2),
                religion=rec.at(3),
                owner=rec.at(4),
                controller=rec.at(5),
                control=rec.num("control"),
                development=rec.num("development"),
                population=rec.i("population"),
                is_capital=rec.fields.get("capital", ""),
            )
        if rec.unresolved:
            snap.unresolved = True

    def commit(self) -> Snapshot | None:
        """Called on END: the dump before it is finished."""
        if not self._started or not self.current.is_valid():
            return None
        self.current.complete = True
        return self.current


def _as_float(value: str) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


# ======================================================================
# Diffing
# ======================================================================

@dataclass
class Change:
    key: str
    before: Any
    after: Any
    note: str

    def __str__(self) -> str:
        return self.note


# How much a number must move before it is worth mentioning. Chosen so that
# ordinary monthly drift stays quiet and only a real turn of events speaks.
_THRESHOLDS = {
    "gold": 250.0,
    "stability": 5.0,
    "prestige": 10.0,
    "legitimacy": 10.0,
    "govpower": 10.0,
    "manpower": 5000.0,
    "army": 5.0,
    "navy": 5.0,
    "warexhaustion": 2.0,
    "inflation": 1.0,
    "prosperity": 5.0,
    "locations": 1.0,
    "subjects": 1.0,
    "loans": 1.0,
    "religiousunity": 8.0,
    "development": 25.0,
    "allies": 1.0,
    "rivals": 1.0,
}

_LABELS = {
    "gold": "the treasury",
    "stability": "stability",
    "prestige": "prestige",
    "legitimacy": "legitimacy",
    "govpower": "government power",
    "manpower": "manpower",
    "army": "the army",
    "navy": "the fleet",
    "warexhaustion": "war exhaustion",
    "inflation": "inflation",
    "prosperity": "prosperity",
    "locations": "the number of holdings",
    "subjects": "the number of subjects",
    "loans": "outstanding loans",
    "religiousunity": "religious unity",
    "development": "development",
    "allies": "the number of allies",
    "rivals": "the number of rivals",
}


def diff(old: Snapshot | None, new: Snapshot) -> list[Change]:
    """What changed between two snapshots, in words a narrator can use."""
    if old is None or not old.is_valid():
        return []
    out: list[Change] = []

    if old.ruler and new.ruler and old.ruler.name != new.ruler.name:
        out.append(Change("ruler", old.ruler.name, new.ruler.name,
                          f"{old.ruler.name} is gone; {new.ruler.name} now reigns"))
    elif not old.ruler and new.ruler:
        out.append(Change("ruler", "", new.ruler.name, f"{new.ruler.name} now reigns"))

    if old.religion and new.religion and old.religion != new.religion:
        out.append(Change("religion", old.religion, new.religion,
                          f"the realm's faith changed from {old.religion} to {new.religion}"))

    if old.capital and new.capital and old.capital != new.capital:
        out.append(Change("capital", old.capital, new.capital,
                          f"the capital moved from {old.capital} to {new.capital}"))

    if old.government and new.government and old.government != new.government:
        out.append(Change("government", old.government, new.government,
                          f"the government became {new.government}"))

    if old.rank != new.rank and new.rank:
        direction = "rose" if new.rank > old.rank else "fell"
        out.append(Change("rank", old.rank, new.rank, f"the realm's standing {direction}"))

    was_war, is_war = old.at_war, new.at_war
    if not was_war and is_war:
        out.append(Change("atwar", False, True, "the realm went to war"))
    elif was_war and not is_war:
        out.append(Change("atwar", True, False, "the war ended"))

    for key, threshold in _THRESHOLDS.items():
        # A key absent from either side means that dump was incomplete, not
        # that the realm lost everything. Never report a change we cannot
        # actually see both ends of.
        if key not in old.numbers or key not in new.numbers:
            continue
        before, after = old.numbers[key], new.numbers[key]
        if abs(after - before) < threshold:
            continue
        label = _LABELS.get(key, key)
        verb = "rose" if after > before else "fell"
        out.append(Change(key, before, after,
                          f"{label} {verb} from {before:g} to {after:g}"))

    for estate in ESTATES:
        before = old.estates.get(estate)
        after = new.estates.get(estate)
        if before is None or after is None:
            continue
        if abs(after - before) < 8:
            continue
        label = new.estate_names.get(estate) or estate
        verb = "warmed to" if after > before else "cooled towards"
        out.append(Change(f"estate:{estate}", before, after,
                          f"the {label} {verb} the crown ({before:.0f} -> {after:.0f})"))

    old_names = {p.name for p in old.court}
    for p in new.court:
        if p.name and p.name not in old_names and (p.in_cabinet or p.is_general):
            out.append(Change("court", "", p.name, f"{p.describe()} rose at court"))

    return out
