"""Which save file belongs to which campaign, and to which point of its memory.

Every save the game writes while the mod runs carries two global variables,
votc_campaign and votc_memhead (see memory.py). They sit near the top of the
file, so reading the first few megabytes is enough to know, for any save:
the in-game date, the game's own playthrough id and name, the campaign and
the exact point of the court's memory it belongs to.

Saves written in debug mode (which the mod needs) are plain text. Saves from
normal play are binary: for those only the playthrough id and the date in
the file name can be read, and they are listed without a memory point.

The index is cached by file name, size and modification time, so only new
or changed files are ever read.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, asdict
from pathlib import Path

HEAD_BYTES = 6 * 1024 * 1024

_UUID = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_META_DATE = re.compile(rb"metadata=\{\s*date=(\d+\.\d+\.\d+)")
_PT_ID = re.compile(rb'playthrough_id="([^"]+)"')
_PT_NAME = re.compile(rb'playthrough_name="([^"]*)"')
_NAME_DATE = re.compile(r"_(\d{3,4})_(\d{1,2})_(\d{1,2})_")


def _global_value(data: bytes, name: str) -> int:
    """A global variable's value; values are stored as fixed point x100000."""
    m = re.search(rb"flag=" + name.encode() + rb"\s+data=\{\s*type=value\s+identity=(-?\d+)", data)
    if not m:
        return 0
    return int(round(int(m.group(1)) / 100000))


@dataclass
class SaveInfo:
    file: str
    size: int
    mtime: float
    text: bool = False
    date: str = ""
    playthrough: str = ""
    playthrough_name: str = ""
    campaign: int = 0
    head: int = 0


def read_info(path: Path) -> SaveInfo:
    st = path.stat()
    info = SaveInfo(file=path.name, size=st.st_size, mtime=st.st_mtime)
    with open(path, "rb") as fh:
        data = fh.read(HEAD_BYTES)
    info.text = b"metadata={" in data[:200]
    if info.text:
        m = _META_DATE.search(data)
        info.date = m.group(1).decode() if m else ""
        m = _PT_ID.search(data)
        info.playthrough = m.group(1).decode() if m else ""
        m = _PT_NAME.search(data)
        info.playthrough_name = m.group(1).decode("utf-8", "replace") if m else ""
        info.campaign = _global_value(data, "votc_campaign")
        info.head = _global_value(data, "votc_memhead")
    else:
        m = _UUID.search(data[:4096])
        info.playthrough = m.group(0).decode() if m else ""
    if not info.date:
        m = _NAME_DATE.search(path.name)
        if m:
            info.date = f"{int(m.group(1))}.{int(m.group(2))}.{int(m.group(3))}"
    return info


class SavesIndex:
    def __init__(self, save_dir: Path, cache_file: Path) -> None:
        self.save_dir = Path(save_dir)
        self.cache_file = Path(cache_file)
        self.items: dict[str, SaveInfo] = {}
        self._lock = threading.Lock()
        try:
            raw = json.loads(self.cache_file.read_text(encoding="utf-8"))
            self.items = {k: SaveInfo(**v) for k, v in raw.items()}
        except (OSError, ValueError, TypeError):
            self.items = {}

    def refresh(self) -> list[SaveInfo]:
        """Read what is new; return the saves, newest first."""
        with self._lock:
            return self._refresh()

    def _refresh(self) -> list[SaveInfo]:
        seen: dict[str, SaveInfo] = {}
        changed = False
        for path in self.save_dir.glob("*.eu5"):
            try:
                st = path.stat()
            except OSError:
                continue
            old = self.items.get(path.name)
            if old and old.size == st.st_size and abs(old.mtime - st.st_mtime) < 1:
                seen[path.name] = old
                continue
            try:
                seen[path.name] = read_info(path)
                changed = True
            except OSError:
                continue
        if changed or seen.keys() != self.items.keys():
            self.items = seen
            try:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                self.cache_file.write_text(json.dumps({k: asdict(v) for k, v in seen.items()}, indent=1),
                                           encoding="utf-8")
            except OSError:
                pass
        return sorted(self.items.values(), key=lambda s: s.mtime, reverse=True)

    def find(self, *, campaign: int = 0, head: int = -1, date: str = "") -> SaveInfo | None:
        """The newest save matching what the game just reported."""
        for s in self.refresh():
            if campaign and s.campaign != campaign:
                continue
            if head >= 0 and s.head != head:
                continue
            if date and s.date != date:
                continue
            return s
        return None
