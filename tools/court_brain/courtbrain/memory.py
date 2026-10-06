"""The campaign's memory, which follows the player through their saves.

Without memory every conversation is the first one: the chancellor forgets
he warned you about the debt, the merchant you enriched never comes back. But
memory must also forget. Load a save from 1402 after playing to 1410 and the
court must not remember the war of 1405 - it has not happened.

So memory is a JOURNAL, not a file that is overwritten. Every thing the court
learns (an event, a person, a page of chronicle, a thread opened or closed)
is an entry with an id, the in-game date, and the id of the entry before it.
Entries form a tree: play on after loading an older save and the new entries
hang off the old point, a new branch, while the abandoned future stays in the
journal without being seen.

What the court remembers at any moment is the chain from the root to one
entry, the HEAD. The game keeps the head too: the middleware writes it into a
global variable (votc_memhead) with every mail, so every save file carries the
exact point of the journal it belongs to. When a save is loaded the game
reports that head, and the court remembers exactly what had happened up to
that save, and nothing after it.

A campaign is identified the same way, by a global variable (votc_campaign)
the middleware sets the first time it sees a game. Different campaigns -
even two games of the same country - never share a memory; saves of the same
campaign always do.

Layout, under <EU5 user dir>/court_brain/campaigns/<id>/:
  journal.jsonl   one entry per line, append-only
  meta.json       tag, name, the last head used, personas, realm profile
Personas and the realm profile are kept per campaign but outside the journal:
how a man talks does not change because the player reloaded.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import gamedate

MAX_PAGES = 200
MAX_JUDGEMENTS = 60
RECENT_EVENTS = 40
MAX_EVENTS = 400
MAX_PEOPLE = 120
#: re-record a person who has not changed once this many days have passed,
#: so "last seen" survives a reload
PERSON_REFRESH_DAYS = 180


@dataclass
class PersonNote:
    name: str
    role: str = ""
    real: bool = True          # False for people the narration invented
    first_seen: str = ""       # in-game date
    last_seen: str = ""
    standing: str = ""         # how they regard the crown, in a few words
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        bits = [self.name]
        if self.role:
            bits.append(f"({self.role})")
        if self.standing:
            bits.append(f"- {self.standing}")
        tail = "; ".join(self.notes[-2:])
        if tail:
            bits.append(f": {tail}")
        return " ".join(bits)


@dataclass
class Thread:
    key: str
    title: str
    opened: str = ""
    state: str = "open"        # open | resolved | abandoned
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        tail = "; ".join(self.notes[-2:])
        return f"[{self.state}] {self.title}" + (f" - {tail}" if tail else "")


@dataclass
class Event:
    date: str
    kind: str
    text: str


@dataclass
class Entry:
    id: int
    parent: int
    date: str
    op: str
    args: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps({"id": self.id, "p": self.parent, "d": self.date, "op": self.op, "a": self.args},
                          ensure_ascii=False)


class Memory:
    """One campaign's journal, checked out at one head."""

    def __init__(self, folder: Path, campaign: int) -> None:
        self.folder = Path(folder)
        self.campaign_id = campaign
        self.meta: dict[str, Any] = {}
        self.entries: dict[int, Entry] = {}
        self.head = 0
        self._next = 1
        self._meta_dirty = False
        self._last_meta_save = 0.0
        self._reset_state()

    # ------------------------------------------------------------ state
    def _reset_state(self) -> None:
        self.summary = ""
        self.people: dict[str, PersonNote] = {}
        self.threads: dict[str, Thread] = {}
        self.events: list[Event] = []
        self._last_chronicle = ""
        self._last_knock = ""
        self.counters: dict[str, int] = {}
        self.pages: list[dict[str, str]] = []
        # How wise the ruler's own choices were (-2..2), judged when made: the ruler's skill,
        # which moves how well their decrees turn out. In the journal, so a reload rewinds it.
        self.judgements: list[dict[str, Any]] = []
        # Follow-ups the court owes the ruler (the reactions to a decree...):
        # id -> {"due": gamedate key, "kind", "about", "who"}. In the journal,
        # so loading an older save also forgets plans made after it.
        self.plans: dict[int, dict[str, Any]] = {}
        # Stories in several stages (a plot, a feud, a city's petition...):
        # id -> {"title", "kind", "steps": [...], "open", "due", "awaiting",
        # "asked"}. "awaiting" holds the choices of the event the ruler has
        # not answered yet. In the journal, so a reload rewinds them too.
        self.arcs: dict[int, dict[str, Any]] = {}
        self.awaiting_arc = 0
        # Promises and pacts (with a foreign court, an estate, a person):
        # id -> {"party", "kind", "tag", "estate", "ruler_promise",
        # "party_promise", "watch", "watch_tag", "due", "if_kept",
        # "if_broken", "title", "status", "opened", "armed", "history"}.
        # Court Brain watches the game for what fulfils or breaks them.
        self.pacts: dict[int, dict[str, Any]] = {}
        # Standing measures in force: slot -> {"name", "gain", "tier", "money", "burden", "btier", "date"}.
        # In the journal, so a loaded save has exactly the measures it had.
        self.measures: dict[int, dict[str, Any]] = {}
        self._last_story = ""
        self._last_new = ""       # the last NEW thing the court brought unasked (the frequency's clock)
        self._last_war = ""
        # The royal biographer who writes the entry of each day: a person of the
        # court, with a character of their own, until they die or are dismissed.
        # {"name", "persona", "born", "since", "last_check"}; past ones in "biographers".
        self.biographer: dict[str, Any] | None = None
        self.biographers: list[dict[str, Any]] = []

    @property
    def path(self) -> Path:
        return self.folder / "journal.jsonl"

    @property
    def campaign(self) -> str:
        return f"{self.meta.get('name') or self.meta.get('tag') or '?'} #{self.campaign_id}"

    # ------------------------------------------------------------ files
    def load(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        meta_path = self.folder / "meta.json"
        if meta_path.is_file():
            try:
                self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.meta = {}
        self.meta.setdefault("campaign", self.campaign_id)
        self.meta.setdefault("personas", {})
        self.meta.setdefault("realm_profile", "")
        if self.path.is_file():
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        e = Entry(int(raw["id"]), int(raw.get("p") or 0), str(raw.get("d") or ""),
                                  str(raw["op"]), dict(raw.get("a") or {}))
                    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                        continue            # a torn last line after a crash
                    self.entries[e.id] = e
        self._next = max(self.entries, default=0) + 1
        head = int(self.meta.get("head") or 0)
        self.checkout(head if head in self.entries else max(self.entries, default=0))

    def save(self, *, force: bool = False) -> None:
        """The journal is written as it grows; this only flushes meta.json."""
        if not self._meta_dirty and not force:
            return
        if not force and time.time() - self._last_meta_save < 5.0:
            return
        self.meta["head"] = self.head
        self.meta["used"] = time.time()
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self.folder / "meta.tmp"
            tmp.write_text(json.dumps(self.meta, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.folder / "meta.json")
        except OSError:
            return
        self._meta_dirty = False
        self._last_meta_save = time.time()

    # ------------------------------------------------------------ the tree
    def chain(self, head: int) -> list[Entry]:
        out: list[Entry] = []
        seen: set[int] = set()
        while head and head in self.entries and head not in seen:
            seen.add(head)
            e = self.entries[head]
            out.append(e)
            head = e.parent
        out.reverse()
        return out

    def checkout(self, head: int) -> None:
        """Remember exactly what the chain ending at `head` holds."""
        self._reset_state()
        self.head = head if head in self.entries else 0
        for e in self.chain(self.head):
            self._apply(e)
        self._meta_dirty = True

    def knows(self, head: int) -> bool:
        return head in self.entries

    def head_at_date(self, date: str, *, from_head: int | None = None) -> int:
        """The last entry on the current line dated on or before `date`."""
        limit = gamedate.key(date)
        best = 0
        for e in self.chain(self.head if from_head is None else from_head):
            k = gamedate.key(e.date)
            if k and k > limit:
                break
            best = e.id
        return best

    def head_date(self) -> str:
        return self.entries[self.head].date if self.head in self.entries else ""

    def forgotten_since(self, old_head: int) -> int:
        """How many entries of the chain at `old_head` the current head does not share."""
        mine = {e.id for e in self.chain(self.head)}
        return sum(1 for e in self.chain(old_head) if e.id not in mine)

    def _record(self, op: str, date: str, **args: Any) -> None:
        e = Entry(self._next, self.head, date or self.head_date(), op, args)
        self._next += 1
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(e.to_json() + "\n")
        except OSError:
            pass
        self.entries[e.id] = e
        self.head = e.id
        self._apply(e)
        self._meta_dirty = True

    def _apply(self, e: Entry) -> None:
        a = e.args
        if e.op == "event":
            self.events.append(Event(date=e.date, kind=a.get("kind", ""), text=a.get("text", "")))
            if len(self.events) > MAX_EVENTS:
                self.events = self.events[-MAX_EVENTS:]
        elif e.op == "person":
            key = a.get("name", "").strip().lower()
            if not key:
                return
            p = self.people.get(key)
            if p is None:
                p = PersonNote(name=a["name"].strip(), real=bool(a.get("real", True)), first_seen=e.date)
                self.people[key] = p
            if a.get("role"):
                p.role = a["role"]
            if a.get("standing"):
                p.standing = a["standing"]
            if a.get("note"):
                p.notes = (p.notes + [a["note"]])[-4:]
            p.last_seen = e.date
            self._trim_people()
        elif e.op == "thread":
            key = a.get("key", "")
            t = self.threads.get(key)
            if t is None:
                t = Thread(key=key, title=a.get("title", key), opened=e.date)
                self.threads[key] = t
            if a.get("state"):
                t.state = a["state"]
            if a.get("note"):
                t.notes = (t.notes + [a["note"]])[-4:]
        elif e.op == "page":
            self.pages.append({"date": e.date, "kind": a.get("kind", ""), "title": a.get("title", ""),
                               "body": a.get("body", ""), "by": a.get("by", "")})
            self.pages = self.pages[-MAX_PAGES:]
        elif e.op == "judged":
            self.judgements.append({"date": e.date, "source": a.get("source", ""),
                                    "score": max(-2, min(2, int(a.get("score") or 0)))})
            self.judgements = self.judgements[-MAX_JUDGEMENTS:]
        elif e.op == "mark":
            if a.get("what") == "chronicle":
                self._last_chronicle = e.date
            elif a.get("what") == "knock":
                self._last_knock = e.date
            elif a.get("what") == "war":
                self._last_war = e.date
            elif a.get("what") == "new":
                self._last_new = e.date
            if a.get("what") in ("chronicle", "knock", "story"):
                self._last_story = e.date
        elif e.op == "count":
            k = a.get("key", "")
            self.counters[k] = self.counters.get(k, 0) + 1
        elif e.op == "arc_open":
            self.arcs[e.id] = {"title": a.get("title", ""), "kind": a.get("kind", ""),
                               "steps": [a.get("summary", "")] if a.get("summary") else [],
                               "open": True, "due": 0, "awaiting": None, "asked": "", "opened": e.date,
                               "span": int(a.get("span") or 0), "provoked": bool(a.get("provoked"))}
            # Only NEW things reset the clock of new things: the next stage of
            # a story already under way must not silence everything else.
            self._last_story = e.date
        elif e.op == "arc_event":
            arc = self.arcs.get(int(a.get("arc", 0)))
            if arc is not None:
                arc["awaiting"] = a.get("options") or []
                arc["asked"] = e.date
                # every dilemma already put to the ruler in this story: a next stage must not ask it again
                arc["past_options"] = (arc.get("past_options", []) + [str(o.get("label", "")) for o in
                                       (a.get("options") or []) if isinstance(o, dict)])[-12:]
                arc["past_titles"] = (arc.get("past_titles", []) + [a.get("title", "")])[-6:]
                arc["last_event"] = {"title": a.get("title", ""), "body": a.get("body", "")}
                if a.get("span"):
                    # re-estimated at every stage: developments stretch or shorten a story
                    arc["span"] = int(a["span"])
                self.awaiting_arc = int(a.get("arc", 0))
        elif e.op == "arc_step":
            arc = self.arcs.get(int(a.get("arc", 0)))
            if arc is not None:
                if a.get("provoked"):
                    arc["provoked"] = True
                if a.get("text"):
                    arc["steps"] = (arc["steps"] + [a["text"]])[-12:]
                arc["awaiting"] = None
                if a.get("close"):
                    arc["open"] = False
                    arc["due"] = 0
                else:
                    arc["due"] = int(a.get("due") or 0)
            if self.awaiting_arc == int(a.get("arc", 0)):
                self.awaiting_arc = 0
        elif e.op == "plan":
            self.plans[e.id] = dict(e.args)
        elif e.op == "measure":
            self.measures[int(a.get("slot", 0))] = {**a, "date": e.date}
        elif e.op == "measure_end":
            self.measures.pop(int(a.get("slot", 0)), None)
        elif e.op == "pact":
            self.pacts[e.id] = {**a, "status": "open", "opened": e.date, "history": []}
        elif e.op == "pact_state":
            pact = self.pacts.get(int(a.get("pact", 0)))
            if pact is not None:
                pact["status"] = a.get("status", pact["status"])
                if "due" in a:
                    pact["due"] = int(a.get("due") or 0)
                if a.get("note"):
                    pact["history"] = (pact["history"] + [f"{e.date}: {a['note']}"])[-6:]
                for k, v in a.items():
                    if k not in ("pact", "status", "note", "due"):
                        pact[k] = v
        elif e.op == "plan_done":
            self.plans.pop(int(e.args.get("plan", 0)), None)
        elif e.op == "biographer":
            if self.biographer:
                self.biographers.append(self.biographer)
            self.biographer = {**a, "since": e.date}
        elif e.op == "biographer_end":
            if self.biographer:
                self.biographers.append({**self.biographer, "until": e.date, "end": a.get("reason", ""),
                                         "wish": a.get("wish", "")})
            self.biographer = None
        elif e.op == "biographer_check":
            if self.biographer:
                self.biographer["last_check"] = a.get("year", 0)
        elif e.op == "summary":
            self.summary = a.get("text", "")
            self.events = self.events[-RECENT_EVENTS:]

    def _trim_people(self) -> None:
        if len(self.people) <= MAX_PEOPLE:
            return
        droppable = sorted((p for p in self.people.values() if not p.real), key=lambda p: gamedate.key(p.last_seen))
        for person in droppable:
            if len(self.people) <= MAX_PEOPLE:
                break
            self.people.pop(person.name.strip().lower(), None)

    # ------------------------------------------------------------ the API
    def remember_event(self, date: str, kind: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.events and self.events[-1].text == text:
            return
        self._record("event", date, kind=kind, text=text)

    def remember_person(self, name: str, *, date: str = "", role: str = "", real: bool = True,
                        standing: str = "", note: str = "") -> PersonNote | None:
        name = name.strip()
        if not name:
            return None
        known = self.people.get(name.lower())
        changed = (
            known is None
            or (role and role != known.role)
            or (standing and standing != known.standing)
            or bool(note)
            or gamedate.days_between(known.last_seen, date) >= PERSON_REFRESH_DAYS
        )
        if changed:
            args: dict[str, Any] = {"name": name, "real": real}
            if role:
                args["role"] = role
            if standing:
                args["standing"] = standing
            if note:
                args["note"] = note.strip()
            self._record("person", date, **args)
        return self.people.get(name.lower())

    def open_thread(self, key: str, title: str, date: str = "", note: str = "") -> Thread:
        if key not in self.threads or note:
            self._record("thread", date, key=key, title=title, note=note)
        return self.threads[key]

    def close_thread(self, key: str, note: str = "", date: str = "") -> None:
        if key in self.threads:
            self._record("thread", date, key=key, state="resolved", note=note)

    def judged(self, date: str, source: str, score: Any) -> None:
        """Note how wise one of the ruler's own choices was (-2 reckless .. 2 masterly)."""
        try:
            score = max(-2, min(2, int(score)))
        except (TypeError, ValueError):
            return
        self._record("judged", date, source=source, score=score)

    def add_page(self, date: str, kind: str, title: str, body: str, by: str = "") -> None:
        if body.strip():
            extra = {"by": by} if by else {}
            self._record("page", date, kind=kind, title=title.strip(), body=body.strip(), **extra)

    @staticmethod
    def later(date: str, days: int) -> int:
        """The gamedate key `days` after `date` (months counted as 30 days)."""
        y, m, d = gamedate.parse(date) or (0, 1, 1)
        total = m * 30 + d + max(1, int(days))
        y += (total - 31) // 360
        rest = (total - 31) % 360
        return y * 10000 + (rest // 30 + 1) * 100 + (rest % 30 + 1)

    def plan(self, date: str, *, after_days: int, kind: str, about: str, who: str = "", pact: int = 0,
             chain: int = 0) -> None:
        extra = {"pact": int(pact)} if pact else {}
        if chain:
            extra["chain"] = int(chain)      # how many follow-ups deep: what came of what came of a deed
        self._record("plan", date, due=self.later(date, after_days), kind=kind, about=about.strip(),
                     who=who.strip(), planned=date, **extra)

    # -- the royal biographer
    def appoint_biographer(self, date: str, **args: Any) -> None:
        self._record("biographer", date, **args)

    def end_biographer(self, date: str, reason: str, wish: str = "") -> None:
        self._record("biographer_end", date, reason=reason, wish=wish)

    def biographer_checked(self, date: str, year: int) -> None:
        self._record("biographer_check", date, year=year)

    # -- promises and pacts
    PACT_OPEN = ("open", "triggered", "delayed")

    def measure_start(self, date: str, m: dict[str, Any]) -> None:
        self._record("measure", date, **{k: m[k] for k in ("slot", "name", "gain", "tier", "money", "burden", "btier")})

    def measure_end(self, date: str, slot: int) -> None:
        self._record("measure_end", date, slot=slot)

    def pact_open(self, date: str, **args: Any) -> int:
        self._record("pact", date, **args)
        return self.head

    def pact_state(self, date: str, pact: int, status: str, *, note: str = "", due: int | None = None,
                   **fields: Any) -> None:
        extra: dict[str, Any] = {"due": int(due)} if due is not None else {}
        self._record("pact_state", date, pact=pact, status=status, note=note.strip(), **extra, **fields)

    def open_pacts(self) -> dict[int, dict[str, Any]]:
        return {k: v for k, v in self.pacts.items() if v.get("status") in self.PACT_OPEN}

    def owed_pacts(self) -> dict[int, dict[str, Any]]:
        """Promises the ruler broke and could still make good (late): what the ruler owed is
        still wanted. Once made good they are 'redeemed' - settled for good."""
        return {k: v for k, v in self.pacts.items()
                if v.get("status") in ("broken", "closed") and v.get("ruler_promise")
                and any("broken" in h or "deadline passed" in h or "not done" in h for h in v.get("history") or [])}

    def end_pact_plans(self, pact: int, date: str) -> int:
        """Drop what was still to come of a pact (its deadline, its party's answer): it is settled."""
        ids = [pid for pid, p in self.plans.items() if int(p.get("pact") or 0) == pact]
        for pid in ids:
            self.plan_done(pid, date)
        return len(ids)

    @staticmethod
    def _pact_concerns(p: dict[str, Any], scene: dict[str, Any] | None) -> bool:
        """Is this promise the business of the people in this scene? (scene None: every promise.)
        Its party is there - by name, by court, by estate - or it is a great matter the realm
        watches. A small promise to one person is never everyone's talk."""
        if scene is None:
            return True
        party = " ".join(str(p.get("party", "")).lower().split())
        for name in scene.get("names") or ():
            n = " ".join(str(name).lower().split())
            if len(n) > 2 and party and (n in party or party in n
                                         or any(len(w) > 3 and w in party.split() for w in n.split())):
                return True
        if p.get("tag") and p.get("tag") == scene.get("tag"):
            return True
        if p.get("estate") and p.get("estate") == scene.get("estate"):
            return True
        weight = p.get("weight") or ("small" if p.get("kind") == "person" else "great")
        return weight == "great"

    def pacts_brief(self, scene: dict[str, Any] | None = None) -> str:
        """Promises in force, and the ruler's record of keeping their word. scene: who is in
        the scene ({names, tag, estate}) - then only the promises that are their business."""
        out = []
        for pid, p in self.open_pacts().items():
            if not self._pact_concerns(p, scene):
                continue
            due = f", due by {p['due'] // 10000}.{p['due'] // 100 % 100}.{p['due'] % 100}" if p.get("due") else ""
            tag = f" [{p['tag']}]" if p.get("tag") else ""
            out.append(f"  - #{pid} with {p.get('party', '?')}{tag} (since {p.get('opened', '')}{due}): the ruler "
                       f"promised: {p.get('ruler_promise') or '-'}; they promised: {p.get('party_promise') or '-'}"
                       + (f" [{p['status']}]" if p.get("status") != "open" else ""))
        owed = self.owed_pacts()
        owing = []
        for pid, p in owed.items():
            if not self._pact_concerns(p, scene):
                continue
            owing.append(f"  - #{pid} with {p.get('party', '?')}: the ruler promised {p.get('ruler_promise')} "
                         f"- and did not do it ({(p.get('history') or ['broken'])[-1]})")
        closed = [p for k, p in self.pacts.items() if p.get("status") not in self.PACT_OPEN and k not in owed][-6:]
        word = []
        for p in closed:
            tag = f" [{p['tag']}]" if p.get("tag") else ""
            word.append(f"  - {p.get('party', '?')}{tag}: {p.get('title') or p.get('ruler_promise', '')} - {p['status']}"
                        + (f" ({p['history'][-1]})" if p.get("history") else ""))
        text = ""
        if out:
            text += ("PROMISES AND PACTS IN FORCE (the world remembers them; see KEEPING ONE'S WORD):\n"
                     + "\n".join(out))
        if owing:
            text += (("\n" if text else "") + "BROKEN PROMISES STILL OWED (the ruler may still make them good, late: "
                     "'made_good' in pacts_touched, by #number):\n" + "\n".join(owing[-4:]))
        if word:
            text += ("\n" if text else "") + "HOW PAST PROMISES ENDED (the ruler's word has a reputation):\n" \
                    + "\n".join(word)
        return text

    def due_plan(self, date: str) -> tuple[int, dict[str, Any]] | None:
        """The follow-up whose time has come - a pact falling due first, whatever waits before it
        (behind older follow-ups it never came)."""
        today = gamedate.key(date)
        due = [(pid, p) for pid, p in self.plans.items() if 0 < int(p.get("due", 0)) <= today]
        if not due:
            return None
        pacts = [x for x in due if x[1].get("kind") == "pact"]
        return min(pacts or due, key=lambda kv: kv[1].get("due", 0))

    @staticmethod
    def overdue_days(plan: dict[str, Any], date: str) -> int:
        """How many days a follow-up has waited past its time (months taken as 30 days)."""
        def days(k: int) -> int:
            return k // 10000 * 360 + (k // 100 % 100) * 30 + k % 100
        due = int(plan.get("due", 0) or 0)
        return days(gamedate.key(date)) - days(due) if due else 0

    def expire_plans(self, date: str, days: int) -> int:
        """Follow-ups that waited so long their moment has passed are let go (never a pact's)."""
        stale = [pid for pid, p in self.plans.items()
                 if p.get("kind") != "pact" and self.overdue_days(p, date) > days]
        for pid in stale:
            self.plan_done(pid, date)
        return len(stale)

    def plan_done(self, pid: int, date: str = "") -> None:
        self._record("plan_done", date, plan=pid)

    # -- stories in stages
    def arc_open(self, date: str, *, title: str, kind: str, summary: str, span: int = 0,
                 provoked: bool = False) -> int:
        extra = {"provoked": True} if provoked else {}
        self._record("arc_open", date, title=title.strip(), kind=kind.strip(), summary=summary.strip(),
                     span=int(span or 0), **extra)
        return self.head

    def arc_event(self, date: str, arc: int, *, title: str, body: str, options: list[dict[str, Any]],
                  span: int = 0) -> None:
        extra = {"span": int(span)} if span else {}
        self._record("arc_event", date, arc=arc, title=title, body=body[:1500], options=options, **extra)

    def arc_step(self, date: str, arc: int, *, text: str, after_days: int = 0, close: bool = False,
                 provoked: bool = False) -> None:
        due = 0 if close else self.later(date, after_days or 30)
        extra = {"provoked": True} if provoked else {}
        self._record("arc_step", date, arc=arc, text=text.strip(), due=due, close=bool(close), **extra)

    def open_arcs(self) -> dict[int, dict[str, Any]]:
        return {k: v for k, v in self.arcs.items() if v.get("open")}

    def due_arc(self, date: str) -> int:
        today = gamedate.key(date)
        for aid, arc in sorted(self.open_arcs().items(), key=lambda kv: kv[1].get("due", 0)):
            if not arc.get("awaiting") and 0 < int(arc.get("due", 0)) <= today:
                return aid
        return 0

    @property
    def last_story_date(self) -> str:
        return self._last_story

    def arcs_brief(self) -> str:
        """Open stories, for the prompt: continue them rather than inventing new ones."""
        out = []
        for aid, arc in self.open_arcs().items():
            steps = "; ".join(x for x in arc.get("steps", [])[-4:] if x)
            out.append(f"- [{arc.get('kind', '')}] {arc.get('title', '')} (since {arc.get('opened', '')}): {steps}")
        return "\n".join(out)

    def bump(self, key: str, date: str = "") -> int:
        self._record("count", date, key=key)
        return self.counters[key]

    @property
    def last_chronicle_date(self) -> str:
        return self._last_chronicle

    @last_chronicle_date.setter
    def last_chronicle_date(self, date: str) -> None:
        self._record("mark", date, what="chronicle")

    @property
    def last_war_date(self) -> str:
        """When the court last heard a tale of the war (a war chronicle or a matter of the army)."""
        return self._last_war

    @last_war_date.setter
    def last_war_date(self, date: str) -> None:
        self._record("mark", date, what="war")

    @property
    def last_new_date(self) -> str:
        """When the court last brought something NEW, unasked (a story, a page, a visitor).
        What follows from the ruler's own deeds, and the next stage of a story, does not
        count: they must never silence the new things the player chose the pace of."""
        return self._last_new

    @last_new_date.setter
    def last_new_date(self, date: str) -> None:
        self._record("mark", date, what="new")

    @property
    def last_knock_date(self) -> str:
        return self._last_knock

    @last_knock_date.setter
    def last_knock_date(self, date: str) -> None:
        self._record("mark", date, what="knock")

    # -- outside the journal
    def persona(self, name: str) -> str:
        return self.meta["personas"].get(name.strip().lower(), "")

    def set_persona(self, name: str, text: str) -> None:
        self.meta["personas"][name.strip().lower()] = text.strip()
        self._meta_dirty = True

    @property
    def realm_profile(self) -> str:
        return self.meta.get("realm_profile", "")

    @realm_profile.setter
    def realm_profile(self, text: str) -> None:
        self.meta["realm_profile"] = text
        self._meta_dirty = True

    # ------------------------------------------------------------ prompt
    @staticmethod
    def _familiarity(p: PersonNote, now: str) -> str:
        """How likely the player is to remember this person, for the prompt."""
        since = gamedate.days_between(p.last_seen, now) if p.last_seen and now else 0
        once = not p.first_seen or p.first_seen == p.last_seen
        seen = f"last seen {p.last_seen}" if p.last_seen else "seen once"
        if once or since > 120:
            return f"{seen}; the ruler may not remember them - say who they are and how the ruler knows them"
        return f"{seen}; familiar, a word on their role is enough"

    # ------------------------------------------------------------ recall
    # The brief carries only the recent past; everything older stays in the
    # journal. When something is said that touches an older matter - a name,
    # a place, a year - recall() fetches exactly those entries, so the court
    # remembers the old grudge without carrying all of history in every prompt.
    _STOP = frozenset("""the and but for with this that there their they them then than what when where which who
        whom whose will would could should have has had been being from into your yours our ours his her hers its
        not now here very more most some such only also just like well even much many other others about over under
        after before again once upon while since until until king queen lord lady count duke majesty sire highness
        messer signore father mother brother sister son daughter court crown realm council ruler people""".split())

    @classmethod
    def _terms(cls, text: str) -> set[str]:
        import re as _re
        words = _re.findall(r"[A-Za-zÀ-ÿ'][A-Za-zÀ-ÿ'-]{3,}|\b1[2-9]\d\d\b", text or "")
        return {w.lower().strip("'") for w in words if w.lower() not in cls._STOP}

    def recall(self, query: str, *, limit_chars: int = 900, skip_recent: int = 14) -> list[str]:
        """Older entries of the journal that touch what `query` is about, most relevant first."""
        terms = self._terms(query)
        if not terms:
            return []
        recent = {e.text for e in self.events[-skip_recent:]}
        scored: list[tuple[float, str]] = []
        chain = self.chain(self.head)
        n = len(chain)
        for i, e in enumerate(chain):
            if e.op == "event":
                text = e.args.get("text", "")
            elif e.op == "page":
                text = f"[{e.args.get('title', '')}] {e.args.get('body', '')}"
            elif e.op == "person" and e.args.get("note"):
                text = f"{e.args.get('name', '')} ({e.args.get('role', '')}): {e.args.get('note', '')}"
            elif e.op == "pact":
                text = (f"pact with {e.args.get('party', '')}: the ruler promised {e.args.get('ruler_promise', '')}; "
                        f"they promised {e.args.get('party_promise', '')}")
            elif e.op == "arc_step":
                text = e.args.get("text", "")
            else:
                continue
            if not text or text in recent:
                continue
            hits = terms & self._terms(text)
            if not hits:
                continue
            # rare words (names, places) weigh more than common ones; recent entries a little more
            weight = sum(3 if len(h) > 6 or h[0].isdigit() else 1 for h in hits) + i / max(1, n)
            scored.append((weight, f"{e.date}: {text}"))
        out, used = [], 0
        for _w, line in sorted(scored, key=lambda x: -x[0]):
            line = line if len(line) <= 320 else line[:317].rsplit(" ", 1)[0] + "..."
            if used + len(line) > limit_chars:
                break
            out.append(line)
            used += len(line)
        return out

    def brief(self, *, max_people: int = 12, max_threads: int = 6, focus: tuple[str, ...] = (),
              scene_tag: str = "", scene_estate: str = "") -> str:
        """What the court remembers, written for the system prompt. With a focus (the people of
        a conversation), only the promises that are their business, and the unfinished business
        as something that may come back - not something every scene must bring up."""
        out: list[str] = []
        if self.summary:
            out += ["EARLIER IN THIS REIGN:", self.summary.strip(), ""]
        if self.biographer:
            b = self.biographer
            out += [f"THE ROYAL BIOGRAPHER: {b.get('name', '')}, {b.get('origin', '')}, keeps the book of the "
                    f"ruler's life (since {b.get('since', '')}).", ""]
        open_threads = [t for t in self.threads.values() if t.state == "open"]
        scene = {"names": focus, "tag": scene_tag, "estate": scene_estate} if focus or scene_tag or scene_estate \
            else None
        pacts = self.pacts_brief(scene)
        if pacts:
            out += [pacts, ""]
        stories = self.arcs_brief()
        if stories:
            out.append("STORIES STILL UNFOLDING (they come back in later events):")
            out.append(stories)
            out.append("")
        if open_threads:
            out.append("UNFINISHED BUSINESS (pick these up rather than inventing new ones):" if scene is None else
                       "UNFINISHED BUSINESS (it may come back when it truly bears on the matter at hand, and only "
                       "from those it concerns - never dragged into a conversation about something else):")
            out += [f"  - {t.line()}" for t in open_threads[-max_threads:]]
            out.append("")
        if self.people:
            known = sorted(self.people.values(), key=lambda p: gamedate.key(p.last_seen), reverse=True)
            if focus:
                # the people of this scene first, then the most recently seen
                wanted = {f.strip().lower() for f in focus if f}
                known.sort(key=lambda p: p.name.strip().lower() not in wanted)
            now = self.head_date()
            out.append("PEOPLE ALREADY IN THIS STORY (bring them back when they fit; when you do, say who "
                       "they are - see INTRODUCE PEOPLE):")
            for p in known[:max_people]:
                out.append(f"  - {p.line()} [{self._familiarity(p, now)}]")
            out.append("")
        if self.events:
            out.append("RECENTLY:")
            out += [f"  {e.date}: {e.text}" for e in self.events[-RECENT_EVENTS:][-14:]]
        return "\n".join(out).strip()

    def records(self, *, start: int = 0, end: int = 0, limit: int = 200) -> list[str]:
        """Everything the journal holds on the current line, dated, oldest first.

        Unlike `events` this is never trimmed, so the advisor can report on
        any period of the campaign. `start`/`end` are gamedate keys.
        """
        out: list[str] = []
        for e in self.chain(self.head):
            k = gamedate.key(e.date)
            if (start and k and k < start) or (end and k and k > end):
                continue
            if e.op == "event":
                out.append(f"{e.date}: {e.args.get('text', '')}")
            elif e.op == "page":
                body = e.args.get("body", "")
                out.append(f"{e.date}: [chronicle page] {e.args.get('title', '')} - {body[:500]}")
            elif e.op == "thread" and e.args.get("state") == "resolved":
                out.append(f"{e.date}: [storyline closed] {e.args.get('title') or e.args.get('key', '')}")
        return out[-limit:]

    def needs_summary(self) -> bool:
        """Enough older facts have piled up to fold them into the chronicle summary."""
        return len(self.events) > RECENT_EVENTS + 30

    def fold_into_summary(self, new_summary: str, date: str = "") -> None:
        self._record("summary", date, text=new_summary.strip())

    def events_to_summarise(self) -> list[Event]:
        return self.events[:-RECENT_EVENTS] if self.needs_summary() else []


class CampaignStore:
    """All the campaigns Court Brain knows, one folder each."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.root = self.state_dir / "campaigns"

    def ids(self) -> list[int]:
        if not self.root.is_dir():
            return []
        return sorted(int(p.name) for p in self.root.iterdir() if p.is_dir() and p.name.isdigit())

    def open(self, campaign: int, *, tag: str = "", name: str = "") -> Memory:
        mem = Memory(self.root / str(campaign), campaign)
        mem.load()
        if tag and not mem.meta.get("tag"):
            mem.meta["tag"] = tag
        if name and not mem.meta.get("name"):
            mem.meta["name"] = name
        mem.save(force=True)
        return mem

    def new_id(self) -> int:
        taken = set(self.ids())
        while True:
            # Kept well inside what a game variable holds exactly.
            n = random.randint(100_000, 999_999)
            if n not in taken:
                return n

    def meta(self, campaign: int) -> dict[str, Any]:
        try:
            return json.loads((self.root / str(campaign) / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def latest_for_tag(self, tag: str) -> int:
        """The most recently used campaign of this country, or 0."""
        best, when = 0, -1.0
        for cid in self.ids():
            meta = self.meta(cid)
            if meta.get("tag") == tag and float(meta.get("used") or 0) > when:
                best, when = cid, float(meta.get("used") or 0)
        return best

    # -------------------------------------------------- the old format
    def import_legacy(self, mem: Memory, tag: str) -> int:
        """Fold memory_<TAG>_*.json files (version 1) into a new campaign.

        Those files had no notion of saves: everything they hold becomes one
        line of the journal, in date order, so loading an older save still
        forgets what came after it.
        """
        count = 0
        for path in sorted(self.state_dir.glob(f"memory_{tag}_*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items: list[tuple[int, str, str, dict]] = []
            for e in data.get("events") or []:
                items.append((gamedate.key(e.get("date", "")), e.get("date", ""), "event",
                              {"kind": e.get("kind", ""), "text": e.get("text", "")}))
            for p in (data.get("people") or {}).values():
                args = {"name": p.get("name", ""), "real": p.get("real", True)}
                for k in ("role", "standing"):
                    if p.get(k):
                        args[k] = p[k]
                items.append((gamedate.key(p.get("first_seen", "")), p.get("first_seen", ""), "person", args))
            for t in (data.get("threads") or {}).values():
                items.append((gamedate.key(t.get("opened", "")), t.get("opened", ""), "thread",
                              {"key": t.get("key", ""), "title": t.get("title", ""), "state": t.get("state", "open")}))
            for pg in data.get("pages") or []:
                items.append((gamedate.key(pg.get("date", "")), pg.get("date", ""), "page",
                              {"kind": pg.get("kind", ""), "title": pg.get("title", ""), "body": pg.get("body", "")}))
            items.sort(key=lambda t: t[0])
            for _k, date, op, args in items:
                mem._record(op, date, **args)
                count += 1
            for name, text in (data.get("personas") or {}).items():
                mem.meta["personas"].setdefault(name, text)
            if data.get("realm_profile") and not mem.realm_profile:
                mem.realm_profile = data["realm_profile"]
            try:
                path.rename(path.with_suffix(".imported"))
            except OSError:
                pass
        if count:
            mem.save(force=True)
        return count
