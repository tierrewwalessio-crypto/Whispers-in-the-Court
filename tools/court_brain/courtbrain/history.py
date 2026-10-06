"""Listening to the game.

The mod speaks by running console commands whose name, votc_say, does not
exist. The console rejects them, but first writes every command it receives
to Documents/Paradox Interactive/Europa Universalis V/console_history.txt.
Court Brain follows that file.

This was established by experiment in the player's own game, after the
script-side logging effects (debug_log, error_log) turned out to be compiled
out of the release build.

The file is only ever appended to by the game, so following it is a matter of
remembering the offset. If it shrinks (the game may trim its history) we
start again from the new end rather than replaying old lines.
"""

from __future__ import annotations

import os
from pathlib import Path

NEWLINE = b"\n"
STRIP = "\r﻿ "

# The game marks rich text with the control character 0x15. A tooltip link
# comes out as  0x15 "TOOLTIP:DYNASTY,291 " 0x15 "L" NEWLINE "name" 0x15 "!" 0x15 "!"
# and a formatting block opens with 0x15 "e" NEWLINE. Those newlines belong to
# one command, they do not end it, so they are removed before splitting.
_EMBEDDED = (
    (b"\x15L\r\n", b"\x15L"),
    (b"\x15e\r\n", b"\x15e"),
    (b"\x15L\n", b"\x15L"),
    (b"\x15e\n", b"\x15e"),
)


class HistoryTail:
    def __init__(self, path: Path, *, marker: str = "votc_say ") -> None:
        self.path = Path(path)
        self.marker = marker
        self._offset: int | None = None
        self._partial = b""

    def _size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return -1

    def skip_to_end(self) -> None:
        self._offset = max(self._size(), 0)
        self._partial = b""

    def poll(self) -> list[str]:
        size = self._size()
        if size < 0:
            return []
        if self._offset is None or size < self._offset:
            # First look, or the file was trimmed: only what comes next counts.
            self._offset = size
            self._partial = b""
            return []
        if size == self._offset:
            return []
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read(size - self._offset)
        except OSError:
            return []
        self._offset = size
        data = self._partial + chunk
        for old, new in _EMBEDDED:
            data = data.replace(old, new)
        lines = data.split(NEWLINE)
        self._partial = lines.pop()
        out: list[str] = []
        for raw in lines:
            line = raw.decode("utf-8", errors="replace").strip(STRIP)
            if line.startswith(self.marker):
                out.append(line)
        return out
