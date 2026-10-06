"""Talking back to the game: the mailbox.

There is no keyboard involved any more. The mod's bridge widget runs
run/votc_poll.txt every three seconds. To deliver something, Court Brain:

  1. rewrites the mod's dynamic localisation file (the AI's prose),
  2. writes run/votc_in.txt (consequences + which event to show),
  3. writes run/votc_poll.txt announcing mail number N.

The next poll raises a variable; the bridge reloads localisation if the mail
carries text and runs votc_in.txt, whose last line asks the bridge to send an
ACK back through console_history.txt. Only after that ACK is the next mail
written, so a mail can never be overwritten before the game has read it.

Every piece of this was verified in the player's game with a probe mod:
`run` from the interface, the variable watcher, `reload switchlanguage`
picking up a file changed while the game was running.
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

BOM = b"\xef\xbb\xbf"


@dataclass
class Mail:
    script: list[str]
    loc: dict[str, str] | None = None
    label: str = ""
    save: bool = False
    queued_at: float = field(default_factory=time.time)
    token: Any = None          # what stamp() said this mail carries
    tries: int = 0             # a text mail that was never acknowledged is sent again
    stamps: list = field(default_factory=list)   # the numbers its text was written under
    not_before: float = 0.0    # sent again only after a pause
    needs_text: bool = False   # shows a scene: pointless (and wrong) without its text
    probe_of: Any = None       # a question to the game: "which text have you loaded?" (no reload)
    probes: int = 0            # how many times the game was asked about this text
    sent_first: float = 0.0    # when this text was first sent, to log how long the game took


class Mailbox:
    #: seconds to wait for an ACK before assuming the mail was lost and
    #: sending the next one anyway
    ACK_TIMEOUT = 60.0
    TEXT_ACK_TIMEOUT = 12.0
    SLOW_MARGIN = 15.0          # a mail sent while the bridge polls slowly waits up to 12 s more
    _in_flight_slow = True

    def __init__(self, *, run_dir: Path, dynamic_loc: Path, language_key: str = "l_english") -> None:
        self.run_dir = Path(run_dir)
        self.dynamic_loc = Path(dynamic_loc)
        self.language_key = language_key
        self.seq = 0
        self._queue: "collections.deque[Mail]" = collections.deque()
        self._in_flight: Mail | None = None
        self._sent_at = 0.0
        self._lock = threading.Lock()
        self.loc_values: dict[str, str] = {}
        # The mod's own fallbacks for every dynamic key: written with the AI's
        # text, so that a key no scene has filled yet is never missing (the game
        # then warns about it and an event would show its raw key).
        self.fallbacks: dict[str, str] = self._read_fallbacks()
        self.delivered = 0
        # Lines put at the top of every mail at the moment it is written
        # (the memory head), and a callback told when that mail was read.
        self.stamp: Callable[[], tuple[list[str], Any]] | None = None
        # Lines that number the scene a text belongs to (see CourtBrain._scene_lines).
        self.scene_lines: Callable[[dict[str, str]], list[str]] | None = None
        self.on_delivered: Callable[[Any], None] | None = None
        self.on_dropped: Callable[[str], None] | None = None
        self.on_note: Callable[[str], None] | None = None
        # None: not yet known whether the game reports the text it loaded; True: it does.
        self.stamp_check: bool | None = None
        # The bridge polls every 3 s while var:votc_poll_fast is up, every 12 s otherwise.
        # Every mail says which: fast while more is to come (the queue, or keep_fast() - the
        # court at work, a scene open), slow when it was the last word. Until the game has
        # read a mail that raised it, a mail may wait up to a slow poll: its acknowledgement
        # is waited for that much longer, so nothing is ever sent twice or dropped for it.
        self.keep_fast: Callable[[], bool] | None = None
        self.game_slow = True
        self._fast_sent: dict[int, bool] = {}

    # ------------------------------------------------------------------
    def reset_files(self) -> None:
        """An empty poll file, so the bridge's poll never hits a missing file."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._write(self.run_dir / "votc_poll.txt", "votc_bridge_watchdog = yes\n")
        self._write(self.run_dir / "votc_in.txt", "# Court Brain: mail now travels in votc_poll.txt.\n")

    def sync_seq(self, game_seq: int) -> None:
        """The game reports the last mail it read; never go backwards."""
        with self._lock:
            if game_seq > self.seq:
                self.seq = game_seq

    def send(self, script: list[str], *, loc: dict[str, str] | None = None, label: str = "",
             save: bool = False, mark: bool = True) -> None:
        """Queue a mail. save=True also has the game write save games/votc_world.eu5."""
        with self._lock:
            if loc and script:
                # Text first, on its own: the game must have reloaded the new
                # localisation BEFORE an event that uses it is created, or the
                # event shows the fallback text. The ACK between the two mails
                # guarantees the reload has finished.
                marks = self.scene_lines(loc) if mark and self.scene_lines is not None else []
                self._queue.append(Mail(script=[], loc=dict(loc), label=label + ":text", save=save))
                self._queue.append(Mail(script=marks + list(script), label=label, needs_text=True))
            else:
                self._queue.append(Mail(script=list(script), loc=dict(loc) if loc else None,
                                        label=label, save=save))
        self.pump()

    # A text is waited for this many times (a question every PROBE_EVERY seconds),
    # sent again once halfway; then the scene goes without it, or not at all.
    PROBES = 8
    PROBE_EVERY = 1.5
    # set by Court Brain from the game's acknowledgements: True when EU5 runs with -debug_mode
    debug_mode: bool | None = None
    _NEVER_READ_DEBUG = (". WARNING: EU5 runs in debug mode but has not loaded a text Court Brain wrote: its "
                         "file watcher has stopped. Save, close EU5 and start it again with the Start EU5 button of "
                         "Court Brain.")
    _NEVER_READ = (". WARNING: EU5 has never read a text Court Brain wrote. Start EU5 with -debug_mode (the "
                   "Start EU5 button of Court Brain does), and in the EU5 launcher enable ONLY "
                   "\"Whispers in the Court (USE THIS)\".")

    def acknowledge(self, game_seq: int, loaded: str | None = None) -> None:
        """The game ran mail game_seq, and reports the stamp of the text it has loaded.
        A text mail - or a question about it - is done only when the game really has that
        text: a reload of the whole localisation can take seconds on a slower PC, so
        until then the game is asked again, briefly and without reloading, and the scene
        waits instead of showing without its words."""
        done: Mail | None = None
        note = ""
        with self._lock:
            if game_seq in self._fast_sent:
                # the game has read that mail: it now polls as the mail told it
                self.game_slow = not self._fast_sent[game_seq]
            for old in [s for s in self._fast_sent if s <= game_seq]:
                del self._fast_sent[old]
            mail = self._in_flight
            stamp = (loaded or "").strip()
            text_mail = mail.probe_of if (mail is not None and mail.probe_of is not None) else mail
            waiting = mail is not None and game_seq >= self.seq and text_mail is not None and bool(text_mail.loc)
            if waiting and stamp.isdigit() and int(stamp) in text_mail.stamps:
                self.stamp_check = True               # the game reports its text: the check is trusted
                took = time.time() - (text_mail.sent_first or time.time())
                if took > 4:
                    note = f"the game took {took:.0f} s to load the new text"
                done, self._in_flight = mail, None
                self.delivered += 1
            elif waiting and stamp.isdigit() and self.stamp_check is not False:
                self._in_flight = None
                text_mail.probes += 1
                if text_mail.probes == self.PROBES // 2 and text_mail.tries < 1:
                    # halfway and still the old text: perhaps the reload never ran - once more
                    text_mail.tries += 1
                    text_mail.not_before = time.time() + self.PROBE_EVERY
                    self._queue.appendleft(text_mail)
                    note = f"the game still has the old text ({stamp}) after {text_mail.probes} checks: reloading once more"
                elif text_mail.probes < self.PROBES:
                    self._queue.appendleft(Mail(script=[], label=text_mail.label + ":check", probe_of=text_mail,
                                                not_before=time.time() + self.PROBE_EVERY))
                elif self.stamp_check is None:
                    # Never once seen working: perhaps this game does not refresh the stamp on a
                    # reload. The check is switched off and scenes go as they always did.
                    self.stamp_check = False
                    done = mail
                    self.delivered += 1
                    note = "the game never reported the new text: the check is switched off" + (
                        (self._NEVER_READ_DEBUG if self.debug_mode else self._NEVER_READ) if stamp == "0" else "")
                else:
                    if self._queue and self._queue[0].needs_text:
                        dropped = self._queue.popleft()
                        if self.on_dropped is not None:
                            self.on_dropped(dropped.label)
                    note = "the game never loaded the new text: the scene is not shown" + (
                        (self._NEVER_READ_DEBUG if self.debug_mode else self._NEVER_READ) if stamp == "0" else "")
            elif mail is not None and game_seq >= self.seq:
                done = self._in_flight
                self._in_flight = None
                self.delivered += 1
            if game_seq > self.seq:
                self.seq = game_seq
        if note and self.on_note is not None:
            self.on_note(note)
        if done is not None and self.on_delivered is not None:
            self.on_delivered(done.token)
        self.pump()

    def in_flight_token(self) -> Any:
        with self._lock:
            return self._in_flight.token if self._in_flight is not None else None

    def scene_in_flight(self) -> bool:
        """A scene's words or its event are still on the way to the game."""
        with self._lock:
            mails = list(self._queue) + ([self._in_flight] if self._in_flight else [])
        return any(m.needs_text or m.probe_of is not None or (m.loc and m.label.endswith(":text")) for m in mails)

    def pending(self) -> int:
        with self._lock:
            return len(self._queue) + (1 if self._in_flight else 0)

    # ------------------------------------------------------------------
    def pump(self) -> None:
        with self._lock:
            if self._in_flight is not None:
                # A text whose reload never answered is sent again soon: the next request
                # raises the other flag, so the bridge fires even if the first stuck.
                wait = self.TEXT_ACK_TIMEOUT if self._in_flight.loc else self.ACK_TIMEOUT
                if self._in_flight_slow:
                    wait += self.SLOW_MARGIN          # it may have waited for a slow poll
                if time.time() - self._sent_at < wait:
                    return
                lost, self._in_flight = self._in_flight, None
                if lost.loc and lost.tries < 2:
                    # The game never confirmed it reloaded the text (a console busy at
                    # that moment): send the text again rather than show a scene without it.
                    lost.tries += 1
                    self._queue.appendleft(lost)
                elif lost.loc and self._queue and self._queue[0].needs_text:
                    # Still no text: the scene is not shown at all - never an empty event
                    # whose consequences nobody chose.
                    dropped = self._queue.popleft()
                    if self.on_dropped is not None:
                        self.on_dropped(dropped.label)
            if not self._queue or self._queue[0].not_before > time.time():
                return
            mail = self._queue.popleft()
            self.seq += 1
            seq = self.seq
            self._in_flight = mail
            self._sent_at = time.time()
            self._in_flight_slow = self.game_slow
            more = bool(self._queue)
        # Keep the bridge polling fast while more is to come; the last word lets it rest.
        # A text or a question about it always expects an answer back.
        try:
            busy = self.keep_fast() if self.keep_fast is not None else True
        except Exception:  # noqa: BLE001 - never let this stop a mail: poll fast when unsure
            busy = True
        fast = more or busy or bool(mail.loc) or mail.probe_of is not None
        with self._lock:
            self._fast_sent[seq] = fast

        if mail.loc:
            self.loc_values.update(mail.loc)
            mail.stamps.append(seq)
            mail.sent_first = mail.sent_first or time.time()
            self._write_loc(seq)
        head: list[str] = []
        if self.stamp is not None:
            head, mail.token = self.stamp()
        if mail.loc:
            # The acknowledgement comes from votc_loc_done, after the reload.
            tail = ["votc_request_reload = yes"]
        elif mail.save:
            tail = ["votc_request_save = yes", "votc_in_ack = yes"]
        else:
            tail = ["votc_in_ack = yes"]
        pace = "votc_poll_fast_on = yes" if fast else "votc_poll_fast_off = yes"
        body = [f"votc_mail_open = {{ seq = {seq} }}", *head, *mail.script, pace, *tail]
        text = (f"if = {{\n\tlimit = {{ votc_mail_is_new = {{ seq = {seq} }} }}\n"
                + "".join(f"\t{line}\n" for line in body)
                + "}\nelse = {\n\tvotc_bridge_watchdog = yes\n}\n")
        # One file, one console command: the poll runs it, the seq runs it once.
        self._write(self.run_dir / "votc_poll.txt", text)

    # ------------------------------------------------------------------
    @staticmethod
    def _read_fallbacks() -> dict[str, str]:
        import re
        try:
            from .bundle import mod_source
            path = mod_source() / "main_menu" / "localization" / "english" / "votc_dynamic_l_english.yml"
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, ImportError):
            return {}
        return {m.group(1): m.group(2) for m in re.finditer(r'^ ([\w.]+):0 "(.*)"$', text, re.M)}

    def _write_loc(self, stamp: int = 0) -> None:
        lines = [f"{self.language_key}:", "", " # Written by Court Brain before every scene.",
                 # read back by the bridge's acknowledgement: proof the game loaded THIS text
                 f' votc_loc_stamp:0 "{stamp}"']
        for key, value in self.fallbacks.items():
            if key not in self.loc_values and key != "votc_loc_stamp":
                lines.append(f' {key}:0 "{value}"')
        for key, value in self.loc_values.items():
            lines.append(f' {key}:0 "{escape_loc(value)}"')
        self._write(self.dynamic_loc, "\n".join(lines) + "\n")

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(BOM + text.encode("utf-8"))
        tmp.replace(path)


