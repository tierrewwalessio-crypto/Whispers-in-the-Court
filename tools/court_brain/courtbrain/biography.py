"""The book of the ruler's life.

Everything the biographer will need to write a ruler's Life is kept here, in
its own file beside the campaign's journal (biography.json): one short note
per thing that mattered - an audience, a decree, a choice, a battle, a pact -
and the chapters already written. Nothing of it is ever put in a prompt,
except when the player asks to read the Life: that is the only time these
notes cost tokens.

When a ruler dies the biographer writes the short account of the reign that
appears in game; the notes stay until the whole Life has been written (or
until the next reign ends). Then they are deleted and only the finished text
remains, to be read again for free - while the court's own memory keeps its
usual summary.

Every hundred years the biographer offers the history of the realm over that
century. It is written from the annals - the weighty notes of every reign,
kept here (never in a prompt) until that history is written - and from the
account of each reign; then those annals are deleted too.

The book does not need the AI to fill it: besides what happens in Court
Brain's own scenes, it takes down what the game itself shows - wars begun and
ended, alliances, rivals, subjects, laws, marriages, land won and lost, the
game's own events - and once a year the realm's accounts in one line. A player
who hardly talks to the court still gets a Life and a history of the century.

A loaded save rewinds the book: notes and chapters dated after the game's
date are dropped the moment something new is written, and never read.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import gamedate

#: the Life so far, shown in the panel: never longer than this
BOOK_LIMIT = 10_000
#: notes kept per reign; beyond it the least weighty old ones go
MAX_NOTES = 400
#: dead reigns whose notes are kept for a Life not yet written
KEEP_DEAD_MATERIAL = 2
#: the weighty notes kept across reigns for the history of a century
MAX_ANNALS = 1200
CENTURY = 100


def _key(date: str) -> int:
    return gamedate.key(date) if date else 0


class Book:
    def __init__(self, folder: Path) -> None:
        self.path = Path(folder) / "biography.json"
        try:
            self.data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("reigns", [])
        self.data.setdefault("annals", [])
        self.data.setdefault("centuries", [])
        self.data.setdefault("years", [])

    # ------------------------------------------------------------- storage
    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    @property
    def reigns(self) -> list[dict[str, Any]]:
        return self.data["reigns"]

    # --------------------------------------------------------------- reigns
    def reign(self, ruler: str, title: str, date: str) -> dict[str, Any]:
        """The book of the reigning ruler, opened at their first note."""
        k = _key(date)
        for r in reversed(self.reigns):
            if r["ruler"] != ruler:
                continue
            if not r.get("end"):
                return r
            if k and k < _key(r["end"]):
                # A save from before the reign ended: the ruler lives again.
                r["end"], r["obit"], r["life"] = "", "", ""
                return r
        for r in self.reigns:
            if not r.get("end"):
                r["end"] = date
        r = {"ruler": ruler, "title": title, "start": date, "end": "", "notes": [], "chapters": [],
             "obit": "", "life": "", "life_by": ""}
        self.reigns.append(r)
        return r

    def current(self, ruler: str) -> dict[str, Any] | None:
        for r in reversed(self.reigns):
            if r["ruler"] == ruler and not r.get("end"):
                return r
        return None

    def end_reign(self, ruler: str, date: str) -> dict[str, Any] | None:
        r = self.current(ruler)
        if r is None:
            return None
        r["end"] = date
        # Only the last few dead reigns keep their notes for a Life not yet written.
        dead = [x for x in self.reigns if x.get("end") and (x.get("notes") or x.get("chapters"))]
        for old in dead[:-KEEP_DEAD_MATERIAL]:
            old["notes"], old["chapters"] = [], []
        self.save()
        return r

    def last_dead(self) -> dict[str, Any] | None:
        for r in reversed(self.reigns):
            if r.get("end") and (r.get("life") or r.get("notes") or r.get("chapters") or r.get("obit")):
                return r
        return None

    # ---------------------------------------------------------------- notes
    def note(self, reign: dict[str, Any], date: str, text: str, *, weight: int = 1, by: str = "",
             rewind: bool = True) -> None:
        """rewind=False for records dated in the past (read from a save a little
        older than the game): they are slotted in by date and drop nothing."""
        text = " ".join(str(text).split())[:260]
        if not text:
            return
        k = _key(date)
        if rewind:
            notes = [n for n in reign["notes"] if n[0] <= k]          # a loaded save rewinds the book
        else:
            notes = list(reign["notes"])
            if reign["chapters"]:
                k = max(k, reign["chapters"][-1]["upto"] + 1)         # a chapter already written is not reopened
        if any(n[3] == text and abs(n[0] - k) < 10000 for n in notes[-40:]):
            return
        entry = [k, date, int(weight), text, by]
        if rewind or not notes or notes[-1][0] <= k:
            notes.append(entry)
        else:
            at = next(i for i, n in enumerate(notes) if n[0] > k)
            notes.insert(at, entry)
        if len(notes) > MAX_NOTES:
            # the lightest of the oldest half go first
            half = len(notes) // 2
            light = next((i for i in range(half) if notes[i][2] <= 1), 0)
            notes.pop(light)
        reign["notes"] = notes
        if rewind:
            reign["chapters"] = [c for c in reign["chapters"] if c["upto"] <= k]
        if weight >= 2:
            annals = [a for a in self.data["annals"] if a[0] <= k] if rewind else list(self.data["annals"])
            annals.append([k, date, weight, text[:200], ""])
            annals.sort(key=lambda a: a[0])
            if len(annals) > MAX_ANNALS:
                half = len(annals) // 2
                annals = annals[:half:2] + annals[half:]
            self.data["annals"] = annals
        self.save()

    @staticmethod
    def notes_upto(reign: dict[str, Any], date: str, after: int = 0) -> list[list[Any]]:
        k = _key(date) or 99_999_999
        return [n for n in reign["notes"] if after < n[0] <= k]

    @staticmethod
    def chapters_upto(reign: dict[str, Any], date: str) -> list[dict[str, Any]]:
        k = _key(date) or 99_999_999
        return [c for c in reign["chapters"] if c["upto"] <= k]

    @staticmethod
    def line(n: list[Any]) -> str:
        return f"{n[1]}: {n[3]}" + (f" [written by {n[4]}]" if n[4] else "")

    @staticmethod
    def pick(notes: list[list[Any]], limit: int) -> list[list[Any]]:
        """The notes that fit in `limit` characters, in order: every weighty
        note stays, the light ones are sampled evenly."""
        size = [len(Book.line(n)) + 1 for n in notes]
        if sum(size) <= limit:
            return list(notes)
        heavy = sum(sz for n, sz in zip(notes, size) if n[2] >= 2)
        light = [i for i, n in enumerate(notes) if n[2] < 2]
        room = max(0, limit - heavy)
        avg = (sum(size[i] for i in light) / len(light)) if light else 1
        keep_n = min(len(light), int(room / avg)) if avg else 0
        step = len(light) / keep_n if keep_n else 0
        keep = {light[int(j * step)] for j in range(keep_n)} if keep_n else set()
        out = [n for i, n in enumerate(notes) if n[2] >= 2 or i in keep]
        while out and sum(len(Book.line(n)) + 1 for n in out) > limit:
            out = out[::2] if len(out) > 40 else out[1:]      # even the weighty ones, evenly
        return out

    @staticmethod
    def material(notes: list[list[Any]], limit: int) -> str:
        """The notes as lines for the prompt, thinned to `limit` characters."""
        return "\n".join(Book.line(n) for n in Book.pick(notes, limit))

    # ------------------------------------------------------ the year's accounts
    def year_line(self, date: str, ruler: str, text: str) -> bool:
        """The realm's accounts, once a game year (a save from earlier rewinds them)."""
        k = _key(date)
        if not k:
            return False
        years = [y for y in self.data["years"] if y[0] <= k]
        if years and years[-1][0] // 10000 == k // 10000:
            if len(years) != len(self.data["years"]):
                self.data["years"] = years
                self.save()
            return False
        years.append([k, date, ruler, text])
        self.data["years"] = years
        self.save()
        return True

    def years_between(self, start: int, end: int) -> list[list[Any]]:
        return [y for y in self.data["years"] if start <= y[0] <= (end or 99_999_999)]

    @staticmethod
    def ledger(years: list[list[Any]], max_lines: int) -> str:
        """The accounts as lines, thinned evenly (the first and the last always kept)."""
        if len(years) > max_lines > 1:
            step = (len(years) - 1) / (max_lines - 1)
            years = [years[round(i * step)] for i in range(max_lines)]
        return "\n".join(f"{y[1]} ({y[2]} reigning): {y[3]}" for y in years)

    # ------------------------------------------------------------ centuries
    def start(self, fallback: str) -> str:
        """The date the campaign's book begins (set once)."""
        if not self.data.get("start"):
            self.data["start"] = fallback
            self.save()
        return self.data["start"]

    def centuries_upto(self, date: str) -> list[dict[str, Any]]:
        k = _key(date) or 99_999_999
        kept = [c for c in self.data["centuries"] if _key(c["to"]) <= k]
        if len(kept) != len(self.data["centuries"]):
            self.data["centuries"] = kept          # a save from before that century ended
            self.save()
        return kept

    def offer_century(self, start: str, end: str) -> dict[str, Any]:
        c = {"from": start, "to": end, "text": "", "by": ""}
        self.data["centuries"].append(c)
        self.save()
        return c

    def annals_between(self, start: str, end: str) -> list[list[Any]]:
        a, b = _key(start), _key(end)
        return [n for n in self.data["annals"] if a <= n[0] <= b]

    def century_written(self, century: dict[str, Any], text: str, by: str) -> None:
        """The history is written: the annals of those years have done their work."""
        century["text"], century["by"] = text, by
        end = _key(century["to"])
        self.data["annals"] = [n for n in self.data["annals"] if n[0] > end]
        self.data["years"] = [y for y in self.data["years"] if y[0] > end]
        self.save()

    def forget_material(self, reign: dict[str, Any]) -> None:
        """The Life is written and the ruler dead: the notes have done their work."""
        reign["notes"], reign["chapters"] = [], []
        self.save()
