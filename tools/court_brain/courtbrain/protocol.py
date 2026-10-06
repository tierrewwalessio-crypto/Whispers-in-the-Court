"""Parsing the mod's outgoing records.

Every line the mod emits is a console command the game records in
console_history.txt:

    votc_say HEAD|URB|Urbino|County of Urbino|...|date=1 April, 1337

(votc_say is deliberately not a real command; see the mod's
gui/votc_bridge.gui.) Parsing is: strip "votc_say ", split on "|", and treat
"k=v" fragments as fields. Anything that still contains a "[" is a data
function the game could not resolve, which is worth knowing about rather
than passing on to the model as if it were a value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MARKER = "votc_say "

# A data function the game did not resolve, e.g. "[ROOT.GetCountry.GetTag]".
_UNRESOLVED = re.compile(r"\[[A-Za-z][^\]]*\]")


@dataclass
class Record:
    kind: str                       # HEAD, STATE, REQ, EVT, ACK, TICK, ...
    subkind: str = ""               # for REQ/EVT/ACK: talk_open, new_ruler, ...
    positional: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    raw: str = ""
    unresolved: bool = False

    def num(self, key: str, default: float = 0.0) -> float:
        raw = self.fields.get(key)
        if raw is None:
            return default
        try:
            return float(raw.replace(",", "").replace("%", "").strip())
        except ValueError:
            return default

    def i(self, key: str, default: int = 0) -> int:
        return int(self.num(key, default))

    def at(self, index: int, default: str = "") -> str:
        if 0 <= index < len(self.positional):
            return self.positional[index]
        return default


# Records whose second element names a sub-type rather than being data.
_SUBKINDED = {"SIG", "END"}


def parse_line(line: str) -> Record | None:
    idx = line.find(MARKER)
    if idx < 0:
        return None
    body = line[idx + len(MARKER):].rstrip("\r\n")
    parts = [p.strip() for p in body.split("|")]
    if not parts or not parts[0]:
        return None

    kind = parts[0].upper()
    rest = parts[1:]
    subkind = ""
    if kind in _SUBKINDED and rest:
        subkind = rest[0]
        rest = rest[1:]

    positional: list[str] = []
    fields: dict[str, str] = {}
    for frag in rest:
        # "gold=1234" is a field; "France" is positional. An "=" inside a
        # name (rare, but possible in a dynasty name) only splits once.
        if "=" in frag and not frag.startswith("="):
            key, _, value = frag.partition("=")
            key = key.strip()
            if key and " " not in key:
                fields[key] = value.strip()
                continue
        positional.append(frag)

    rec = Record(
        kind=kind,
        subkind=subkind,
        positional=positional,
        fields=fields,
        raw=body,
    )
    rec.unresolved = bool(_UNRESOLVED.search(body))
    return scrub_record(rec)


def parse_lines(lines) -> list[Record]:
    out: list[Record] = []
    for line in lines:
        rec = parse_line(line)
        if rec is not None:
            out.append(rec)
    return out


# Rich text the game leaves in resolved strings, marked with the control
# character 0x15. Two tooltip shapes occur:
#   0x15 TOOLTIP:DYNASTY,291 0x15 L <shown text> 0x15 ! 0x15 !
#   0x15 TOOLTIP:GAME_CONCEPT,ruler <shown text> 0x15 ! 0x15 !
# Only the shown text is kept.
CTRL = ""
_TOOLTIP_LINK = re.compile(CTRL + r"TOOLTIP:[^" + CTRL + r"]*" + CTRL + r"L(.*?)" + CTRL + "!" + CTRL + "!", re.S)
_TOOLTIP_CONCEPT = re.compile(CTRL + r"TOOLTIP:[A-Z_]+,\S+ (.*?)" + CTRL + "!" + CTRL + "!", re.S)
_CONTROL = re.compile(CTRL + ".?")
_MARKUP = re.compile(r"#[A-Za-z_]+ ?|#!|@[A-Za-z_]+!")


def scrub(value: str) -> str:
    """Drop unresolved data functions and formatting codes from a value.

    Better a blank than "[SCOPE.sCharacter('votc_heir').GetName]" presented
    as somebody's name.
    """
    cleaned = _TOOLTIP_LINK.sub(lambda m: m.group(1), value)
    cleaned = _TOOLTIP_CONCEPT.sub(lambda m: m.group(1), cleaned)
    cleaned = _CONTROL.sub("", cleaned)
    cleaned = _UNRESOLVED.sub("", cleaned)
    cleaned = _MARKUP.sub("", cleaned)
    return cleaned.strip()


def scrub_record(rec: Record) -> Record:
    rec.positional = [scrub(v) for v in rec.positional]
    rec.fields = {k: scrub(v) for k, v in rec.fields.items()}
    return rec


def as_dict(rec: Record) -> dict[str, Any]:
    return {
        "kind": rec.kind,
        "subkind": rec.subkind,
        "positional": list(rec.positional),
        "fields": dict(rec.fields),
    }
