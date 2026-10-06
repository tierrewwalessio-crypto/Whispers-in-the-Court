"""The whole world, read from a save.

EU5 saves are plain text (verified: "SAV02..." header, then readable script).
A context line from the game can only carry what the player just clicked on;
the save carries everything: every realm with its government, laws and
ruler, every character with traits and skills, every alliance, subject bond,
rivalry and war, and the history of every event that has fired.

Court Brain asks the game for a save now and then (the mod's bridge runs the
console command `save votc_world`), and otherwise falls back to the newest
autosave. This module reads one without parsing all of it: it indexes the
sections it needs and pulls fields out with targeted patterns, which keeps a
300 MB save to a few seconds.
"""

from __future__ import annotations

import mmap
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Character:
    cid: int
    first_name: str = ""
    country: int = 0
    adm: int = 0
    dip: int = 0
    mil: int = 0
    traits: list[str] = field(default_factory=list)
    estate: str = ""
    birth: str = ""


@dataclass
class Country:
    cid: int
    tag: str
    kind: str = ""
    gold: float = 0.0
    stability: float = 0.0
    prestige: float = 0.0
    legitimacy: float = 0.0
    government: str = ""
    ruler: int = 0
    heir: int = 0
    consort: int = 0
    parliament: str = ""
    laws: dict[str, str] = field(default_factory=dict)
    rank: str = ""
    score_place: int = 0
    institutions: list[str] = field(default_factory=list)   # the institutions this realm has embraced
    advances: list[str] = field(default_factory=list)       # what it has learnt (the player's realm only)


@dataclass
class War:
    attackers: list[int] = field(default_factory=list)
    defenders: list[int] = field(default_factory=list)
    key: int = 0
    start: str = ""
    # country id -> {"battle": men lost in battle, "attrition": men lost to
    # hunger and disease, "combat": war score from battles, "siege": from sieges}
    losses: dict[int, dict[str, float]] = field(default_factory=dict)


@dataclass
class Army:
    country: int
    location: int
    previous: int = 0
    last_victory: str = ""        # the date of its last battle won
    retreating: bool = False      # falling back after a lost battle
    leader: int = 0
    sieging: bool = False


@dataclass
class Siege:
    location: int
    besiegers: list[int]
    defender: int
    days: int = 0
    status: str = ""


@dataclass
class FiredEvent:
    key: str
    date: str
    country: int


@dataclass
class World:
    path: str = ""
    date: str = ""
    read_at: float = 0.0
    player: int = 0
    countries: dict[int, Country] = field(default_factory=dict)
    by_tag: dict[str, int] = field(default_factory=dict)
    characters: dict[int, Character] = field(default_factory=dict)
    alliances: set[tuple[int, int]] = field(default_factory=set)
    subjects: list[tuple[int, int, str]] = field(default_factory=list)   # overlord, subject, type
    rivals: dict[int, list[int]] = field(default_factory=dict)
    wars: list[War] = field(default_factory=list)
    events: list[FiredEvent] = field(default_factory=list)
    sieges: list[Siege] = field(default_factory=list)
    armies: list[Army] = field(default_factory=list)
    # location -> (owner, controller), only where they differ: land held by an enemy
    occupied: dict[int, tuple[int, int]] = field(default_factory=dict)
    # location -> owner, for every owned location (what each realm holds, by place)
    owners: dict[int, int] = field(default_factory=dict)
    # The age of the world, as the game has it: its current age, the
    # institutions that have appeared somewhere, and the great situations
    # (the Black Death, the Hundred Years' War, the Reformation...) under way.
    age: str = ""
    institutions: list[str] = field(default_factory=list)
    situations: list[str] = field(default_factory=list)
    buildings: dict[str, int] = field(default_factory=dict)    # the player's realm: type -> levels in all
    raw_goods: dict[str, int] = field(default_factory=dict)    # the player's realm: raw material -> places

    # ------------------------------------------------------------------
    def tag(self, cid: int) -> str:
        c = self.countries.get(cid)
        return c.tag if c else str(cid)

    def allies_of(self, cid: int) -> list[int]:
        return sorted({b if a == cid else a for a, b in self.alliances if cid in (a, b)})

    def overlord_of(self, cid: int) -> tuple[int, str] | None:
        for over, sub, kind in self.subjects:
            if sub == cid:
                return over, kind
        return None

    def subjects_of(self, cid: int) -> list[tuple[int, str]]:
        return [(sub, kind) for over, sub, kind in self.subjects if over == cid]

    def wars_of(self, cid: int) -> list[War]:
        return [w for w in self.wars if cid in w.attackers or cid in w.defenders]

    def great_powers(self, n: int = 8) -> list[Country]:
        real = [c for c in self.countries.values() if c.kind == "Real" and c.score_place > 0]
        return sorted(real, key=lambda c: c.score_place)[:n]


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
#
# A save in debug mode is 300-400 MB of text. Reading it whole (the bytes, a
# decoded copy, slices of it) took over 1.2 GB of memory for several seconds,
# next to a game that itself uses 10 GB or more: on a PC short of memory, that
# is what could bring the game down. So the file is only indexed (a memory map,
# closed again at once, so that the game is never kept from writing its next
# save), and each section needed is then read, parsed and dropped on its own,
# as bytes: never more than the largest section (about 160 MB) at a time.