# "In short" on top of an event: the gist travels inside the body between these marks, so that only
# here - after the prose is made safe - it is set apart with the game's own formatting codes.
GIST_MARK = "\u2063\u2063"                   # invisible separators, never in any prose


def with_gist(gist: object, body: str) -> str:
    """The event's text with its gist on top, for a ruler who will not read it all. The gist
    begins with the words for "In short" in the event's language and a colon (prompts.GIST)."""
    text = " ".join(str(gist or "").split())[:240]
    return f"{GIST_MARK}{text}{GIST_MARK}{body}" if text and body else body


def escape_loc(value: str) -> str:
    """Make prose safe inside a Paradox localisation value.

    Double quotes would end the value; raw newlines break the one-key-per-
    line format; [ ] would be read as data functions, $ as a nested key and
    # as a formatting code.
    """
    if value.startswith(GIST_MARK) and value.count(GIST_MARK) >= 2:
        _, gist, body = value.split(GIST_MARK, 2)
        label, sep, rest = gist.partition(":")
        if not sep or len(label) > 30:
            label, rest = "", gist
        head = (f"#bold {_escape(label)}:#! " if label else "") + f"#italic {_escape(rest)}#!"
        return f"{head}\\n\\n{_escape(body)}"     # a blank line, as the localisation file writes it
    return _escape(value)


def _escape(value: str) -> str:
    out = value.replace("\\", "/").replace('"', "”")
    out = out.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    out = out.replace("[", "(").replace("]", ")").replace("$", "").replace("#", "n.")
    return out.strip()
