"""In-game dates, in the forms the game writes them.

The bridge prints GetDateString ("08:00, 1 April, 1337" in English), saves
write "1337.4.1", and the middleware's own files keep whatever it was given.
Everything that compares dates - the memory timeline above all - goes
through `key()`, which turns any of them into a sortable number.
"""

from __future__ import annotations

import re

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # Italian, in case the game ever runs in it
    "gen": 1, "mag": 5, "giu": 6, "lug": 7, "ago": 8, "set": 9, "ott": 10, "dic": 12,
}

_DOTTED = re.compile(r"(-?\d{1,4})\.(\d{1,2})\.(\d{1,2})")
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")


def parse(text: str) -> tuple[int, int, int] | None:
    """(year, month, day), or None when there is no date in the text."""
    if not text:
        return None
    m = _DOTTED.search(text)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    low = _CLOCK.sub(" ", text.lower())          # "08:00" is the hour, not the day
    year = re.search(r"\b(\d{3,4})\b", low)
    if not year:
        return None
    month = 1
    for word in re.findall(r"[a-z]+", low):
        if word[:3] in _MONTHS:
            month = _MONTHS[word[:3]]
            break
    rest = low[:year.start()] + " " + low[year.end():]
    day = re.search(r"\b(\d{1,2})\b", rest)
    return int(year.group(1)), month, int(day.group(1)) if day else 1


def key(text: str) -> int:
    """A number that sorts like the date; 0 when there is none."""
    p = parse(text)
    if p is None:
        return 0
    y, m, d = p
    return y * 10000 + m * 100 + d


def days_between(old: str, new: str) -> int:
    """Rough distance in days; a large number when either end is unknown."""
    a, b = parse(old), parse(new)
    if a is None or b is None:
        return 10_000
    return (b[0] - a[0]) * 365 + (b[1] - a[1]) * 30 + (b[2] - a[2])


def dotted(text: str) -> str:
    """"08:00, 1 April, 1337" -> "1337.4.1", the way saves write it."""
    p = parse(text)
    return f"{p[0]}.{p[1]}.{p[2]}" if p else ""
