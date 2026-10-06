"""The game's own log files.

In debug mode (which the mod needs, to load the AI's text) every console
command makes EU5 re-check the whole script and write the result to
logs/debug.log: some 4,000 lines for each command, about 15 MB a minute, over
a gigabyte in a long evening. The bridge polls every three seconds, so the
file grows for as long as the game runs.

Court Brain therefore has the game empty its logs now and then, with the
game's own console command Log.ClearAll (run by the bridge; see
votc_request_logs). The file is emptied by the game itself, which keeps
writing to it normally afterwards.

The one thing in those logs Court Brain uses is error.log: at start-up it tells
the player about real errors of the mod in the last session. So before every
clearing, those errors are copied to a file of Court Brain's own, and read
from there as well.
"""

from __future__ import annotations

from pathlib import Path

# The engine's static analysis cannot see variables that are only read by the
# interface or the localisation (which is how the bridge reads them), so it
# warns about every one of them. Those are noise.
HARMLESS = ("was not used by the script", "is set but is never used", "is used but is never set")
KEPT_NAME = "mod_errors_kept.log"


def _real_errors(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        low = line.lower()
        if "votc" in low and not any(h in low for h in HARMLESS):
            out.append(line.strip())
    return out


def _key(line: str) -> str:
    return line.split("]:", 1)[-1].strip()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def mod_errors(logs_dir: Path, kept: Path | None = None) -> list[str]:
    """What the game said about this mod: kept from before a clearing, and in error.log now."""
    out, seen = [], set()
    texts = [_read(kept)] if kept is not None else []
    texts.append(_read(logs_dir / "error.log"))
    for text in texts:
        for line in _real_errors(text):
            if _key(line) not in seen:
                seen.add(_key(line))
                out.append(line)
    return out


def keep_errors(logs_dir: Path, kept: Path) -> int:
    """Copy the mod's real errors out of the game's error logs before they are cleared."""
    have = {_key(line) for line in _real_errors(_read(kept))}
    new = []
    for path in sorted(logs_dir.glob("error*.log")):
        for line in _real_errors(_read(path)):
            if _key(line) not in have:
                have.add(_key(line))
                new.append(line)
    if new:
        try:
            kept.parent.mkdir(parents=True, exist_ok=True)
            with open(kept, "a", encoding="utf-8") as fh:
                fh.write("\n".join(new) + "\n")
        except OSError:
            return 0
    return len(new)


def debug_log_mb(logs_dir: Path) -> float:
    try:
        return (logs_dir / "debug.log").stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0