NL = b"\n"


# Top-level sections in the order EU5 writes them. Country blocks contain
# column-0 lines of their own (regnal names like "name_rudolf=2"), so the end
# of a section is found by looking for the next KNOWN section, not the next
# line that merely looks top-level.
SECTIONS = (
    "metadata start_of_day current_age speed random_seed random_count variables "
    "language_manager ironman_manager great_power_manager road_network resolution_manager "
    "situation_manager dynamic_game_object_manager institution_manager loan_manager "
    "construction_manager tutorial_manager culture_manager holy_site_manager periphora "
    "dynasty_manager character_db religion_manager work_of_art_manager rulerterm_manager "
    "cabinet_manager estate_manager bureaucracy_manager countries market_manager "
    "disaster_manager building_manager population subunit_manager unit_manager provinces "
    "townrights_manager trade_path_manager trade_manager mercenary_manager prisoner_manager "
    "imperialcircle_manager international_organization_manager weather exploration_manager "
    "migration_manager privateer_manager maritime_manager colony_manager diplomacy_manager "
    "war_manager siege_manager combat_manager rebel_manager terra_incognita military_objective "
    "strategic_military_objective diplomatic_objective cardinals cheats locations "
    "disease_outbreak_manager movement_outbreak_manager naval_transport_manager advance_manager "
    "event_manager unit_template_manager played_country game_rules counters tasks "
    "coat_of_arms_manager playing_past_end_date first_start previous_played start_oos_resync"
).split()

# The sections read, each up to the next known section after it...
_NEEDED = ("countries", "character_db", "diplomacy_manager", "war_manager", "siege_manager",
           "unit_manager", "locations", "event_manager")
# ...and two small ones, up to the next line at the margin (all inside them is indented).
_TOP = ("institution_manager", "situation_manager", "building_manager")
_TOP_KEY = re.compile(rb"\n[a-z_]+=")
_PLAYED = re.compile(rb"\nplayed_country=\{[^}]*?country=(\d+)")
_AGE = re.compile(rb"\ncurrent_age=(\w+)")
_WORD = rb'"?((?:[\w.\-]|[\x80-\xff])+)"?'      # a value; a UTF-8 letter counts as a letter


def _s(b: bytes) -> str:
    return b.decode("utf-8", errors="replace")


def _section(buf, name: str) -> tuple[int, int]:
    start = buf.find(NL + name.encode() + b"=")
    if start < 0:
        return -1, -1
    end = len(buf)
    try:
        later = SECTIONS[SECTIONS.index(name) + 1:]
    except ValueError:
        later = []
    for nxt in later:
        pos = buf.find(NL + nxt.encode() + b"=", start + 1)
        if pos > 0:
            end = pos
            break
    return start, end


def _top_block(buf, name: str) -> tuple[int, int]:
    start = buf.find(NL + name.encode() + b"={")
    if start < 0:
        return -1, -1
    m = _TOP_KEY.search(buf, start + 1)
    return start, (m.start() if m else len(buf))


_PATTERNS: dict[tuple[str, bytes], re.Pattern] = {}


def _field(key: str, value: bytes) -> re.Pattern:
    pat = _PATTERNS.get((key, value))
    if pat is None:
        pat = _PATTERNS[(key, value)] = re.compile(rb"\n\t+" + key.encode() + b"=" + value)
    return pat


def _num(block: bytes, key: str) -> float:
    m = _field(key, rb"(-?[\d.]+)").search(block)
    try:
        return float(m.group(1)) if m else 0.0
    except ValueError:
        return 0.0


def _int(block: bytes, key: str) -> int:
    return int(_num(block, key))


def _word(block: bytes, key: str) -> str:
    m = _field(key, _WORD).search(block)
    return _s(m.group(1)) if m else ""


def _index(path: str | Path) -> tuple[dict[str, tuple[int, int]], World]:
    """Where each needed section lies, and the few loose values of the top level."""
    w = World(path=str(path), read_at=time.time())
    spans: dict[str, tuple[int, int]] = {}
    with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        m = re.search(rb"metadata=\{\s*date=([\d.]+)", mm[:4000])
        w.date = _s(m.group(1)) if m else ""
        pos = mm.find(b"\nplayed_country={")
        while pos >= 0:
            m = _PLAYED.match(mm, pos)
            if m:
                w.player = int(m.group(1))
                break
            pos = mm.find(b"\nplayed_country={", pos + 1)
        pos = mm.find(b"\ncurrent_age=")
        m = _AGE.match(mm, pos) if pos >= 0 else None
        w.age = _s(m.group(1)) if m else ""
        for name in _NEEDED:
            start, end = _section(mm, name)
            if start >= 0:
                spans[name] = (start, end)
        for name in _TOP:
            start, end = _top_block(mm, name)
            if start >= 0:
                spans[name] = (start, end)
    return spans, w


def _load(path: str | Path, span: tuple[int, int]) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(span[0])
        return fh.read(span[1] - span[0])


def read(path: str | Path) -> World:
    t0 = time.time()
    spans, w = _index(path)
    readers = (("countries", _read_countries), ("character_db", _read_characters),
               ("diplomacy_manager", _read_diplomacy), ("war_manager", _read_wars),
               ("siege_manager", _read_sieges), ("unit_manager", _read_armies),
               ("locations", _read_occupation), ("event_manager", _read_events),
               ("institution_manager", _read_institutions), ("situation_manager", _read_situations),
               ("building_manager", _read_buildings))
    for name, reader in readers:
        if name in spans:
            section = _load(path, spans[name])
            reader(section, w)
            del section
    w.read_at = time.time()
    w.path = f"{path} ({time.time() - t0:.1f}s)"
    return w


def _blocks(text: bytes, start: int, end: int, head: re.Pattern) -> list[tuple[int, int, int]]:
    """(id, block start, block end) for each id-block in [start, end)."""
    hits = [(int(m.group(1)), m.start()) for m in head.finditer(text, start, end)]
    out = []
    for i, (cid, pos) in enumerate(hits):
        nxt = hits[i + 1][1] if i + 1 < len(hits) else end
        out.append((cid, pos, nxt))
    return out


# country_name="SIC" - or, for a realm renamed in play (by the mod, or the game), a block
# with its new name; the tag is then its "definition".
_COUNTRY = re.compile(rb"\n(\d+)=\{\n\tcountry_name=[\"{]")
_CURRENCY = re.compile(rb"currency_data=\{(.*?)\n\t\}", re.S)
_GOVERNMENT = re.compile(rb"\n\tgovernment=\{(.*?)\n\t\}", re.S)
_LAWS = re.compile(rb"implemented_laws=\{(.*?)\n\t\t\}", re.S)
_LAW = re.compile(rb"\n\t+(\w+)=\{[^}]*?object=(\w+)")
_INSTITUTIONS = re.compile(rb"\n\tinstitutions=\{([^}]*)\}")
_ADVANCES = re.compile(rb"researched_advances=\{([^}]*)\}")
_YES = re.compile(rb"(\w+)=yes")
_RANK = re.compile(rb"country_rank=(\w+)")


def _read_countries(text: bytes, w: World) -> None:
    db = text.find(b"database={")
    if db < 0:
        return
    for cid, a, b in _blocks(text, db, len(text), _COUNTRY):
        block = text[a:b]
        tag = _word(block, "country_name") if block.find(b"country_name=\"") >= 0 else ""
        tag = tag or _word(block, "definition") or _word(block, "original_tag")
        c = Country(cid=cid, tag=tag, kind=_word(block, "country_type"))
        cur = _CURRENCY.search(block)
        if cur:
            cb = NL + cur.group(1)
            c.gold, c.stability = _num(cb, "gold"), _num(cb, "stability")
            c.prestige, c.legitimacy = _num(cb, "prestige"), _num(cb, "legitimacy")
        gov = _GOVERNMENT.search(block)
        if gov:
            g = NL + gov.group(1)
            c.government = _word(g, "type")
            c.ruler, c.heir, c.consort = _int(g, "ruler"), _int(g, "heir"), _int(g, "consort")
            c.parliament = _word(g, "parliament_type")
            laws = _LAWS.search(g)
            if laws:
                for lm in _LAW.finditer(laws.group(1)):
                    c.laws[_s(lm.group(1))] = _s(lm.group(2))
        inst = _INSTITUTIONS.search(block)
        if inst:
            c.institutions = [_s(x) for x in _YES.findall(inst.group(1))]
        if cid == w.player:
            adv = _ADVANCES.search(block)
            if adv:
                c.advances = [_s(x) for x in _YES.findall(adv.group(1))]
        ranks = _RANK.findall(block)
        c.rank = _s(ranks[-1]) if ranks else ""
        c.score_place = _int(block, "score_place")
        w.countries[cid] = c
        if tag and c.kind == "Real":
            w.by_tag[tag] = cid


_CHARACTER = re.compile(rb"\n(\d+)=\{\n\tcountry=")
_TRAITS = re.compile(rb"\n\ttraits=\{([^}]*)\}")


def _read_characters(text: bytes, w: World) -> None:
    db = text.find(b"database={")
    if db < 0:
        return
    wanted = set()
    for c in w.countries.values():
        wanted.update(x for x in (c.ruler, c.heir, c.consort) if x)
    for cid, a, b in _blocks(text, db, len(text), _CHARACTER):
        block = text[a:b]
        country = _int(block, "country")
        # Everyone at the player's court, plus the rulers and heirs of the
        # world; the rest of the population of characters is not needed.
        if country != w.player and cid not in wanted:
            continue
        ch = Character(cid=cid, country=country, first_name=_word(block, "first_name"),
                       adm=_int(block, "adm"), dip=_int(block, "dip"), mil=_int(block, "mil"),
                       estate=_word(block, "estate"), birth=_word(block, "birth_date"))
        tm = _TRAITS.search(block)
        if tm:
            ch.traits = [_s(x) for x in tm.group(1).split()]
        w.characters[cid] = ch


_MUTUAL = re.compile(rb"scripted_mutual=\{\s*first=(\d+)\s*second=(\d+).*?object=(\w+)", re.S)
_DEPENDENCY = re.compile(rb"dependency=\{\s*first=(\d+)\s*second=(\d+).*?object=(\w+)", re.S)
# A realm's block in the diplomacy section opens with "<id>={" and its diplomats, and
# may list its rivals before the block ends. Searched block by block: one pattern over
# the whole section took 8 s, and gave the rivals of a realm to the realm before it
# whenever that one's block was empty.
_DIPLO_MARK = b"={\n\t\tdiplomats="
_RIVAL_LIST = re.compile(rb"rivals_2=\{\s*list=\{(.*?)\}\s*\}", re.S)
_COUNTRY_REF = re.compile(rb"country=(\d+)")


def _read_diplomacy(body: bytes, w: World) -> None:
    for m in _MUTUAL.finditer(body):
        if m.group(3) == b"alliance":
            a, b = int(m.group(1)), int(m.group(2))
            w.alliances.add((min(a, b), max(a, b)))
    for m in _DEPENDENCY.finditer(body):
        w.subjects.append((int(m.group(1)), int(m.group(2)), _s(m.group(3))))
    mark = body.find(_DIPLO_MARK)
    while mark >= 0:
        first = mark
        while first > 0 and 48 <= body[first - 1] <= 57:
            first -= 1
        nxt = body.find(_DIPLO_MARK, mark + 1)
        if first < mark:
            end = body.find(b"\n\t}", mark)
            end = end if 0 <= end and (nxt < 0 or end < nxt) else (nxt if nxt >= 0 else len(body))
            pos = body.find(b"rivals_2=", mark, end)
            while pos >= 0:
                m = _RIVAL_LIST.match(body, pos)
                if m:
                    w.rivals[int(body[first:mark])] = [int(x) for x in _COUNTRY_REF.findall(m.group(1))]
                    break
                pos = body.find(b"rivals_2=", pos + 1, end)
        mark = nxt


_WAR = re.compile(rb"\n(\d+)=\{\n\tall=\{(.*?)\n\t\}\n(.*?)(?=\n\d+=\{\n\tall=|\Z)", re.S)
_START = re.compile(rb"\n\tstart_date=([\d.]+)")
_PARTY = re.compile(rb"country=(\d+)(.*?)status=(\w+)", re.S)
_SIDE = re.compile(rb"side=(\w+)")
_LOSSES = (("battle", re.compile(rb"\bBattle=([\d.]+)")), ("attrition", re.compile(rb"\bAttrition=([\d.]+)")),
           ("combat", re.compile(rb"\bCombat=(-?[\d.]+)")), ("siege", re.compile(rb"\bSiege=(-?[\d.]+)")))


def _read_wars(body: bytes, w: World) -> None:
    for wm in _WAR.finditer(body):
        war = War(key=int(wm.group(1)))
        m = _START.search(wm.group(3))
        war.start = _s(m.group(1)) if m else ""
        for part in _PARTY.finditer(wm.group(2)):
            if part.group(3) != b"Active":
                continue
            cid, block = int(part.group(1)), part.group(2)
            side = _SIDE.search(block)
            (war.attackers if side and side.group(1) == b"Attacker" else war.defenders).append(cid)
            war.losses[cid] = {name: sum(float(x) for x in pat.findall(block)) for name, pat in _LOSSES}
        if war.attackers and war.defenders:
            w.wars.append(war)


_ARMY = re.compile(rb"\n\tis_army=yes\n(.*?)\n\}", re.S)
_ARMY_COUNTRY = re.compile(rb"\n\tcountry=(\d+)")
_ARMY_LOCATION = re.compile(rb"\n\tlocation=(\d+)")
_ARMY_PREVIOUS = re.compile(rb"\n\tprevious=(\d+)")
_ARMY_WON = re.compile(rb"\n\tlast_victory=([\d.]+)")
_ARMY_LEADER = re.compile(rb"\n\tleader=(\d+)")


def _read_armies(body: bytes, w: World) -> None:
    """Where each army is, whom it follows, when it last won, whether it is falling back."""
    for m in _ARMY.finditer(body):
        block = m.group(1)
        country, location = _ARMY_COUNTRY.search(block), _ARMY_LOCATION.search(block)
        if not country or not location:
            continue
        prev = _ARMY_PREVIOUS.search(block)
        won = _ARMY_WON.search(block)
        leader = _ARMY_LEADER.search(block)
        w.armies.append(Army(country=int(country.group(1)), location=int(location.group(1)),
                             previous=int(prev.group(1)) if prev else 0,
                             last_victory=_s(won.group(1)) if won else "",
                             retreating=b"\n\tretreating=yes" in block,
                             leader=int(leader.group(1)) if leader else 0,
                             sieging=b"activity_type=Sieging" in block))


_SIEGE = re.compile(rb"\n\d+=\{\n\tlocation=(\d+)(.*?)\n\}", re.S)
_BESIEGERS = re.compile(rb"countries=\{([^}]*)\}")
_DEFENDER = re.compile(rb"defender=(\d+)")
_TOTAL = re.compile(rb"\btotal=(\d+)")
_STATUS = re.compile(rb"siege_status=(\w+)")


def _read_sieges(body: bytes, w: World) -> None:
    for m in _SIEGE.finditer(body):
        block = m.group(2)
        besiegers = _BESIEGERS.search(block)
        defender = _DEFENDER.search(block)
        if not besiegers or not defender:
            continue
        total = _TOTAL.search(block)
        status = _STATUS.search(block)
        w.sieges.append(Siege(location=int(m.group(1)), besiegers=[int(x) for x in besiegers.group(1).split()],
                              defender=int(defender.group(1)), days=int(total.group(1)) if total else 0,
                              status=_s(status.group(1)) if status else ""))


_HOLDING = re.compile(rb"\n\t\t(\d+)=\{\n\t\t\towner=(\d+)\n\t\t\tcontroller=(\d+)")


_RAW = re.compile(rb"\n\t\t\traw_material=(\w+)")


def _read_occupation(body: bytes, w: World) -> None:
    raw: dict[str, int] = {}
    holdings = list(_HOLDING.finditer(body))
    for i, m in enumerate(holdings):
        loc, owner = int(m.group(1)), int(m.group(2))
        w.owners[loc] = owner
        if m.group(2) != m.group(3):
            w.occupied[loc] = (owner, int(m.group(3)))
        if owner == w.player:
            # what the player's own lands bring out of the ground (the rest of the world is not kept)
            end = holdings[i + 1].start() if i + 1 < len(holdings) else len(body)
            g = _RAW.search(body, m.end(), end)
            if g:
                key = _s(g.group(1))
                raw[key] = raw.get(key, 0) + 1
    w.raw_goods = raw


_ACTIVE = re.compile(rb"\n\t\t(\w+)=\{\n\t\t\tactive=yes")
# A situation sits one tab in, often right after the previous one's "}" on the same line;
# only those under way now count ("after" is over, an empty block never began).
_SITUATION = re.compile(rb"[\n}]\t(\w+)=\{\s*status=(\w+)")


_BUILDING = re.compile(rb"\n\d+=\{\n\ttype=(\w+)\n\tlevel=(\d+)(.*?)\n\towner=(\d+)", re.S)


def _read_buildings(body: bytes, w: World) -> None:
    """What the player's realm has built: building type -> levels in all (mills, guilds,
    manufactories, universities...). The rest of the world's buildings are not kept."""
    mine: dict[str, int] = {}
    tag = str(w.player).encode()
    for m in _BUILDING.finditer(body):
        if m.group(4) == tag:
            key = _s(m.group(1))
            mine[key] = mine.get(key, 0) + int(m.group(2))
    w.buildings = mine


def _read_institutions(body: bytes, w: World) -> None:
    w.institutions = [_s(x) for x in _ACTIVE.findall(body)]


def _read_situations(body: bytes, w: World) -> None:
    # an inactive situation is an empty block; a live one carries its state
    w.situations = [_s(m.group(1)) for m in _SITUATION.finditer(body) if m.group(2) == b"active"]


_EVENT = re.compile(rb"root=\{\s*type=ctry\s*identity=(\d+).*?event_key=([\w.]+)\s*(?:primary=\w+\s*)?date=([\d.]+)",
                    re.S)


def _read_events(body: bytes, w: World) -> None:
    for m in _EVENT.finditer(body):
        w.events.append(FiredEvent(key=_s(m.group(2)), date=_s(m.group(3)), country=int(m.group(1))))


# --------------------------------------------------------------------------
# Finding a save to read
# --------------------------------------------------------------------------

def free_memory_mb() -> int | None:
    """Physical memory still available to programs, in MB (None where it cannot be known)."""
    try:
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPhys // (1024 * 1024))
    except (AttributeError, OSError, ValueError):
        return None


def newest_save(save_dir: Path, prefer: str = "votc_world") -> Path | None:
    if not save_dir.is_dir():
        return None
    # scandir: one listing with the file times in it (looked at every few seconds)
    try:
        with os.scandir(save_dir) as it:
            newest = max((e for e in it if e.name.endswith(".eu5") and e.is_file()),
                         key=lambda e: e.stat().st_mtime, default=None)
    except OSError:
        return None
    return Path(newest.path) if newest is not None else None
