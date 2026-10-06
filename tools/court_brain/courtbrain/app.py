"""Court Brain: the loop that joins EU5, the side panel and the Player2 app.

    click in game ─► mod raises a signal ─► bridge widget writes "votc_say ..."
        lines into console_history.txt ─► Court Brain reads them, opens the
        side panel on the right person/court/place
    the player talks in the panel ─► Player2 answers ─► small consequences
        pile up, heavy ones become buttons
    the conversation closes ─► Court Brain mails the outcome ─► the bridge
        widget picks up the mail, runs it, and the event appears in game

Everything the player triggers arrives as a SIG ... END block; everything
that just happens in the world is noticed by diffing consecutive context
dumps (the heartbeat asks the game for one every so often).
"""

from __future__ import annotations

import collections
import json
import queue
import random
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from . import actions as A
from . import gamedate
from . import gamelogs
from . import measures as MS
from . import works as W
from . import trade as TR
from . import reactions as R
from .battle import BattleCommand
from .biography import BOOK_LIMIT, CENTURY, Book
from . import live as live_mod
from . import lore
from . import worldsave
from . import codex as codex_mod
from . import config as config_mod
from . import prompts
from .config import Config
from .drawer import Header
from .history import HistoryTail
from .mailbox import Mailbox, with_gist
from .memory import CampaignStore, Memory
from .names import NameRegistry, PlaceNames
from .savesindex import SavesIndex, read_info
from .player2 import Player2Client, Player2Error, make_client
from .protocol import Record, parse_lines
from .world import ESTATES, Person, Snapshot, SnapshotBuilder, diff, own_crown

# The pace of NEW, unprompted events, chosen by the player in the Court Brain
# window: (fewest in-game days between two, most). None: nothing new comes on
# its own. Consequences are never limited by it, and never delay it.
FREQUENCIES: dict[str, tuple[int, int] | None] = {
    "off": None,
    "yearly": (330, 400),
    "rare": (140, 200),
    "calm": (90, 140),
    "normal": (50, 90),
    "very": (30, 50),
}
# Paces of earlier versions, still in some players' settings.
FREQUENCY_ALIASES = {"frequent": "very"}

FREQUENCY_TEXT = {"off": "none", "yearly": "once a year", "rare": "rare", "calm": "calm", "normal": "normal",
                  "very": "frequent"}

# What the panel's header says for each kind of scene.
KIND_LABEL = {
    "talk": "Audience",
    "petition": "Requested audience",
    "envoy": "Envoy",
    "meeting": "Meeting of rulers",
    "visit": "State visit",
    "decree": "Decree",
    "council": "Crown Council",
    "estate": "Audience of the Estates",
    "progress": "Royal Progress",
    "reply": "Answer in person",
    "biographer": "The Royal Biographer",
    "book": "The Book of the Ruler's Life",
    "life": "The Life of the Late Ruler",
    "century": "A Century of the Realm",
    "hub": "The Court",
    "advisor": "AI Advisor",
    "battle": "Battle",
}

# Scenes the ruler opens: they sent for the person, called the council, sent
# the envoy, chose to answer in person. When someone comes to the ruler, or
# the ruler is a guest or on the road, the others speak first.
# The agenda the mod raises with a reply when the ruler goes to the biographer,
# asks to read their Life so far, or the whole Life of the ruler just dead.
BIOGRAPHER_AGENDA = 88
BOOK_AGENDA = 89
LIFE_AGENDA = 90
CENTURY_AGENDA = 91
_READING = {BOOK_AGENDA: "book", LIFE_AGENDA: "life", CENTURY_AGENDA: "century"}

PLAYER_SPEAKS_FIRST = ("talk", "council", "estate", "envoy", "reply", "biographer")


def _waiting_note(mode: str, snap: Snapshot, estate: str) -> str:
    who = snap.target_person.name if snap.target_person and snap.target_person.name else ""
    if mode == "talk" and who:
        return f"{who} stands before you, waiting for you to speak."
    if mode == "council":
        return "The council is assembled. They wait for you to open the session."
    if mode == "estate":
        return "The spokesmen of the estate stand before you, waiting for you to speak."
    if mode == "envoy" and snap.target_country:
        return (f"Your envoy has been received at the court of {snap.target_country.long_name or snap.target_country.name}. "
                "What should they say in your name?")
    if mode == "reply":
        return "What do you wish to say?"
    if mode == "biographer" and estate:
        return f"{estate} looks up from the desk, pen still in hand, and waits for you to speak."
    return "They wait for you to speak."


# How consequences are announced in the panel. Never numbers: the game's own
# tooltips on the outcome event show those.
ACTION_NOTE = {
    "stability": ("The realm is reassured", "The realm grows uneasy"),
    "prestige": ("The Crown's name grows", "The Crown's name is diminished"),
    "legitimacy": ("The right to the throne grows firmer", "The right to the throne wavers"),
    "government_power": ("The Crown tightens the reins", "The Crown loses its grip"),
    "army_tradition": ("The army takes pride in it", "The army is disheartened"),
    "war_exhaustion": ("War-weariness grows", "War-weariness eases"),
    "devotion": ("Devotion grows", "Devotion cools"),
    "estate": ("An estate of the realm is pleased", "An estate of the realm feels slighted"),
    "all_estates": ("The whole realm takes comfort", "The whole realm is displeased"),
    "local_control": ("The Crown holds this place more firmly", "The grip on the town loosens"),
    "local_prosperity": ("The town prospers from it", "The town suffers from it"),
}

ACTION_NOTE_SINGLE = {
    "gold_gain": "Money comes into the treasury", "gold_loss": "Money leaves the treasury",
    "manpower_gain": "New recruits are found", "manpower_loss": "Recruits are lost",
    "edict_short": "An edict is issued", "edict_long": "A lasting edict is issued",
    "societal": "The realm's society slowly shifts", "character_modifier": "The person you spoke to is marked by it",
    "opinion": "Their court's judgement changes", "trust_gain": "Their trust grows",
    "trust_loss": "Their trust cracks", "favors": "Now they owe you a favour",
    "gift_gold": "A gift leaves for their court", "rival_declare": "You declare them rivals",
    "rival_drop": "You no longer count them as rivals", "progress_acclaimed": "The realm will remember the visit fondly",
    "progress_resented": "The realm will remember the visit with resentment",
}

CURRENCY_HINTS = {
    "devotion": ("catholic", "orthodox", "protestant", "reformed", "coptic", "miaphysite", "cattolic", "ortodoss"),
    "karma": ("buddhism", "mahayana", "vajrayana", "theravada", "buddh"),
    "horde_unity": ("tengri", "horde", "orda"),
    "republican_tradition": ("republic", "merchant", "repubblic"),
}


@dataclass
class Session:
    kind: str
    snap: Snapshot
    header: Header
    system: str = ""
    referee: str = ""                                               # the judge of consequences (its own prompt)
    cessions: list = field(default_factory=list)                    # land agreed to change hands
    lands: dict = field(default_factory=dict)                       # land of the other court named, and its worth
    weighty: bool = False                                           # something may have been decided (judged at the close)
    noted_turn: int = 0                                             # the turn the "noted" line was last shown
    refused: list = field(default_factory=list)                     # what the other side or the world would not allow
    standing: list = field(default_factory=list)                    # standing measures started or revoked
    details_shown: set = field(default_factory=set)                 # (name, detail) surfaced in this scene
    part_ready: bool = False                                        # the actors' preparation is done
    works: list = field(default_factory=list)                       # works of the realm the ruler ordered
    trade: list = field(default_factory=list)                       # goods sold or bought abroad, for years
    reactions: list = field(default_factory=list)                   # how the realm and the world took it
    balance: dict = field(default_factory=dict)                     # what weighs the consequences (see _balanced)
    messages: list[dict[str, str]] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    spent: int = 0
    offers: list[dict[str, Any]] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    diplomatic: bool = False
    # a far weaker foreign court: None, or whether its ruler is the rare one too proud to yield
    proud: bool | None = None
    chain: int = 0          # 0 a scene of the ruler's own; 1 one that a follow-up brought; 2 ...
    turns: int = 0
    closed: bool = False
    # heavy outcomes the ruler ordered; applied with the outcome's "Cosi sia"
    decisions: list[dict[str, Any]] = field(default_factory=list)
    # the consequences themselves (clean action dicts), queued at the close
    acts: list[dict[str, Any]] = field(default_factory=list)
    draft: dict[str, Any] = field(default_factory=dict)          # a decree waiting to be proclaimed
    arc_id: int = 0                                                 # the story a free answer belongs to
    cast: list[str] = field(default_factory=list)                   # who is in the room
    moods: dict[str, str] = field(default_factory=dict)             # someone's mood of the day
    opener: str = ""                                                # the scene, told with the ruler's first line
    aside: str = ""                                                 # what the model must know with the next line
    codex_later: str = ""                                           # laws and privileges, given when they come up
    narrated: bool = True                                           # the system prompt has the narration rules
    recalled: set = field(default_factory=set)                      # memories already called up in this scene
    # What has already been used in this scene, so nobody turns into a set of
    # repeated tics: gestures and their props, slips, titles, how speeches began.
    used_props: set = field(default_factory=set)
    gestured: dict = field(default_factory=dict)                    # speaker -> turn of their last gesture
    slips: list = field(default_factory=list)                       # speakers who already slipped
    addressed_turn: int = -9                                        # the turn a title was last used
    openings: list = field(default_factory=list)                    # the first words of recent speeches
    spoken: dict = field(default_factory=dict)                      # speaker -> their earlier speeches
    worn: list = field(default_factory=list)                        # phrases someone has already repeated
    introduced: set = field(default_factory=set)                    # speakers already shown with their role


class CourtBrain:
    def __init__(self, cfg: Config, log: Callable[[str], None] | None = None) -> None:
        self.cfg = cfg
        cfg.event_frequency = FREQUENCY_ALIASES.get(cfg.event_frequency, cfg.event_frequency)
        self.log = log or (lambda msg: print(msg, flush=True))
        self.client = make_client(cfg, log=self.log)
        self.client.room = self._load_room()
        self.client.on_room = self._save_room
        self.client.on_meter = self._ai_metered
        self._tally = self._new_tally()
        self.tail = HistoryTail(cfg.user_path / "console_history.txt")
        self.mail = Mailbox(run_dir=cfg.run_path, dynamic_loc=cfg.dynamic_loc_path,
                            language_key=f"l_{cfg.game_language}")
        self.codex = codex_mod.Codex()
        self.memory: Memory | None = None
        self.builder = SnapshotBuilder()
        self.snapshot: Snapshot | None = None
        self.previous: Snapshot | None = None
        self.session: Session | None = None
        self.last_scene: dict[str, str] = {}
        self.drawer = None                      # set by __main__ when the panel exists
        self._work: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._stop = threading.Event()
        self._sig_kind = ""
        self._last_pulse = 0.0
        self._pulse_gap = cfg.pulse_interval_s
        self._last_pulse_date = ""
        self._pulse_pending = False
        self.game_seen = False
        self.busy = False
        # The whole world, read from a save (see worldsave.py).
        self.world: worldsave.World | None = None
        self._world_file: tuple[str, float] = ("", 0.0)
        self._world_requested = 0.0
        self._world_loading = False
        self._world_check = 0.0
        self._world_read_at = 0.0        # when a save was last read (real time)
        self._world_asked_at = 0.0       # when the game was last asked to save
        self._peer_sent = 0.0            # the peers' usual treasury last told to the game
        self._autosave_last = 0.0        # when the game last autosaved (file time)
        self._autosave_told = False
        self._world_marks_seen: dict[str, Any] = {}   # the realm's facts at the last pulse
        self._world_changed_on = ""      # game date of a change the picture does not show yet
        self._world_changed_at = 0.0     # when that change was noticed (real time)
        self._logs_look = 0.0            # the game's debug.log: last looked at
        self._logs_asked_at = 0.0        # when the game was asked to empty its logs
        self._logs_before_mb = 0.0
        self._logs_failures = 0
        self._logs_off = False           # the game never did it: not asked again
        self._world_low_noted = 0.0
        self._last_stamp = 0.0
        # Campaigns and saves (see memory.py): which game this is, and the
        # point of the memory journal the game itself has last been told.
        self.store = CampaignStore(cfg.state_dir)
        self.names = NameRegistry(cfg.state_dir)
        self.place_names = PlaceNames(cfg.state_dir)
        # A great effort is allowed only when the realm is behind its ruler (actions.validate_action).
        A.GREAT_GATE = lambda: self._great_block(self.snapshot)
        self.saves = SavesIndex(cfg.user_path / "save games", cfg.state_dir / "saves_index.json")
        self._game_campaign = 0              # what the game last reported
        self.synced_head: int | None = None  # the head the game holds, as far as we know
        self._labelling: tuple[int, str, float] | None = None
        self._sent_heads: "collections.deque[tuple[int, int]]" = collections.deque(maxlen=40)
        # The live picture (live.py) from the last heartbeat that carried one.
        self.live_picture: dict = {}
        self._reply_arc = 0
        self._book: Book | None = None
        self._book_for: Any = None
        self._century_year = 0
        self._story_gap = 0
        self._new_failures = 0
        self._new_retry_at = 0.0         # after a new event could not be written: not before (real time)
        self._join_rebels: dict[str, Any] | None = None
        self._folding = False
        # The war as the reports have it (see _war_update): the state at the last
        # save read, and the dated facts of fighting learnt since.
        self._war_state: dict[str, Any] | None = None
        self.war_log: list[str] = []
        self._war_gap = 0
        self._war_save_asked = 0.0
        self._places = None
        self.live_date = ""
        # A battle the ruler commands in person (battle.py).
        self.battle = BattleCommand(self)
        self.mail.stamp = self._stamp
        self.mail.on_delivered = self._delivered
        # Scenes are numbered; their texts are kept so a loaded save shows them again.
        self._scene_loaded: dict[str, int] = {}
        self.mail.scene_lines = self._scene_lines
        self.mail.on_note = lambda text: self.log(f"  text of a scene: {text}")
        # The bridge polls fast while the court is at work, a scene is open or a battle is
        # commanded - an answer is on its way; otherwise it rests (see Mailbox.keep_fast).
        self.mail.keep_fast = lambda: bool(
            self.busy or not self._work.empty()
            or (self.session is not None and not self.session.closed)
            or getattr(getattr(self, "battle", None), "live", False))
        self.mail.on_dropped = self._scene_dropped
        self.game_debug: bool | None = None     # was EU5 started with -debug_mode? (from its acknowledgements)
        self._clock_asked = 0.0
        self._clock_scene = 0
        self._touched = 0.0                 # the ruler's last click or word in the panel
        self._clock_base: tuple[float, str] = (0.0, "")   # (when, game date) the watch starts from

    def _debug_mode(self, on: bool) -> None:
        """What every acknowledgement says: was EU5 started with -debug_mode? Without it
        the game never reloads text (verified), so every scene would come with no words,
        and its saves are binary (no picture of the world). The player is told at once,
        in game and here; nothing new is brought meanwhile."""
        if not on and self.mail.stamp_check is True:
            on = True                           # the game has shown it loads new text: trust that
        was, self.game_debug = self.game_debug, on
        self.mail.debug_mode = on
        if on or was is False:
            if on and was is False:
                self.log("EU5 now runs with -debug_mode: the court's texts reach the game again.")
                self.mail.stamp_check = None
            return
        from .bundle import DEBUG_HELP
        self.log("WARNING: EU5 was started WITHOUT -debug_mode, so it cannot load the texts Court Brain "
                 "writes (events and decrees would have no words). Close EU5 and start it with the "
                 "Start EU5 button of this window. " + DEBUG_HELP)
        self._ui("add_note", "EU5 was started without -debug_mode: events would have no text. Close EU5 and "
                             "start it with the Start EU5 button here (or add -debug_mode to its launch "
                             "options in Steam).", "bad")
        self.mail.send(["votc_show_need_debug = yes"], label="need_debug")

    # ------------------------------------------------------------ the AI's bill
    def _load_room(self) -> dict[str, int]:
        try:
            data = json.loads((self.cfg.state_dir / "ai_room.json").read_text(encoding="utf-8"))
            return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_room(self, room: dict[str, int]) -> None:
        try:
            (self.cfg.state_dir / "ai_room.json").write_text(json.dumps(room), encoding="utf-8")
        except OSError:
            pass

    USAGE_LIMIT = 2 * 1024 * 1024

    def _meter(self) -> None:
        meter = getattr(self.client, "meter", None)
        if meter is not None:
            meter()

    @staticmethod
    def _new_tally() -> dict[str, Any]:
        return {"since": time.time(), "calls": 0, "in": 0, "out": 0, "joules": 0.0, "known": True}

    def _ai_metered(self, records: list[dict[str, Any]]) -> None:
        """Every request's cost - model, tokens, and the joules Player2 took for it - one line in
        ai_usage.jsonl (Court Brain's folder); the scene's own requests are also added up."""
        tally = self._tally
        lines = []
        for r in records:
            if r["t"] >= tally["since"]:
                tally["calls"] += 1
                tally["in"] += r["in"]
                tally["out"] += r["out"]
                if r.get("joules") is None:
                    tally["known"] = False
                else:
                    tally["joules"] += r["joules"]
            lines.append(json.dumps({"t": round(r["t"]), "kind": r["kind"], "model": r["model"], "in": r["in"],
                                     "out": r["out"], "try": r["try"], "joules": r.get("joules"),
                                     **({"voice": True} if r.get("voice") else {})}))
        path = self.cfg.state_dir / "ai_usage.jsonl"
        try:
            if path.is_file() and path.stat().st_size > self.USAGE_LIMIT:
                path.replace(path.with_suffix(".old.jsonl"))
            with path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass

    def _log_scene_cost(self) -> None:
        """What the scene cost, said in the record once its last answer is billed (a few seconds later)."""
        def later() -> None:
            self._meter()
            t, self._tally = self._tally, self._new_tally()
            if t["calls"]:
                joules = f", {t['joules']:.0f} joules" if t["known"] else ""
                self.log(f"  AI for this scene: {t['calls']} requests, {t['in']:,} tokens read, "
                         f"{t['out']:,} written{joules}")
        threading.Timer(8.0, later).start()

    def _scene_dropped(self, label: str) -> None:
        self.log(f"The game did not load the text of a scene ({label}) after three tries: it is not shown.")
        mem, snap = self.memory, self.snapshot
        if label == "story" and mem is not None and mem.awaiting_arc and snap is not None:
            # The ruler never saw the question: the story must not wait for an answer
            # that cannot come (and hold back everything else meanwhile).
            mem.arc_step(snap.date, mem.awaiting_arc, text="The news did not reach the crown in time; "
                         "the matter waits a little longer.", after_days=7)

    # ================================================================ lifecycle
    def start(self) -> None:
        self.log("Court Brain started.")
        if self.cfg.game_dir:
            self.codex = codex_mod.load_or_build(self.cfg.game_dir, self.cfg.state_dir, self.cfg.game_language)
            self.log(f"Codex: {len(self.codex.laws)} laws, {len(self.codex.privileges)} privileges, "
                     f"{len(self.codex.tags)} countries read from the game files.")
        if self.client.is_up():
            self.log(f"Player2 answers at {self.cfg.player2_base}." if self.client.NAME == "Player2" else
                     f"{self.client.NAME} answers ({getattr(self.client, 'model', '')}).")
        else:
            self.log("WARNING: Player2 does not answer. Open the Player2 app and log in."
                     if self.client.NAME == "Player2" else
                     f"WARNING: {self.client.NAME} does not answer: check the API key and the model in Settings.")
        self.client.start_health_pings(self.cfg.health_ping_s)
        self.mail.reset_files()
        # The names chosen in earlier games must exist before any save using them loads.
        if self.cfg.mod_dir:
            self.names.write_loc(self.cfg.mod_path)
        self.tail.skip_to_end()
        # A battle commanded before a restart is nobody's any more: never leave the
        # game held in pause by it, nor the armies with its orders.
        self.mail.send(["votc_battle_end = yes"], label="battle:reset")
        threading.Thread(target=self._worker, name="court-brain-worker", daemon=True).start()
        # Ask the game for a first picture; the answer also proves the bridge works.
        self._request_pulse()
        # The save to read is chosen once the game says which campaign and
        # date it is at (_world_after_load); until then no save is trusted.
        self._world_requested = time.time()

    def status(self) -> dict[str, Any]:
        """For the Court Brain window: cheap reads only, no network."""
        snap = self.snapshot
        return {
            "game": self.game_seen,
            "player2": getattr(self.client, "up", None),
            "provider": getattr(self.client, "NAME", "Player2"),
            "model": getattr(self.client, "model", ""),
            "campaign": self.memory.campaign if self.memory else "",
            "memories": len(self.memory.events) if self.memory else 0,
            "date": snap.date if snap else "",
            "busy": self.busy,
            "next_event": self._next_event_days(),
            "frequency": self.cfg.event_frequency,
        }

    def set_provider(self) -> None:
        """From the Court Brain window: the AI that writes was changed in Settings (any thread).
        The new client takes over from the next request; what it learned carries over."""
        old = self.client
        new = make_client(self.cfg, log=self.log)
        new.room, new.on_room, new.on_meter = old.room, old.on_room, old.on_meter
        self.client = new

        def check() -> None:
            if new.NAME == "Player2":
                ok = new.is_up()
                self.log("AI: Player2" + (" answers." if ok else " does not answer - open the Player2 app."))
                return
            ok, detail = new.check()
            self.log(f"AI: {new.NAME}, {detail}." if ok else f"WARNING: {new.NAME} does not answer: {detail}")
        threading.Thread(target=check, daemon=True).start()

    def set_frequency(self, key: str) -> None:
        """From the Court Brain window (any thread)."""
        if key not in FREQUENCIES or key == self.cfg.event_frequency:
            return
        self.cfg.event_frequency = key
        self._story_gap = 0              # the next pause is drawn at the new pace
        try:
            config_mod.set_option("event_frequency", key)
        except OSError:
            pass
        self.log(f"Frequency of unprompted events: {FREQUENCY_TEXT.get(key, key)}")

    def _next_event_days(self) -> int | None:
        """In-game days until the director may bring something new (None: unknown)."""
        if self.memory is None or self.snapshot is None:
            return None
        if FREQUENCIES.get(self.cfg.event_frequency, 0) is None:
            return -1
        if not self._story_gap:
            return 0
        done = gamedate.days_between(self.memory.last_new_date, self.snapshot.date)
        return max(0, self._story_gap - done) if done >= 0 else 0

    def stop(self) -> None:
        self._stop.set()
        self.client.close()
        if self.memory:
            self.memory.save(force=True)

    def run_forever(self) -> None:
        self.start()
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                self.log("Error in the loop:\n" + traceback.format_exc())
            time.sleep(0.25)

    # ===================================================================== loop
    def tick(self) -> None:
        for rec in parse_lines(self.tail.poll()):
            # One record that fails must not lose the ones after it (the
            # signal of the Court button among them).
            try:
                self._handle(rec)
            except Exception:  # noqa: BLE001
                self.log(f"Error reading a {rec.kind} record from the game:\n" + traceback.format_exc()[-1200:])
        self.mail.pump()
        self.battle.tick()
        if self._pulse_pending and time.time() - self._last_pulse > self.PULSE_LOST_S:
            self._pulse_pending = False          # lost on the way; ask again
        self._watch_left_scene()
        if (self.session is None and not self.busy and not self._pulse_pending
                and time.time() - self._last_pulse > self._pulse_gap):
            self._request_pulse()
        self._maybe_refresh_world()
        self._maybe_clear_logs()
        jr = self._join_rebels
        if jr and time.time() >= jr["next"]:
            if self.snapshot is not None and self.snapshot.tag != jr["from"]:
                self.log(f"You now play the rebels ({self.snapshot.tag}).")
                self._join_rebels = None
            elif jr["tries"] >= 20:
                self.log("The rebel country did not appear: you stay with the Crown.")
                self._join_rebels = None
            else:
                jr["tries"] += 1
                jr["next"] = time.time() + 6
                self.mail.send(["votc_find_rebel_country = yes", "votc_signal_pulse = yes"], label="rebels")
        # Tell the game about new memories at once, so that a save made right
        # after a conversation (often with the game paused) carries them.
        if (self.memory is not None and self.synced_head is not None
                and self.memory.head != self.synced_head and self.mail.pending() == 0
                and time.time() - self._last_stamp > 3):
            self._last_stamp = time.time()
            self.mail.send([], label="memory")
        if self.memory:
            self.memory.save()

    # A pulse the game never answered is asked again after this many seconds.
    PULSE_LOST_S = 30.0
    # A scene left untouched for LEFT_SCENE_IDLE_S while the game ran on LEFT_SCENE_DAYS
    # has been left: the ruler went back to ruling. It is closed as if they had closed
    # it, or no event would ever come again (nothing new comes while a scene is open).
    # Talking while the game runs is fine: every word or click starts the watch again.
    LEFT_SCENE_DAYS = 14
    LEFT_SCENE_IDLE_S = 150.0
    LEFT_SCENE_ASK_S = 45.0

    def _watch_left_scene(self) -> None:
        """While a scene is open no picture of the realm is asked (it would move the
        people the scene is about); only the date, now and then, with an empty mail."""
        session = self.session
        if session is None or session.kind == "battle" or self.busy or self.battle.live:
            return
        if time.time() - self._clock_asked < self.LEFT_SCENE_ASK_S or self.mail.pending():
            return
        self._clock_asked = time.time()
        self.mail.send([], label="clock")

    def _game_clock(self, date: str) -> None:
        """The game's date, from an acknowledgement."""
        session = self.session
        if not date or session is None or session.kind == "battle" or session.closed or self.busy:
            return
        since, base = self._clock_base
        if not base or self._touched > since or id(session) != self._clock_scene:
            # the ruler did something since (or this is another scene): watch from here
            self._clock_base, self._clock_scene = (time.time(), date), id(session)
            return
        ran = gamedate.days_between(base, date)
        if (0 <= ran < self.LEFT_SCENE_DAYS or ran >= 10_000
                or time.time() - max(self._touched, since) < self.LEFT_SCENE_IDLE_S):
            return
        self.log(f"The scene ({KIND_LABEL.get(session.kind, session.kind)}) was left open while the game "
                 f"went on for {ran} days: it is closed.")
        self.on_close()

    def _request_pulse(self) -> None:
        self._last_pulse = time.time()
        self._pulse_pending = True
        self.mail.send(["votc_signal_pulse = yes"], label="pulse")

    def _handle(self, rec: Record) -> None:
        if not self.game_seen:
            self.game_seen = True
            self.log("The game answers: the bridge works.")
        kind = rec.kind
        # The field of a battle the ruler commands: its own records, never a picture of the realm.
        if kind in ("BATTLE", "BSIDE", "BCENSUS"):
            self.battle.feed(rec)
            return
        if kind == "BCHOICE":
            self.battle.on_choice(rec.i("n"))
            return
        if kind == "END" and rec.subkind == "bstate":
            self.battle.on_report(self.battle.commit())
            return
        if kind == "SIG":
            self._sig_kind = rec.subkind
            return
        if kind == "ACK":
            self._debug_mode(rec.fields.get("nodebug") != "1")
            self._check_mod_version(rec.i("mod"))
            self.mail.acknowledge(rec.i("mail"), rec.fields.get("loc"))
            if self.snapshot is not None:
                self.snapshot.numbers["budget"] = rec.num("budget", self.snapshot.budget)
            self._game_clock(rec.fields.get("date", ""))
            return
        if kind != "END":
            self.builder.feed(rec)
            return

        snap = self.builder.commit()
        sig = rec.subkind or self._sig_kind
        if snap is None:
            return
        self.previous, self.snapshot = self.snapshot, snap
        self._note_new_reign()
        self.mail.sync_seq(int(snap.n("mailseq")))
        self._sync_timeline(snap)
        self._restore_scenes(snap)
        self._take_live(snap)
        self._watch_army(snap)
        self._note_changes()

        if sig == "pulse":
            self._pulse_pending = False
            self._on_pulse(snap)
        elif sig == "hub":
            self._open_hub(snap)
        elif sig == "advisor":
            self._open_advisor(snap)
        elif sig == "arc":
            self._on_story_choice(snap)
        elif sig == "battle":
            self.battle.begin(snap, self.battle.commit())
        elif sig in ("talk", "envoy", "council", "progress", "reply", "decree"):
            self._open_session(sig, snap)

    # ================================================================== memory
    def _sync_timeline(self, snap: Snapshot) -> None:
        """Make the memory match the game in front of us.

        The game reports its campaign and memory head with every picture.
        A different campaign means another game; a head we did not give it
        (or a date earlier than what we remember) means the player loaded a
        save - and the court must remember only what had happened by then.
        """
        if not snap.has_id:
            # An older mod without the ID line: one memory per country.
            if self.memory is None or self.memory.meta.get("tag") != snap.tag:
                cid = self.store.latest_for_tag(snap.tag) or self.store.new_id()
                self._open_campaign(cid, snap)
            return

        cid = snap.campaign
        if cid == 0:
            self._label_new_game(snap)
            return
        if self._labelling and self._labelling[0] == cid:
            self._labelling = None
        if self.memory is None or self.memory.campaign_id != cid:
            self._open_campaign(cid, snap)
            self.synced_head = None
        self._game_campaign = cid
        mem = self.memory
        assert mem is not None

        reported = snap.memhead
        in_flight = self.mail.in_flight_token()
        expected = {self.synced_head}
        if isinstance(in_flight, tuple) and in_flight[0] == cid:
            expected.add(in_flight[1])
        mine = {h for c, h in self._sent_heads if c == cid}
        if reported in mine and reported not in expected and mem.knows(reported) \
                and reported in {e.id for e in mem.chain(mem.head)}:
            # A mail of ours ran but its acknowledgement was lost: the game is
            # simply behind or level with us on our own line. Not a load.
            self.synced_head = reported
            expected.add(reported)
        if self.synced_head is None or reported not in expected:
            target = reported if (reported == 0 or mem.knows(reported)) else mem.head_at_date(snap.date)
            self._move_to(target, snap, why="a save was loaded" if self.synced_head is not None else "")
            self.synced_head = reported
        # Entries dated after the game's own date belong to a future this
        # game does not have (a save loaded a moment after a sync).
        if gamedate.key(mem.head_date()) > gamedate.key(snap.date) > 0:
            self._move_to(mem.head_at_date(snap.date), snap, why="a date earlier than the memory")

    # ============================================================ scene texts
    # The game keeps an unanswered event, or one still waiting on the calendar, in
    # its saves; the words it shows live in a file Court Brain rewrites. So every
    # scene gets a number that the game keeps too, and Court Brain keeps the words
    # by number: after a load or a restart they are put back, and a scene whose
    # words are gone is marked lost - its event says so and changes nothing.
    SCENE_KEYS = (("votc_bt_end_", "bte"), ("votc_bt_", "bt"), ("votc_out_", "out"), ("votc_chr_", "chr"),
                  ("votc_knk_", "knk"), ("votc_arc_", "arc"), ("votc_obt_", "obt"), ("votc_cen_", "cen"))
    SCENES_KEPT = 12

    @classmethod
    def _scene_kind(cls, key: str) -> str:
        return next((kind for prefix, kind in cls.SCENE_KEYS if key.startswith(prefix)), "")

    def _scene_lines(self, loc: dict[str, str]) -> list[str]:
        """Number the scenes whose words this mail carries, and keep the words."""
        mem = self.memory
        if mem is None:
            return []
        kinds: dict[str, dict[str, str]] = {}
        for key, value in loc.items():
            kind = self._scene_kind(key)
            if kind:
                kinds.setdefault(kind, {})[key] = value
        lines = []
        for kind, words in kinds.items():
            n = int(mem.meta.get("scene_seq", 0)) + 1
            mem.meta["scene_seq"] = n
            kept = mem.meta.setdefault("scenes", {}).setdefault(kind, [])
            kept.append({"id": n, "loc": words})
            del kept[:-self.SCENES_KEPT]
            mem._meta_dirty = True
            self._scene_loaded[kind] = n
            lines.append(f"votc_scene_mark = {{ kind = {kind} id = {n} }}")
        return lines

    def _restore_scenes(self, snap: Snapshot) -> None:
        """The words of the scenes the game holds, put back where a load or a restart lost them."""
        mem = self.memory
        if mem is None or not snap.scenes or self.mail.scene_in_flight():
            return
        loc: dict[str, str] = {}
        lost: list[str] = []
        for kind, n in snap.scenes.items():
            if n <= 0 or self._scene_loaded.get(kind) == n:
                continue
            self._scene_loaded[kind] = n
            kept = next((s for s in mem.meta.get("scenes", {}).get(kind, []) if s.get("id") == n), None)
            if kept is not None:
                loc.update(kept["loc"])
            else:
                lost.append(f"votc_scene_void = {{ kind = {kind} }}")
        if loc:
            kinds = {self._scene_kind(k) for k in loc}
            self.log(f"The words of {len(kinds)} scene(s) put back in the game (after a load or a restart).")
        if lost:
            self.log(f"{len(lost)} scene(s) in this save have no words any more: they are set aside, with no effect.")
        if loc or lost:
            self.mail.send(lost, loc=loc or None, label="scenes", mark=False)

    def _open_campaign(self, cid: int, snap: Snapshot) -> None:
        if self.memory is not None:
            self.memory.save(force=True)
        self.memory = self.store.open(cid, tag=snap.tag, name=snap.name)
        self.live_picture, self.live_date = {}, ""
        self._war_state, self.war_log = None, []
        self.previous = None
        self.world = None
        self._world_after_load(snap)
        self.log(f"Campaign {self.memory.campaign}: {len(self.memory.entries)} memories in the journal.")
        for slot, m in self.memory.measures.items():
            self.mail.loc_values[f"votc_st{slot}_name"] = m.get("name", f"Standing measure {slot}")

    def _label_new_game(self, snap: Snapshot) -> None:
        """A game Court Brain has never seen: give it a campaign number."""
        pending = self._labelling
        if pending and pending[1] == snap.tag and time.time() - pending[2] < 90:
            return                                   # the label is on its way
        cid = self.store.new_id()
        self._open_campaign(cid, snap)
        mem = self.memory
        assert mem is not None
        imported = self.store.import_legacy(mem, snap.tag)
        if imported:
            self.log(f"Imported {imported} memories from the old memory of {snap.tag}.")
            mem.checkout(mem.head_at_date(snap.date))
        self._labelling = (cid, snap.tag, time.time())
        # The label mail carries the head; everything learnt after it is
        # stamped by the mails that follow (FIFO), so nothing is left behind.
        self._game_campaign = cid
        self.synced_head = mem.head
        self._sent_heads.append((cid, mem.head))
        self.mail.send([f"votc_set_campaign = {{ id = {cid} head = {mem.head} }}"], label="campaign")
        self.log(f"New game recognised: campaign {cid}.")

    def _move_to(self, head: int, snap: Snapshot, *, why: str = "") -> None:
        mem = self.memory
        assert mem is not None
        if head == mem.head:
            return
        old = mem.head
        mem.checkout(head)
        lost = mem.forgotten_since(old)
        self.previous = None
        self.live_picture, self.live_date = {}, ""
        self._war_state, self.war_log = None, []
        if why:
            self.log(f"Memory aligned with the save ({why}): the court remembers up to "
                     f"{mem.head_date() or 'the beginning'}; {lost} memories of another course of "
                     f"events are set aside.")
        self._world_after_load(snap)

    # -- the game's side of the journal
    def _stamp(self) -> tuple[list[str], Any]:
        """Top of every mail: move the game's head to ours."""
        mem = self.memory
        if mem is None or self.synced_head is None or self._game_campaign != mem.campaign_id:
            return [], None
        cid = mem.campaign_id
        if mem.head == self.synced_head:
            return [], (cid, mem.head)
        self._sent_heads.append((cid, mem.head))
        return ([f"votc_set_memhead = {{ campaign = {cid} from = {self.synced_head} head = {mem.head} }}"],
                (cid, mem.head))

    def _delivered(self, token: Any) -> None:
        if isinstance(token, tuple) and self.memory and token[0] == self.memory.campaign_id:
            self.synced_head = token[1]

    # -- the live picture
    def _take_live(self, snap: Snapshot) -> None:
        if not snap.has_live:
            return
        news: list[str] = []
        if self.memory and self.live_picture and gamedate.key(snap.date) >= gamedate.key(self.live_date):
            news = live_mod.news(self.live_picture, snap.live, snap.name)
            for line in news:
                self.memory.remember_event(snap.date, "world", line)
                self.log(f"  notizia: {line}")
        self.live_picture, self.live_date = snap.live, snap.date
        self._watch_world(snap, news)

    def _note_changes(self) -> None:
        if not self.memory or not self.snapshot:
            return
        changes = diff(self.previous, self.snapshot)
        for change in changes:
            self.memory.remember_event(self.snapshot.date, "world", str(change))
        old, snap = self.previous, self.snapshot
        self._submit(lambda: self._book_records(old, snap, changes))
        for person in self.snapshot.court:
            self.memory.remember_person(person.name, date=self.snapshot.date,
                                        role=person.role or person._implied_role(), real=True)

    # =================================================================== world
    # A save in debug mode is 300-400 MB: writing one stalls the game, and reading one
    # takes memory next to a game that already uses most of it. So saves are read when
    # the court needs them, not whenever one appears:
    #  - at once, when something that matters has changed since the last picture (the
    #    realm's ruler or heir, its rank, wars, lands, allies, rivals, subjects, or news
    #    from the world around it) - and if no save newer than the change comes on its
    #    own soon, the game is asked for one;
    #  - at once, when the picture is more than half a year of game time old (and the
    #    game is asked for a save when it is more than a year old);
    #  - otherwise now and then, and always the one just loaded or asked for.
    # Between reads the game's own report of each pulse stays in front: the prompts
    # take it as the truth wherever the save's older picture differs (_world_text).
    WORLD_REFRESH_S = 45 * 60        # ask for a save at least this often, when nothing else did
    WORLD_READ_GAP_S = 5 * 60        # read a save that simply appeared at most this often
    WORLD_MIN_SPACING_S = 120        # never two reads closer than this (high speed, monthly autosaves)
    WORLD_STALE_DAYS = 365           # a picture older than this (game time) is renewed at once
    WORLD_CHANGE_WAIT_S = 180        # after a change, wait this long for an autosave before asking for one
    WORLD_ASK_GAP_S = 10 * 60        # never ask the game for saves closer than this
    WORLD_MIN_FREE_MB = 1200         # a save is read only with this much memory free

    def _world_marks(self, snap: Snapshot) -> dict[str, Any]:
        """The facts of the realm the save's picture must agree with."""
        marks: dict[str, Any] = {"tag": snap.tag, "rank": snap.rank,
                                 "ruler": snap.ruler.name if snap.ruler else None,
                                 "heir": snap.heir.name if snap.heir else None}
        for key in ("atwar", "wars", "locations", "subjects", "issubject", "allies", "rivals", "regency"):
            if key in snap.numbers:
                marks[key] = int(snap.numbers[key])
        return marks

    def _watch_world(self, snap: Snapshot, news: list[str]) -> None:
        """Has something that matters changed since the last picture of the realm? (News of
        the world at large - a war or a new ruler far away - is in the pulse's own report: it
        does not call for a new save.)"""
        marks = self._world_marks(snap)
        old, self._world_marks_seen = self._world_marks_seen, marks
        what = [line for line in news if snap.name and snap.name in line][:2]
        if old and old.get("tag") == marks["tag"]:
            what += [f"{k}: {old[k]} -> {v}" for k, v in marks.items()
                     if v is not None and old.get(k) is not None and old[k] != v]
        if not what:
            return
        if not self._world_changed_on:
            self._world_changed_at = time.time()
            self.log("  the world changed (" + "; ".join(what[:3]) + "): the picture of the world will be renewed")
        self._world_changed_on = snap.date

    def _world_due(self) -> str:
        """Why the picture of the world must be renewed now ("" if it need not)."""
        snap, w = self.snapshot, self.world
        if snap is None:
            return ""
        if self._world_changed_on:
            return "change"
        if w is None:
            return "none"
        age = gamedate.days_between(w.date, snap.date)
        if age > 2 * self.WORLD_STALE_DAYS:
            return "old"
        if age > self.WORLD_STALE_DAYS:
            return "aging"          # read any newer save at once, but do not ask for one yet
        return ""

    def _maybe_refresh_world(self) -> None:
        """Pick up a save when the court needs one; ask the game for one when none comes."""
        now = time.time()
        due = self._world_due()
        if now - self._world_check > 5 and not self._world_loading:
            self._world_check = now
            newest = worldsave.newest_save(self.cfg.user_path / "save games")
            if newest is not None:
                try:
                    stamp = newest.stat().st_mtime
                except OSError:
                    stamp = 0.0
                self._note_autosave(newest, stamp)
                asked = now - self._world_asked_at < 5 * 60
                wanted = asked or bool(due) or now - self._world_read_at > self.WORLD_READ_GAP_S
                spaced = now - self._world_read_at > self.WORLD_MIN_SPACING_S
                # Only once the file has stopped growing (the game writes 300 MB).
                if wanted and spaced and (str(newest), stamp) != self._world_file and now - stamp > 4:
                    self._world_file = (str(newest), stamp)
                    if self._save_fits(newest):
                        self._load_world_async(newest)
        if self._world_loading or not self.game_seen or self.game_debug is False:
            return
        if self.session is not None or self.busy or now - self._world_read_at <= self.WORLD_MIN_SPACING_S:
            return          # a scene in progress is not interrupted by a save: right after it
        # No save newer than the change came on its own (autosaves off, or far apart): ask for one.
        gap = self.WORLD_ASK_GAP_S * (2 if due == "old" else 1)
        if (due in ("change", "old", "none") and now - self._world_asked_at > gap
                and (due != "change" or now - self._world_changed_at > self.WORLD_CHANGE_WAIT_S)):
            self._ask_world_save({"change": "the world changed", "old": "the picture is old",
                                  "none": "there is none yet"}[due])
        elif now - self._world_requested > self.WORLD_REFRESH_S:
            self._ask_world_save("")

    # ============================================================ the game's logs
    # In debug mode every console command of the bridge makes EU5 write thousands of
    # lines to logs/debug.log (see gamelogs.py): past this size the game is asked to
    # empty its logs, after the mod's real errors are copied out of them.
    LOGS_CLEAR_MB = 120
    LOGS_LOOK_S = 30

    def _maybe_clear_logs(self) -> None:
        if not self.cfg.clear_game_logs:
            return
        now = time.time()
        if now - self._logs_look < self.LOGS_LOOK_S or self._logs_off:
            return
        self._logs_look = now
        size = gamelogs.debug_log_mb(self.cfg.logs_path)
        if self._logs_asked_at:
            if size < self._logs_before_mb * 0.5:
                self.log(f"  the game emptied its logs (debug.log was {self._logs_before_mb:.0f} MB)")
                self._logs_asked_at, self._logs_failures = 0.0, 0
            elif now - self._logs_asked_at > 90:
                self._logs_asked_at = 0.0
                self._logs_failures += 1
                if self._logs_failures >= 3:
                    self._logs_off = True
                    self.log("The game did not empty its logs when asked: debug.log is left to grow "
                             "(it is emptied at every start of the game).")
            return
        if (size < self.LOGS_CLEAR_MB or not self.game_seen or self.game_debug is False
                or self.mail.pending()):
            return
        kept = gamelogs.keep_errors(self.cfg.logs_path, self.cfg.state_dir / gamelogs.KEPT_NAME)
        if kept:
            self.log(f"  {kept} error(s) of the mod kept from the game's error log before it is emptied")
        self._logs_asked_at, self._logs_before_mb = now, size
        self.mail.send(["votc_request_logs = yes"], label="logs")

    def _note_autosave(self, path: Any, stamp: float) -> None:
        """EU5 in debug mode writes every autosave as a 350-400 MB text file, and the game stands
        still while it does: autosaves a minute or two apart are felt as regular freezes. When
        they come that often, the player is told once where to change it."""
        if not str(getattr(path, "name", "")).startswith("autosave") or stamp <= self._autosave_last:
            return
        gap = stamp - self._autosave_last if self._autosave_last else 0
        self._autosave_last = stamp
        if 0 < gap < 240 and not self._autosave_told:
            self._autosave_told = True
            msg = ("EU5 autosaves every few minutes. In debug mode each autosave is a 400 MB file and the game "
                   "freezes for a few seconds while it writes it: set Autosave to yearly (or off) in the game's "
                   "settings for a smoother game - Court Brain asks for the saves it needs by itself.")
            self.log("TIP: " + msg)
            self._ui("add_note", "\u2139 " + msg, "")

    def _ask_world_save(self, why: str) -> None:
        self._world_requested = self._world_asked_at = time.time()
        self.mail.send([], label="world", save=True)
        self.log("Asking the game for a save to update the picture of the world" + (f" ({why})." if why else "."))

    def _save_fits(self, path) -> bool:
        """Is this save of the game being played, not from its future - and, when the
        world changed, newer than the picture already held?"""
        snap = self.snapshot
        if snap is None:
            return False
        try:
            info = read_info(path)
        except OSError:
            return False
        if info.campaign and self._game_campaign and info.campaign != self._game_campaign:
            return False
        if gamedate.key(info.date) > gamedate.key(snap.date) > 0:
            return False
        if self.world is not None and 0 < gamedate.key(info.date) <= gamedate.key(self.world.date):
            return False            # no newer than the picture held: nothing new in it
        return True

    def _world_after_load(self, snap: Snapshot) -> None:
        """After a load: drop a picture from the future, read the save just loaded."""
        if self.world is not None and gamedate.key(self.world.date) > gamedate.key(snap.date):
            self.world = None
        self._world_marks_seen, self._world_changed_on = {}, ""
        cid, head, when = snap.campaign, snap.memhead, gamedate.dotted(snap.date)

        def job() -> None:
            if not cid:
                return          # a game not yet labelled: no save is known to be its own
            info = self.saves.find(campaign=cid, head=head, date=when)
            if info is None:
                # No save of this very moment: the newest of this campaign
                # that is not from its future.
                limit = gamedate.key(when)
                info = next((s for s in self.saves.refresh()
                             if s.campaign == cid and 0 < gamedate.key(s.date) <= limit), None)
            if info is not None:
                self._load_world_async(self.cfg.user_path / "save games" / info.file)

        threading.Thread(target=job, name="save-finder", daemon=True).start()

    def _load_world_async(self, path) -> None:
        if path is None or self._world_loading:
            return
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return
        free = worldsave.free_memory_mb()
        if free is not None and free < self.WORLD_MIN_FREE_MB:
            # Too little memory left beside the game: reading now could bring it down.
            # The same save is tried again at the next look (the pulse's report still
            # tells the court what changed meanwhile).
            self._world_file = ("", 0.0)
            if time.time() - self._world_low_noted > 600:
                self._world_low_noted = time.time()
                self.log(f"The save is not read for now: only {free} MB of memory are free beside the game.")
            return
        self._world_loading = True
        self._world_file = (str(path), stamp)
        self._world_read_at = time.time()

        def job() -> None:
            try:
                world = worldsave.read(path)
                self.world = world
                # The change that called for a new picture is in this one (it is not older).
                changed = self._world_changed_on
                if changed and gamedate.key(world.date) >= gamedate.key(changed):
                    self._world_changed_on = ""
                self._send_peer_gold(world)
                # on the worker, like everything that writes the journal
                self._submit(lambda: self._war_update(world))
                self._submit(lambda: self._foreign_rally(world))
                self._submit(lambda: self._book_world(world))
                self.log(f"Picture of the world updated to {world.date}: {len(world.by_tag)} countries, "
                         f"{len(world.wars)} wars, {len(world.events)} events.")
            except Exception as exc:  # noqa: BLE001 - a bad save must not stop the court
                self.log(f"Could not read the save: {exc}")
            finally:
                self._world_loading = False

        threading.Thread(target=job, name="world-reader", daemon=True).start()

    GREAT_EFFORT_DAYS = 3650         # one great effort in ten years at most

    def _great_block(self, snap: Snapshot | None) -> str:
        """Why the realm could not rise to a great effort now ("" if it could): its state,
        and one in ten years at most (remembered with the campaign; the game checks too)."""
        if snap is None:
            return "the realm's state is not known"
        why = [prompts.great_effort_block(snap)]
        last = self.memory.meta.get("great_effort_on") if self.memory else ""
        if last and gamedate.days_between(last, snap.date) < self.GREAT_EFFORT_DAYS:
            why.append("the realm made a great effort less than ten years ago")
        return "; ".join(w for w in why if w)

    def _note_great(self, acts: list[dict[str, Any]], snap: Snapshot) -> None:
        """A great effort ordered: the realm cannot make another for ten years."""
        if self.memory is not None and any(a.get("kind") == "great_effort" for a in acts):
            self.memory.meta["great_effort_on"] = snap.date
            self.memory._meta_dirty = True
            self.log("A great effort of the realm is ordered: the next is possible in ten years.")

    def peer_gold(self, world: Any = None) -> float | None:
        """What the realms of the player's rank usually hold in their chest (the median), from
        the latest save - so that sums of money stay in proportion to the world."""
        w, snap = world or self.world, self.snapshot
        if w is None or snap is None or not w.countries:
            return None
        cid = w.by_tag.get(snap.tag)
        me = w.countries.get(cid) if cid is not None else None
        real = [c for c in w.countries.values() if c.kind == "Real" and c.cid != cid]
        peers = [c.gold for c in real if me is not None and me.rank and c.rank == me.rank]
        if len(peers) < 5:
            peers = [c.gold for c in real]
        peers = sorted(g for g in peers if g > 0)
        return peers[len(peers) // 2] if peers else None

    def _send_peer_gold(self, world: Any) -> None:
        """Tell the game the usual treasury of the player's peers (votc_values: the gold bands
        never exceed a share of it). Only when it has really changed."""
        peer = self.peer_gold(world)
        if peer is None:
            return
        last = self._peer_sent
        if last and abs(peer - last) / last < 0.1:
            return
        self._peer_sent = peer
        self.mail.send([f"set_variable = {{ name = votc_peer_gold value = {max(1, round(peer))} }}"], label="peers")

    def _digest(self, snap: Snapshot) -> str:
        """What exists in this game - its laws only those this realm has, when the save says."""
        w = self.world
        cid = w.by_tag.get(snap.tag) if w is not None else None
        own = dict(w.countries[cid].laws) if cid is not None and cid in w.countries else None
        return self.codex.digest(government_group=_gov_group(snap), own_laws=own)

    def _world_text(self, snap: Snapshot) -> str:
        parts = []
        live_text = live_mod.render(self.live_picture, snap.name)
        if live_text:
            parts.append(f"THE WORLD AROUND THE REALM (reported by the game on {self.live_date}):\n{live_text}")
        brief = lore.world_brief(self.world, self.codex, snap.tag)
        if brief:
            if self.world is not None:
                brief = (f"FROM THE STATE PAPERS OF {self.world.date} (older than the report above; "
                         f"where they differ, the report is right):\n{brief}")
            parts.append(brief)
        war = self._war_report(snap)
        if war:
            parts.append(war)
        standing = MS.brief(self.memory.measures) if self.memory else ""
        if standing:
            parts.append(standing)
        return "\n\n".join(parts)

    # ============================================================= personas
    # ======================================================= the actors' part
    # Before the first answer, as an actor prepares a part: what each person wants
    # from the ruler in THIS scene, what stands in the way, how they will go about
    # it - from who they are and from where they really stand (the balance of
    # power, the state papers, the memory). Played, never recited.
    PART_KINDS = ("talk", "petition", "reply", "envoy", "meeting", "visit", "estate", "council")

    def _balance_text(self, snap: Snapshot) -> str:
        """Who needs whom, from the figures - in words, for judgement, never to be quoted."""
        c = snap.target_country
        if c is None:
            return ""

        def compare(ours: float, theirs: float, what: str) -> str:
            if ours <= 0 or theirs <= 0:
                return ""
            r = ours / theirs
            for limit, words in ((8, "ours are many times theirs"), (2.5, "ours are several times theirs"),
                                 (1.4, "ours are clearly greater"), (1 / 1.4, "about even"),
                                 (1 / 2.5, "theirs are clearly greater"), (1 / 8, "theirs are several times ours")):
                if r >= limit:
                    return f"- {what}: {words}"
            return f"- {what}: theirs are many times ours"

        lines = ["THE BALANCE BETWEEN THE TWO SIDES (from the state papers: for judging who needs whom - never "
                 "quoted, never a figure in anyone's mouth):"]
        doubt = self._suspicion()
        if doubt >= 2:
            lines.append("- the ruler's word is " + ("widely held worthless: deceits and outrages have been found out"
                                                     if doubt >= 6 else "doubted: word of their deceits has "
                                                     "travelled"))
        for x in (compare(snap.n("locations"), c.locations, "lands"), compare(snap.n("army"), c.army, "armies"),
                  compare(snap.n("gold"), c.gold, "treasuries")):
            if x:
                lines.append(x)
        danger = False
        w = self.world
        cid = w.by_tag.get(c.tag) if w is not None else None
        if w is not None and cid is not None:
            size: dict[int, int] = {}
            for owner in w.owners.values():
                size[owner] = size.get(owner, 0) + 1
            mine = size.get(cid, 0) or c.locations
            for war in w.wars_of(cid)[:3]:
                enemies = war.defenders if cid in war.attackers else war.attackers
                foe = sum(size.get(e, 0) for e in enemies)
                names = ", ".join(self._cname(w.tag(e)) for e in enemies[:3])
                weight = ("an enemy far greater than they are" if mine and foe > mine * 2 else
                          "an enemy of their own size" if mine and foe > mine * 0.6 else "a lesser enemy")
                danger = danger or (bool(mine) and foe > mine * 1.5 and snap.tag not in [w.tag(e) for e in enemies])
                lines.append(f"- they are at war with {names}: {weight}")
            them = w.countries.get(cid)
            if them is not None and them.stability < 0:
                lines.append("- their realm is in disorder at home")
            if any(p.cid == cid for p in w.great_powers(8)):
                lines.append("- they count among the great powers of the world")
        n = snap.numbers
        ties = [t for k, t in (("target_allied", "already our allies"), ("target_at_war", "at war with us"),
                               ("target_truce", "under a truce with us"), ("target_rival", "our declared rivals"))
                if n.get(k)]
        lines.append("- between us: " + (", ".join(ties) if ties else "no formal ties"))
        lines.append("- IN A WAR BETWEEN THE TWO ALONE: " + self._war_alone(snap))
        r = (snap.n("locations") / c.locations) if c.locations else 1.0
        if danger and r >= 1.5:
            need = ("They are in danger and we are the stronger: our help could save them - they need us far "
                    "more than we need them.")
        elif r >= 2.5:
            need = "We are far the stronger: they gain much from our goodwill and cannot afford to offend us lightly."
        elif r <= 0.4:
            need = "They are far the stronger: they need little from us and can set their terms."
        else:
            need = "Neither side dominates: whatever is agreed is a real bargain."
        lines.append("- WHO NEEDS WHOM: " + need)
        return "\n".join(lines)

    def _war_alone(self, snap: Snapshot) -> str:
        """How a war between the ruler's realm and the foreign court would go, the two alone -
        from their armies, lands and allies: what both courts know, whatever they say."""
        c = snap.target_country
        if c is None:
            return ""
        ra = (snap.n("army") + 1) / (c.army + 1)
        rl = (snap.n("locations") or 1) / (c.locations or 1)
        power = (ra * 2 + rl) / 3
        if power >= 4:
            out = "they could not hope to win, nor to hold out long - and everyone at their court knows it"
        elif power >= 2:
            out = "they would very likely lose, and they know it"
        elif power > 0.5:
            out = "a hard, uncertain war for both sides"
        elif power > 0.25:
            out = "we would very likely lose"
        else:
            out = "we could not hope to win"
        w = self.world
        if w is not None:
            me, cid = w.by_tag.get(snap.tag), w.by_tag.get(c.tag)
            theirs = len(w.allies_of(cid)) if cid is not None else 0
            ours = len(w.allies_of(me)) if me is not None else 0
            if theirs or ours:
                out += (f" (allies may change it: they have {theirs}, we have {ours} - allies come when it suits them, "
                        "not always)")
        return out

    # How often a weaker court facing a far stronger realm refuses out of sheer pride.
    PROUD_CHANCE = 0.15

    def _outmatched(self, snap: Snapshot) -> bool:
        """Is the foreign court far weaker than the ruler's realm (a war would ruin it)?"""
        c = snap.target_country
        if c is None:
            return False
        ra = (snap.n("army") + 1) / (c.army + 1)
        rl = (snap.n("locations") or 1) / (c.locations or 1)
        return (ra * 2 + rl) / 3 >= 2

    def _stance_text(self, proud: bool | None) -> str:
        """How a far weaker court meets pressure: as the weak do - or, rarely, too proud to bend."""
        if proud is None:
            return ""
        if proud:
            return ("THIS RULER IS PROUD (rare): they know full well they cannot match the ruler's realm - and they "
                    "say so, plainly or bitterly - yet what honour forbids they will not yield, whatever it costs. "
                    "Let it show that PRIDE is the reason, not a miscalculation: a bitter jest, 'you may take it, "
                    "but not from my hand', a silence where others would bargain. They never pretend they could win.")
        return ("THEY ARE NO FOOLS: they know resistance would ruin them. They do not answer a demand with "
                "defiance, nor hint that the ruler would fail, suffer or be deceived - that would be a lie and a "
                "danger to them. They look for a way out that saves face: a price, a counter-offer, something lesser "
                "instead, time to consult, an appeal to friends or to the ruler's honour - or they yield with what "
                "dignity they can keep. Their fear may show; their pride may sting; their words stay true.")

    def _their_means(self, snap: Snapshot, proud: bool | None = None) -> str:
        """For a foreign court: what it could REALLY do to the ruler or for them - its real means,
        where it lies - and, rarely, a pride that will not bend. Threats and promises come from
        this, never from a power the world does not give it."""
        c = snap.target_country
        if c is None:
            return ""
        live = self.live_picture or {}
        near = {x.tag for x in live.get("near", [])}
        w = self.world
        cid = w.by_tag.get(c.tag) if w is not None else None
        their_allies = [self._cname(w.tag(a)) for a in w.allies_of(cid)][:4] if cid is not None else []
        rivals = [x.name for x in live.get("rival", [])][:3]
        enemies = [x.name for x in live.get("war", [])][:3]
        can = []
        if not snap.n("target_at_war"):
            can.append("go to war with us" + ("" if not snap.n("target_truce") else " - once the truce runs out, "
                                                                                  "or breaking it with shame"))
        else:
            can.append("fight on, sue for peace, or offer terms")
        if their_allies:
            can.append("call on their allies (" + ", ".join(their_allies) + ") - who come only if it suits them")
        if rivals or enemies:
            can.append("side with those who already stand against us (" + ", ".join(dict.fromkeys(rivals + enemies))
                       + ")")
        else:
            can.append("seek allies against us among other courts")
        can.append("grant or refuse an alliance, a marriage, passage for armies, a guarantee, their trust")
        if c.gold and snap.n("gold") and c.gold >= snap.n("gold") * 0.5:
            can.append("pay, lend or refuse money - they have some to give")
        else:
            can.append("little in money: their chest is small beside ours")
        can.append("raise and move their own army")
        where = ("they border us: their armies can reach our lands directly" if c.tag in near else
                 "they do not border us: their armies reach us only through others' lands or by sea, and "
                 "nothing they do reaches our fields, roads or granaries directly")
        out = ["WHAT THEIR COURT COULD REALLY DO (their threats and promises come only from this - a threat of "
               "something their realm cannot do, or cannot reach, is never made):",
               "- " + "; ".join(can) + ".",
               f"- Where they lie: {where}.",
               "- " + ("Told as people of the age say it - their own words, never the game's."),
               "- IN A WAR BETWEEN THE TWO ALONE: " + self._war_alone(snap) + ". They may hide their fear, "
               "flatter, stall, look for allies or ask for time - they never claim a strength they do not have."]
        stance = self._stance_text(proud)
        if stance:
            out.append(stance)
        return "\n".join(out)

    def _prepare_part(self, session: Session, said: str) -> None:
        session.part_ready = True
        mem, snap = self.memory, session.snap
        if mem is None or not session.cast or session is not self.session or session.closed:
            return
        self._ui("set_busy", "…")
        people = []
        for name in session.cast[:3]:
            text = mem.persona(name)
            people.append(f"{name}: {_persona_for_actor(text) if text else '(no notes: judge from their place)'}")
        balance = self._balance_text(snap)
        stance = self._stance_text(session.proud)
        if stance:
            balance += "\n" + stance
        if snap.target_country:
            papers = ("THE STATE PAPERS OF THEIR REALM:\n"
                      + (lore.realm_brief(self.world, self.codex, snap.target_country.tag) or "(nothing more)"))
        else:
            papers = "THE REALM, AS THOSE WHO GOVERN IT KNOW IT:\n" + prompts.render_dossier(snap)
        scene = f"{KIND_LABEL.get(session.kind, session.kind)} - {session.header.title}"
        if session.opener:
            scene += f". {session.opener[:600]}"
        task = prompts.ACTOR_PART_TASK.format(people="\n\n".join(people), balance=balance, papers=papers[:2500],
                                        memory=mem.brief(focus=tuple(session.cast), scene_tag=(
                                            snap.target_country.tag if snap.target_country else ""))[:2500],
                                        scene=scene,
                                        said=said[:800])
        try:
            data = self.client.complete_json([{"role": "user", "content": task}], prompts.ACTOR_PART_SCHEMA,
                                             schema_name="votc_part", temperature=0.5, max_tokens=1600)
        except Player2Error as exc:
            self.log(f"  the part was not prepared ({exc})")
            return
        parts = []
        for p in data.get("parts") or []:
            if not isinstance(p, dict) or not _t(p.get("name"), 80):
                continue
            name = _cast_name(_t(p.get("name"), 80), session.cast)
            if name not in session.cast:
                if len(session.cast) != 1:
                    continue
                name = session.cast[0]             # the only person there: the part is theirs
            p["name"] = name
            parts.append(f"{_t(p.get('name'), 80)} - where they stand: {_t(p.get('position'), 300)} | wants from "
                         f"the ruler now: {_t(p.get('objective'), 200)} | would gladly accept: "
                         f"{_t(p.get('would_welcome'), 200)} | could never accept: {_t(p.get('cannot_accept'), 200)} | "
                         f"in the way: {_t(p.get('obstacle'), 200)} | how they go about it: "
                         f"{_t(p.get('tactics'), 250)} | today: {_t(p.get('mood'), 150)}")
            self.log(f"  part: {_t(p.get('name'), 40)} wants {_t(p.get('objective'), 120)}")
        if not parts:
            return
        block = ("=== THEIR PART IN THIS SCENE (hidden: play it, never recite it; it changes as the scene "
                 "changes - a new offer, a threat, good news - and \"inner\" follows it) ===\n" + "\n".join(parts))
        if balance:
            block += "\n\n" + balance
        session.messages[0]["content"] += "\n\n" + block

    # A detail of a persona rests this long (game days) before it may surface again.
    DETAIL_REST_DAYS = 240

    def _detail_beat(self, session: Session) -> str:
        """One living detail of someone in the scene, the one least recently shown and
        rested - so that a person is never the same anecdote twice."""
        mem = self.memory
        if mem is None:
            return ""
        used = mem.meta.setdefault("details_used", {})
        date = session.snap.date
        options = []
        for name in session.cast:
            m = re.search(r"DETAILS:\s*(.+)", mem.persona(name))
            if not m:
                continue
            rec = used.setdefault(name.lower(), {})
            for i, d in enumerate(x.strip() for x in m.group(1).split(" / ")):
                last = rec.get(d[:60], "")
                if not d or (name, d[:60]) in session.details_shown:
                    continue
                if last and 0 <= gamedate.days_between(last, date) < self.DETAIL_REST_DAYS:
                    continue
                options.append((gamedate.key(last) if last else 0, random.random(), name, d))
        if not options:
            return ""
        _k, _r, name, detail = min(options)
        used[name.lower()][detail[:60]] = date
        session.details_shown.add((name, detail[:60]))
        mem._meta_dirty = True
        return (f"THE PERSON UNDER THE ROLE: if the moment allows, let this surface from {name}, in passing and in "
                f"their own way - a line, an aside, a flash of it, never a speech: \"{detail}\".")

    def _evolve_persona(self, snap: Snapshot, person: Any, text: str) -> str:
        """Every few years of game, the people the ruler keeps meeting change: what they
        want and fear now, and their living details, from what happened to them."""
        mem = self.memory
        key = person.name.strip().lower()
        since = mem.meta.setdefault("persona_since", {})
        if key not in since:
            since[key] = snap.date
            mem._meta_dirty = True
            return text
        years = gamedate.days_between(since[key], snap.date) // 365
        if years < 4:
            return text
        note = mem.people.get(key)
        news = [f"{e.date}: {e.text}" for e in mem.events if person.name.split()[0] in e.text][-12:]
        if note is not None:
            news = [f"standing now: {note.standing}"] * bool(note.standing) + list(note.notes[-6:]) + news
        if not news:
            since[key] = snap.date                  # nothing happened with them: nothing to change
            mem._meta_dirty = True
            return text
        try:
            data = self.client.complete_json(
                [{"role": "user", "content": prompts.PERSONA_UPDATE_TASK.format(
                    years=years, persona=text, news="\n".join(news))}],
                prompts.PERSONA_UPDATE_SCHEMA, schema_name="votc_persona_now", temperature=0.85, max_tokens=900)
        except Player2Error:
            return text
        now, details = _t(data.get("now"), 500), _t(data.get("details"), 600)
        lines = [l for l in text.split("\n") if not l.startswith(("NOW:", "DETAILS:"))]
        if now:
            lines.append(f"NOW: {now}")
        if details:
            lines.append(f"PRIVATE: {details}")
        text = "\n".join(lines)
        mem.set_persona(person.name, text)
        since[key] = snap.date
        mem.meta.setdefault("details_used", {}).pop(key, None)
        self.log(f"  {person.name} has changed with the years: their persona is brought up to date")
        return text

    def _ensure_personas(self, snap: Snapshot, people: list) -> str:
        """Write, once, the persona of each person about to speak."""
        if self.memory is None:
            return ""
        nl = chr(10)
        out = []
        for person in people:
            if person is None or not person.name:
                continue
            text = self.memory.persona(person.name)
            if text:
                text = self._evolve_persona(snap, person, text)
            if not text:
                foreign = snap.target_country if (snap.target_country and person.court
                                                  and person.court in (snap.target_country.long_name,
                                                                       snap.target_country.name)) else None
                facts = lore.person_facts(self.world, self.codex, foreign.tag if foreign else snap.tag, person.name,
                                          is_ruler=person.is_ruler, is_heir=person.is_heir)
                lines = [prompts.PERSONA_TASK, "", "PERSON: " + person.describe()]
                if person.court:
                    lines.append("OF THE COURT OF: " + person.court)
                if facts:
                    lines.append("FROM THE STATE PAPERS: " + facts)
                if foreign:
                    lines.append(f"THEIR REALM: {foreign.long_name or foreign.name}, {foreign.culture}, "
                                 f"{foreign.religion}, {foreign.government}, {snap.date}")
                else:
                    lines.append(f"REALM: {snap.long_name or snap.name}, {snap.culture}, {snap.religion}, "
                                 f"{snap.government}, {snap.date}")
                try:
                    data = self.client.complete_json(
                        [{"role": "user", "content": nl.join(lines)}], prompts.NOTE_SCHEMA,
                        schema_name="votc_persona", temperature=0.9, max_tokens=2000)
                    text = _cap(data.get("text"), 2000)
                except Player2Error:
                    text = ""
                if text:
                    self.memory.set_persona(person.name, text)
            elif "VOICE:" not in text:
                # Written before voices existed: the character stays as it was,
                # only the voice is added - once, and then it is kept.
                try:
                    data = self.client.complete_json(
                        [{"role": "user", "content": prompts.VOICE_TASK + nl + nl + f"{person.name}: {text}"}],
                        prompts.NOTE_SCHEMA, schema_name="votc_voice", temperature=0.8, max_tokens=1500)
                    voice = _cap(data.get("text"), 600)
                except Player2Error:
                    voice = ""
                if voice:
                    text = f"{text}{nl}VOICE: {voice.removeprefix('VOICE:').strip()}"
                    self.memory.set_persona(person.name, text)
            if text:
                # What the actor plays: never a catchphrase to repeat, never private
                # anecdotes to drop into business (older personas had both).
                out.append(f"{person.name}: {_persona_for_actor(text)}")
        return (nl + nl).join(out)

    PROFILE_YEARS = 30          # a realm changes: its profile is rewritten once a generation

    def _realm_signature(self, snap: Snapshot) -> dict[str, str]:
        year = (gamedate.parse(snap.date) or (1337, 1, 1))[0]
        w, cid = self._world_of(snap)
        great = w is not None and cid is not None and any(c.cid == cid for c in w.great_powers(8))
        order = [k for k, _y, _n in prompts.AGES]
        age = prompts.age_of(year)[0]
        if w is not None and w.age in order:
            age = max((w.age, age), key=order.index)
        return {"age": prompts.age_of(year, age)[1],
                "scale": prompts.scale_of(snap.n("locations"), snap.rank, great)[0],
                "name": snap.long_name or snap.name, "faith": snap.religion, "government": snap.government,
                "capital": snap.capital}

    def _ensure_realm_profile(self, snap: Snapshot) -> str:
        if self.memory is None:
            return ""
        year = (gamedate.parse(snap.date) or (0, 0, 0))[0]
        written = int(self.memory.meta.get("realm_profile_year") or 0)
        # What the realm IS: its age, its scale, its name, faith, government and seat.
        # When any of these changes, the old profile describes another realm.
        sig = self._realm_signature(snap)
        old_sig = self.memory.meta.get("realm_profile_sig") or {}
        if self.memory.realm_profile and not old_sig:
            # a profile written before this was tracked: take the realm as it is now
            self.memory.meta["realm_profile_sig"] = old_sig = sig
        changed = [f"{k}: {old_sig.get(k)} -> {v}" for k, v in sig.items() if old_sig and old_sig.get(k) != v]
        stale = bool(self.memory.realm_profile) and year and written and (
            year - written >= self.PROFILE_YEARS or bool(changed))
        if not self.memory.realm_profile or stale:
            nl = chr(10)
            history = []
            if stale:
                why = ("THE REALM HAS CHANGED IN KIND (" + "; ".join(changed) + ")." if changed
                       else "A GENERATION HAS PASSED since the last profile was written.")
                history = ["", why + " What the realm was then:", self.memory.realm_profile, "",
                           "What has happened since:",
                           self.memory.summary or "", nl.join(e.text for e in self.memory.events[-20:]),
                           "", "Write the profile of the realm as it is NOW: keep what endures, change what the "
                               "years, the reigns, the wars and the age have changed."]
            ask = nl.join([prompts.REALM_TASK, "", "REALM NOW:", prompts.render_snapshot(snap), "",
                           self._setting(snap), "", "STATE PAPERS:", self._world_text(snap), *history])
            try:
                data = self.client.complete_json([{"role": "user", "content": ask}], prompts.NOTE_SCHEMA,
                                                 schema_name="votc_realm", temperature=0.8, max_tokens=2500)
                self.memory.realm_profile = _t(data.get("text"), 2000)
                self.memory.meta["realm_profile_year"] = year
                self.memory.meta["realm_profile_sig"] = sig
                self.memory.save(force=True)
                if stale:
                    self.log("The realm has changed" + (f" ({'; '.join(changed)})" if changed else " with the years")
                             + ": its profile was rewritten.")
            except Player2Error:
                pass
        return self.memory.realm_profile

    # ================================================================ sessions
    def _open_session(self, kind: str, snap: Snapshot) -> None:
        if self.session and not self.session.closed:
            self.log("A conversation was still open: closing it without an outcome.")
            self.session = None
        agenda = int(snap.n("agenda"))
        mode = kind
        estate_name = ""
        if kind == "council" and 1 <= agenda <= len(ESTATES):
            mode = "estate"
            key = ESTATES[agenda - 1]
            estate_name = snap.estate_names.get(key) or key
        if kind == "envoy":
            mode = {2: "meeting", 3: "visit"}.get(agenda, "envoy")
        if kind == "reply" and agenda == BIOGRAPHER_AGENDA:
            mode = "biographer"
        if kind == "reply" and agenda in _READING:
            mode = _READING[agenda]
        petition = None
        if kind == "talk" and snap.target_person:
            petition = self._pending_petition(snap.target_person.name, snap.date)
            if petition:
                mode = "petition"
                if self.memory:
                    self.memory.remember_event(snap.date, "petition_received", snap.target_person.name)

        header = self._header_for(mode, snap, estate_name)
        diplomatic = kind == "envoy"
        session = Session(kind=mode, snap=snap, header=header, diplomatic=diplomatic)
        if mode in ("petition", "reply") and self.last_scene.get("kind") in ("knock", "story"):
            session.chain = int(self.last_scene.get("chain") or 0)
        if kind == "reply" and self._reply_arc and mode == "reply":
            session.arc_id, self._reply_arc = self._reply_arc, 0
        self.session = session
        self._meter()                     # what came before is charged to what came before
        self._tally = self._new_tally()
        self._ui("open", header)
        self._ui("set_busy", "…")
        self.log(f"[{KIND_LABEL.get(mode, mode)}] {header.title}")
        note = ""
        if petition:
            note = (f"THEIR REQUEST FOR AN AUDIENCE: \"{petition.get('title', '')}\" - "
                    f"{petition.get('body', '')}")
        if kind == "reply" and self.last_scene and mode == "reply":
            note = (f"THE SCENE: \"{self.last_scene.get('title','')}\" - "
                    f"{self.last_scene.get('body','')}")

        def prepare() -> None:
            # Everything slow happens here, on the worker, while the panel
            # already shows who is coming.
            if mode == "biographer":
                self._prepare_biographer(session, snap)
                return
            if mode in ("book", "life", "century"):
                self._read_book(snap, mode)
                return
            speakers = [snap.target_person]
            if mode in ("envoy", "meeting", "visit") and snap.target_country and own_crown(snap):
                # the ruler's own other crown: its sovereign is the ruler; its people speak
                c = snap.target_country
                realm = c.long_name or c.name
                speakers = [p for p in speakers if p is not None and p.name and p.name != c.ruler
                            and not (snap.ruler and p.name == snap.ruler.name)]
                if not speakers:
                    speakers = [Person(name=f"the Chancellor of {c.name}", role=f"chancellor of {realm}, "
                                       f"who governs it in the ruler's name", court=realm, religion=c.religion)]
                self.log(f"  {realm} is the ruler's own other crown: its people speak, not a second king")
            elif mode in ("envoy", "meeting", "visit") and snap.target_country and snap.target_country.ruler:
                c = snap.target_country
                host = Person(name=c.ruler, role=f"ruler of {c.long_name or c.name}", court=c.long_name or c.name,
                              religion=c.religion, is_ruler=True)
                speakers = [host] + [p for p in speakers if p is not None and p.name and p.name != c.ruler]
            if mode == "decree":
                speakers = []
            elif mode in ("council", "estate"):
                speakers = [p for p in snap.court if p.in_cabinet or p.is_general][:4] or snap.court[:3]
            foreign = ""
            if snap.target_country:
                foreign = lore.realm_brief(self.world, self.codex, snap.target_country.tag, geo=self.geo)
            session.cast = [p.name for p in speakers if p is not None and p.name]
            session.moods = {}
            # Made to measure: the laws of the realm where they are the business of
            # the scene (the council, an estate, a decree) and otherwise only once
            # the talk turns to them; the narration rules with the outcome, since
            # the turns of a conversation never narrate.
            digest = self._digest(snap)
            session.codex_later = "" if mode in self._LAW_MODES else digest
            session.narrated = False
            focus = tuple(session.cast) + ((snap.target_person.name,) if snap.target_person else ())
            # A conversation is played by people (the actor's prompt: the realm as the
            # court sees it, no figures, no game terms, no catalogue); what it changes
            # in the game is judged apart, by the referee. A decree is written by the
            # chancery with the game's rules in hand, as before.
            acting = mode != "decree"
            changes = diff(self.previous, snap)
            # Those who govern get the ministers' dossier: the real troubles and
            # strengths of the state, and what the state can actually do.
            governs = mode in ("council", "estate") or (
                mode in ("talk", "petition", "reply") and snap.target_person is not None
                and (snap.target_person.in_cabinet or snap.target_person.is_ruler))
            dossier = prompts.render_dossier(snap, prompts.state_measures()) if acting and governs else ""
            session.system = prompts.system_prompt(
                language=self.cfg.language, snap=snap, mode=mode,
                codex_digest=digest if mode in self._LAW_MODES and not acting else "",
                narrator=False, summon=mode != "decree", in_world=acting, dossier=dossier,
                memory_brief=self.memory.brief(
                    focus=focus, scene_tag=snap.target_country.tag if snap.target_country else "",
                    scene_estate=ESTATES[agenda - 1] if mode == "estate" else "") if self.memory else "",
                changes_text=prompts.render_changes(changes, in_world=acting),
                diplomatic=diplomatic, currencies=_currencies_for(snap),
                world_text=self._papers(self._world_text(snap)) if acting else self._world_text(snap),
                foreign_text=self._papers(foreign) if acting else foreign,
                realm_profile=self._ensure_realm_profile(snap), difficulty=self.cfg.difficulty,
                personas=self._ensure_personas(snap, speakers), setting=self._setting(snap),
                works=self._works_text() if mode == "decree" else "",
                schema=prompts.decree_schema() if mode == "decree" else None,
            )
            if mode == "decree":
                # the realm's provinces by their names: a decree may rename them, convert them...
                lands = self._lands_text(snap)
                if lands:
                    session.system += "\n\n" + lands
            if mode in ("envoy", "meeting", "visit") and snap.target_country and not own_crown(snap):
                # what that court could really do, and how a war would really go - read afresh
                if self._outmatched(snap):
                    session.proud = random.random() < self.PROUD_CHANCE
                session.system += "\n\n" + self._their_means(snap, session.proud)
            if acting:
                session.referee = prompts.referee_prompt(
                    snap=snap, codex_digest=digest, pacts=self.memory.pacts_brief() if self.memory else "",
                    diplomatic=diplomatic, currencies=_currencies_for(snap), foreign_text=foreign,
                    difficulty=self.cfg.difficulty, war_text=self._war_report(snap), scale=self._scale_text(snap),
                    lands=self._lands_text(snap), works=self._works_text(), setting=self._setting(snap))
                balance = self._balance_text(snap)
                if balance:
                    session.referee += "\n\n" + balance
                standing = MS.brief(self.memory.measures) if self.memory else ""
                if standing:
                    session.referee += "\n\n" + standing
                self._ui("set_hint", CLOSE_HINT)
            session.messages = [{"role": "system", "content": session.system}]
            if mode == "decree":
                # Nobody speaks: the ruler writes, the chancery proclaims.
                self._ui("add_note", prompts.DECREE_INVITE)
                self._ui("set_suggestions", [])
                self._ui("set_close_label", "Close")
                self._ui("set_busy", "")
                return
            lang = prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language)
            if mode in PLAYER_SPEAKS_FIRST:
                # The ruler sent for them: the ruler has the first word. The
                # scene is set in the panel; the model hears it with the
                # ruler's first line.
                session.opener = prompts.opener(mode, estate=estate_name, note=note)
                self._ui("add_note", _waiting_note(mode, snap, estate_name))
                self._ui("set_busy", "")
                self._ui("offer_suggestions", True)
                return
            session.messages.append({"role": "user",
                                     "content": prompts.opener(mode, estate=estate_name, note=note)
                                     + "\n\n" + _beats(session, opening=True)
                                     + f"\n(Write it all, gestures included, in {lang}.)"})
            if mode in self.PART_KINDS:
                self._prepare_part(session, "(nothing yet: they speak first - and they do not know why the ruler has "
                                                    "come; their aim is their own, never a guess at the ruler's)")
            self._turn(session)

        self._submit(prepare)

    def _header_for(self, mode: str, snap: Snapshot, estate_name: str) -> Header:
        label = KIND_LABEL.get(mode, "")
        if mode == "biographer":
            bio = self.memory.biographer if self.memory else None
            if bio:
                return Header(label, bio.get("name", ""), bio.get("origin", ""))
            return Header(label, snap.long_name or snap.name, snap.date)
        if mode in ("talk", "reply", "petition") and snap.target_person:
            p = snap.target_person
            sub = " · ".join(x for x in (p.role, p.court) if x)
            return Header(label, p.name, sub)
        if mode in ("envoy", "meeting", "visit") and snap.target_country:
            c = snap.target_country
            return Header(label, c.long_name or c.name, f"{c.ruler} · {c.religion}")
        if mode == "progress" and snap.target_place:
            pl = snap.target_place
            return Header(label, pl.name, f"{pl.province} · {pl.culture} · {pl.religion}")
        if mode == "estate":
            return Header(label, estate_name, snap.long_name or snap.name)
        return Header(label, snap.long_name or snap.name, snap.date)

    # The version of the mod this Court Brain carries (votc_v_modver in votc_runtime_values.txt).
    MOD_VERSION = 710

    def _check_mod_version(self, loaded: int) -> None:
        """The game reports the version of the mod it loaded: an older one (the mod was updated while
        the game ran, or the game was not restarted) does not know what this Court Brain sends - said
        once, plainly, so the player restarts the game before it goes wrong."""
        if loaded >= self.MOD_VERSION or getattr(self, "_mod_warned", False):
            return
        self._mod_warned = True
        self.log("WARNING: EU5 is running an older version of the mod than this Court Brain. Close EU5 and "
                 "start it again (with Start EU5): until then some orders will not work and the game may crash.")
        self._ui("add_note", "✖ EU5 is running an older version of the mod: close the game and start it again "
                             "with Start EU5.", "bad")

    def _lang(self) -> str:
        return prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language)

    def _turn(self, session: Session) -> None:
        if session is not self.session or session.closed:
            return
        self._ui("set_busy", "…")
        if session.kind == "biographer":
            schema = prompts.biographer_turn_schema()
        elif session.referee:
            schema = prompts.actor_turn_schema(diplomatic=session.diplomatic)
        else:
            schema = prompts.turn_schema(diplomatic=session.diplomatic)
        data = self.client.complete_json(session.messages, schema, schema_name="votc_turn")
        if session is not self.session or session.closed:
            return
        # The voice models fall back to - acknowledge, validate, agree, add a
        # caveat, close on a maxim - is the one thing a person at court never
        # sounds like. Caught in the text, it is rewritten once (tokens are spent
        # only when it happens).
        tame = _assistant_voice(data.get("lines") or [])
        written = "" if tame else _written_voice(data.get("lines") or [])
        if written:
            self.log(f"  rewritten: it sounded written, not spoken ({written})")
        if tame or written:
            if tame:
                self.log(f"  rewritten: it sounded like an assistant ({tame})")
            redo = (f"That sounds like a helpful assistant, not a person ({tame}). Write this answer again "
                    "as the people they are: react to what touched them most, in their own words - no "
                    "acknowledging or validating first, no 'yes, though...', no closing maxim. They may be "
                    "shaken, blunt, evasive, moved or wrong. Same facts, same consequences.") if tame else (
                    f"That reads as written prose, not speech ({written}). Say the same things again the way these "
                    "people would SAY them (see HOW IT SOUNDS): short sentences, full stops, plain words, start "
                    "with the thing itself, leave out what both know, no crafted or quotable line, no sum-up. "
                    "Same people, same content, same decisions, same gestures - only how it is said changes.")
            redo += f" Write it all, gestures included, in {self._lang()}."
            try:
                again = self.client.complete_json(
                    session.messages + [
                        {"role": "assistant", "content": "\n".join(
                            f"{l.get('speaker', '')}: {l.get('text', '')}" for l in data.get("lines") or []
                            if isinstance(l, dict))},
                        {"role": "user", "content": redo}],
                    schema, schema_name="votc_turn")
                if session is not self.session or session.closed:
                    return
                if again.get("lines"):
                    data = again
            except Player2Error:
                pass
        # The scenes richest in English papers (the council: laws, dossiers, the parts prepared in
        # English) sometimes answer in English: said again, once, in the player's language.
        if _wrong_language(data.get("lines") or [], self.cfg.language):
            self.log(f"  rewritten: it came out in English, not in {self._lang()}")
            try:
                again = self.client.complete_json(
                    session.messages + [
                        {"role": "assistant", "content": "\n".join(
                            f"{l.get('speaker', '')}: {l.get('text', '')}" for l in data.get("lines") or []
                            if isinstance(l, dict))},
                        {"role": "user", "content": (
                            f"That is in English. Say exactly the same again - same people, same words and "
                            f"gestures, same decisions - but in {self._lang()}, every line and gesture.")}],
                    schema, schema_name="votc_turn")
                if session is not self.session or session.closed:
                    return
                if again.get("lines"):
                    data = again
            except Player2Error:
                pass
        session.turns += 1
        raw_lines = [l for l in (data.get("lines") or []) if isinstance(l, dict) and str(l.get("text", "")).strip()]
        # Models sometimes split one speech into several lines by the same
        # speaker; in the panel that reads as a stutter, so they are joined.
        lines: list[dict[str, str]] = []
        ruler_name = (session.snap.ruler.name if session.snap.ruler else "").strip().lower()
        for line in raw_lines[:5]:
            speaker, text = _t(line.get("speaker"), 90), _cap(line.get("text"), 1000)
            speaker = _cast_name(speaker, session.cast)
            gesture = _bare_gesture(_t(line.get("gesture"), 240), speaker)
            if ruler_name and speaker.strip().lower() in (ruler_name, "il sovrano", "the ruler", "you"):
                continue            # the ruler is the player: nobody speaks for them
            if lines and lines[-1]["speaker"] == speaker:
                lines[-1]["text"] += " " + text
            else:
                lines.append({"speaker": speaker, "text": text, "gesture": gesture})
        # A title in every speech is a machine's courtesy: a real court uses it
        # now and then. One speech in three turns may keep it; the others lose it.
        for line in lines:
            if _ADDRESS.search(line["text"]) or _OPENING_ADDRESS.match(line["text"]):
                if session.turns - session.addressed_turn <= 2:
                    line["text"] = _drop_address(line["text"])
                else:
                    session.addressed_turn = session.turns
        # Gestures are rare, and never the same motion or object twice in a scene:
        # at most one per turn, none for someone who gestured in their last turns.
        seen_gesture = False
        for line in lines:
            g, who = line.get("gesture", ""), line["speaker"]
            words = _gesture_words(g)
            if (not g or seen_gesture or _STOCK_GESTURE.search(g) or words & session.used_props
                    or session.turns - session.gestured.get(who, -9) <= 2):
                line["gesture"] = ""
            else:
                seen_gesture = True
                session.used_props |= words
                session.gestured[who] = session.turns
        for line in lines:
            if _SLIP.search(line["text"]) and line["speaker"] not in session.slips:
                session.slips.append(line["speaker"])
            head = " ".join(line["text"].split()[:2])
            if head:
                session.openings = (session.openings + [head])[-6:]
            # A pet phrase said twice is already a tic: it goes on the list of things not to say again.
            before = session.spoken.get(line["speaker"], [])
            for phrase in _shared_phrases(line["text"], before):
                entry = f'"{phrase}" ({line["speaker"].split()[0]})'
                if entry not in session.worn:
                    session.worn = (session.worn + [entry])[-8:]
            session.spoken[line["speaker"]] = (before + [line["text"]])[-4:]
        said = []
        for line in lines[:4]:
            speaker = line["speaker"]
            text = line["text"]
            gesture = line.get("gesture", "")
            role = ""
            if speaker and speaker not in session.introduced:
                # The first time someone speaks, say who they are beside the name.
                session.introduced.add(speaker)
                role = self._role_of(session.snap, speaker)
            self._ui("add_line", speaker, text, gesture, role)
            shown = f"({gesture}) {text}" if gesture else text
            said.append(f"{speaker}: {shown}" if speaker else shown)
            session.transcript.append(f"{speaker}: {text}")
            if self.cfg.tts_enabled:
                self.client.speak(text, self.cfg.tts_voice_id)
        inner = [i for i in (data.get("inner") or []) if isinstance(i, dict) and i.get("name")][:4]
        stand = " | ".join(f"{_t(i.get('name'), 40)}: {_t(i.get('feeling'), 60)}; unsaid: {_t(i.get('unsaid'), 90)}; "
                           f"wants {_t(i.get('wants'), 80)}; played: {_t(i.get('tactic'), 30)}" for i in inner)
        # How they stand now goes back with the lines, so the next turn moves on from it.
        session.messages.append({"role": "assistant", "content": (f"[how they stand: {stand}]\n" if stand else "")
                                 + ("\n".join(said) or "...")})

        if session.referee:
            # Nothing is judged while they talk: the referee reads the whole conversation once,
            # when the ruler dismisses them (see _judge_scene) - one call instead of one for
            # every order - and the consequences come with the event that follows.
            ordered = bool(_DECISIVE.search(self._last_ruler_words(session)))
            if data.get("stakes") or ordered:
                session.weighty = True
            if ordered and (not session.noted_turn or session.turns - session.noted_turn >= 3):
                # heard as an order: said quietly, now and then, when it will take effect
                session.noted_turn = session.turns
                self._ui("add_note", NOTED_NOTE)
            judged: dict[str, Any] = {}
        else:
            judged = data
            self._collect_actions(session, judged)
            if session.diplomatic:
                self._collect_decisions(session, judged)
            self._collect_state_changes(session, judged.get("state_changes") or [])
            self._collect_power(session, judged.get("power_moves") or [])
            self._collect_pacts(session.snap, judged.get("pacts") or [])
            self._touch_pacts(session.snap, judged.get("pacts_touched") or [])
            self._collect_territory(session, judged.get("territory") or [])
            self._collect_works(session, judged.get("works") or [])
        self._summon(session, data.get("summoned") or [])
        if session.kind == "biographer":
            self._biographer_turn(session, data)
        self._remember(data, session.snap)
        why = _t(judged.get("reasoning"), 300)
        if why:
            self.log(f"  why: {why}")
        self._ui("set_busy", "")
        if data.get("concluded") and session.turns > 1:
            self._ui("add_note", "They have nothing more to say. You may dismiss them.")
        self._ui("offer_suggestions", True)

    # A biographer's opinion said outright (outside quoted speech) - the reader
    # must sense it, never be told it.
    _VERDICT = re.compile(
        r"\b(for my (own )?part|in my (own )?(view|opinion|judge?ment|eyes)|to my mind|i (confess|judge|believe|"
        r"think|deem|hold that|must say|cannot praise|will not praise|would say|count (it|this|the)|call (it|this))|"
        r"per parte mia|a mio (avviso|giudizio|parere)|confesso|io ritengo|io giudico)\b", re.I)
    _QUOTED = re.compile(r"[\"\u201c\u00ab][^\"\u201d\u00bb]{0,600}[\"\u201d\u00bb]")

    def _verdict(self, text: str) -> str:
        m = self._VERDICT.search(self._QUOTED.sub("", text or ""))
        return m.group(0) if m else ""

    def _indirect(self, messages: list[dict[str, str]], data: dict[str, Any], key: str, schema: dict[str, Any],
                  schema_name: str, max_tokens: int) -> dict[str, Any]:
        """Once, if the biographer gave their opinion outright: the same text, judged only by the telling."""
        text = str(data.get(key) or "")
        found = self._verdict(text)
        if not found:
            return data
        self.log(f"  the biographer said what they think ('{found}'): rewritten so it only shows")
        try:
            again = self.client.complete_json(
                messages + [{"role": "assistant", "content": text},
                            {"role": "user", "content": prompts.BIOGRAPHER_INDIRECT_FIX.format(found=found)}],
                schema, schema_name=schema_name, temperature=0.7, max_tokens=max_tokens)
        except Player2Error:
            return data
        if len(str(again.get(key) or "")) > len(text) * 0.6:
            return {**data, **again}
        return data

    # ------------------------------------------------------ the age and the scale
    def _world_of(self, snap: Snapshot) -> tuple[Any, Any]:
        w = self.world
        cid = w.by_tag.get(snap.tag) if w is not None else None
        return w, cid

    def _scale_text(self, snap: Snapshot) -> str:
        """How big the realm is, and so who comes to court and what is at stake."""
        w, cid = self._world_of(snap)
        subjects, colonies, overlord, great = [], 0, "", False
        if w is not None and cid is not None:
            for sub_id, kind in w.subjects_of(cid):
                if "colon" in kind:
                    colonies += 1
                else:
                    subjects.append(self._cname(w.tag(sub_id)))
            over = w.overlord_of(cid)
            overlord = self._cname(w.tag(over[0])) if over else ""
            great = any(c.cid == cid for c in w.great_powers(8))
        elif snap.n("issubject"):
            overlord = "a greater lord"
        return prompts.render_scale(locations=snap.n("locations"), subjects=subjects, colonies=colonies,
                                    overlord=overlord, great_power=great, rank=snap.rank)

    def _setting(self, snap: Snapshot) -> str:
        """The age as this world has it, and the scale of the realm: read from the
        game each time, so the court changes as the world and the realm do."""
        year = (gamedate.parse(snap.date) or (1337, 1, 1))[0]
        w, cid = self._world_of(snap)
        age = ""
        if w is not None:
            # the save may be older than the game: the later of its age and the year's
            order = [k for k, _y, _n in prompts.AGES]
            by_year = prompts.age_of(year)[0]
            age = max((w.age, by_year), key=lambda k: order.index(k) if k in order else -1)
        realm_inst = w.countries[cid].institutions if w is not None and cid in (w.countries or {}) else None
        era = prompts.render_era(year=year, age=age, world_inst=w.institutions if w is not None else None,
                                 realm_inst=realm_inst, situations=w.situations if w is not None else None)
        character = prompts.realm_character(snap, great_power=self._great(snap), peer_gold=self.peer_gold(),
                                            great_block=self._great_block(snap))
        goods = TR.realm_goods_text(w, self.codex) if w is not None else ""
        return (era + "\n\n" + self._scale_text(snap) + ("\n\n" + character if character else "")
                + ("\n\n" + goods if goods else ""))

    # ------------------------------------------------------- land changing hands
    @property
    def geo(self):
        if getattr(self, "_geo", None) is None:
            from .geography import Geography
            self._geo = Geography(self.cfg.game_dir, self.cfg.state_dir, self.cfg.game_language)
        return self._geo

    def _lands_text(self, snap: Snapshot) -> str:
        """The provinces of the realms in this scene, by their exact names - so land can
        be named when it changes hands."""
        w = self.world
        if w is None or not w.owners:
            return ""
        tags = [snap.tag] + ([snap.target_country.tag] if snap.target_country else [])
        out = []
        for tag in dict.fromkeys(t for t in tags if t):
            cid = w.by_tag.get(tag)
            if cid is None:
                continue
            held = self.geo.holdings(w.owners, cid, limit=30)
            if held:
                out.append(f"- {self._cname(tag)} ({tag}): " + "; ".join(held))
        if not out:
            return ""
        return ("THE LANDS OF THE REALMS (provinces held, with their area; for \"territory\" use these exact names, "
                "or an area's name for the whole of it):\n" + "\n".join(out))

    def _cession(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """A cession checked against the map and, when a save is known, against who holds what."""
        src, dst = self._known_tag(raw.get("from_tag")), self._known_tag(raw.get("to_tag"))
        if not src or not dst or src == dst:
            self.log(f"  land not moved: unknown realms ({raw.get('from_tag')} -> {raw.get('to_tag')})")
            return None
        place, scope = _t(raw.get("place"), 80), str(raw.get("scope") or "")
        w = self.world
        number = {k: i + 1 for i, k in enumerate(self.geo.index)} if w is not None and w.owners else {}
        giver = w.by_tag.get(src) if number else None
        if scope == "location":
            # The lands the court was shown are provinces: a town that gives its name to a
            # province the giver holds means the province, lands and all.
            pkey, plocs = self.geo.resolve(place, "province")
            held = [k for k in plocs if giver is not None and w.owners.get(number.get(k, 0)) == giver]
            if pkey and self.geo.name(pkey).strip().lower() == place.strip().lower() and len(held) > 1:
                scope = "province"
        key, locs = self.geo.resolve(place, scope)
        if not locs:
            self.log(f"  land not moved: no place called \"{_t(raw.get('place'), 80)}\" on the map")
            return None
        w = self.world
        if w is not None and w.owners and w.by_tag.get(src) is not None:
            number = {k: i + 1 for i, k in enumerate(self.geo.index)}
            cid = w.by_tag[src]
            held = [k for k in locs if w.owners.get(number.get(k, 0)) == cid]
            if not held:
                self.log(f"  land not moved: {self._cname(src)} does not hold {self.geo.name(key)}")
                return None
            locs = held
        locs = locs[:300]
        label = (f"Land: {self.geo.name(key)} ({len(locs)} place{'s' if len(locs) != 1 else ''}) passes from "
                 f"{self._cname(src)} to {self._cname(dst)}, as a core, fully integrated")
        return {"from": src, "to": dst, "key": key, "locs": locs, "label": label,
                "terms": _t(raw.get("terms"), 300)}

    def _collect_territory(self, session: Session, raws: list[Any]) -> None:
        for raw in [r for r in raws if isinstance(r, dict)][:3]:
            c = self._cession(raw)
            if c is None or any(x["key"] == c["key"] and x["to"] == c["to"] for x in session.cessions):
                continue
            yes, why = self._would_cede(c, session)
            if not yes:
                self.log(f"  land not moved: {why}")
                session.refused.append(f"{c['label']} - not given up: {why}")
                self._ui("add_note", f"\u2716 {self._cname(c['from'])} does not give up {c['label'].split(' (')[0][6:]}: "
                                     f"{why}.", "bad")
                session.messages.append({"role": "user", "content": (
                    f"(News: {self._cname(c['from'])}'s court will not give up that land - {why}.)")})
                continue
            session.cessions.append(c)
            self._ui("add_note", f"⚑ {c['label']} - when you confirm the outcome.", "good")
            self.log(f"  {c['label']}")
            if self.memory:
                self.memory.remember_event(session.snap.date, "territory", f"{c['label']}. Agreed: {c['terms']}")
            self._book_note(session.snap.date, f"[agreement] {c['label']} - {c['terms']}", weight=2)

    def _story_integration(self, raw: Any) -> dict[str, Any] | None:
        """A story choice that binds a place closer to the realm: checked against the map as the
        "integrate" work is (the game checks that the realm holds it and that it is not a core)."""
        if not isinstance(raw, dict) or not str(raw.get("place") or "").strip():
            return None
        try:
            size = int(raw.get("size") or 0)
        except (TypeError, ValueError):
            return None
        if size < 1 or self.works_catalog is None:
            return None
        work, why = W.validate({"kind": "integrate", "where": raw["place"], "size": min(3, size), "what": "",
                                "scope": ""}, self.works_catalog, self.geo)
        if work is None:
            self.log(f"  place not bound: {why}")
        return work

    @staticmethod
    def _cession_marks(cessions: list[dict[str, Any]], set_: str) -> list[str]:
        lines = [f"votc_clear_cession_set = {{ set = {set_} }}"]
        for c in cessions:
            for key in c["locs"]:
                if A._token_ok(key):
                    lines.append(f"location:{key} = {{ votc_cede_mark = {{ from = c:{c['from']} to = c:{c['to']} "
                                 f"set = {set_} }} }}")
        return lines

    _HEAVY = ("actions", "state_changes", "power_moves", "pacts", "ruler_decisions", "territory", "works",
              "measures")

    @staticmethod
    def _scene_talk(session: Session, limit: int = 14000) -> str:
        """The conversation as it was said, for judging: whole when it fits; otherwise every
        line of the ruler with what was said around it, and the end of the talk."""
        lines = [_cap(t, 700) for t in session.transcript]
        whole = "\n".join(lines)
        if len(whole) <= limit:
            return whole
        keep = set(range(max(0, len(lines) - 10), len(lines)))
        for i, t in enumerate(lines):
            if t.startswith("The ruler: "):
                keep |= {i - 1, i, i + 1}
        out: list[str] = []
        last = -2
        for i in sorted(k for k in keep if 0 <= k < len(lines)):
            if i != last + 1:
                out.append("[...]")
            out.append(lines[i])
            last = i
        return "\n".join(out)[-limit:]

    @staticmethod
    def _plain(text: str) -> str:
        return " ".join(str(text or "").lower().split()).strip(" .,;:!?\"'\u201c\u201d\u00ab\u00bb")

    def _scene_decisions(self, session: Session) -> list[dict[str, Any]]:
        """What the ruler decided in the whole conversation, each with their exact words -
        asked apart, in a few hundred tokens, before the referee is called at all. A quote
        that is not in the ruler's own lines does not count."""
        ruler = [self._plain(t[len("The ruler: "):]) for t in session.transcript if t.startswith("The ruler: ")]
        if not ruler:
            return []
        try:
            data = self.client.complete_json(
                [{"role": "user", "content": prompts.DECISIONS_CHECK.format(talk=self._scene_talk(session, 9000))}],
                prompts.DECISIONS_SCHEMA, schema_name="votc_decision", temperature=0.0, max_tokens=600)
        except Player2Error:
            return []                     # when in doubt, nothing happens
        out = []
        for d in data.get("decisions") or []:
            if not isinstance(d, dict):
                continue
            words = self._plain(d.get("words"))
            if len(words.split()) >= 2 and any(words in line for line in ruler):
                out.append({"words": _t(d.get("words"), 300), "formal": bool(d.get("formal"))})
            elif words:
                self.log(f'  not the ruler\'s own words, set aside: "{words[:80]}"')
        return out[:8]

    def _ordered(self, session: Session, raw: Any) -> bool:
        """Did the ruler order this in so many words? (their words, checked against their lines)"""
        words = self._plain(raw.get("ruler_words") if isinstance(raw, dict) else "")
        ruler = [self._plain(t[len("The ruler: "):]) for t in session.transcript if t.startswith("The ruler: ")]
        if len(words.split()) >= 2 and any(words in line for line in ruler):
            return True
        self.log("  set aside: the ruler never ordered it in so many words")
        return False

    def _judge_scene(self, session: Session) -> None:
        """When the ruler dismisses them: the whole conversation is judged once, and what was
        decided becomes the game's consequences - in the event that follows. Nothing is
        decided while they talk."""
        ruler_lines = [t[len("The ruler: "):] for t in session.transcript if t.startswith("The ruler: ")]
        if not ruler_lines:
            return
        pacts_open = bool(self.memory and self.memory.pacts_brief())
        if not (session.weighty or pacts_open or any(_DECISIVE.search(x) for x in ruler_lines)):
            self.log("  nothing was decided in this conversation: it changes only the mood")
            return
        decisions = self._scene_decisions(session)
        if not decisions and not pacts_open:
            self.log("  the ruler decided nothing: no consequences")
            return
        words = " / ".join(ruler_lines)
        target = session.snap.target_country
        wars = self._war_views(" ".join(session.transcript[-14:]), session.snap,
                               target.tag if session.diplomatic and target is not None else "")
        listed = "\n".join(f'- "{d["words"]}" - ' + ("FORMAL: an order of a concrete measure" if d["formal"]
                                                     else "a stance or a wish, not yet an order")
                           for d in decisions) or "- none"
        ask = ("THE WHOLE CONVERSATION, NOW OVER (\"The ruler\" is the player):\n" + self._scene_talk(session)
               + "\n\nTHE RULER'S OWN DECISIONS IN IT (checked against their words):\n" + listed
               + "\n\nJudge the conversation as a whole, now that the ruler has dismissed them: what the ruler "
                 "decided changes the game, each decision once; where they changed their mind, their last word "
                 "counts. Only a FORMAL decision may bring measures (laws, money, men, land, war and peace, works, "
                 "changes of the state, power over people, pacts); a stance moves only how people regard the "
                 "ruler. A proposal the ruler did not accept in plain words is nothing. (This replaces any rule "
                 "about judging only the last exchange.)"
               + _reality_hints(words)
               + ("\n\n" + wars.strip() if wars else "")
               + ("\n\nLAND NAMED IN THIS TALK (what it is to its lord; a cession needs its price paid "
                  "- in \"actions\", as gold_loss - or real pressure; the game checks it): " + "; ".join(
                      f"{self.geo.name(k)}: {v['kind']}, {v['here']} of {v['total']} places"
                      for k, v in list(session.lands.items())[:3]) if session.lands else ""))
        data = None
        for last_try in (False, True):
            try:
                data = self.client.complete_json(
                    [{"role": "system", "content": session.referee}, {"role": "user", "content": ask}],
                    prompts.referee_schema(diplomatic=session.diplomatic), schema_name="votc_referee",
                    temperature=0.3, max_tokens=2500)
                break
            except Player2Error as exc:
                if last_try or not exc.retryable:
                    self.log(f"  the referee could not judge this conversation: {exc}")
                    self._ui("add_note", "✖ The AI could not be reached to weigh what was decided: this "
                                         "conversation changes nothing in the game.", "bad")
                    return
                # The AI is overloaded: what the ruler decided is not thrown away - wait, and ask again.
                self.log(f"  the AI is overloaded ({exc.status}): the referee tries again in a minute")
                self._ui("set_busy", "The AI is overloaded: weighing what was decided again in a minute…")
                time.sleep(60)
        if not isinstance(data, dict):
            return
        heavy = sum(len(data.get(k) or []) for k in self._HEAVY)
        if data.get("ruler_decided") and heavy and not any(d["formal"] for d in decisions):
            # A stance, a goal or a vision: it may move how people regard the
            # ruler, never the laws, the treasury or the realm's commitments.
            kept = [a for a in data.get("actions") or [] if isinstance(a, dict) and a.get("kind") in self._REACTIONS]
            dropped = (len(data.get("actions") or []) - len(kept)
                       + sum(len(data.get(k) or []) for k in self._HEAVY if k != "actions"))
            if dropped:
                self.log(f"  {dropped} consequence(s) set aside: the ruler stated an aim, but ordered no measure")
            data = {**data, "actions": kept, "state_changes": [], "power_moves": [], "pacts": [],
                    "ruler_decisions": [], "territory": [], "works": [], "measures": []}
        if data.get("ruler_decided"):
            data = self._reality(session, data, words)
        else:
            dropped = sum(len(data.get(k) or []) for k in self._HEAVY)
            if dropped:
                self.log(f"  {dropped} consequence(s) set aside: the ruler decided nothing")
            data = {k: v for k, v in data.items() if k in ("reasoning", "pacts_touched", "ruler_decided", "threats")}
        if data.get("ruler_decided") and self.memory is not None and any(d["formal"] for d in decisions):
            self.memory.judged(session.snap.date, "audience", data.get("ruler_wisdom", 0))
        # Ordinary statecraft is never an outrage: how grave it can be, and what weighs its price.
        kinds = {d.get("kind") for d in data.get("ruler_decisions") or [] if isinstance(d, dict)}
        war_tags = [target.tag] if target is not None and kinds & {"declare_war", "press_claim"} else []
        cap, statecraft = self._gravity_cap(session.snap, kinds, data.get("works") or [],
                                            data.get("power_moves") or [], war_tags)
        if int(data.get("gravity") or 0) > cap:
            self.log(f"  gravity {data.get('gravity')} -> {cap}: ordinary statecraft ({statecraft}), not an outrage")
            data = {**data, "gravity": cap}
        session.balance = {"gravity": int(data.get("gravity") or 0), "statecraft": statecraft,
                           "brings_money": self._brings_money(words)}
        self._collect_actions(session, data)
        if session.diplomatic:
            self._collect_decisions(session, data)
        self._collect_state_changes(session, data.get("state_changes") or [], direct=True)
        self._collect_power(session, data.get("power_moves") or [], direct=True)
        self._collect_pacts(session.snap, data.get("pacts") or [])
        self._touch_pacts(session.snap, data.get("pacts_touched") or [])
        self._collect_territory(session, data.get("territory") or [])
        self._collect_works(session, data.get("works") or [])
        self._collect_standing(session, data.get("measures") or [])
        self._collect_threats(session, data.get("threats") or [])
        self._collect_trade(session, data.get("trade") or [])
        if int(data.get("gravity") or 0) >= R.GRAVE:
            sub = data.get("substance") if isinstance(data.get("substance"), dict) else {}
            what = _t(sub.get("what_it_really_is"), 300)
            deed = (f"WHAT THE RULER REALLY DID (plainly, without their framing): {what}" if what
                    else "\n".join(session.transcript[-6:]))
            done = self._reactions(session.snap, deed, self._applied_text(session.acts, session.decisions,
                                                                          session.works),
                                   cap=cap, war_on=frozenset(war_tags))
            if int(data.get("gravity") or 0) >= 7:
                self._raise_suspicion(int(data.get("gravity") or 0) - 5, "an outrage")
            if done:
                session.reactions.append(done)
        why = _t(data.get("reasoning"), 300)
        if why:
            self.log(f"  why: {why}")

    @staticmethod
    def _last_ruler_words(session: Session) -> str:
        return next((t[len("The ruler: "):] for t in reversed(session.transcript) if t.startswith("The ruler: ")), "")

    # How likely "likely" is: the referee judges the odds from concrete things; the
    # dice only decide within them. "certain" is never rolled.
    ODDS = {"certain": 1.0, "likely": 0.8, "even": 0.5, "unlikely": 0.2, "none": 0.0}
    ODDS_TEXT = {"likely": "likely", "even": "it could go either way", "unlikely": "unlikely",
                 "none": "it cannot be done"}
    _MOOD = ("opinion", "trust_gain", "trust_loss", "character_modifier", "favors", "prestige", "legitimacy",
             "estate", "all_estates", "stability", "devotion", "republican_tradition")

    def _in_this_world(self, data: dict[str, Any], words: str) -> str:
        """possible / only_as_belief / impossible - the referee's judgement, and never
        "possible" for a machine no age of this game has known."""
        sub = data.get("substance") if isinstance(data.get("substance"), dict) else {}
        world = str(sub.get("in_this_world") or "possible")
        if world == "possible" and _NEVER_INVENTED.search(words or ""):
            return "impossible"
        return world if world in ("possible", "only_as_belief", "impossible") else "possible"

    def _keep_reactions(self, data: dict[str, Any]) -> dict[str, Any]:
        """Only people's reaction survives, and every cost: no gain, no work, no change."""
        acts = [a for a in data.get("actions") or [] if isinstance(a, dict) and a.get("kind")
                and (a.get("kind") in self._MOOD or not self._is_good(a))]
        return {**data, "actions": acts, "state_changes": [], "power_moves": [], "pacts": [], "territory": [],
                "works": [], "measures": self._ends(data)}

    def _reality(self, session: Session | None, data: dict[str, Any], words: str,
                 roll: bool = True) -> dict[str, Any]:
        """What the ruler decided, weighed on what it really is. Something this world
        does not have brings only people's reaction; a possible order is carried out
        as likely as the referee judged from concrete means - rolled, never decided by
        how it was said. When it goes wrong the costs stay, the gains go, and the world
        tells later what happened."""
        sub = data.get("substance") if isinstance(data.get("substance"), dict) else {}
        what = _t(sub.get("what_it_really_is"), 200)
        world = self._in_this_world(data, words)
        if world != "possible":
            self._note_attempt(what)
            self.log(f"  {'only a belief' if world == 'only_as_belief' else 'not possible in this world'}: "
                     f"{what or words[:120]} - only people's reaction counts")
            self._ui("add_note", "\u2716 " + ("No power of this world answers such an order: only how people take it "
                                          "counts." if world == "only_as_belief" else
                                          "This cannot be done in this world: only how people take it counts."),
                     "bad")
            return self._keep_reactions(data)
        odds = self._adjusted_odds(sub)
        if self._is_statecraft(data) and odds != "certain":
            # declaring a war, making a peace, a treaty the other court agreed: the ruler's to do. How the
            # war goes is fought out in the game, never decided here.
            self.log(f"  a war, a peace or an agreed treaty is the ruler's to make: carried out ({odds} was "
                     f"about what comes after)")
            odds = "certain"
        chance = self.ODDS.get(odds, 1.0)
        if not roll:
            return data
        why = _t(sub.get("why"), 200)
        if chance >= 1.0 or random.random() < chance:
            if chance < 1.0:
                self.log(f"  carried out ({odds}: {why})")
            self._deceit(session, sub, what)
            return data
        failed = _t(sub.get("if_it_fails"), 300) or "The order (" + (what or "the ruler's order") + ") went wrong."
        self.log(f"  it goes wrong ({odds}: {why}): {failed}")
        self._note_attempt(what)
        self._deceit(session, sub, what, failed=True)
        self._ui("add_note", "\u26a0 Things will not go as ordered - you will hear of it.", "bad")
        if session is not None:
            self._plan_followups(session.snap, [{"kind": "story", "after_days": random.randint(12, 35),
                                                 "about": f"What went wrong with the ruler's order "
                                                          f"({what or 'the order'}): {failed}"}], failed)
        kept = [a for a in data.get("actions") or [] if isinstance(a, dict) and a.get("kind")
                and not self._is_good(a)]
        return {**data, "actions": kept, "state_changes": [], "power_moves": [], "works": [],
                "measures": self._ends(data)}

    def _is_statecraft(self, data: dict[str, Any]) -> bool:
        kinds = {d.get("kind") for d in data.get("ruler_decisions") or [] if isinstance(d, dict)}
        treaty = data.get("treaty") if isinstance(data.get("treaty"), dict) else {}
        return bool(kinds & self._STATECRAFT) or treaty.get("kind") in self._STATECRAFT

    @staticmethod
    def _ends(data: dict[str, Any]) -> list[Any]:
        return [m for m in data.get("measures") or [] if isinstance(m, dict) and m.get("action") == "end"]

    # ------------------------------------------------ what cannot be talked into being
    _STEP_DOWN = {"certain": "likely", "likely": "even", "even": "unlikely", "unlikely": "none", "none": "none"}

    def _adjusted_odds(self, sub: dict[str, Any]) -> str:
        """The referee's odds - one step worse for every recent failure at the same thing
        (people have seen it tried), so trying again and again is no way round them."""
        odds = str(sub.get("odds") or "certain")
        odds = odds if odds in self.ODDS else "certain"
        for _ in range(min(2, self._failed_before(_t(sub.get("what_it_really_is"), 200)))):
            odds = self._STEP_DOWN[odds]
        return odds

    @staticmethod
    def _gist(text: str) -> set[str]:
        return {w for w in re.findall(r"\w{4,}", (text or "").lower())}

    def _failed_before(self, what: str) -> int:
        mem, snap = self.memory, self.snapshot
        if mem is None or snap is None or not what:
            return 0
        mine = self._gist(what)
        n = 0
        for a in mem.meta.get("attempts") or []:
            days = gamedate.days_between(a.get("date", ""), snap.date)
            other = self._gist(a.get("what", ""))
            if 0 <= days <= 730 and mine and other and len(mine & other) / len(mine | other) >= 0.4:
                n += 1
        return n

    def _note_attempt(self, what: str) -> None:
        mem, snap = self.memory, self.snapshot
        if mem is None or snap is None or not what:
            return
        mem.meta["attempts"] = ((mem.meta.get("attempts") or []) + [{"what": what, "date": snap.date}])[-12:]
        mem._meta_dirty = True

    def _suspicion(self) -> float:
        """How far the ruler's word is distrusted abroad and at home (0-10): it grows with
        deceit found out and with outrages, and fades by a point every half year."""
        mem, snap = self.memory, self.snapshot
        s = (mem.meta.get("suspicion") or {}) if mem is not None else {}
        days = gamedate.days_between(s.get("date", ""), snap.date if snap else "") if s else 0
        if not s or days >= 10_000:
            return float(s.get("value", 0)) if s else 0.0
        return max(0.0, float(s.get("value", 0)) - max(0, days) / 180)

    def _raise_suspicion(self, by: float, why: str) -> None:
        mem, snap = self.memory, self.snapshot
        if mem is None or snap is None:
            return
        mem.meta["suspicion"] = {"value": min(10.0, self._suspicion() + by), "date": snap.date}
        mem._meta_dirty = True
        self.log(f"  the ruler's word is trusted less ({why})")

    _FOUND_OUT = {"small": 0.25, "great": 0.6}

    def _deceit(self, session: Session | None, sub: dict[str, Any], what: str, failed: bool = False) -> None:
        """A lie, a forgery, a trap: it may work, and it may come to light later - the
        more hands and the more at stake, the likelier. A failed one is found out."""
        kind = str(sub.get("deceit") or "none")
        if kind not in self._FOUND_OUT or session is None:
            return
        if not failed and random.random() >= self._FOUND_OUT[kind]:
            return
        self._raise_suspicion(2.0 if kind == "small" else 4.0, "a deceit comes to light")
        self._plan_followups(session.snap, [{"kind": "story", "after_days": random.randint(20, 120),
                                             "about": f"The ruler's deceit comes to light: {what}. Those deceived "
                                                      f"learn who was behind it, and answer as they would."}],
                             what)

    def _power_ratio(self, snap: Snapshot) -> float:
        """Our strength against theirs, from armies and lands (1 = even)."""
        c = snap.target_country
        if c is None:
            return 1.0
        army = (snap.n("army") + 1) / (c.army + 1)
        lands = (snap.n("locations") + 1) / (c.locations + 1)
        return (army * lands) ** 0.5

    def _in_danger(self, snap: Snapshot) -> bool:
        """Is the other court at war with someone far greater than itself (not us)?"""
        c, w = snap.target_country, self.world
        cid = w.by_tag.get(c.tag) if (w is not None and c is not None) else None
        if cid is None:
            return False
        size: dict[int, int] = {}
        for owner in w.owners.values():
            size[owner] = size.get(owner, 0) + 1
        mine = size.get(cid, 0) or c.locations
        for war in w.wars_of(cid):
            enemies = war.defenders if cid in war.attackers else war.attackers
            if snap.tag in [w.tag(e) for e in enemies]:
                continue
            if mine and sum(size.get(e, 0) for e in enemies) > mine * 1.5:
                return True
        return False

    def _decree_treaty(self, raw: Any) -> dict[str, str] | None:
        """A union or vassalage already agreed with another court, carried out by decree at home:
        only with a pact with that court still standing (its consent), never on the decree alone.
        Or a war the decree itself declares."""
        if isinstance(raw, dict) and raw.get("kind") == "declare_war":
            return self._decree_war(raw)
        if not isinstance(raw, dict) or raw.get("kind") not in ("accept_vassalage", "union"):
            return None
        tag = str(raw.get("tag") or "").strip().upper()
        if not A._token_ok(tag) or self.memory is None:
            return None
        pact = next((p for p in self.memory.pacts.values() if p.get("tag") == tag
                     and p.get("status") not in ("broken", "refused", "closed")), None)
        if pact is None:
            self.log(f"  treaty dropped: no agreement with {tag} stands - a decree alone cannot make it the realm's")
            self._ui("add_note", "✖ A decree alone cannot make another realm yours: that is agreed with its "
                                 "court (a visit, an envoy, a meeting).", "bad")
            return None
        label = ("they come under your crown as a vassal" if raw["kind"] == "accept_vassalage"
                 else "they become part of your realm")
        self._ui("add_note", f"⚖ {pact.get('party', tag)}: {label}, as agreed - it happens with the decree "
                             f"(if the game allows it: a vassal smaller than your realm, a union at most half its "
                             f"size).", "good")
        return {"tag": tag, "kind": raw["kind"], "party": pact.get("party", tag), "label": label}

    def _decree_war(self, raw: dict[str, Any]) -> dict[str, str] | None:
        """A war proclaimed by decree: on a court the realm knows, not an ally, not already at war,
        not the realm's overlord (the game checks the truce and the rest)."""
        tag = self._known_tag(raw.get("tag"))
        snap = self.snapshot
        if not tag or snap is None or tag == snap.tag:
            self.log(f"  war by decree dropped: no known court {raw.get('tag')!r}")
            return None
        for kind, why in (("ally", "they are your ally"), ("war", "you are already at war with them"),
                          ("lord", "they are your overlord")):
            if self._in_live(kind, tag):
                self._ui("add_note", f"\u2716 No war can be declared on them: {why}.", "bad")
                return None
        cb = raw.get("cb") if raw.get("cb") in A.CASUS_BELLI else "cb_war_from_event"
        live = self._live_country(tag)
        name = getattr(live, "name", "") or tag
        self._ui("add_note", f"\u2694 War is declared on {name} with the decree.", "good")
        return {"tag": tag, "kind": "declare_war", "cb": cb, "party": name, "label": f"war is declared on {name}"}

    def _sealed_with(self, snap: Snapshot) -> bool:
        """Is there a pact with the other court still standing (not broken or refused)?"""
        c = snap.target_country
        if self.memory is None or c is None:
            return False
        return any(p.get("tag") == c.tag and p.get("status") not in ("broken", "refused", "closed")
                   for p in self.memory.pacts.values())

    def _is_our_subject(self, snap: Snapshot) -> bool:
        """Is the other court already a subject of the ruler's realm (from the world picture)?"""
        w, cid = self._world_of(snap)
        c = snap.target_country
        if w is None or cid is None or c is None:
            return False
        tid = w.by_tag.get(c.tag)
        return any(over == cid and sub == tid for over, sub, _k in getattr(w, "subjects", []))

    def _would_accept(self, kind: str, snap: Snapshot) -> tuple[bool, str]:
        """Would the other court really agree? Decided by the numbers - strength, their
        own dangers, rivalry, the ruler's name for honesty - never by how the case was
        put or by an envoy's "yes": a treaty needs their court. Orders the ruler alone
        gives (war, breaking an alliance, a guarantee, access) need nobody's consent."""
        n = snap.numbers
        r, danger, doubt = self._power_ratio(snap), self._in_danger(snap), self._suspicion()
        c = snap.target_country
        same_faith = bool(c and c.religion and c.religion == snap.religion)
        if kind == "white_peace":
            if r >= 0.8 or danger:
                return True, ""
            return False, "they are winning this war and will not settle for nothing"
        if kind == "take_submission":
            if n.get("target_at_war") and r >= 2.5:
                return True, ""
            return False, "they are not beaten enough to bow"
        if kind in ("accept_vassalage", "union"):
            # coming under another crown by agreement: a weaker realm that needs it, or is bound to it
            if n.get("target_rival"):
                return False, "they count you among their rivals"
            if doubt >= 5:
                return False, "they do not trust your word enough to put themselves in your hands"
            ours = self._is_our_subject(snap)
            # an agreement already sealed with them (a compact, an oath, terms of union) is consent given
            sealed = self._sealed_with(snap)
            if kind == "accept_vassalage":
                if (ours or r >= 3 or (r >= 1.5 and (danger or same_faith or sealed))
                        or (danger and r >= 1.0)):
                    return True, ""
                return False, "they are not weak or threatened enough to give up their freedom"
            if ((ours and r >= 1.3) or (r >= 2 and (danger or same_faith) and sealed)
                    or (r >= 3.5 and (danger or same_faith))):
                return True, ""
            return False, "they would bow as a vassal at most, not give up their state altogether"
        if kind in ("form_alliance", "swear_truce"):
            if n.get("target_rival"):
                return False, "they count you among their rivals"
            if doubt >= (5 if kind == "form_alliance" else 7):
                return False, "they do not trust your word"
            if kind == "swear_truce":
                return (True, "") if (r >= 0.5 or danger) else (False, "they have no need to bind their hands")
            if danger or r >= 1.25 or (r >= 0.6 and same_faith):
                return True, ""
            return False, "they gain too little from binding themselves to you"
        return True, ""

    # ------------------------------------------------------------ land and its price
    _TIERS_ORDER = ("none", "weak", "mild", "severe")
    _GREEDY = ("greed", "avar", "covet", "venal", "corrupt", "profligate")
    _PROUD = ("proud", "arrog", "ambiti", "stubborn", "zeal", "vain", "wrath", "martial")

    def _land_value(self, src_tag: str, locs: list[str]) -> dict[str, Any] | None:
        """What a piece of land is to the realm that holds it, from the map and the save:
        how much of the realm it is, and whether it lies in its heartland or far from it."""
        w = self.world
        cid = w.by_tag.get(src_tag) if w is not None and w.owners else None
        if cid is None:
            return None
        index, parent = self.geo.index, self.geo.parent

        def region(loc: str) -> str:
            return parent.get(parent.get(parent.get(loc, ""), ""), "")

        theirs = [index[n - 1] for n, owner in w.owners.items() if owner == cid and 0 < n <= len(index)]
        if not theirs:
            return None
        counts: dict[str, int] = {}
        for loc in theirs:
            counts[region(loc)] = counts.get(region(loc), 0) + 1
        top = max(counts.values())
        heart = {r for r, k in counts.items() if k >= max(top * 0.5, len(theirs) * 0.2)}
        where = region(locs[0]) if locs else ""
        share = len(locs) / len(theirs)
        kind = ("heartland" if where in heart or share > 0.25 else
                "small outlying" if share <= 0.06 else "outlying")
        return {"kind": kind, "share": share, "here": len(locs), "total": len(theirs),
                "region": self.geo.name(where) if where else "", "heart": ", ".join(self.geo.name(r) for r in heart)}

    def _ruler_temper(self, tag: str) -> tuple[int, str]:
        """(price step, traits) of another realm's ruler: the greedy sell cheaper, the proud dearer."""
        w = self.world
        cid = w.by_tag.get(tag) if w is not None else None
        country = w.countries.get(cid) if cid is not None else None
        ch = w.characters.get(country.ruler) if country is not None else None
        traits = [t for t in (ch.traits if ch is not None else []) if t]
        low = " ".join(traits).lower()
        step = (-1 if any(k in low for k in self._GREEDY) else 0) + (1 if any(k in low for k in self._PROUD) else 0)
        return step, ", ".join(t.replace("_", " ") for t in traits[:5])

    def _price_needed(self, value: dict[str, Any], snap: Snapshot) -> str:
        """The least payment (a tier of gold, or an exchange) that land could be bought for:
        'never' for a realm's heartland. Friends sell cheaper, rivals not at all, a poor
        treasury or a greedy ruler lowers the price, a proud one raises it."""
        if value["kind"] == "heartland":
            return "never"
        n, c = snap.numbers, snap.target_country
        if n.get("target_rival"):
            return "never"
        tier = 2 if value["kind"] == "small outlying" else 3
        if n.get("target_allied"):
            tier -= 1
        if c is not None and snap.n("gold") > 0 and c.gold < snap.n("gold") * 0.25:
            tier -= 1                              # an empty treasury needs the money
        tier += self._ruler_temper(c.tag)[0] if c is not None else 0
        tier += 1 if self._suspicion() >= 4 else 0
        if tier > 3:
            return "never"
        return self._TIERS_ORDER[max(2, tier)]         # land is never had for a trifle

    def _land_brief(self, session: Session, said: str) -> str:
        """When the ruler names land of the other court: what it is to them and what could
        buy it - for the people to bargain on the real stakes, never quoted as figures."""
        snap, w = session.snap, self.world
        c = snap.target_country
        if c is None or w is None or not w.owners or not said:
            return ""
        cid = w.by_tag.get(c.tag)
        if cid is None:
            return ""
        low = " " + re.sub(r"[^\w]+", " ", said.lower()) + " "
        held: dict[str, list[str]] = {}
        for n, owner in w.owners.items():
            if owner == cid and 0 < n <= len(self.geo.index):
                loc = self.geo.index[n - 1]
                for key in (self.geo.parent.get(loc, ""), self.geo.parent.get(self.geo.parent.get(loc, ""), "")):
                    if key:
                        held.setdefault(key, []).append(loc)
        out = []
        for key, locs in held.items():
            name = self.geo.name(key)
            if key in session.lands or len(name) < 4 or f" {name.lower()} " not in low:
                continue
            v = self._land_value(c.tag, locs)
            if v is None:
                continue
            session.lands[key] = v
            price = self._price_needed(v, snap)
            _step, traits = self._ruler_temper(c.tag)
            what = {"heartland": "part of their heartland",
                    "small outlying": f"a small holding far from their heartland ({v['heart']})",
                    "outlying": f"a sizeable possession far from their heartland ({v['heart']})"}[v["kind"]]
            deal = ("they give up such land only when beaten or overawed, never for money alone" if price == "never"
                    and v["kind"] == "heartland" else
                    "nothing would buy it from them as things stand (rivalry, or distrust of your word)"
                    if price == "never" else
                    f"a court in their place would consider parting with it for a real price - money on the scale "
                    f"of a {'modest' if price == 'weak' else 'large' if price == 'mild' else 'very large'} sum for "
                    f"your realm, or an exchange of land, or a service of real weight - and bargain hard over it")
            out.append(f"WHAT {name.upper()} IS TO {c.name.upper()} (from the papers, for judging - never quoted): "
                       f"{what}, {v['here']} of their {v['total']} places. {deal[0].upper() + deal[1:]}. Words alone "
                       f"buy nothing; the price, the power behind it and their own interest decide."
                       + (f" Their ruler: {traits}." if traits else ""))
        return ("\n".join(out[:2]) + "\n\n") if out else ""

    def _would_cede(self, c: dict[str, Any], session: Session) -> tuple[bool, str]:
        """Land given TO the ruler must be given by someone who would: the other court of
        this very conversation - beaten, overawed, or paid what that land is worth to
        them (a small far-off holding sells; a heartland does not) - never talked out of it."""
        snap = session.snap
        if c["to"] != snap.tag or c["from"] == snap.tag:
            return True, ""                    # the ruler gives, or others settle between them
        target = snap.target_country
        if target is None or c["from"] != target.tag:
            return False, "it is not theirs to give, and its lord is not here"
        r, n = self._power_ratio(snap), snap.numbers
        need = 1.3 if n.get("target_at_war") else 2.5
        if self._in_danger(snap):
            need *= 0.7
        need *= 1 + self._suspicion() / 20
        if r >= need:
            return True, ""                    # beaten or overawed
        value = self._land_value(c["from"], c.get("locs") or [])
        if value is None:
            return False, "nothing you offer or threaten is worth that land to them"
        price = self._price_needed(value, snap)
        if price == "never":
            return False, ("it is the heart of their realm" if value["kind"] == "heartland" else
                           "they will not sell to you as things stand")
        paid = max((self._TIERS_ORDER.index(a.get("tier")) for a in session.acts
                    if a.get("kind") == "gold_loss" and a.get("tier") in self._TIERS_ORDER), default=0)
        if any(x["from"] == snap.tag and x["to"] == target.tag for x in session.cessions):
            paid = max(paid, 2)                  # land given in exchange
        if paid >= self._TIERS_ORDER.index(price):
            return True, ""
        return False, "the price is too low for what that land is to them"

    # What a ruler's stance alone may move: how people regard them. Everything else
    # (edicts, policies, money, men, land, the state, pacts, war and peace) needs
    # an explicit order.
    _REACTIONS = ("opinion", "trust_gain", "trust_loss", "character_modifier", "favors", "prestige",
                  "legitimacy", "estate", "all_estates")

    # The state papers, for people who live in the world: the clerks' labels
    # stay (they are facts to know) but nobody's talents come as scores.
    _SCORES = re.compile(r"\s*\(?adm \d+/dip \d+/mil \d+;?\s*")

    def _papers(self, text: str) -> str:
        if not text:
            return text
        text = self._SCORES.sub(" (", text).replace(" ()", "").replace("( ", "(")
        return (text.replace("RIVALS:", "ADVERSARIES THE RULER HAS FORMALLY NAMED:")
                .replace("  rivals: ", "  adversaries formally named by the ruler: "))

    _ROLE_TEXT = {
        "the heir": "heir", "the ruler": "ruler", "cabinet": "cabinet member",
    }

    def _role_of(self, snap: Snapshot, name: str) -> str:
        """Who a speaker is, in a few words, for the panel."""
        low = name.strip().lower()
        for p in [x for x in (snap.target_person, snap.heir, *snap.court) if x is not None]:
            if p.name and p.name.strip().lower() == low:
                role = (p.role or p._implied_role()).strip()
                return self._ROLE_TEXT.get(role.lower(), role.lower())
        if self.memory:
            note = self.memory.people.get(low)
            if note and note.role:
                return _short_role(note.role)
        return ""

    # Words nobody uses to the ruler's face except to address the ruler: a
    # suggested reply containing them was written from the wrong side.
    _TO_THE_RULER = re.compile(
        r"\b(maest[àa]|vostra maest|vostra altezza|altezza|sire|mio signore|mio re|mia regina|"
        r"signore mio|monsignore|your majesty|my lord|my liege|sire)\b", re.I)

    def _suggest(self, session: Session) -> None:
        """Three things the ruler could say next, written by the ruler alone."""
        if session is not self.session or session.closed or session.kind in ("advisor", "decree", "hub"):
            return
        snap = session.snap
        ruler = snap.ruler.name if snap.ruler else "the ruler"
        if session.kind in ("envoy", "meeting", "visit") and snap.target_country and own_crown(snap):
            other = f"the lords and officers of {snap.target_country.name}, the ruler's own other realm"
        elif session.kind in ("envoy", "meeting", "visit") and snap.target_country:
            other = f"{snap.target_country.ruler or 'the ruler'} of {snap.target_country.name}"
        elif session.kind in ("council", "estate"):
            other = "the council" if session.kind == "council" else "the spokesmen of the estate"
        elif session.kind == "biographer" and session.cast:
            other = f"{session.cast[0]}, the royal biographer"
        elif snap.target_person and snap.target_person.name:
            other = snap.target_person.name
        else:
            other = "the people before them"
        transcript = [_cap(line.replace("The ruler:", f"{ruler} (the ruler):", 1), 400) for line in session.transcript]
        if not transcript and session.opener:
            transcript = [f"(The scene: {session.opener} {ruler} is about to speak first.)"]
        try:
            data = self.client.complete_json(
                prompts.suggest_messages(language=self.cfg.language, ruler=ruler, title=snap.ruler_title or "ruler",
                                         realm=snap.long_name or snap.name, other=other, transcript=transcript),
                prompts.SUGGEST_SCHEMA, schema_name="votc_replies", temperature=0.8, max_tokens=1500)
        except Player2Error as exc:
            self.log(f"  suggested replies not generated: {exc}")
            self._ui("offer_suggestions", True)
            return
        if session is not self.session or session.closed:
            return
        items = [_t(s, 240) for s in (data.get("suggestions") or []) if _t(s, 240)]
        kept = [s for s in items if not self._TO_THE_RULER.search(s)]
        if len(kept) < len(items):
            self.log(f"  dropped {len(items) - len(kept)} replies written from the wrong side")
        self._ui("set_suggestions", kept[:3])

    def _collect_actions(self, session: Session, data: dict[str, Any]) -> None:
        budget = session.snap.budget
        gains = 0
        candidates: list[dict[str, Any]] = []
        for raw in data.get("actions") or []:
            if len(session.pending) + len(candidates) >= self.cfg.max_actions_per_scene + 5:
                break
            action, reason = A.validate_action(raw)
            if action is None:
                if reason:
                    self.log(f"  dropped: {reason}")
                continue
            action = self._weigh(action)
            if action["kind"] == "nothing":
                continue
            if session.kind == "chronicle" and action["kind"] in self._NO_TARGET_IN_STORIES:
                continue
            if action["kind"] in A.CONDITIONAL_CURRENCIES and action["kind"] not in _currencies_for(session.snap):
                self.log(f"  dropped: {action['kind']} does not exist in this realm")
                continue
            candidates.append(action)
        for action in self._balanced(session.kind, session.balance, candidates):
            # What helps the realm is bought with influence and limited in number; what
            # hurts it is the price of the deed - never held back, never paid for. What a
            # decree that truly worked earned was weighed already (app._earned).
            earned = action.get("tier") == A.GRAND or action.get("area") in A.INCOME_AREAS
            if self._is_good(action) and not earned:
                cost = A.action_cost(action)
                if gains >= self.cfg.max_actions_per_scene:
                    continue
                if session.spent + cost > budget:
                    self.log(f"  dropped: {action['kind']} costs {cost}, {budget - session.spent} left")
                    continue
                session.spent += cost
                gains += 1
            session.pending.append(A.render_action(action))
            session.acts.append(action)
            good = action.get("sign", "bonus") == "bonus" and action["kind"] != "policy_penalty"
            if action["kind"] == "war_exhaustion":
                good = not good
            note = ACTION_NOTE_SINGLE.get(action["kind"])
            if action["kind"] in ("policy_bonus", "policy_penalty"):
                strength = A.TIER_TEXT.get(action["tier"], "")
                note = (f"{A.POLICY_TEXT.get(action['area'], action['area'])}: "
                        f"{strength} {'advantage' if good else 'disadvantage'} for {action['years']} year{'s' if action['years'] != '1' else ''}")
            elif note is None:
                pair = ACTION_NOTE.get(action["kind"], ("", ""))
                note = pair[0] if good else pair[1]
            if note:
                self._ui("add_note", "• " + note, "good" if good else "bad")

    def _collect_decisions(self, session: Session, data: dict[str, Any]) -> None:
        """What the ruler ordered in so many words: it will happen at the close."""
        n = session.snap.numbers
        target = session.snap.target_country
        for raw in data.get("ruler_decisions") or []:
            offer, reason = A.validate_offer(
                raw, allied=bool(n.get("target_allied")), at_war=bool(n.get("target_at_war")),
                truce=bool(n.get("target_truce")), budget=10**9)
            kind = raw.get("kind") if isinstance(raw, dict) else ""
            if (offer is not None and kind == "declare_war" and target
                    and any(c.tag == target.tag for c in self.live_picture.get("lord", []))):
                offer, reason = None, "lord"
            if offer is None:
                if reason:
                    why = ("they are your overlord" if reason == "lord"
                           else A.REFUSAL_TEXT.get(kind, reason))
                    self.log(f"  decision not possible: {reason}")
                    self._ui("add_note", f"✖ Not possible ({why}): the decision will have no effect.", "bad")
                    session.messages.append({"role": "user", "content": (
                        f"(The ruler's order cannot be carried out: {reason}. It does not happen.)")})
                continue
            if any(d["kind"] == offer["kind"] for d in session.decisions):
                continue
            yes, why = self._would_accept(offer["kind"], session.snap)
            if not yes and offer["kind"] == "union" and not any(d["kind"] == "accept_vassalage"
                                                                 for d in session.decisions):
                # they will not give up their state - but they may still come under the crown as a vassal
                vassal, _w = self._would_accept("accept_vassalage", session.snap)
                if vassal:
                    self.log(f"  union refused ({why}): they come under the crown as a vassal instead")
                    self._ui("add_note", "⚖ They will not give up their state altogether, but they accept "
                                         "to come under your crown as a vassal, keeping their own government.",
                             "good")
                    session.messages.append({"role": "user", "content": (
                        "(News: they will not dissolve their state, but they accept to come under the ruler's "
                        "crown as a vassal, keeping their own government - that is what happens.)")})
                    offer = {**offer, "kind": "accept_vassalage",
                             "label": "They come under your crown as a vassal"}
                    yes = True
            if not yes:
                self.log(f"  not ratified by the other court: {offer['kind']} ({why})")
                session.refused.append(f"{offer['label']} - the other court would not agree: {why}")
                self._ui("add_note", f"\u2716 Their court does not ratify it: {why}.", "bad")
                session.messages.append({"role": "user", "content": (
                    f"(News: the other court will not agree to this - {why}. It does not happen.)")})
                continue
            session.decisions.append(offer)
            session.offers = [o for o in session.offers if o["kind"] != offer["kind"]]
            self._ui("set_offers", [o["label"] for o in session.offers])
            cost = A.ORDER_COST_TEXT.get(offer["kind"], "")
            self._ui("add_note", f"⚔ You decided: {offer['label']} - it happens when you confirm the outcome"
                                 + (f". It will cost the realm: {cost}." if cost else "."), "good")
            session.transcript.append(f"THE RULER'S DECISION: {offer['label']}")
            if self.memory:
                self.memory.remember_event(session.snap.date, "decision", offer["label"])

    # ======================================================= works of the realm
    @property
    def works_catalog(self) -> W.Catalog | None:
        if getattr(self, "_works_catalog", None) is None and self.cfg.game_dir:
            try:
                self._works_catalog = W.Catalog.load(self.cfg.game_dir, self.cfg.state_dir, self.codex,
                                                     self.cfg.game_language)
            except Exception as exc:  # noqa: BLE001 - a game we cannot read must not stop the court
                self.log(f"The game's buildings and laws could not be read: {exc}")
                self._works_catalog = None
        return getattr(self, "_works_catalog", None)

    def _works_text(self) -> str:
        cat = self.works_catalog
        return cat.summary() if cat is not None else ""

    def _people(self, snap: Snapshot | None) -> list[Any]:
        """The persons the game reported, each with the mod's variable that holds them."""
        if snap is None:
            return []
        return [p for p in [snap.ruler, snap.heir, snap.target_person, *snap.court] if p and p.slot]

    def _owns(self, snap: Snapshot | None) -> Callable[[str], bool] | None:
        """Does the realm hold this place? From the latest save (None when there is none to ask)."""
        w = self.world
        cid = w.by_tag.get(snap.tag) if (w is not None and w.owners and snap is not None) else None
        if cid is None:
            return None
        number = {k: i + 1 for i, k in enumerate(self.geo.index)}
        return lambda key: w.owners.get(number.get(key, 0)) == cid

    def _work_lines(self, works: list[dict[str, Any]]) -> list[str]:
        """The game's effects for these works. A place's new name gets its key first - a
        text of the mod's own, written before the mail that uses it (names.PlaceNames)."""
        renamed = False
        cid = self.memory.campaign_id if self.memory else 0
        for w in works:
            for r in w.get("renames") or []:
                if not r.get("n"):
                    r["n"] = self.place_names.assign(r["new"], location=r["location"], campaign=cid)
                    renamed = True
        if renamed:
            self.place_names.write_loc(self.cfg.mod_path)
        for w in works:
            if w["kind"] in W.CONVERSION_KINDS and not w.get("plan"):
                plan = W.conversion_plan(w, self._conversion_skill(self.snapshot))
                self.log(f"  {w['label']}: {plan['lo']}-{plan['hi']} of the people at once, the rest over "
                         f"{plan['years']} years; resistance: {plan['resistance']}")
        return [line for w in works for line in W.script(w)]

    def _conversion_skill(self, snap: Snapshot | None) -> float:
        """-1..1: how well the ruler rules - their record of choices and how the realm is kept -
        which decides how converting the people goes (works.conversion_plan)."""
        if snap is None:
            return 0.0
        n = snap.numbers
        clip = lambda x: max(-1.0, min(1.0, x))  # noqa: E731
        record, _n = self._ruler_record(snap)
        keep = 0.5 * clip(n.get("stability", 0) / 60)
        if n.get("legitimacy", 0) > 0:
            keep += 0.5 * clip((n.get("legitimacy", 0) - 50) / 40)
        return clip(0.5 * record + 0.5 * keep)

    def _validate_works(self, raws: list[Any], snap: Snapshot | None = None) -> tuple[list[dict[str, Any]], list[str]]:
        cat = self.works_catalog
        if cat is None:
            return [], []
        snap = snap or self.snapshot
        people, owns = self._people(snap), self._owns(snap)
        treasury = snap.numbers.get("gold") if snap is not None and "gold" in snap.numbers else None
        ok, refused = [], []
        for raw in [r for r in raws if isinstance(r, dict)][:4]:
            work, why = W.validate(raw, cat, self.geo, people=people, owns=owns, treasury=treasury)
            if work is not None and work["kind"] == "pay" and treasury is not None:
                treasury -= work["amount"]            # two payments draw on the same chest
            if work is None:
                if why:
                    refused.append(why)
                    self.log(f"  work not possible: {why}")
                continue
            if all(w["label"] != work["label"] for w in ok):
                ok.append(work)
        return ok, refused

    def _applied_text(self, acts: list[dict[str, Any]], orders: list[dict[str, Any]], works: list[dict[str, Any]]) -> str:
        parts = [A.describe_action(a) for a in acts] + [o.get("label", o.get("kind", "")) for o in orders]
        parts += [f"{w['label']} (its price: {W.price_text(w)})" for w in works]
        return "; ".join(p for p in parts if p) or "nothing yet"

    def _foreign_papers(self, snap: Snapshot) -> str:
        """The courts that might answer: neighbours, allies, rivals, the great powers, the head of the faith."""
        live = self.live_picture or {}
        seen, lines = set(), []
        for kind, word in (("near", "neighbour"), ("ally", "ally"), ("rival", "rival"), ("war", "at war with us"),
                           ("gp", "great power"), ("lord", "our overlord"), ("subj", "our subject")):
            for c in live.get(kind, []) or []:
                tag = getattr(c, "tag", "")
                if tag and tag != snap.tag and tag not in seen:
                    seen.add(tag)
                    lines.append(f"- {tag}: {getattr(c, 'name', tag)} ({word})")
        if "cathol" in (snap.religion or "").lower() and "PAP" not in seen:
            lines.append("- PAP: the Papacy (head of the Catholic Church)")
        return ("THE COURTS THAT MIGHT ANSWER (tag: name):\n" + "\n".join(lines[:24])) if lines else ""

    def _reactions(self, snap: Snapshot, deed: str, applied: str, cap: int = 10,
                   war_on: frozenset[str] = frozenset()) -> dict[str, Any] | None:
        """A grave deed: how the whole realm and the world take it. Only ever asked when the
        deed is grave; shown in the panel only when something comes of it."""
        realm = prompts.render_dossier(snap)
        realm += (f"\n- the army: {snap.n('army'):g} regiments; manpower {snap.n('manpower'):g} of "
                  f"{snap.n('maxmanpower'):g}; rank {snap.rank}; faith {snap.religion}; culture {snap.culture}")
        character = prompts.realm_character(snap, great_power=self._great(snap), peer_gold=self.peer_gold(),
                                            great_block=self._great_block(snap))
        if character:
            realm += "\n\n" + character
        task = R.TASK.format(deed=deed[:2500], applied=applied[:1200], realm=realm[:3000],
                             papers=self._foreign_papers(snap),
                             difficulty=prompts.DIFFICULTY.get(self.cfg.difficulty, "DIFFICULTY: NORMAL."))
        try:
            data = self.client.complete_json([{"role": "user", "content": task}], R.schema(),
                                             schema_name="votc_reactions", temperature=0.5, max_tokens=1800)
        except Player2Error as exc:
            self.log(f"  the realm's reaction was not judged: {exc}")
            return None
        done = R.build(data, snap=snap, world=self.world, difficulty=self.cfg.difficulty, known_tag=self._known_tag,
                       validate_work=lambda raw: W.validate(raw, self.works_catalog, self.geo)
                       if self.works_catalog is not None else (None, "no catalogue"), cap=cap, war_on=war_on)
        who = "; ".join(f"{_t(w.get('group'), 30)}: {_t(w.get('reaction'), 120)}" for w in data.get("who") or []
                        if isinstance(w, dict))
        self.log(f"  gravity {done['gravity']}: {done['summary'][:200]}" + (f" | {who[:400]}" if who else ""))
        if done["roll"]:
            self.log(f"  {done['roll']}")
        if done["gravity"] < R.GRAVE or not (done["lines"] or done["orders"] or done["followups"]):
            return None
        if done["notes"]:
            self._ui("add_note", "\u2620 The realm answers: " + "; ".join(done["notes"][:8]) + ".", "bad")
        if self.memory is not None:
            self.memory.remember_event(snap.date, "reaction", done["summary"] or deed[:200])
        return done

    def _collect_trade(self, session: Session, raws: list[Any]) -> None:
        """Goods sold or bought abroad for years: balanced in trade.py, applied with the outcome."""
        if self.world is None:
            return
        for raw in [r for r in raws if isinstance(r, dict)][:2]:
            deal, why = TR.validate(raw, codex=self.codex, world=self.world,
                                    taxbase=float(session.snap.n("taxbase") or 1))
            if deal is None:
                if why:
                    self.log(f"  trade not possible: {why}")
                    self._ui("add_note", f"✖ Not possible ({why}): that trade does not happen.", "bad")
                    session.refused.append(f"a trade: {why}")
                continue
            if any(d["good"] == deal["good"] and d["direction"] == deal["direction"] for d in session.trade):
                continue
            session.trade.append(deal)
            self.log(f"  {deal['label']}")
            self._ui("add_note", "⚖ " + deal["label"] + ".", "good")
            if self.memory:
                self.memory.remember_event(session.snap.date, "decision", deal["label"])

    def _collect_works(self, session: Session, raws: list[Any]) -> None:
        """What the ruler ordered done to the land and the state: carried out with the outcome."""
        works, refused = self._validate_works(raws, session.snap)
        for why in refused:
            self._ui("add_note", f"\u2716 Not possible ({why}): nothing is done.", "bad")
            session.refused.append(f"an order that cannot be carried out: {why}")
            session.messages.append({"role": "user", "content": f"(That order cannot be carried out: {why}.)"})
        for work in works:
            if any(w["label"] == work["label"] for w in session.works):
                continue
            session.works.append(work)
            self._ui("add_note", f"\u2692 Ordered: {work['label']} - carried out with the outcome. It will cost the "
                                 f"realm: {W.price_text(work)}.", "good")
            session.transcript.append(f"THE RULER'S ORDER: {work['label']}")
            if self.memory:
                self.memory.remember_event(session.snap.date, "decision", work["label"])

    def _draft_standing(self, session: Session, raws: list[Any]) -> list[dict[str, Any]]:
        self._collect_standing(session, raws)
        return list(session.standing)

    def _collect_standing(self, session: Session, raws: list[Any]) -> None:
        """Standing measures the ruler started or revoked: balanced in measures.py (nothing
        lasting is free), carried out with the outcome, revocable at any time."""
        active = dict(self.memory.measures) if self.memory else {}
        for m in session.standing:                    # what this scene already decided counts too
            if m["action"] == "end":
                active.pop(m["slot"], None)
            else:
                active[m["slot"]] = m
        for raw in [r for r in raws if isinstance(r, dict)][:2]:
            m, why = MS.validate(raw, active)
            if m is None:
                if why:
                    self.log(f"  standing measure not taken: {why}")
                    session.refused.append(f"a standing measure: {why}")
                continue
            session.standing.append(m)
            if m["action"] == "end":
                active.pop(m["slot"], None)
            else:
                active[m["slot"]] = m
            self.log(f"  {m['label']}")
            self._ui("add_note", "\u2696 " + m["label"] + ".", "good")

    def _apply_standing(self, snap: Snapshot, standing: list[dict[str, Any]], loc: dict[str, str]) -> list[str]:
        """The game's lines for the measures, their names for the game's text, and the journal."""
        lines: list[str] = []
        for m in standing:
            lines += MS.script(m)
            if m["action"] == "start":
                loc[f"votc_st{m['slot']}_name"] = m["name"]
            if self.memory:
                if m["action"] == "start":
                    self.memory.measure_start(snap.date, m)
                else:
                    self.memory.measure_end(snap.date, m["slot"])
                self.memory.remember_event(snap.date, "decision", m["label"])
        return lines

    def _decide(self, session: Session, offer: dict[str, Any]) -> None:
        """What the ruler ordered in so many words, judged at the close: it happens with the outcome."""
        session.decisions.append(offer)
        session.transcript.append(f"THE RULER'S DECISION: {offer['label']}")
        if self.memory:
            self.memory.remember_event(session.snap.date, "decision", offer["label"])

    def _collect_state_changes(self, session: Session, raws: list[Any], direct: bool = False) -> None:
        """A new name, form of government or rank: a button for the ruler during a scene; judged at
        its close (direct), only what the ruler ordered in so many words - their words are checked."""
        for raw in raws[:3]:
            if direct and not self._ordered(session, raw):
                continue
            offer, reason = A.validate_institution(raw, current_name=session.snap.name)
            if offer is None:
                if reason:
                    self.log(f"  change of the state dropped: {reason}")
                continue
            if any(o.get("kind") == offer["kind"] for o in session.offers + session.decisions):
                continue
            if direct:
                self._decide(session, offer)
                continue
            session.offers.append(offer)
            self._ui("set_offers", [o["label"] for o in session.offers])

    def _collect_power(self, session: Session, raws: list[Any], direct: bool = False) -> None:
        """Killing, deposing, crowning, exiling, a rising: a button for the ruler during a scene;
        judged at its close (direct), only what the ruler ordered in so many words."""
        for raw in raws[:2]:
            if direct and not self._ordered(session, raw):
                continue
            move, reason = A.validate_power(raw, session.snap)
            if move is None:
                if reason:
                    self.log(f"  power move dropped: {reason}")
                continue
            if any(o.get("kind") == move["kind"] and o.get("who") == move.get("who")
                   for o in session.offers + session.decisions):
                continue
            if direct:
                self._decide(session, move)
                continue
            session.offers.append(move)
            self._ui("set_offers", [o["label"] for o in session.offers])

    @staticmethod
    def _order_entries(orders: list[dict[str, Any]]) -> list[Any]:
        """Queue entries for orders: (number, person slot) when someone is named."""
        return [(A.order_variant(o), o.get("who_slot", "")) for o in orders]

    def _prepare_orders(self, snap: Snapshot, orders: list[dict[str, Any]]) -> None:
        """New names need their localisation key written before the mail goes."""
        for o in orders:
            if o.get("kind") == "rename_country" and not o.get("slot"):
                cid = self.memory.campaign_id if self.memory else 0
                o["slot"] = self.names.assign(o["name"], o.get("adjective") or o["name"], campaign=cid, tag=snap.tag)
                if o["slot"] and self.cfg.mod_dir:
                    self.names.write_loc(self.cfg.mod_path)
                    self.log(f"New name registered: {o['name']} (key votc_cname_{o['slot']})")

    # --------------------------------------------------------- panel callbacks
    def on_send(self, text: str) -> None:
        self._touched = time.time()
        text = _clean_words(text)
        session = self.session
        if session is None or session.closed:
            return
        if session.kind == "advisor":
            self._advisor_ask(session, text)
            return
        if session.kind == "battle":
            self.battle.on_send(session, text)
            return
        if session.kind in ("book", "life", "century"):
            return                      # a book is read, not answered
        session.transcript.append(f"The ruler: {text}")
        if session.kind == "decree":
            self._submit(lambda: self._decree(session, text))
            return
        else:
            scene = ""
            if session.opener:
                scene = ("THE SCENE: " + session.opener + "\nThe ruler speaks first; they answer.\n\n")
                session.opener = ""
            if session.aside:
                scene = session.aside + "\n\n" + scene
                session.aside = ""
            scene = self._context_for(session, text) + scene
            self._compact(session)
            detail = ""
            session.messages.append({"role": "user", "content": (
                scene + f'The ruler says: "{text}"\n\n' + _beats(session, said=text)
                + (f"\n{detail}" if detail else "")
                + ("\n(They cannot stand against the ruler's realm in a war, and they know it: no bluster, no hint "
                   "that the ruler would fail or pay dearly - they bargain, counter-offer, stall or yield with "
                   "dignity. Only what they could really do.)" if session.proud is False else
                   "\n(They know they could not win a war against the ruler's realm and they SAY so - yet they "
                   "refuse out of pride, and it shows that pride is the reason. Only what they could really do.)"
                   if session.proud else "")
                + "\n(A line's text is only what is SAID aloud; where they are and what they do go in its "
                "gesture, never at the end of the speech.)"
                + "\n(Whoever the ruler spoke to "
                "answers; others only if they truly would cut in. Plain human speech, none of the "
                "machine habits, no memo, no list of options. Write it all, gestures included, in "
                f"{prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language)}.)")})
        if not session.part_ready and session.kind in self.PART_KINDS:
            self._submit(lambda: (self._prepare_part(session, text), self._turn(session)))
            return
        self._submit(lambda: self._turn(session))

    def _decree(self, session: Session, text: str) -> None:
        """Write, weigh and stage a decree. It takes effect with the event's "Cosi sia"."""
        if session is not self.session or session.closed:
            return
        self._ui("set_busy", "The chancery drafts the decree…")
        # A new text replaces the previous draft entirely.
        session.acts, session.pending, session.spent = [], [], 0
        session.standing, session.refused = [], []
        table = self._decree_fortunes(session.snap)
        wars = self._war_views(text, session.snap)
        messages = [{"role": "system", "content": session.system},
                    {"role": "user", "content": f'THE DECREE: "{text}"\n\n{prompts.text("decree")}'
                                                f'\n\n{prompts.decree_fortune_table(table)}'
                                                + (f"\n\n{wars.strip()}" if wars else "")}]
        data = self.client.complete_json(
            [messages[0], {"role": "user", "content": messages[1]["content"] + _reality_hints(text)}],
            prompts.decree_schema(), schema_name="votc_decree", temperature=0.6)
        if session is not self.session or session.closed:
            return
        try:
            wisdom = max(-2, min(2, int(data.get("wisdom") or 0)))
            vision = max(0, min(2, int(data.get("vision") or 0)))
        except (TypeError, ValueError):
            wisdom, vision = 0, 0
        fortune = table[(wisdom, vision)]
        self.log(f"  this decree is judged wisdom {wisdom:+d}, vision {vision}: it turns out {fortune}")
        data = self._reality(None, data, text, roll=False)
        # what this world cannot do was already set aside: only a possible order is rolled at the proclamation
        sub = data.get("substance") if isinstance(data.get("substance"), dict) else {}
        if self._in_this_world(data, text) != "possible":
            sub = {}
        odds = self._adjusted_odds(sub) if sub else "certain"
        if self._is_statecraft(data):
            odds = "certain"
        treaty = data.get("treaty") if isinstance(data.get("treaty"), dict) else {}
        kinds = {treaty.get("kind")} - {None, "", "none"}
        war_tags = [str(treaty.get("tag") or "").upper()] if treaty.get("kind") == "declare_war" else []
        cap, statecraft = self._gravity_cap(session.snap, kinds, data.get("works") or [],
                                            data.get("power_moves") or [], war_tags)
        if int(data.get("gravity") or 0) > cap:
            self.log(f"  gravity {data.get('gravity')} -> {cap}: ordinary statecraft ({statecraft}), not an outrage")
            data = {**data, "gravity": cap}
        session.balance = {"gravity": int(data.get("gravity") or 0), "statecraft": statecraft,
                           "brings_money": self._brings_money(text),
                           "fortune_class": ("good" if fortune in ("triumph", "better", "intended") else
                                             "mixed" if fortune == "complication" else "")}
        title = _t(data.get("title"), 70) or "Decree"
        body = _cap(data.get("proclamation"), 1500) or text
        self._ui("add_line", f"Decree: {title}", body)
        # what it may earn beyond the usual - only while its own actions are weighed
        A.EARNED = self._earned(session.snap, fortune, vision)
        try:
            self._collect_actions(session, data)
        finally:
            A.EARNED = None
        changes = []
        for raw in (data.get("state_changes") or [])[:3]:
            change, reason = A.validate_institution(raw, current_name=session.snap.name)
            if change:
                changes.append(change)
                self._ui("add_note", "✦ " + A.describe_order(change).lstrip("✦ "), "good")
            elif reason:
                self.log(f"  change of the state dropped: {reason}")
        for raw in (data.get("power_moves") or [])[:2]:
            move, reason = A.validate_power(raw, session.snap)
            if move:
                changes.append(move)
                self._ui("add_note", A.describe_power(move), "bad" if move["kind"] in ("kill", "exile", "depose") else "good")
            elif reason:
                self.log(f"  power move dropped: {reason}")
        works, refused = self._validate_works(data.get("works") or [], session.snap)
        for why in refused:
            self._ui("add_note", f"\u2716 Not possible ({why}): that part of the decree does nothing.", "bad")
        for w in works:
            self._ui("add_note", f"\u2692 {w['label']} - it will cost the realm: {W.price_text(w)}.", "good")
        reaction = None
        if int(data.get("gravity") or 0) >= R.GRAVE:
            self._ui("set_busy", "The realm hears of it…")
            plain = _t(sub.get("what_it_really_is"), 300)
            reaction = self._reactions(session.snap, (f"A DECREE: {title}. What it really does (plainly, without "
                                                      f"its framing): {plain}" if plain else
                                                      f"A DECREE: {title}. {body}"),
                                       self._applied_text(session.acts, [], works), cap=cap,
                                       war_on=frozenset(war_tags))
        session.draft = {"title": title, "body": body, "note": _t(data.get("memory_note"), 300), "works": works,
                         "reaction": reaction,
                         "pacts": [p for p in (data.get("pacts") or []) if isinstance(p, dict)][:1],
                         "touched": [p for p in (data.get("pacts_touched") or []) if isinstance(p, dict)][:3],
                         "changes": changes,
                         "followups": [f for f in (data.get("followups") or []) if isinstance(f, dict)][:2],
                         "standing": self._draft_standing(session, data.get("measures") or []),
                         "substance": sub, "text": text, "wisdom": wisdom, "gist": _t(data.get("gist"), 240),
                         "treaty": self._decree_treaty(data.get("treaty"))}
        if self.ODDS.get(odds, 1.0) < 1.0:
            self._ui("add_note", f"\u2696 Will it be carried out? {self.ODDS_TEXT.get(odds, odds)}"
                                 + (f" - {_t(sub.get('why'), 200)}" if sub.get("why") else ""), "bad")
        why = _t(data.get("reasoning"), 300)
        if why:
            self.log(f"  why: {why}")
        self._ui("add_note", "Press \"Proclaim\" to put it into force, or write another text.")
        self._ui("set_close_label", "Proclaim")
        self._ui("set_busy", "")

    # How a decree turns out is decided here, not by the AI (which would spin every one into
    # trouble, or none). Even odds in an ordinary realm; extreme times move the base; how the
    # ruler keeps the realm moves it again.
    DECREE_BASE = 0.5
    DECREE_BASE_GOLDEN = 0.62      # an exceptionally sound realm
    DECREE_BASE_CRISIS = 0.38      # a realm in crisis
    # The ruler's skill moves it by up to 0.30 either way, in three parts:
    DECREE_KEEPING = 0.10          # how the realm is kept (stability, estates, debts...)
    DECREE_RECORD = 0.10           # how wise the ruler's recent choices were (events, audiences, decrees)
    DECREE_WISDOM_STEP = 0.05      # how wise this decree is (-2..2)
    RECORD_DAYS = 3 * 365          # the choices that count for the record
    DECREE_DIFFICULTY = {"easy": 0.05, "normal": 0.0, "hard": -0.04, "very_hard": -0.08}
    DECREE_BETTER_SHARE = 0.25     # of the decrees that work, this share turns out better than hoped
    DECREE_BACKFIRE_SHARE = 0.3    # of those that go wrong, this share miscarries outright

    def _golden(self, snap: Snapshot) -> bool:
        n = snap.numbers
        estates = [v for v in (snap.estates.get(e, 0) for e in ("nobles", "clergy", "burghers", "peasants")) if v > 0]
        return (n.get("stability", 0) >= 60 and (n.get("legitimacy", 0) == 0 or n.get("legitimacy", 0) >= 75)
                and not snap.at_war and n.get("loans", 0) == 0
                and bool(estates) and min(estates) >= 50)

    def _decree_chance(self, snap: Snapshot) -> tuple[float, str]:
        """The chance that a decree works (as intended or better), and the reasons, for the log."""
        n = snap.numbers
        crisis, golden = self._crisis(snap), self._golden(snap)
        base = self.DECREE_BASE_CRISIS if crisis else self.DECREE_BASE_GOLDEN if golden else self.DECREE_BASE
        clip = lambda x: max(-1.0, min(1.0, x))  # noqa: E731
        keep = 0.0
        keep += 0.35 * clip(n.get("stability", 0) / 60)
        if n.get("legitimacy", 0) > 0:
            keep += 0.2 * clip((n.get("legitimacy", 0) - 50) / 40)
        if "govpower" in n:
            keep += 0.15 * clip((n.get("govpower", 50) - 50) / 40)
        estates = [v for v in (snap.estates.get(e, 0) for e in ("nobles", "clergy", "burghers", "peasants")) if v > 0]
        if estates:
            keep += 0.2 * clip((sum(estates) / len(estates) - 50) / 30)
        if n.get("loans", 0) > 0 or n.get("inflation", 0) > 5:
            keep -= 0.1
        if snap.at_war:
            keep -= 0.1 * clip(0.3 + n.get("warexhaustion", 0) / 10)
        # a realm swamped with edicts obeys each of them less
        recent = 0
        if self.memory is not None:
            recent = sum(1 for p in self.memory.pages if p.get("kind") == "decree"
                         and 0 <= gamedate.days_between(p.get("date", ""), snap.date) <= 365)
        keep -= 0.12 * min(3, max(0, recent - 1))
        keep = clip(keep)
        record, counted = self._ruler_record(snap)
        chance = (base + self.DECREE_KEEPING * keep + self.DECREE_RECORD * record
                  + self.DECREE_DIFFICULTY.get(self.cfg.difficulty, 0.0))
        why = (f"{'crisis' if crisis else 'golden age' if golden else 'ordinary times'}, keeping {keep:+.2f}, "
               f"record {record:+.2f} over {counted} choices, {recent} decrees this year")
        return chance, why

    def _ruler_record(self, snap: Snapshot) -> tuple[float, int]:
        """How wise the ruler's recent choices were, -1..1 - weighed by how many there are, so that
        one lucky or foolish choice does not make a reputation."""
        if self.memory is None:
            return 0.0, 0
        scores = [j["score"] for j in self.memory.judgements
                  if 0 <= gamedate.days_between(j.get("date", ""), snap.date) <= self.RECORD_DAYS]
        if not scores:
            return 0.0, 0
        mean = sum(scores) / len(scores) / 2
        return mean * len(scores) / (len(scores) + 3), len(scores)

    # Of the decrees that work, the share that turns out a triumph: rare, more often for a wise
    # decree, and above all for a plan with a real vision.
    TRIUMPH_SHARE = 0.06
    TRIUMPH_PER_WISDOM = 0.04
    TRIUMPH_PER_VISION = 0.07

    # Of the decrees that fail, the share that ends in tragedy: far rarer than a triumph, and
    # only for a reckless decree (wisdom -2) - more often when the ruler's record is one of
    # bad choices too, never by bad luck alone.
    TRAGEDY_SHARE = 0.03
    TRAGEDY_PER_BAD_RECORD = 0.05

    def _fortune_of(self, chance: float, roll: float, wisdom: int = 0, vision: int = 0,
                    record: float = 0.0) -> str:
        chance = max(0.08, min(0.92, chance))
        if roll < chance:
            triumph = self.TRIUMPH_SHARE + self.TRIUMPH_PER_WISDOM * max(0, wisdom) + self.TRIUMPH_PER_VISION * vision
            better = triumph + self.DECREE_BETTER_SHARE
            return ("triumph" if roll < chance * triumph else "better" if roll < chance * better
                    else "intended")
        if wisdom <= -2:
            tragedy = self.TRAGEDY_SHARE + self.TRAGEDY_PER_BAD_RECORD * max(0.0, -record)
            if roll > 1 - (1 - chance) * tragedy:
                return "tragedy"
        return "backfire" if roll > 1 - (1 - chance) * self.DECREE_BACKFIRE_SHARE else "complication"

    # What each outcome may earn beyond the usual, by the decree's vision: (grand levers, income
    # up to). The same as prompts.EARNED_TEXT.
    EARNED = {
        ("triumph", 0): (2, "severe"), ("triumph", 1): (2, "severe"), ("triumph", 2): (2, "grand"),
        ("better", 1): (0, "mild"), ("better", 2): (1, "severe"),
        ("intended", 1): (0, "weak"), ("intended", 2): (0, "mild"),
    }

    def _earned(self, snap: Snapshot, fortune: str, vision: int) -> dict[str, Any] | None:
        grand, income = self.EARNED.get((fortune, vision), (0, ""))
        if not grand and not income:
            return None
        return {"grand": grand > 0, "grand_left": grand, "income": income, "income_left": 1 if income else 0,
                "band": A.income_band(snap.n("taxbase"))}

    def _decree_fortunes(self, snap: Snapshot) -> dict[tuple[int, int], str]:
        """One roll of fortune, and what it gives for each wisdom and vision the decree may be
        judged to have: a wiser, farther-seeing decree never turns out worse."""
        chance, why = self._decree_chance(snap)
        record, _n = self._ruler_record(snap)
        roll = random.random()
        table = {(w, v): self._fortune_of(chance + self.DECREE_WISDOM_STEP * w, roll, w, v, record)
                 for w in range(-2, 3) for v in range(3)}
        self.log(f"  chance that this decree works: {max(0.08, min(0.92, chance)):.0%} before its own wisdom "
                 f"({why})")
        return table

    def _proclaim(self, session: Session) -> None:
        draft = session.draft
        snap = session.snap
        title = f"Decree: {draft['title']}"
        if draft.get("substance"):
            standing = draft.get("standing") or []
            judged = self._reality(session, {"substance": draft["substance"], "actions": session.acts,
                                             "state_changes": draft.get("changes") or [], "works": draft.get("works"),
                                             "measures": standing, "treaty": draft.get("treaty")},
                                   draft.get("text", ""))
            if len(judged.get("actions") or []) < len(session.acts) or len(judged.get("works") or []) < len(
                    draft.get("works") or []) or (draft.get("changes") and not judged.get("state_changes")) or (
                    len(judged.get("measures") or []) < len(standing)):
                session.acts = list(judged.get("actions") or [])
                draft = {**draft, "works": [], "changes": [],
                         "standing": [m for m in standing if m.get("action") == "end"]}
        orders = list(session.decisions) + list(draft.get("changes") or [])
        self._prepare_orders(snap, orders)
        ids = self._order_entries(orders) + [A.action_variant(a) for a in session.acts]
        self._note_great(session.acts, snap)
        works = draft.get("works") or []
        reaction = draft.get("reaction")
        lines = self._work_lines(works)
        treaty = draft.get("treaty")
        if treaty:
            # the court the agreement was made with becomes the target, and the agreed order runs
            lines += [f"set_variable = {{ name = votc_tcountry value = c:{treaty['tag']} }}",
                      A.ALL_OFFERS[treaty["kind"]].format(cb=A.CASUS_BELLI.get(treaty.get("cb", ""), 1))]
            if self.memory:
                self.memory.remember_event(snap.date, "decision", f"{treaty['party']}: {treaty['label']}, by decree")
        names: dict[str, str] = {}
        lines += self._apply_standing(snap, draft.get("standing") or [], names)
        if reaction:
            lines += reaction["lines"]
            ids += self._order_entries(reaction["orders"])
        script = lines + A.queue_lines(ids) + ["votc_show_outcome = yes", "votc_in_close = yes"]
        requested = A.requested_text(session.acts, orders) + "".join(f"\n{w['label']}" for w in works)
        requested += "".join(f"\n{m['label']}" for m in draft.get("standing") or [])
        self.mail.send(script, loc={"votc_out_title": title, "votc_out_body": with_gist(draft.get("gist"), draft["body"]),
                                    "votc_out_requested": requested, **names}, label="decree")
        self.last_scene = {"title": title, "body": draft["body"], "kind": "decree"}
        if self.memory:
            self.memory.meta["last_scene"] = snap.date; self.memory._meta_dirty = True
        if self.memory:
            self.memory.judged(snap.date, "decree", draft.get("wisdom", 0))
            self.memory.add_page(snap.date, "decree", title, draft["body"])
            self.memory.remember_event(snap.date, "decree", draft["note"] or f"Decreed: {draft['title']}")
            self._book_note(snap.date, f"Decree: {draft['title']} - {draft['note']}", weight=2)
            self._remember_measures(snap, session.acts, draft["title"])
            self._plan_followups(snap, draft["followups"], f"reactions to the decree {draft['title']}")
            if reaction:
                self._plan_followups(snap, reaction["followups"], f"what came of the decree {draft['title']}")
                self.memory.remember_event(snap.date, "reaction", reaction["summary"] or draft["title"])
            self._collect_pacts(snap, draft.get("pacts") or [])
            self._touch_pacts(snap, draft.get("touched") or [])
        self.log(f"Decree proclaimed: {draft['title']} ({len(session.acts)} consequences waiting for \"So be it\")")
        if self.session is session:
            self.session = None

    # How far a matter may run on its own: a scene of the ruler's own may bring two follow-ups,
    # one that a follow-up brought one more, and then the matter has had its course (the ruler
    # can always take it up again).
    CHAIN_ROOM = {0: 2, 1: 1}

    def _plan_followups(self, snap: Snapshot, followups: list[Any], default: str, *,
                        aftermath: bool = False, chain: int = 0) -> None:
        """What the AI decided will come of a deed later. Never limited by the event frequency.
        aftermath: what comes of a settled matter - told as a page of chronicle, never put to
        the ruler again as a new dilemma. chain: how deep the scene that decided it already was."""
        if self.memory is None:
            return
        items = [x for x in followups if isinstance(x, dict)]
        room = self.CHAIN_ROOM.get(chain, 0)
        if len(items) > room:
            self.log(f"  {len(items) - room} follow-up(s) not planned: the matter has had its course")
        names = {p.name.lower(): p.name for p in snap.court}
        for f in items[:room]:
            who = names.get(str(f.get("who", "")).strip().lower(), "")
            kind = ("knock" if f.get("kind") == "audience" and who else
                    "chronicle" if aftermath or f.get("kind") == "chronicle" else "story")
            about = _t(f.get("about"), 300) or default
            days = max(5, min(365, int(f.get("after_days") or 30)))
            self.memory.plan(snap.date, after_days=days, kind=kind, about=about, who=who, chain=chain + 1)
            self.log(f"  follow-up expected in {days} days: {about}")

    def _plan_results(self, snap: Snapshot, items: list[Any], *, chain: int = 0) -> None:
        """What the ruler set in motion always comes back: the answer of the envoy, the
        outcome of the inquiry - when it would really be known. Not from a scene that is
        already a follow-up: carrying out what was settled there needs no further report."""
        if self.memory is None:
            return
        if chain >= 1:
            if items:
                self.log("  no report planned: this scene already came of an earlier one")
            return
        names = {p.name.lower(): p.name for p in snap.court}
        for f in [x for x in items if isinstance(x, dict) and _t(x.get("what"), 300)][:3]:
            what = _t(f.get("what"), 300)
            # Already coming back (a follow-up of this scene, or an earlier plan): once is enough.
            if any(_similar(what, p.get("about", "")) for p in self.memory.plans.values()):
                self.log(f"  result already expected: {what[:120]}")
                continue
            who = names.get(str(f.get("who", "")).strip().lower(), "")
            kind = "knock" if f.get("kind") == "audience" and who else "story"
            days = max(5, min(365, int(f.get("after_days") or 30)))
            about = "the answer or result of what the ruler set in motion: " + what
            self.memory.plan(snap.date, after_days=days, kind=kind, about=about, who=who, chain=chain + 1)
            self.log(f"  result expected in {days} days: {what[:160]}")

    def _settle(self, snap: Snapshot, titles: list[Any]) -> None:
        """Matters this scene concluded no longer come back as if still open."""
        mem = self.memory
        if mem is None:
            return
        for raw in [t for t in titles if isinstance(t, str) and t.strip()][:4]:
            want = raw.strip().lower()

            def hit(name: str) -> bool:
                name = (name or "").strip().lower()
                return bool(name) and (name in want or want in name)

            for key, th in list(mem.threads.items()):
                if th.state == "open" and (hit(th.title) or hit(key)):
                    mem.close_thread(key, note="settled", date=snap.date)
                    self.log(f"  settled: {th.title}")
            for aid, arc in list(mem.open_arcs().items()):
                if hit(arc.get("title", "")):
                    mem.arc_step(snap.date, aid, text="Settled in a conversation with the ruler.", after_days=0,
                                 close=True)
                    self.log(f"  story closed, settled: {arc.get('title', '')}")

    def on_suggest(self) -> None:
        """The ruler asked for a few ideas of what to say (the quiet line in the panel)."""
        self._touched = time.time()
        session = self.session
        if session is None or session.closed or session.kind in ("advisor", "decree", "hub", "battle", "book",
                                                                 "life", "century"):
            self._ui("set_suggestions", [])
            return
        self._submit(lambda: self._suggest(session))

    def on_offer(self, idx: int) -> None:
        self._touched = time.time()
        session = self.session
        if session is None:
            return
        if idx < 0 or idx >= len(session.offers):
            self._ui("set_offers", [o["label"] for o in session.offers])
            return
        offer = session.offers.pop(idx)
        session.decisions.append(offer)
        cost = A.ORDER_COST_TEXT.get(offer["kind"], "")
        self._ui("add_note", f"⚔ Decided: {offer['label']} - it happens when you confirm the outcome"
                             + (f". It will cost the realm: {cost}." if cost else "."), "good")
        session.transcript.append(f"THE RULER'S DECISION: {offer['label']}")
        session.messages.append({"role": "user", "content": (
            f"The ruler has decided, and it is done: {offer['label']}. React to it.")})
        if self.memory:
            self.memory.remember_event(session.snap.date, "decision", offer["label"])
        self._ui("set_offers", [o["label"] for o in session.offers])
        self._submit(lambda: self._turn(session))

    def on_close(self) -> None:
        session = self.session
        self._ui("close")
        # The game was paused while the ruler talked: listen closely again.
        self._pulse_gap = self.cfg.pulse_interval_s
        self._last_pulse = min(self._last_pulse, time.time() - self._pulse_gap + 5)
        if session is not None and session.kind == "battle":
            self.session = None
            self.battle.on_close(session)
            return
        if session is None or session.closed:
            return
        session.closed = True
        if session.kind == "decree":
            if session.draft:
                self._proclaim(session)
            else:
                self.mail.send(["votc_in_close = yes"], label="close")
            self.session = None
            return
        if session.kind == "advisor":
            # Nothing waits for the advisor in game: just close.
            self.session = None
            return
        if session.kind in ("hub", "biographer", "book", "life", "century") or session.turns == 0:
            # The biographer's audience changes only the book: nothing to tell in game.
            self.session = None
            self.mail.send(["votc_in_close = yes"], label="close")
            return
        self._submit(lambda: self._close(session))

    def _close(self, session: Session) -> None:
        if session.referee:
            self._judge_scene(session)
        happening = [f"- DONE BY THE RULER'S ORDER: {d['label']} (it costs the realm some "
                     f"{A.ORDER_COST_TEXT.get(d['kind'], 'standing')})" for d in session.decisions]
        happening += [f"- consequence: {A.describe_action(a)}" for a in session.acts]
        happening += [f"- land: {c['label']} (the agreement: {c['terms']})" for c in session.cessions]
        happening += [f"- work ordered: {w['label']} (it costs: {W.price_text(w)})" for w in session.works]
        happening += [f"- how the realm and the world took it: {r['summary']}" + (f" ({r['roll']})" if r["roll"] else "")
                      for r in session.reactions]
        happening += [f"- {m['label']}" for m in session.standing]
        happening += [f"- NOT DONE: {x}" for x in session.refused]
        bio = self._biographer(session.snap)
        task = ("" if session.narrated else prompts.text("narrator") + "\n\n") \
            + (self._biographer_brief(bio) + "\n\n" if bio else "") + prompts.close_task() \
            + "\n\nWHAT THE GAME APPLIES NOW:\n" + (
            "\n".join(happening) if happening else "- nothing: no war, no treaty, no change of law or policy")
        messages = session.messages + [{"role": "user", "content": task}]
        try:
            schema = prompts.outcome_schema_story() if session.arc_id else prompts.outcome_schema()
            if bio:
                schema["properties"]["byline"] = {
                    "type": "string",
                    "description": (f"One short line naming who wrote this entry, in the language of the entry, "
                                    f"e.g. 'From the book of {bio.get('name', '')}, biographer to the "
                                    f"{session.snap.ruler_title or 'ruler'}'. Max 90 characters.")}
                schema["required"] = list(schema["required"]) + ["byline"]
            data = self.client.complete_json(messages, schema, schema_name="votc_outcome",
                                             temperature=0.7, max_tokens=2500)
        except Player2Error as exc:
            self.log(f"Outcome not generated: {exc}")
            data = {"title": session.header.title, "body": "\n".join(session.transcript[-3:]), "memory_note": ""}
        if len(str(data.get("body") or "")) < 450:
            # A three-line aftermath reads as a note, not a scene: ask once more.
            try:
                again = self.client.complete_json(
                    messages + [{"role": "assistant", "content": str(data.get("body") or "")},
                                {"role": "user", "content": (
                                    "Too short: that is a note, not the day's entry. Write it again, 150-300 "
                                    "words, as the ruler's biographer: where and who, what was decided with "
                                    "a word really spoken, how each took it, what was done that day, what "
                                    "was said by evening - no hindsight.")}],
                    schema, schema_name="votc_outcome", temperature=0.7, max_tokens=2500)
                self.log(f"  outcome too short ({len(str(data.get('body') or ''))} characters), rewritten: "
                         f"{len(str(again.get('body') or ''))} characters")
                if len(str(again.get("body") or "")) > len(str(data.get("body") or "")):
                    data = {**data, **again}
            except Player2Error:
                pass
        if bio:
            data = self._indirect(messages, data, "body", schema, "votc_outcome", 2500)
        title = _t(data.get("title"), 70) or session.header.title
        body = _cap(data.get("body"), 2000) or "..."
        byline = _t(data.get("byline"), 100).strip(" -—:") if bio else ""
        if self.memory:
            self.memory.add_page(session.snap.date, session.kind, title, body, by=bio.get("name", "") if bio else "")
        self._book_note(session.snap.date, f"{title}: {_t(data.get('memory_note'), 200) or _t(body, 200)}",
                        weight=2 if session.acts or session.decisions else 1,
                        by=bio.get("name", "") if bio else "")
        if byline:
            # Said plainly on the page: the ruler reads the book of a person of the court.
            body = _cap(f"— {byline} —\n\n{body}", 2150)
        self.last_scene = {"title": title, "body": body, "kind": session.kind}
        if self.memory and _t(data.get("memory_note"), 300):
            self.memory.remember_event(session.snap.date, session.kind, _t(data.get("memory_note"), 300))
        if self.memory:
            self.memory.meta["last_scene"] = session.snap.date; self.memory._meta_dirty = True
        self._prepare_orders(session.snap, session.decisions)
        ids = self._order_entries(session.decisions) + [A.action_variant(a) for a in session.acts]
        self._note_great(session.acts, session.snap)
        marks: list[str] = []
        if session.cessions:
            marks = self._cession_marks(session.cessions, "q")
            ids.append(A.cession_variant("q"))
        works = self._work_lines(session.works)
        works += [line for deal in session.trade for line in deal["lines"]]
        works += [line for r in session.reactions for line in r["lines"]]
        names: dict[str, str] = {}
        works += self._apply_standing(session.snap, session.standing, names)
        ids += self._order_entries([o for r in session.reactions for o in r["orders"]])
        for r in session.reactions:
            self._plan_followups(session.snap, r["followups"], "what came of the ruler's deed", chain=session.chain)
        script = marks + works + A.queue_lines(ids) + ["votc_show_outcome = yes", "votc_in_close = yes"]
        requested = A.requested_text(session.acts, session.decisions)
        requested += "".join(f"\n{w['label']}" for w in session.works)
        requested += "".join(f"\n{d['label']}" for d in session.trade)
        requested += "".join(f"\n{m['label']}" for m in session.standing)
        if session.cessions:
            requested += "".join(f"\n{c['label']}" for c in session.cessions)
        self.mail.send(script, loc={"votc_out_title": title, "votc_out_body": with_gist(data.get("gist"), body),
                                    "votc_out_requested": requested, **names}, label="outcome")
        self._remember_measures(session.snap, session.acts, title)
        self._plan_followups(session.snap, data.get("followups") or [], f"what came of: {title}", chain=session.chain)
        self._plan_results(session.snap, data.get("set_in_motion") or [], chain=session.chain)
        self._settle(session.snap, data.get("settled") or [])
        self._touch_pacts(session.snap, data.get("pacts_touched") or [])
        if session.arc_id and self.memory:
            goes_on = bool(data.get("story_goes_on"))
            self.memory.arc_step(session.snap.date, session.arc_id,
                                 text=f"The ruler answered in person: {_t(data.get('memory_note'), 300) or title}",
                                 after_days=int(data.get("next_after_days") or 30), close=not goes_on)
            self.log("  the story " + ("goes on" if goes_on else "ends"))
        self.log(f"Outcome sent to the game: {title} ({len(session.pending)} consequences)")
        self._log_scene_cost()
        if self.session is session:
            self.session = None

    # ============================================================= biographer
    def _biographer(self, snap: Snapshot) -> dict[str, Any] | None:
        """The person who writes the book of the ruler's life: found when first needed,
        each one a different kind of writer, the ruler's wish heeded after a dismissal."""
        mem = self.memory
        if mem is None:
            return None
        if mem.biographer:
            return mem.biographer
        past = mem.biographers
        wish = past[-1].get("wish", "") if past and past[-1].get("end") == "dismissed" else ""
        used = {b.get("kind", "") for b in past[-4:]}
        kind = wish or random.choice([k for k in prompts.BIOGRAPHER_KINDS if k not in used]
                                     or prompts.BIOGRAPHER_KINDS)
        stance = random.choice(prompts.BIOGRAPHER_STANCES)
        extra = (f" The ruler asked for a writer of this kind; the court found the closest it could."
                 if wish else "")
        ruler = snap.ruler
        realm = (f"{snap.ruler_title or 'Ruler'} {ruler.name if ruler else ''} of {snap.long_name or snap.name}; "
                 f"{snap.date}; capital {snap.capital}; culture {snap.culture}; faith {snap.religion}; "
                 f"government {snap.government}.")
        others = ", ".join(b.get("name", "") for b in past[-6:] if b.get("name"))
        ask = "\n".join(x for x in (
            prompts.BIOGRAPHER_TASK.format(kind=kind, stance=stance, extra=extra), "",
            "THE RULER AND THE REALM: " + realm,
            "THE AGE: the Age of " + prompts.age_of((gamedate.parse(snap.date) or (1337,))[0])[1]
            + " - a person, a learning and a manner of writing that belong to it.",
            (mem.realm_profile or "")[:600],
            f"Earlier biographers of this court (never reuse their names or their manner): {others}." if others else "",
        ) if x is not None)
        try:
            data = self.client.complete_json([{"role": "user", "content": ask}], prompts.BIOGRAPHER_SCHEMA,
                                             schema_name="votc_biographer", temperature=0.95, max_tokens=1200)
        except Player2Error as exc:
            self.log(f"  no biographer could be found: {exc}")
            return None
        name = _t(data.get("name"), 60)
        persona = _t(data.get("persona"), 1200)
        origin = _t(data.get("origin"), 200)
        if len(origin) > 70:
            origin = origin.split(",")[0][:70].rsplit(" ", 1)[0] if len(origin.split(",")[0]) > 70 else origin.split(",")[0]
        if origin[:1].isupper() and origin.split(" ", 1)[0] in ("Born", "A", "An", "The", "Once", "Son", "Daughter",
                                                               "Widow", "Former", "Old", "Young"):
            origin = origin[0].lower() + origin[1:]
        data["origin"] = origin
        if not name or not persona:
            return None
        year = (gamedate.parse(snap.date) or (0, 1, 1))[0]
        age = max(20, min(75, int(data.get("age") or 40)))
        mem.appoint_biographer(snap.date, name=name, origin=_t(data.get("origin"), 80), persona=persona,
                               female=bool(data.get("female")), born=year - age, kind=kind, stance=stance,
                               last_check=year)
        mem.set_persona(name, persona)
        mem.save(force=True)
        self._book_note(snap.date, f"{name}, {_t(data.get('origin'), 80)}, takes up the book as royal biographer.")
        self.log(f"A new royal biographer: {name}, {_t(data.get('origin'), 80)} ({age}).")
        return mem.biographer

    def _biographer_brief(self, bio: dict[str, Any]) -> str:
        """Who writes this entry, how, and whether they speak their mind this time."""
        mem = self.memory
        since = f" since {bio.get('since')}" if bio.get("since") else ""
        parts = [prompts.BIOGRAPHER_VOICE.format(name=bio.get("name", ""), origin=bio.get("origin", ""),
                                                 since=since, persona=bio.get("persona", ""))]
        # Now and then - not every time - they let their own opinion in.
        parts.append(prompts.BIOGRAPHER_OPINION if random.random() < 0.35 else prompts.BIOGRAPHER_RESERVE)
        wrote = mem is not None and any(p.get("by") == bio.get("name") for p in mem.pages)
        if mem is not None and not wrote and mem.biographers:
            prev = mem.biographers[-1]
            how = {"died": "died", "dismissed": "was dismissed by the ruler"}.get(prev.get("end", ""), "left the court")
            parts.append(prompts.BIOGRAPHER_FIRST.format(name=bio.get("name", ""), until=prev.get("until", ""),
                                                         prev=prev.get("name", ""), how=how))
        return "\n\n".join(parts)

    def _biographer_ages(self, snap: Snapshot) -> None:
        """Once a year the biographer may die, as people of their age do."""
        mem = self.memory
        bio = mem.biographer if mem else None
        if not bio:
            return
        year = (gamedate.parse(snap.date) or (0, 1, 1))[0]
        if not year or int(bio.get("last_check") or 0) >= year:
            return
        mem.biographer_checked(snap.date, year)
        age = year - int(bio.get("born") or year - 40)
        chance = 0.02 if age < 50 else 0.05 if age < 65 else 0.12 if age < 75 else 0.3
        if random.random() >= chance:
            return
        name = bio.get("name", "")
        mem.end_biographer(snap.date, "died")
        mem.remember_event(snap.date, "biographer",
                           f"{name}, the royal biographer, died at {age}. The book of the ruler's life passes to "
                           f"another hand.")
        mem.save(force=True)
        self._book_note(snap.date, f"The royal biographer {name} died, aged {age}.")
        self.log(f"The royal biographer {name} has died, aged {age}. The next entry will be written by a successor.")

    def _prepare_biographer(self, session: Session, snap: Snapshot) -> None:
        """The audience with the biographer: their book open on the desk."""
        bio = self._biographer(snap)
        if not bio or self.memory is None:
            self._ui("add_note", "There is no biographer at court yet: Player2 could not be reached.")
            self._ui("set_busy", "")
            return
        name, origin = bio.get("name", ""), bio.get("origin", "")
        session.header = Header(KIND_LABEL["biographer"], name, origin)
        self._ui("open", session.header)
        session.cast = [name]
        session.moods = _moods_of_the_day(session.cast)
        pages = [p for p in self.memory.pages if p.get("by") == name][-3:]
        entries = "\n\n".join(f"[{p.get('date', '')}] {p.get('title', '')}\n{p.get('body', '')}" for p in pages)
        rules = prompts.text("biographer_audience").replace("{name}", name).replace("{origin}", origin)
        extra = rules + "\n\nTHEIR LAST ENTRIES (the latest last):\n" + (entries or "(nothing written yet)")
        session.system = prompts.system_prompt(
            language=self.cfg.language, snap=snap, mode="biographer", codex_digest="",
            narrator=False, summon=False, business=False, in_world=True,
            memory_brief=self.memory.brief(focus=(name,)), changes_text="",
            currencies=_currencies_for(snap), realm_profile=self._ensure_realm_profile(snap),
            difficulty=self.cfg.difficulty, extra=extra,
            personas=f"{name} ({origin}; the royal biographer): {bio.get('persona', '')}",
            setting=self._setting(snap),
        )
        session.messages = [{"role": "system", "content": session.system}]
        session.opener = prompts.BIOGRAPHER_OPENER
        self._ui("add_note", _waiting_note("biographer", snap, name))
        self._ui("set_busy", "")
        self._ui("offer_suggestions", True)

    def _biographer_turn(self, session: Session, data: dict[str, Any]) -> None:
        """What the audience did to the book: a page rewritten, a writer dismissed."""
        mem = self.memory
        bio = mem.biographer if mem else None
        if not bio:
            return
        name, date = bio.get("name", ""), session.snap.date
        revised = _cap(data.get("revised_entry"), 2000)
        if revised and len(revised) > 200:
            last = next((p for p in reversed(mem.pages) if p.get("by") == name), {})
            title = _t(data.get("revised_title"), 70) or last.get("title", "") or "The entry, rewritten"
            mem.add_page(date, "revised", title, revised, by=name)
            mem.remember_event(date, "biographer", f"At the ruler's word, {name} rewrote the entry "
                                                   f"'{last.get('title', title)}'.")
            if self.last_scene and self.last_scene.get("title") == last.get("title"):
                self.last_scene = {**self.last_scene, "body": revised}
            self._ui("add_line", f"{title}  \u00b7  as rewritten", revised)
            self._ui("add_note", "The entry is rewritten in the book.")
            self.log(f"  the biographer rewrote the entry: {title}")
        if data.get("dismissed"):
            wish = _t(data.get("successor"), 160)
            mem.end_biographer(date, "dismissed", wish=wish)
            mem.remember_event(date, "biographer", f"The ruler dismissed {name} as royal biographer.")
            self._book_note(date, f"The ruler dismissed {name} as royal biographer.")
            mem.save(force=True)
            self._ui("add_note", f"{name} is no longer the royal biographer. Another will be found"
                                 + (f" - {wish}" if wish else "") + ".")
            self.log(f"  {name} dismissed as royal biographer" + (f"; wanted next: {wish}" if wish else ""))

    # ========================================================= the book of a life
    @property
    def book(self) -> Book | None:
        """The notes for the Life of each ruler of this campaign (biography.json)."""
        if self.memory is None:
            return None
        if self._book is None or self._book_for is not self.memory:
            self._book, self._book_for = Book(self.memory.folder), self.memory
        return self._book

    def _book_note(self, date: str, text: str, *, weight: int = 1, by: str = "", rewind: bool = True) -> None:
        """One line for the book: never read by the AI until the player asks for the Life."""
        snap, book = self.snapshot, self.book
        if book is None or snap is None or not snap.ruler or not snap.ruler.name:
            return
        book.note(book.reign(snap.ruler.name, snap.ruler_title or "", date), date, text, weight=weight, by=by,
                  rewind=rewind)

    # -- what the game itself shows: no AI is needed to write it down
    _MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")
    _RECORD_KINDS = ("religion", "capital", "government", "rank")

    def _nice_date(self, text: str) -> str:
        p = gamedate.parse(text)
        return f"{p[2]} {self._MONTHS[p[1] - 1]}, {p[0]}" if p and 1 <= p[1] <= 12 else text

    def _book_records(self, old: Snapshot | None, snap: Snapshot | None, changes: list[Any]) -> None:
        """Between two pictures of the game: what changed in the realm that a
        biographer would write down, and once a year the realm's accounts."""
        if snap is None or snap.ruler is None:
            return
        if old is not None and old.tag == snap.tag and 0 < gamedate.days_between(old.date, snap.date) <= 400:
            for c in changes:
                if c.key in self._RECORD_KINDS:
                    self._book_note(snap.date, f"[records] {c}", weight=2)
                elif c.key in ("locations", "subjects"):
                    moved = float(c.after) - float(c.before)
                    what = "holdings" if c.key == "locations" else "subject realms"
                    verb = "grew" if moved > 0 else "shrank"
                    self._book_note(snap.date, f"[records] the realm's {what} {verb} from {c.before:g} to {c.after:g}",
                                    weight=2 if abs(moved) >= 2 or c.key == "subjects" else 1)
                elif c.key in ("loans", "court"):
                    self._book_note(snap.date, f"[records] {c}", weight=1)
        self._book_year(snap)

    def _book_year(self, snap: Snapshot) -> None:
        """The realm's accounts in one line, once a game year."""
        book, n = self.book, snap.numbers
        if book is None or not n or snap.ruler is None:
            return
        bits = []
        for key, label, fmt in (("gold", "treasury", "{:.0f}"), ("balance", "monthly balance", "{:+.0f}"),
                                ("loans", "loans", "{:.0f}"), ("stability", "stability", "{:.0f}"),
                                ("prestige", "prestige", "{:.0f}"), ("legitimacy", "legitimacy", "{:.0f}"),
                                ("army", "army", "{:g}"), ("navy", "fleet", "{:g}"), ("manpower", "manpower", "{:.0f}"),
                                ("population", "population", "{:.0f}"), ("development", "development", "{:.0f}"),
                                ("locations", "holdings", "{:.0f}"), ("subjects", "subjects", "{:.0f}"),
                                ("allies", "allies", "{:.0f}"), ("rivals", "rivals", "{:.0f}"),
                                ("warexhaustion", "war exhaustion", "{:.0f}"),
                                ("religiousunity", "religious unity", "{:.0f}%")):
            if key in n and (n[key] or key in ("gold", "balance", "loans", "stability", "warexhaustion",
                                                   "subjects", "allies", "rivals")):
                bits.append(f"{label} " + fmt.format(n[key]))
        if snap.at_war:
            bits.append("AT WAR")
        estates = ", ".join(f"{snap.estate_names.get(k) or k} {v:.0f}" for k, v in snap.estates.items() if v)
        if estates:
            bits.append("estates' satisfaction: " + estates)
        book.year_line(snap.date, snap.ruler.name, "; ".join(bits))

    def _book_world(self, world: Any) -> None:
        """From a save: wars, alliances, rivals, subjects, laws, marriages, heirs
        and the game's own events since the last save read - set down as the
        realm's records, dated as the game dated them."""
        book, snap = self.book, self.snapshot
        if book is None or snap is None or world is None:
            return
        cid = world.by_tag.get(snap.tag)
        me = world.countries.get(cid) if cid is not None else None
        if me is None:
            return
        wk = gamedate.key(world.date)
        date = self._nice_date(world.date)

        def cname(c: int) -> str:
            return self._cname(world.tag(c))

        def person(ch_id: int) -> str:
            ch = world.characters.get(ch_id)
            return ch.first_name if ch and ch.first_name else ""

        over = world.overlord_of(cid)
        now = {
            "wars": {str(w.key): (f"{', '.join(cname(a) for a in w.attackers[:3])} against "
                                  f"{', '.join(cname(d) for d in w.defenders[:3])}",
                                  "attacks" if cid in w.attackers else "defends")
                     for w in world.wars_of(cid)},
            "allies": sorted(world.tag(a) for a in world.allies_of(cid)),
            "rivals": sorted(world.tag(r) for r in world.rivals.get(cid, [])),
            "subjects": sorted(f"{world.tag(sub)}|{kind}" for sub, kind in world.subjects_of(cid)),
            "overlord": f"{world.tag(over[0])}|{over[1]}" if over else "",
            "laws": dict(me.laws),
            "ruler": me.ruler, "consort": me.consort, "heir": me.heir,
        }
        seen = book.data.get("seen") or {}
        if not seen or wk < seen.get("date", 0) or seen.get("tag") != snap.tag:
            if not seen:
                # The book opens: the realm as it stands, not as news.
                bits = []
                if now["allies"]:
                    bits.append("allied with " + ", ".join(self._cname(t) for t in now["allies"]))
                if now["rivals"]:
                    bits.append("rival of " + ", ".join(self._cname(t) for t in now["rivals"]))
                if now["wars"]:
                    bits.append("at war: " + "; ".join(d for d, _ in now["wars"].values()))
                if now["overlord"]:
                    bits.append("subject of " + self._cname(now["overlord"].split("|")[0]))
                if now["subjects"]:
                    bits.append("overlord of " + ", ".join(self._cname(x.split("|")[0]) for x in now["subjects"]))
                if bits:
                    self._book_note(date, "[records] when the book opens, the realm is " + "; ".join(bits),
                                    weight=2, rewind=False)
            book.data["seen"] = {**now, "date": wk, "events": wk, "tag": snap.tag}
            book.save()
            return
        if wk <= seen.get("date", 0):
            return
        notes: list[tuple[str, str, int]] = []
        for key, (desc, side) in now["wars"].items():
            if key not in seen.get("wars", {}):
                notes.append((date, f"[records] war: {desc} - the realm {side}", 2))
        for key, value in (seen.get("wars") or {}).items():
            if key not in now["wars"]:
                notes.append((date, f"[records] peace: the war of {value[0]} is over", 2))
        for label, key, weight in (("alliance with", "allies", 2), ("rivalry with", "rivals", 1)):
            for t in set(now[key]) - set(seen.get(key, [])):
                notes.append((date, f"[records] new {label} {self._cname(t)}", weight))
            for t in set(seen.get(key, [])) - set(now[key]):
                notes.append((date, f"[records] the {label} {self._cname(t)} ended", weight))
        for x in set(now["subjects"]) - set(seen.get("subjects", [])):
            t, kind = x.split("|", 1)
            notes.append((date, f"[records] {self._cname(t)} became a subject of the realm ({self.codex.word(kind)})", 2))
        for x in set(seen.get("subjects", [])) - set(now["subjects"]):
            notes.append((date, f"[records] {self._cname(x.split('|')[0])} is no longer a subject of the realm", 2))
        if now["overlord"] != seen.get("overlord", ""):
            if now["overlord"]:
                notes.append((date, f"[records] the realm became a subject of {self._cname(now['overlord'].split('|')[0])}", 2))
            else:
                notes.append((date, "[records] the realm is no longer anyone's subject", 2))
        for law, policy in now["laws"].items():
            before = (seen.get("laws") or {}).get(law)
            if before and before != policy:
                notes.append((date, f"[records] new law - {self.codex.word(law)}: {self.codex.word(policy)} "
                                    f"(was {self.codex.word(before)})", 2))
        if now["ruler"] == seen.get("ruler"):
            # the same ruler: a new consort is a marriage, a new heir a change in the succession
            if now["consort"] and now["consort"] != seen.get("consort") and person(now["consort"]):
                notes.append((date, f"[records] the ruler married {person(now['consort'])}", 2))
            if now["heir"] and now["heir"] != seen.get("heir") and person(now["heir"]):
                notes.append((date, f"[records] {person(now['heir'])} became heir to the throne", 1))
        # The game's own events, by title. A title that begins in lower case is
        # the tail of one the game composes at run time ("... and the People"):
        # without its head it would mislead, so it is left out; one that comes
        # back every year is taken down once in three years.
        reign = book.current(snap.ruler.name) if snap.ruler else None
        recent = {n[3]: n[0] for n in (reign or {}).get("notes", [])[-120:]}
        titles = set()
        for e in world.events:
            k = gamedate.key(e.date)
            if e.country != cid or not seen.get("events", 0) < k <= wk or e.key.startswith("votc"):
                continue
            title = self.codex.event_title(e.key).strip()
            if not title or not title[:1].isupper() or title in titles:
                continue
            text = f"[records] the realm's event \u00ab{title}\u00bb"
            if k - recent.get(text, -10**9) < 30000:
                continue
            titles.add(title)
            recent[text] = k
            notes.append((self._nice_date(e.date), text, 1))
        for when, text, weight in sorted(notes, key=lambda x: gamedate.key(x[0])):
            self._book_note(when, text, weight=weight, rewind=False)
        book.data["seen"] = {**now, "date": wk, "events": wk, "tag": snap.tag}
        book.save()
        if notes:
            self.log(f"  the book takes down {len(notes)} things from the save of {date}")

    def _book_system(self, bio: dict[str, Any] | None) -> str:
        lang = prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language)
        who = ""
        if bio:
            who = (f"\n\nTHE BIOGRAPHER WHO HOLDS THE PEN NOW: {bio.get('name', '')}, {bio.get('origin', '')}, "
                   f"biographer since {bio.get('since', '')}. Their character:\n{bio.get('persona', '')}")
        return (prompts.text("book") + who + "\n\n" + prompts.player_block("narration", "setting", "avoid")
                + f"\n\nLANGUAGE: the book is written in {lang}.")

    def _ruler_facts(self, snap: Snapshot, reign: dict[str, Any], *, since: int = 0, lines: int = 30) -> str:
        r = snap.ruler
        alive = r is not None and r.name == reign["ruler"]
        facts = [f"{reign.get('title') or snap.ruler_title or 'Ruler'} {reign['ruler']} of {snap.long_name or snap.name}"
                 f"; culture {snap.culture}; faith {snap.religion}; capital {snap.capital}."]
        y1 = (gamedate.parse(reign.get("start", "")) or (0,))[0]
        y2 = (gamedate.parse(reign.get("end") or snap.date) or (0,))[0]
        if y1 and y2:
            ages = [n for _k, y, n in prompts.AGES if y1 < y <= y2]
            facts.append(f"The reign began in the Age of {prompts.age_of(y1)[1]}"
                         + (f" and lived into the Age of {', then of '.join(ages)}" if ages else "") + ". "
                         + prompts.scale_of(snap.n("locations"), snap.rank)[0].capitalize() + " by the end of the book.")
        if alive and r.age:
            facts.append(f"Now {r.age:g} years old; administration {r.adm:g}, diplomacy {r.dip:g}, military {r.mil:g}.")
        facts.append(f"The book begins on {reign.get('start', '')}" + (f" and the reign ended on {reign['end']}."
                                                                        if reign.get("end") else "."))
        if self.memory and self.memory.summary:
            facts.append("WHAT THE COURT REMEMBERS OF THE REIGN: " + self.memory.summary.strip()[:2500])
        if self.memory and self.memory.realm_profile:
            facts.append("THE REALM: " + self.memory.realm_profile.strip()[:600])
        if self.book is not None:
            years = self.book.years_between(max(since, gamedate.key(reign.get("start", ""))),
                                            gamedate.key(reign.get("end", "")))
            if years:
                facts.append("THE REALM'S ACCOUNTS, YEAR BY YEAR (from the game's own registers):\n"
                             + Book.ledger(years, lines))
        return "\n".join(facts)

    def _book_so_far(self, snap: Snapshot, reign: dict[str, Any]) -> list[dict[str, Any]]:
        """The Life up to today. Only what is new since the last reading is written:
        the chapters already there cost nothing to read again."""
        book = self.book
        assert book is not None
        date = snap.date
        chapters = book.chapters_upto(reign, date)
        last_upto = chapters[-1]["upto"] if chapters else 0
        new = book.notes_upto(reign, date, after=last_upto)
        if chapters and not new:
            return chapters
        bio = self._biographer(snap)
        if bio is None:
            return chapters
        name = bio.get("name", "")
        extend = bool(chapters) and chapters[-1]["by"] == name and len(chapters[-1]["text"]) < 2600
        notes = new
        if extend:
            notes = book.notes_upto(reign, date, after=chapters[-2]["upto"] if len(chapters) > 1 else 0)
            what = ("rewrite the LAST CHAPTER below so that it now runs up to the present - keep what is good in "
                    "it and weave the new notes in")
        elif chapters:
            what = "a new chapter that continues the book from where it stops"
        else:
            what = ("the opening chapter: who this ruler is, how they came to the throne as far as is known, and "
                    "the reign up to today")
        length = min(2600, max(700, 500 + 220 * len(notes)))
        how = ("" if not chapters or chapters[-1]["by"] == name
               else f"The chapters before were written by {chapters[-1]['by']}; the pen is now {name}'s.")
        # only the accounts of the years this chapter covers
        since = (chapters[-2]["upto"] if len(chapters) > 1 else 0) if extend else last_upto
        ask = [self._ruler_facts(snap, reign, since=since, lines=8)]
        if chapters and not extend:
            ask.append("THE BOOK SO FAR ENDS WITH:\n..." + chapters[-1]["text"][-700:])
        if extend:
            ask.append("THE LAST CHAPTER, TO REWRITE:\n" + chapters[-1]["text"])
        ask.append("NOTES OF WHAT HAPPENED (oldest first):\n" + (Book.material(notes, 9000) or "(nothing yet)"))
        ask.append(prompts.BOOK_CHAPTER_TASK.format(
            ruler=reign["ruler"], date=date, name=name, how=how, length=length, limit=length + 400, what=what,
            language=prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language)))
        msgs = [{"role": "system", "content": self._book_system(bio)}, {"role": "user", "content": "\n\n".join(ask)}]
        try:
            data = self.client.complete_json(msgs, prompts.BOOK_SCHEMA, schema_name="votc_book", temperature=0.8,
                                             max_tokens=3000)
        except Player2Error as exc:
            self.log(f"  the book could not be written: {exc}")
            return chapters
        data = self._indirect(msgs, data, "text", prompts.BOOK_SCHEMA, "votc_book", 3000)
        text = _cap(data.get("text"), length + 600)
        if not text:
            return chapters
        chapter = {"upto": gamedate.key(date), "by": name, "text": text}
        chapters = chapters[:-1] + [chapter] if extend else chapters + [chapter]
        reign["chapters"] = self._book_fit(chapters)
        book.save()
        self.log(f"The book of {reign['ruler']}'s life brought up to {date} "
                 f"({sum(len(c['text']) for c in reign['chapters'])} characters).")
        return reign["chapters"]

    def _book_fit(self, chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Never more than BOOK_LIMIT characters: the oldest chapters are told more briefly."""
        lang = prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language)
        for _ in range(8):
            if sum(len(c["text"]) for c in chapters) <= BOOK_LIMIT or len(chapters) < 2:
                break
            i = next((k for k, c in enumerate(chapters[:-1]) if len(c["text"]) > 800), None)
            if i is None:
                a, b = chapters[0], chapters[1]
                chapters = [{"upto": b["upto"], "by": a["by"] if a["by"] == b["by"] else f"{a['by']}; {b['by']}",
                             "text": a["text"] + "\n\n" + b["text"]}] + chapters[2:]
                i = 0
            c = chapters[i]
            target = max(500, int(len(c["text"]) * 0.55))
            try:
                data = self.client.complete_json(
                    [{"role": "user", "content": (
                        f"Below is a passage from the Life of a ruler, written by {c['by']}. Tell it again more "
                        f"briefly, in about {target} characters: keep its writer's voice, its best scene and any "
                        f"words quoted; drop detail, never facts that matter. In {lang}. Only the text.\n\n"
                        + c["text"])}],
                    prompts.BOOK_SCHEMA, schema_name="votc_book_short", temperature=0.6, max_tokens=1500)
                short = _cap(data.get("text"), target + 300)
            except Player2Error:
                short = ""
            chapters[i] = {**c, "text": short or _cap(c["text"], target)}
        return chapters

    def _reign_ends(self, snap: Snapshot, ruler: str, title: str) -> None:
        """The ruler is gone: the biographer's page on their life and works, shown in game."""
        book = self.book
        if book is None:
            return
        reign = book.current(ruler) or book.reign(ruler, title, snap.date)
        book.end_reign(ruler, snap.date)
        bio = self._biographer(snap)
        name = bio.get("name", "") if bio else ""
        lang = prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language)
        successor = snap.ruler.name if snap.ruler else "the new ruler"
        ask = "\n\n".join([
            self._ruler_facts(snap, reign, lines=15),
            "NOTES OF THE REIGN (oldest first):\n" + (Book.material(reign["notes"], 12000) or "(few are known)"),
            prompts.BOOK_OBIT_TASK.format(ruler=ruler, date=snap.date, name=name or "The royal biographer",
                                          successor=successor, language=lang)])
        schema = dict(prompts.BOOK_OBIT_SCHEMA, properties=dict(prompts.BOOK_OBIT_SCHEMA["properties"]))
        if name:
            schema["properties"]["byline"] = {
                "type": "string", "description": (f"One short line naming who wrote this page, in the language of "
                                                  f"the page, e.g. 'From the book of {name}, royal biographer'.")}
            schema["required"] = list(schema["required"]) + ["byline"]
        msgs = [{"role": "system", "content": self._book_system(bio)}, {"role": "user", "content": ask}]
        try:
            data = self.client.complete_json(msgs, schema, schema_name="votc_obituary", temperature=0.75,
                                             max_tokens=2000)
        except Player2Error as exc:
            self.log(f"  the account of the reign could not be written: {exc}")
            return
        data = self._indirect(msgs, data, "body", schema, "votc_obituary", 2000)
        head = _t(data.get("title"), 70) or f"The Death of {ruler}"
        body = _cap(data.get("body"), 1800) or "..."
        reign["obit"] = body
        book.save()
        if self.memory:
            self.memory.add_page(snap.date, "obituary", head, body, by=name)
            note = _t(data.get("memory_note"), 300)
            if note:
                self.memory.remember_event(snap.date, "reign", note)
        byline = _t(data.get("byline"), 100).strip(" -\u2014:")
        shown = f"\u2014 {byline} \u2014\n\n{body}" if byline else body
        who = name or "the royal biographer"
        self.mail.send(["votc_show_obituary = yes"], loc={
            "votc_obt_title": head, "votc_obt_body": with_gist(data.get("gist"), shown),
            "votc_obt_read": f"Ask {who} to read you the whole Life of {ruler} (uses many AI tokens)"},
            label="obituary")
        self.log(f"The reign of {ruler} has ended: {who} wrote the account of it.")
        self._book_note(snap.date, f"{successor} comes to the throne on the death of {ruler}.", weight=2)

    _ROMAN = ("I", "II", "III", "IV", "V", "VI")

    def _compose(self, bio: dict[str, Any] | None, *, facts: str, notes: list[list[Any]], what: str,
                 length: int, sources: Callable[[int, int], str] | None = None) -> str:
        """A long book written as a book, not as a pile of daily entries: first
        planned into periods with a line running through them, then told part by
        part, in order, each part from the notes of its own years only."""
        lang = prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language)
        name = bio.get("name", "") if bio else "the royal biographer"
        system = self._book_system(bio)
        picked = Book.pick(notes, 30000)
        if not picked:
            picked = [[0, "", 1, "(almost nothing was written down in these years)", ""]]
        count = len(picked)
        wanted = 1 if count < 10 else 2 if count < 30 else 3 if count < 60 else 4 if count < 100 else 5
        numbered = "\n".join(f"[{i + 1}] {Book.line(n)}" for i, n in enumerate(picked))
        line = ""
        parts: list[dict[str, Any]] = [{"title": "", "first_note": 1, "last_note": count,
                                         "about": "the whole story, from its beginning to its end"}]
        if wanted > 1:
            try:
                plan = self.client.complete_json(
                    [{"role": "system", "content": system},
                     {"role": "user", "content": "\n\n".join([
                         facts, "ALL THE NOTES (numbered, oldest first):\n" + numbered,
                         prompts.OUTLINE_TASK.format(what=what, name=name, last=count,
                                                     parts=f"{max(2, wanted - 1)} to {wanted + 1}")])}],
                    prompts.OUTLINE_SCHEMA, schema_name="votc_outline", temperature=0.6, max_tokens=2500)
                line = _t(plan.get("through_line"), 600)
                got = sorted((p for p in plan.get("parts") or [] if isinstance(p, dict)),
                             key=lambda p: int(p.get("first_note") or 0))
                fixed, start = [], 1
                for i, p in enumerate(got):
                    end = count if i == len(got) - 1 else max(start, min(count, int(p.get("last_note") or start)))
                    if start > count:
                        break
                    fixed.append({"title": _t(p.get("title"), 70), "first_note": start, "last_note": end,
                                  "about": _t(p.get("about"), 500)})
                    start = end + 1
                if fixed:
                    parts = fixed
            except (Player2Error, ValueError, TypeError) as exc:
                self.log(f"  the plan of the book failed ({exc}): writing it in even parts")
                step = -(-count // wanted)
                parts = [{"title": "", "first_note": i + 1, "last_note": min(count, i + step), "about": ""}
                         for i in range(0, count, step)]
        plan_text = "\n".join(f"{self._ROMAN[i] if i < 6 else i + 1}. {p['title']} - {p['about']}"
                               for i, p in enumerate(parts))
        per = max(900, length // len(parts))
        limit = min(per + 600, BOOK_LIMIT // len(parts) - 100)
        # The model is asked for `limit`; a small overrun is kept whole rather than
        # losing the end of a part (a cut would drop the very sentences that close it).
        hard = max(limit + 1000, BOOK_LIMIT // len(parts))
        written: list[str] = []
        for i, part in enumerate(parts):
            seg = picked[part["first_note"] - 1:part["last_note"]]
            if not seg:
                continue
            if len(parts) == 1:
                position = ("Begin with the person (or the realm) as they were when the story starts - not with "
                            "the biographer - and end with a last paragraph that closes it.")
            elif i == 0:
                position = ("This is the OPENING: begin with the person (or the realm) as they were when the story "
                            "starts - never with the biographer taking up the pen.")
            elif i == len(parts) - 1:
                position = ("This is the END of the book: carry on from where the previous part stopped, and close "
                            "it with a last paragraph worthy of it.")
            else:
                position = "Carry on directly from where the previous part stopped (below): no recap."
            ask = [facts]
            src = sources(seg[0][0], seg[-1][0]) if sources else ""
            if src:
                ask.append("THESE YEARS AS THE BIOGRAPHERS WROTE THEM AT THE TIME (material to rework, not to copy; "
                           "keep a line of an earlier biographer's voice where it helps, and say where the pen "
                           "changed hands):\n" + src[:4000])
            ask.append("THE NOTES OF THIS PART (oldest first):\n" + "\n".join(Book.line(n) for n in seg))
            if written:
                ask.append("THE BOOK SO FAR ENDS WITH:\n..." + written[-1][-700:])
            ask.append(prompts.PART_TASK.format(
                n=i + 1, total=len(parts), what=what, title=part["title"], about=part["about"] or "these years",
                line=line or "(see the notes)", plan=plan_text, position=position, length=per, limit=limit,
                language=lang))
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(ask)}]
            try:
                data = self.client.complete_json(msgs, prompts.BOOK_SCHEMA, schema_name="votc_book_part",
                                                 temperature=0.8, max_tokens=3500)
            except Player2Error as exc:
                self.log(f"  part {i + 1} of the book could not be written: {exc}")
                break
            data = self._indirect(msgs, data, "text", prompts.BOOK_SCHEMA, "votc_book_part", 3500)
            text = _cap(data.get("text"), hard)
            if not text:
                continue
            heading = f"{self._ROMAN[i] if i < 6 else i + 1}. {part['title']}" if len(parts) > 1 and part["title"] else ""
            written.append((heading + "\n\n" if heading else "") + text)
            self.log(f"  part {i + 1} of {len(parts)} written ({len(text)} characters)")
        return "\n\n".join(written)

    def _write_life(self, snap: Snapshot, reign: dict[str, Any]) -> str:
        """The whole Life of a dead ruler, written once; then the notes are deleted."""
        if reign.get("life"):
            return reign["life"]
        book = self.book
        assert book is not None
        bio = self._biographer(snap)
        name = bio.get("name", "") if bio else "the royal biographer"
        chapters = reign.get("chapters") or []

        def written_then(k1: int, k2: int) -> str:
            out, prev = [], 0
            for c in chapters:
                if prev < k2 and c["upto"] >= k1:
                    out.append(f"[written by {c['by']}]\n{c['text']}")
                prev = c["upto"]
            return "\n\n".join(out)

        facts = self._ruler_facts(snap, reign)
        if reign.get("obit"):
            facts += "\n\nTHE ACCOUNT WRITTEN ON THE DAY OF THE DEATH:\n" + reign["obit"]
        notes = reign.get("notes") or []
        text = self._compose(bio, facts=facts, notes=notes,
                             what=f"the whole Life of {reign['ruler']} ({reign.get('start', '')} - {reign.get('end', '')})",
                             length=min(BOOK_LIMIT - 1000, 3000 + 160 * len(notes)), sources=written_then)
        if len(text) < 1500:
            return text
        reign["life"], reign["life_by"] = text, name
        book.forget_material(reign)
        if self.memory:
            self.memory.remember_event(snap.date, "biographer", f"{name} finished the Life of {reign['ruler']}.")
        self.log(f"The Life of {reign['ruler']} is written ({len(text)} characters); its notes are deleted.")
        return text

    # ------------------------------------------------------------ a century
    def _campaign_start(self, snap: Snapshot) -> str:
        mem = self.memory
        dated = [e.date for e in (mem.entries.values() if mem else []) if gamedate.key(e.date)]
        return min(dated, key=gamedate.key) if dated else snap.date

    def _century_check(self, snap: Snapshot) -> None:
        """Once a game year: has the realm lived another hundred years since the book began?"""
        book = self.book
        year = (gamedate.parse(snap.date) or (0, 1, 1))[0]
        if book is None or not year or year == self._century_year:
            return
        self._century_year = year
        done = book.centuries_upto(snap.date)
        since = done[-1]["to"] if done else book.start(self._campaign_start(snap))
        if year - (gamedate.parse(since) or (year, 1, 1))[0] < CENTURY:
            return
        century = book.offer_century(since, snap.date)
        self._submit(lambda: self._offer_century(snap, century))

    def _offer_century(self, snap: Snapshot, century: dict[str, Any]) -> None:
        """The biographer proposes the history of the century: only written if the ruler wants it."""
        bio = self._biographer(snap)
        name = bio.get("name", "") if bio else "The royal biographer"
        realm = snap.long_name or snap.name
        lang = prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language)
        title = f"A Hundred Years of {snap.name}"
        gist = ""
        body = (f"{name} comes to you with a bundle of old registers under one arm: a hundred years have passed "
                f"since {century['from']}. They offer to write and read to you the history of {realm} over this "
                f"century.")
        try:
            data = self.client.complete_json(
                [{"role": "system", "content": self._book_system(bio)},
                 {"role": "user", "content": prompts.CENTURY_INVITE_TASK.format(
                     start=century["from"], end=century["to"], name=name, realm=realm, language=lang)}],
                prompts.CENTURY_INVITE_SCHEMA, schema_name="votc_century_invite", temperature=0.8, max_tokens=800)
            title = _t(data.get("title"), 70) or title
            body = _cap(data.get("body"), 900) or body
            gist = _t(data.get("gist"), 240)
        except Player2Error:
            pass
        self.mail.send(["votc_show_century = yes"], loc={
            "votc_cen_title": title, "votc_cen_body": with_gist(gist, body),
            "votc_cen_read": f"Let {name} read you the history of the century (uses many AI tokens)"},
            label="century")
        self.log(f"A hundred years since {century['from']}: {name} offers the history of the century.")

    def _write_century(self, snap: Snapshot, century: dict[str, Any]) -> str:
        if century.get("text"):
            return century["text"]
        book = self.book
        assert book is not None
        bio = self._biographer(snap)
        name = bio.get("name", "") if bio else "the royal biographer"
        k1, k2 = gamedate.key(century["from"]), gamedate.key(century["to"])
        rulers = []
        for r in book.reigns:
            end = gamedate.key(r["end"]) if r.get("end") else k2
            if end >= k1 and gamedate.key(r.get("start", "")) <= k2:
                told = r.get("obit") or r.get("life", "")[:900]
                rulers.append(f"- {r.get('title', '')} {r['ruler']}, {r.get('start', '')} - "
                              f"{r.get('end') or 'reigning today'}" + (f": {told[:900]}" if told else ""))
        facts = [f"THE REALM: {snap.long_name or snap.name}; culture {snap.culture}; faith {snap.religion}; capital "
                 f"{snap.capital}; ruled today by {snap.ruler_title or ''} {snap.ruler.name if snap.ruler else ''}."]
        y1, y2 = k1 // 10000, k2 // 10000
        crossed = [f"the Age of {n} ({y})" for _k, y, n in prompts.AGES if y1 < y <= y2]
        facts.append(f"THE AGES OF THE CENTURY: it began in the Age of {prompts.age_of(y1)[1]}"
                     + (" and passed into " + ", then ".join(crossed) if crossed else "")
                     + f". Today the realm is {prompts.scale_of(snap.n('locations'), snap.rank)[0].lower()}: tell how it "
                       "grew, shrank or changed in kind over the century, and how the age changed around it.")
        years = book.years_between(k1, k2)
        if years:
            facts.append("THE REALM'S ACCOUNTS OVER THE CENTURY (from the game's own registers):\n"
                         + Book.ledger(years, 22))
        if rulers:
            facts.append("THE RULERS OF THESE HUNDRED YEARS (as far as the book knows them):\n" + "\n".join(rulers))
        if self.memory and self.memory.summary:
            facts.append("WHAT THE COURT REMEMBERS: " + self.memory.summary.strip()[:2500])
        if self.memory and self.memory.realm_profile:
            facts.append("THE CHARACTER OF THE REALM TODAY: " + self.memory.realm_profile.strip()[:800])
        notes = book.annals_between(century["from"], century["to"])
        what = prompts.CENTURY_TASK.format(realm=snap.long_name or snap.name, start=century["from"],
                                           end=century["to"], name=name)
        text = self._compose(bio, facts="\n\n".join(facts), notes=notes, what=what,
                             length=min(BOOK_LIMIT - 1000, 4000 + 60 * len(notes)))
        if len(text) < 1500:
            return text
        book.century_written(century, text, name)
        if self.memory:
            self.memory.remember_event(snap.date, "biographer",
                                       f"{name} wrote the history of the realm from {century['from']} to {century['to']}.")
        self.log(f"The history of the century is written ({len(text)} characters); its annals are deleted.")
        return text

    def _read_book(self, snap: Snapshot, what: str, *, in_hub: bool = False, index: int | None = None) -> None:
        """Open a book in the panel: the Life so far ("book"), the whole Life of the
        ruler just dead ("life"), or the history of the last century ("century")."""
        book = self.book
        back = [("books", "\u21A9  Back")] if in_hub else []

        def nothing(note: str) -> None:
            self._ui("add_note", note)
            if in_hub:
                self._ui("set_hub", back)
            else:
                self._ui("set_busy", "")
                self._ui("set_close_label", "Close")

        if book is None or snap.ruler is None:
            nothing("There is no book yet.")
            return
        bio = self.memory.biographer if self.memory else None
        who = bio.get("name", "") if bio else "The biographer"
        if what == "life":
            reign = (book.reigns[index] if index is not None and 0 <= index < len(book.reigns)
                     else book.last_dead())
            if reign is None:
                nothing("No reign has ended yet: there is no finished Life to read.")
                return
            if not reign.get("life"):
                self._ui("set_busy", f"{who} writes the whole Life of {reign['ruler']}…")
            text = self._write_life(snap, reign)
            header = Header(KIND_LABEL["life"], f"{reign.get('title', '')} {reign['ruler']}".strip(),
                            f"{reign.get('start', '')} \u2013 {reign.get('end', '')}"
                            + (f" · by {reign.get('life_by')}" if reign.get("life_by") else ""))
            parts = [(f"The Life of {reign['ruler']}", text)] if text else []
        elif what == "century":
            done = book.centuries_upto(snap.date)
            if not done:
                nothing("A hundred years have not yet passed since the book began.")
                return
            century = done[index] if index is not None and 0 <= index < len(done) else done[-1]
            if not century.get("text"):
                self._ui("set_busy", f"{who} writes the history of the century…")
            text = self._write_century(snap, century)
            header = Header(KIND_LABEL["century"], snap.long_name or snap.name,
                            f"{century['from']} \u2013 {century['to']}"
                            + (f" · by {century.get('by')}" if century.get("by") else ""))
            parts = [(f"{snap.long_name or snap.name}, {century['from']} \u2013 {century['to']}", text)] if text else []
        else:
            reign = book.reign(snap.ruler.name, snap.ruler_title or "", snap.date)
            self._ui("set_busy", f"{who} brings the book up to date…")
            chapters = self._book_so_far(snap, reign)
            header = Header(KIND_LABEL["book"], f"{snap.ruler_title or ''} {snap.ruler.name}".strip(),
                            f"as written up to {snap.date}")
            parts = [(f"In the hand of {c['by']}", c["text"]) for c in chapters]
        if not in_hub:
            self._ui("open", header)
        for title, text in parts:
            self._ui("add_line", title, text)
        if not parts:
            self._ui("add_note", "The book could not be written now (is Player2 running?).")
        self._ui("set_busy", "")
        if in_hub:
            self._ui("set_hub", back)
        else:
            self._ui("set_suggestions", [])
            self._ui("set_close_label", "Close the book")

    # ================================================================ advisor
    def _open_advisor(self, snap: Snapshot) -> None:
        """The AI advisor: out of character, talking to the player."""
        if self.session and not self.session.closed and self.session.kind != "hub":
            self.log("A conversation was still open: closing it to open the advisor.")
        header = Header(KIND_LABEL["advisor"], "Outside the roleplay",
                        f"{snap.long_name or snap.name} · {snap.date}")
        session = Session(kind="advisor", snap=snap, header=header)
        self.session = session
        self._ui("open", header)
        self._ui("set_close_label", "Close")
        self._ui("add_line", "Advisor", prompts.ADVISOR_OPENING)
        self._ui("set_suggestions", prompts.ADVISOR_STARTERS)
        self.log(f"[AI Advisor] {snap.date}")

    def _advisor_ask(self, session: Session, text: str) -> None:
        session.transcript.append(f"Player: {text}")
        question = text
        years = [int(y) for y in re.findall(r"\b(1[2-9]\d\d)\b", text)]
        if years and self.memory:
            lo, hi = min(years), max(years)
            found = self.memory.records(start=lo * 10000, end=hi * 10000 + 1231, limit=400)
            question += (f"\n\n(THE CAMPAIGN RECORD FOR {lo}-{hi}, complete:)\n"
                         + ("\n".join(found) if found else "(nothing recorded in those years)"))
        elif self.memory:
            # Older entries of the record that touch the question (the recent part is always there).
            lines = self.memory.recall(text, limit_chars=1600, skip_recent=self.ADVISOR_RECORDS)
            if lines:
                question += "\n\n(OLDER RECORDS THAT TOUCH THE QUESTION:)\n" + "\n".join(lines)

        def job() -> None:
            if session is not self.session or session.closed:
                return
            snap = self.snapshot or session.snap
            if not session.messages:
                session.system = prompts.advisor_system_prompt(
                    language=self.cfg.language, snap=snap, world_text=self._world_text(snap),
                    codex_digest=self._digest(snap),
                    memory_brief=self.memory.brief() if self.memory else "",
                    records=self.memory.records(limit=self.ADVISOR_RECORDS) if self.memory else [],
                    realm_profile=self.memory.realm_profile if self.memory else "")
                session.messages = [{"role": "system", "content": session.system}]
            session.messages.append({"role": "user", "content": question})
            self._ui("set_busy", "The advisor is thinking…")
            data = self.client.complete_json(session.messages, prompts.advisor_schema(),
                                             schema_name="votc_advisor", temperature=0.6)
            if session is not self.session or session.closed:
                return
            answer = _cap(data.get("answer"), 2500) or "…"
            session.turns += 1
            session.messages.append({"role": "assistant", "content": answer})
            session.transcript.append(f"Advisor: {answer}")
            self._ui("add_line", "Advisor", answer)
            self._ui("set_suggestions", [_t(s, 240) for s in (data.get("suggestions") or []) if _t(s, 240)][:3])
            self._ui("set_busy", "")

        self._submit(job)

    # ==================================================================== hub
    def _open_hub(self, snap: Snapshot) -> None:
        header = Header(KIND_LABEL["hub"], snap.long_name or snap.name,
                        f"{snap.date} · court influence {snap.budget}")
        self.session = Session(kind="hub", snap=snap, header=header)
        self._ui("open", header)
        self._hub_menu()

    def _hub_menu(self) -> None:
        # The first page stays the same size all campaign long: the estates and the
        # books (which grow with every reign and century) open pages of their own.
        items = [("council", "\u2696  Summon the council"),
                 ("decree", "\u270D  Issue a decree")]
        if self._estate_items():
            items.append(("estates", "\u2694  Receive the spokesmen of an estate\u2026"))
        items += [("chronicle", "\U0001F4DC  Read the chronicle"),
                  ("books", "\U0001F4D6  The biographer and the books\u2026")]
        self._ui("set_hub", items)
        self._ui("add_note", "To talk with someone, use \"Speak with\" on their portrait; for foreign courts, the "
                             "mod's diplomatic actions (Send an Envoy, Request a Meeting, Pay a State Visit).")

    def _books_menu(self) -> None:
        """The royal biographer, the Life of the ruler, the Lives of the rulers before
        and the histories of the centuries - on a page of their own."""
        bio = self.memory.biographer if self.memory else None
        items = [("biographer", "\u2712  Speak with the royal biographer"
                  + (f" ({bio.get('name', '')})" if bio else "")),
                 ("book", "\U0001F4D6  Read your Life, as written so far")]
        book, snap = self.book, self.snapshot
        if book is not None:
            for i, r in reversed(list(enumerate(book.reigns))):
                if r.get("end") and (r.get("life") or r.get("notes") or r.get("chapters") or r.get("obit")):
                    items.append((f"life:{i}", f"\U0001F4D5  The Life of {r['ruler']}"
                                  + ("" if r.get("life") else " (uses many AI tokens)")))
            done = book.centuries_upto(snap.date) if snap else []
            for i, c in reversed(list(enumerate(done))):
                span = f"{(gamedate.parse(c['from']) or (0,))[0]}\u2013{(gamedate.parse(c['to']) or (0,))[0]}"
                items.append((f"century:{i}", f"\U0001F4DC  The history of {span}"
                              + ("" if c.get("text") else " (uses many AI tokens)")))
        items.append(("back", "\u21A9  Back"))
        self._ui("set_hub", items)
        self._ui("add_note", "The book of the reign, as the royal biographers keep it.")

    def on_hub(self, key: str) -> None:
        self._touched = time.time()
        if key.startswith("battle:"):
            self.battle.on_hub(key)
            return
        snap = self.snapshot
        if snap is None:
            return
        if key == "chronicle":
            self._ui("clear")
            pages = self.memory.pages[-6:] if self.memory else []
            if not pages:
                self._ui("add_note", "The chronicler has written nothing of this realm yet.")
            for page in pages:
                self._ui("add_line", f"{page['title']}  ·  {page['date']}", page["body"])
            self._ui("set_hub", [("chronicle_new", "\u270E  Have the chronicler write a new page"),
                                 ("back", "\u21A9  Back")])
            return
        if key == "chronicle_new":
            self._ui("set_hub", [])
            self._ui("set_busy", "The chronicler dips his pen…")
            self._submit(lambda: self._story(snap, "chronicle", show_in_panel=True))
            return
        if key == "back":
            self._ui("clear")
            self._hub_menu()
            return
        if key == "estates":
            self._ui("clear")
            self._ui("set_hub", self._estate_items() + [("back", "\u21A9  Back")])
            self._ui("add_note", "Which estate do you wish to hear?")
            return
        if key == "books":
            self._ui("clear")
            self._books_menu()
            return
        if key.startswith(("life:", "century:")):
            what, _, idx = key.partition(":")
            self._ui("clear")
            self._ui("set_hub", [])
            self._submit(lambda: self._read_book(snap, what, in_hub=True, index=int(idx)))
            return
        if key.startswith("estate:"):
            self.session = None
            self.mail.send([f"votc_signal_council = {{ agenda = {int(key.split(':')[1])} }}"], label="estate")
            self._ui("set_hub", [])
            self._ui("set_busy", "The spokesmen are called in…")
            return
        if key == "council":
            self.session = None
            self.mail.send(["votc_signal_council = { agenda = 0 }"], label="council")
            self._ui("set_hub", [])
            self._ui("set_busy", "The council gathers…")
            return
        if key in ("book", "life", "century"):
            self._ui("clear")
            self._ui("set_hub", [])
            self._submit(lambda: self._read_book(snap, key, in_hub=True))
            return
        if key == "biographer":
            self.session = None
            self.mail.send(["votc_signal_biographer = yes"], label="biographer")
            self._ui("set_hub", [])
            self._ui("set_busy", "The biographer is sent for…")
            return
        if key == "decree":
            self.session = None
            self.mail.send(["votc_signal_decree = yes"], label="decree")
            self._ui("set_hub", [])
            self._ui("set_busy", "…")
            return

    def _note_new_reign(self) -> None:
        """A new ruler on the throne: the court remembers the change of reign."""
        old, new = self.previous, self.snapshot
        if (self.memory is None or old is None or new is None or old.tag != new.tag
                or not (old.ruler and new.ruler and old.ruler.name and new.ruler.name)
                or old.ruler.name == new.ruler.name):
            return
        text = (f"A new reign: {new.ruler.name} now rules in place of {old.ruler.name}. Old loyalties, grudges "
                f"and promises pass to the new ruler, and are tested.")
        self.memory.remember_event(new.date, "reign", text)
        self.log(text)
        gone, title = old.ruler.name, old.ruler_title or ""
        self._submit(lambda: self._reign_ends(new, gone, title))

    # =================================================================== war
    # Tales of the war come only from what the game shows really happened:
    # battles (losses that grew between two saves, or the army suddenly
    # smaller), sieges, occupied land. Court Brain reads them from the saves
    # and the heartbeat and hands them to the AI as the reports of the war.
    WAR_GAP = (20, 45)            # in-game days between two tales of the war
    WAR_SAVE_EVERY_S = 600        # an early save for the war's reports: at most this often
    WAR_PACE = {"off": 2.5, "yearly": 2.2, "rare": 1.8, "calm": 1.3, "normal": 1.0, "very": 0.75}

    def _place(self, location: int) -> str:
        if self._places is None:
            from .places import Places
            self._places = Places(self.cfg.game_dir, self.cfg.state_dir, self.cfg.game_language)
        return self._places.name(location)

    def _war_state_of(self, world: Any, me: int) -> dict[str, Any] | None:
        wars = world.wars_of(me)
        if not wars:
            return None
        friends, foes = {me}, set()
        for war in wars:
            mine, theirs = (war.attackers, war.defenders) if me in war.attackers else (war.defenders, war.attackers)
            friends |= set(mine)
            foes |= set(theirs)
        state: dict[str, Any] = {
            "date": world.date,
            "foes": sorted(world.tag(c) for c in foes), "friends": sorted(world.tag(c) for c in friends - {me}),
            "battle": sum(w.losses.get(me, {}).get("battle", 0) for w in wars),
            "attrition": sum(w.losses.get(me, {}).get("attrition", 0) for w in wars),
            "foe_battle": sum(w.losses.get(c, {}).get("battle", 0) for w in wars for c in foes),
            "sieges": {}, "lost": {}, "held": {},
        }
        def name(cid: int) -> str:
            return self._cname(world.tag(cid))

        for sg in world.sieges:
            by = set(sg.besiegers)
            if by & friends and sg.defender in foes:
                who = "we" if me in by else ", ".join(name(c) for c in sorted(by & friends))
                state["sieges"][sg.location] = (f"{who} besiege", name(sg.defender), sg.days, sg.status)
            elif by & foes and sg.defender in friends:
                state["sieges"][sg.location] = (f"{', '.join(name(c) for c in sorted(by & foes))} besiege",
                                                "us" if sg.defender == me else name(sg.defender), sg.days,
                                                sg.status)
        for loc, (owner, ctrl) in world.occupied.items():
            if owner == me and ctrl in foes:
                state["lost"][loc] = name(ctrl)
            elif ctrl == me and owner in foes:
                state["held"][loc] = name(owner)
        # Battles: the game keeps no list of them, but every army remembers the
        # day of its last victory and where it stands. A victory of this war's
        # sides in the last months is a battle that was really fought, there.
        battles: dict[tuple[int, str], str] = {}
        for army in world.armies:
            if not army.last_victory or army.country not in friends | foes:
                continue
            if not 0 <= gamedate.days_between(army.last_victory, world.date) <= 120:    # the last four months
                continue
            leader = self._leader(world, army.leader)
            who = ("our army" if army.country == me else
                   f"the army of {name(army.country)}" + (" (our ally)" if army.country in friends else " (the enemy)"))
            battles[(army.country, army.last_victory)] = (
                f"on {army.last_victory} {who}{' under ' + leader if leader else ''} won a battle near "
                f"{self._place(army.previous or army.location)}")
        state["battles"] = battles
        return state

    def _leader(self, world: Any, cid: int) -> str:
        ch = world.characters.get(cid) if cid else None
        raw = getattr(ch, "first_name", "") if ch else ""
        if not raw:
            return ""
        shown = self.codex.name_of(raw, "") if raw.startswith("name_") else raw
        return shown or raw.removeprefix("name_").replace("_", " ").title()

    def _war_update(self, world: Any) -> None:
        """Compare this save with the last one: what the war did in between."""
        snap = self.snapshot
        me = world.by_tag.get(snap.tag) if snap is not None else None
        state = self._war_state_of(world, me) if me else None
        prev, self._war_state = self._war_state, state
        if state is None or prev is None or prev.get("date") == state["date"]:
            return
        facts = []
        for key, text in state.get("battles", {}).items():
            if key not in prev.get("battles", {}):
                facts.append(f"battle: {text}.")
        ours, theirs = state["battle"] - prev["battle"], state["foe_battle"] - prev["foe_battle"]
        if ours + theirs >= 100:
            facts.append(f"losses: since {prev['date']} about {round(ours, -1):.0f} of our men fell in battle, "
                         f"about {round(theirs, -1):.0f} of the enemy's.")
        sick = state["attrition"] - prev["attrition"]
        if sick >= 200:
            facts.append(f"hardship: hunger, cold and sickness took about {round(sick, -1):.0f} of our soldiers.")
        for loc, (role, other, days, _status) in state["sieges"].items():
            if loc not in prev["sieges"]:
                facts.append(f"siege: {role} {self._place(loc)} ({'held by ' + other if other != 'us' else 'ours'}).")
        for loc, (role, other, days, _status) in prev["sieges"].items():
            if loc in state["sieges"]:
                continue
            if loc in state["held"]:
                facts.append(f"siege: {self._place(loc)} fell to us after {days} days.")
            elif loc in state["lost"]:
                facts.append(f"siege: {self._place(loc)} fell to the enemy after {days} days.")
            else:
                facts.append(f"siege: the siege of {self._place(loc)} ended without its fall.")
        for loc, by in state["lost"].items():
            if loc not in prev["lost"]:
                facts.append(f"occupation: {self._place(loc)} is now held by {by}.")
        for loc in prev["lost"]:
            if loc not in state["lost"]:
                facts.append(f"liberation: {self._place(loc)} is ours again.")
        for loc, owner in state["held"].items():
            if loc not in prev["held"]:
                facts.append(f"conquest: our men hold {self._place(loc)} (of {owner}).")
        for fact in facts[:8]:
            self._war_fact(world.date, fact)

    # A realm the player invades may, now and then, rise as the player's own can (a great
    # effort): the same effects, in its own measure. Rare by design - only a realm really
    # invaded and still behind its ruler, a chance at each look, one look per realm every
    # half year, once in ten years for a realm, and once in three years in all the world.
    FOREIGN_RALLY_CHANCE = 0.07
    FOREIGN_RALLY_LOOK_DAYS = 180
    FOREIGN_RALLY_GAP_DAYS = 3 * 365
    FOREIGN_RALLY_SHARE = 0.2            # at least this share of its lands held by the ruler's men
    _RALLY_SPARK = (
        "their ruler, riding among the people and speaking to them in the squares",
        "their ruler's consort, who pawned her own jewels and shamed the lords into following",
        "a preacher whose sermons against the invader went from town to town",
        "an old captain who refused to yield his walls and whose stand became a legend overnight",
        "a noblewoman who raised her own vassals when her husband fell",
        "the people themselves: villages and guilds that armed before any order came",
        "the burghers of their great towns, who opened their chests and their gates to the levies",
        "a young heir who took the field and would not leave it",
    )

    def _foreign_rally(self, world: Any) -> None:
        snap, mem = self.snapshot, self.memory
        if snap is None or mem is None or not snap.at_war or not world.owners:
            return
        me = world.by_tag.get(snap.tag)
        if me is None:
            return
        date = snap.date
        last_any = mem.meta.get("foreign_rally_on", "")
        if last_any and gamedate.days_between(last_any, date) < self.FOREIGN_RALLY_GAP_DAYS:
            return
        looked = mem.meta.setdefault("foreign_rally_looked", {})
        rallied = mem.meta.setdefault("foreign_rallies", {})
        for war in world.wars:
            foes = war.defenders if me in war.attackers else war.attackers if me in war.defenders else []
            for cid in foes:
                c = world.countries.get(cid)
                if c is None or c.kind != "Real" or not c.tag:
                    continue
                total = sum(1 for owner in world.owners.values() if owner == cid)
                held = sum(1 for owner, ctrl in world.occupied.values() if owner == cid and ctrl == me)
                if not total or held < max(2, self.FOREIGN_RALLY_SHARE * total):
                    continue                  # skirmished with, not invaded
                if c.stability < 30 or (c.legitimacy and c.legitimacy < 60):
                    continue                  # a realm not behind its ruler does not rise
                if rallied.get(c.tag) and gamedate.days_between(rallied[c.tag], date) < self.GREAT_EFFORT_DAYS:
                    continue
                if looked.get(c.tag) and gamedate.days_between(looked[c.tag], date) < self.FOREIGN_RALLY_LOOK_DAYS:
                    continue
                looked[c.tag] = date
                mem._meta_dirty = True
                if random.random() >= self.FOREIGN_RALLY_CHANCE:
                    continue
                self._rally_of(snap, c.tag, self._cname(c.tag), held, total)
                return

    def _rally_of(self, snap: Snapshot, tag: str, name: str, held: int, total: int) -> None:
        """An invaded realm rises: its great effort in the game, and the news of it in the chronicle."""
        mem = self.memory
        assert mem is not None
        domain = "army" if random.random() < 0.8 else "treasury"
        spark = random.choice(self._RALLY_SPARK)
        what = ("its whole people rise in arms: every lord, town and village sends more men than it owes, "
                "for two years" if domain == "army" else
                "its people pour their savings, plate and pledges into their Crown's chest to pay for the war")
        mem.meta.setdefault("foreign_rallies", {})[tag] = snap.date
        mem.meta["foreign_rally_on"] = snap.date
        mem._meta_dirty = True
        mem.remember_event(snap.date, "war", f"{name}, invaded by the ruler's armies, rises as one: {what}.")
        self._book_note(snap.date, f"War: {name} rises against the invasion", weight=2)
        self.log(f"An invaded realm rises: {name} ({domain}) - {held} of its {total} places held by the ruler's men.")
        news = (f"THIS PAGE BRINGS NEWS FROM THE ENEMY: {name}, whose lands the ruler's armies are overrunning "
                f"({held} of its {total} places are held by them), has not broken - it has risen as one: {what}. "
                f"The spark: {spark}. Tell it as the news reaches the ruler's court - from scouts, prisoners, "
                f"merchants or a captured letter, with what is known and what is only rumour - named people, "
                f"no figures. The ruler's commanders now face a harder war; say how it is felt at court. "
                f"Consequences for the ruler's own realm only if the news itself really causes them.")
        self._story(snap, "chronicle", war=True, news=news,
                    extra=[f"c:{tag} ?= {{ votc_act_great_effort = {{ domain = {domain} }} }}"])

    # ============================================================== threats made
    # A threat the other side made, and the ruler did not give in to, may be carried out -
    # not always: by its likelihood (the AI's judgement of them, weighed by their means),
    # some weeks or months later, at most one every two months. What it does in the game is
    # set here, never by the AI, and is the size of a real blow of that kind.
    THREAT_GAP_DAYS = 60
    THREAT_MAX_OPEN = 6
    THREAT_DAYS = (30, 150)

    def _collect_threats(self, session: Session, raws: list[Any]) -> None:
        mem, snap = self.memory, session.snap
        if mem is None:
            return
        threats = mem.meta.setdefault("threats", [])
        w = self.world
        for raw in [r for r in raws if isinstance(r, dict)][:2]:
            kind = raw.get("kind")
            if kind not in prompts.THREAT_KINDS:
                continue
            tag = self._known_tag(raw.get("party_tag")) or ""
            party = _t(raw.get("party"), 80) or (self._cname(tag) if tag else "")
            if not party or tag == snap.tag:
                continue
            chance = max(0, min(100, int(raw.get("likelihood") or 0))) / 100 * 0.6
            if kind == "raid" and not (tag and self._can_reach(snap, tag)):
                continue                          # their men cannot reach the ruler's lands: an empty threat
            if kind == "bribe" and tag and w is not None:
                c = w.countries.get(w.by_tag.get(tag, -1))
                if c is not None and c.gold < max(1.0, snap.n("gold")) * 0.3:
                    chance *= 0.3                 # a poor court buys few barons
            if kind == "assassin":
                chance *= 0.5
            chance = max(0.05, min(0.6, chance))
            threats[:] = [t for t in threats if t.get("party") != party]
            threats.append({"party": party, "tag": tag, "kind": kind, "what": _t(raw.get("what"), 240),
                            "why": _t(raw.get("why"), 240), "chance": round(chance, 2), "made": snap.date,
                            "due": mem.later(snap.date, random.randint(*self.THREAT_DAYS))})
            del threats[:-self.THREAT_MAX_OPEN]
            mem._meta_dirty = True
            self.log(f"  a threat is remembered: {party} ({kind}), perhaps carried out ({round(chance * 100)}%)")

    def _can_reach(self, snap: Snapshot, tag: str) -> bool:
        """Can that realm's men strike the ruler's lands? Neighbours, and enemies at war."""
        live = self.live_picture or {}
        return tag in {x.tag for x in live.get("near", [])} or tag in {x.tag for x in live.get("war", [])}

    def _threats_due(self, snap: Snapshot) -> None:
        """At a pulse: a threat whose time has come is carried out - or comes to nothing."""
        mem = self.memory
        if mem is None or self.busy or self.session is not None:
            return
        threats = mem.meta.get("threats") or []
        today = gamedate.key(snap.date)
        due = next((t for t in threats if 0 < int(t.get("due", 0)) <= today), None)
        if due is None:
            return
        last = mem.meta.get("threat_done_on", "")
        if last and gamedate.days_between(last, snap.date) < self.THREAT_GAP_DAYS:
            due["due"] = mem.later(snap.date, random.randint(10, 40))
            mem._meta_dirty = True
            return
        threats.remove(due)
        mem._meta_dirty = True
        if random.random() >= float(due.get("chance", 0)):
            mem.remember_event(snap.date, "threat", f"{due['party']}'s threat ({due['what']}) came to nothing, "
                                                    f"for now.")
            self.log(f"A threat came to nothing: {due['party']} ({due['kind']}).")
            return
        mem.meta["threat_done_on"] = snap.date
        self._submit(lambda: self._carry_out(snap, due))

    def _border_places(self, snap: Snapshot, tag: str, n: int) -> list[str]:
        """The ruler's places nearest that realm's: in an area it also holds, else a region."""
        w = self.world
        if w is None or not w.owners:
            return []
        me, them = w.by_tag.get(snap.tag), w.by_tag.get(tag)
        if me is None or them is None:
            return []
        geo = self.geo
        index = geo.index

        def up(key: str, levels: int) -> str:
            for _ in range(levels):
                key = geo.parent.get(key, "")
            return key

        capital = (snap.capital or "").strip().lower()
        mine = [index[i - 1] for i, o in w.owners.items() if o == me and 0 < i <= len(index)
                and geo.name(index[i - 1]).strip().lower() != capital]     # a raid strikes the marches
        theirs = [index[i - 1] for i, o in w.owners.items() if o == them and 0 < i <= len(index)]
        for levels in (2, 3):                     # location -> province -> area -> region
            near = {up(k, levels) for k in theirs} - {""}
            found = [k for k in mine if up(k, levels) in near]
            if found:
                random.shuffle(found)
                return found[:n]
        return []

    def _carry_out(self, snap: Snapshot, t: dict[str, Any]) -> None:
        """The threat is carried out: its blow in the game (set here), and an event for the ruler to answer."""
        kind, tag, party = t["kind"], t.get("tag", ""), t["party"]
        lines: list[str] = []
        happened = ""
        if kind == "raid":
            places = self._border_places(snap, tag, 2) if tag else []
            if not places:
                kind = "incite"
            else:
                for loc in places:
                    lines.append(f"location:{loc} ?= {{ if = {{ limit = {{ owner = root }} change_prosperity = "
                                 f"prosperity_mild_penalty every_pop = {{ add_pop_size = {{ value = pop_size multiply "
                                 f"= -0.03 }} }} }} }}")
                    lines.append(f"c:{tag} ?= {{ location:{loc} ?= {{ save_scope_as = votc_raided }} "
                                 f"loot_location = scope:votc_raided }}")
                happened = ("their riders struck the ruler's lands at " + ", ".join(self.geo.name(p) for p in places)
                            + ": villages burned, herds driven off, people killed or fled")
        if kind == "spies":
            lines.append("add_stability = stability_weak_penalty")
            happened = "their spies were at work: secrets of the council taken, a clerk or two bought"
        elif kind == "incite":
            lines += ["votc_act_policy = { area = rebellion sign = penalty tier = mild years = 2 }",
                      "if = { limit = { country_has_estate = estate_type:peasants_estate } add_estate_satisfaction = "
                      "{ type = estate_type:peasants_estate value = estate_satisfaction_weak_penalty } }"]
            happened = happened or "their agents have stirred the ruler's people: pamphlets, preachers, whispers of revolt"
        elif kind == "bribe":
            lines += ["votc_act_policy = { area = nobles_favour sign = penalty tier = mild years = 2 }",
                      "if = { limit = { country_has_estate = estate_type:nobles_estate } add_estate_satisfaction = "
                      "{ type = estate_type:nobles_estate value = estate_satisfaction_weak_penalty } }"]
            happened = "their gold has found its way into the purses of some of the ruler's lords and officials"
        elif kind == "embargo":
            lines.append("votc_act_policy = { area = trade_income sign = penalty tier = mild years = 2 }")
            happened = "the ruler's merchants have been seized or turned away in their ports and markets"
        elif kind in ("kidnap", "assassin"):
            court = [p for p in snap.court if p.slot and not p.is_ruler and not p.is_heir]
            victim = random.choice(court) if court else None
            if victim is None:
                kind = "spies"
                lines.append("add_stability = stability_weak_penalty")
                happened = "their spies were at work: secrets of the council taken"
            elif kind == "kidnap":
                lines.append("votc_act_prestige = { tier = weak sign = penalty }")
                happened = f"{victim.name} of the ruler's court has been seized and is held for ransom"
            elif random.random() < 0.3:
                lines.append(f"votc_order_kill = {{ t = votc_{victim.slot} reason = assassination }}")
                happened = f"{victim.name} of the ruler's court has been murdered by their hired knife"
            else:
                lines.append("add_stability = stability_weak_penalty")
                happened = f"an attempt on the life of {victim.name} of the ruler's court failed - barely"
        mem = self.memory
        if mem is not None:
            mem.remember_event(snap.date, "threat", f"{party} carried out its threat: {happened}.")
        self.log(f"A threat is carried out: {party} ({kind}) - {happened}.")
        seed = (f"A THREAT CARRIED OUT. On {t['made']} {party} warned the ruler: \"{t['what']}\" - because "
                f"{t['why'] or 'the ruler would not give way'}. Now they have done it: {happened}. This has "
                f"ALREADY happened in the game. Show it as it reaches the ruler - named people, what was lost, "
                f"who saw it, what is known and what only feared - and give three real ways to answer "
                f"(strike back, seek redress, bargain, pay a ransom, complain to others, bear it...), each with "
                f"its fitting consequences; none undoes what was done.")
        self._story_event(snap, seed=seed, extra=lines)

    def _war_fact(self, date: str, fact: str) -> None:
        self.war_log = (self.war_log + [f"{date}: {fact}"])[-20:]
        if self.memory is not None:
            self.memory.remember_event(date, "war", fact)
            if not fact.startswith("losses"):
                self._book_note(date, f"War: {fact}", weight=2)
        self.log(f"  war: {fact}")

    def _watch_army(self, snap: Snapshot) -> None:
        """Between saves: an army suddenly smaller at war means fighting - ask for the full reports."""
        old = self.previous
        if old is None or old.tag != snap.tag or not snap.at_war:
            return
        before, now = old.n("army"), snap.n("army")
        if before > 0 and now < before * 0.88:
            self._war_fact(snap.date, f"losses: the army came back smaller - from {before:g} to {now:g} - "
                                      f"there has been fighting or sickness.")
            # The full reports (where, who won) come with a save. A save stalls the
            # game for a moment, so a war must not turn into a stream of them: at most
            # one early save every ten minutes, and none if one was read lately.
            quiet = time.time() - self._war_save_asked >= self.WAR_SAVE_EVERY_S
            stale = time.time() - self._world_requested >= self.WAR_SAVE_EVERY_S / 2
            if quiet and stale:
                self._war_save_asked = time.time()
                self._world_requested = time.time() - self.WORLD_REFRESH_S + 60

    def _war_report(self, snap: Snapshot) -> str:
        """THE WAR, AS THE REPORTS HAVE IT: the only ground for tales of battles and sieges."""
        state = self._war_state
        at_war = snap.at_war or bool((self.live_picture or {}).get("war"))
        if not at_war or state is None:
            return ""

        def name(tag: str) -> str:
            live = self._cname(tag)
            return live if live != tag else self.codex.country(tag)
        out = ["THE WAR, AS THE REPORTS HAVE IT (the only battles, sieges and occupations that happened; "
               f"as of {state['date']}):",
               f"- enemies: {', '.join(name(tag) for tag in state['foes'])}"
               + (f"; on our side: {', '.join(name(tag) for tag in state['friends'])}" if state["friends"] else ""),
               f"- losses so far: about {round(state['battle'], -1):.0f} of our men fell in battle and "
               f"{round(state['attrition'], -1):.0f} to hunger and sickness; the enemy lost about "
               f"{round(state['foe_battle'], -1):.0f} in battle"]
        for loc, (role, other, days, status) in list(state["sieges"].items())[:6]:
            note = {"SiegeStatusSuppliesShortage": ", supplies short", "SiegeStatusDesert": ", men deserting"}.get(
                status, "")
            out.append(f"- siege: {role} {self._place(loc)} ({'ours' if other == 'us' else 'held by ' + name(other)}), "
                       f"day {days}{note}")
        if state["lost"]:
            out.append(f"- our land held by the enemy: " + ", ".join(
                f"{self._place(loc)} ({name(by)})" for loc, by in list(state["lost"].items())[:6])
                + (f" and {len(state['lost']) - 6} more" if len(state["lost"]) > 6 else ""))
        if state["held"]:
            out.append(f"- enemy land we hold: " + ", ".join(
                f"{self._place(loc)} ({name(owner)})" for loc, owner in list(state["held"].items())[:6])
                + (f" and {len(state['held']) - 6} more" if len(state["held"]) > 6 else ""))
        if state.get("battles"):
            out.append("- battles of the last months: " + "; ".join(list(state["battles"].values())[-6:]))
        if self.war_log:
            out.append("- lately:")
            out += [f"  {line}" for line in self.war_log[-8:]]
        return "\n".join(out)

    def _war_due(self, snap: Snapshot) -> bool:
        """A tale of the war now? Only at war, only with something real to tell, at the war's own pace."""
        mem = self.memory
        if mem is None or not self._war_report(snap):
            return False
        state = self._war_state or {}
        grounded = bool(self.war_log or state.get("sieges") or state.get("lost") or state.get("held")
                        or state.get("battle") or state.get("foe_battle"))
        if not grounded:
            return False
        if not self._war_gap:
            pace = self.WAR_PACE.get(self.cfg.event_frequency, 1.0)
            self._war_gap = int(random.randint(*self.WAR_GAP) * pace)
        since = gamedate.days_between(mem.last_war_date, snap.date)
        fresh = any(gamedate.key(line.split(": ", 1)[0]) > gamedate.key(mem.last_war_date) for line in self.war_log)
        # new fighting since the last tale: its own pace; only old news: twice as rare
        return since >= (self._war_gap if fresh else 2 * self._war_gap)

    # ============================================================ the estates
    def _estate_items(self) -> list[tuple[str, str]]:
        """One audience per estate the realm has, for the Court menu."""
        snap = self.snapshot
        if snap is None:
            return []
        out = []
        for i, key in enumerate(ESTATES, start=1):
            if key in snap.estates and (snap.estates[key] or key in snap.estate_names):
                name = snap.estate_names.get(key) or key.title()
                out.append((f"estate:{i}", f"\u2694  Receive the spokesmen of the {name}"))
        return out

    # ========================================================= lean prompts
    # Everything below saves input tokens without taking anything from the court:
    # what is left out of a prompt is either not the business of that request or
    # is fetched the moment it becomes so.
    _LAW_MODES = ("council", "estate", "decree")
    _LAW_TALK = re.compile(r"\b(law|laws|privileg|reform|edict|decree|statute|tax|taxes|tithe|toll|parliament|"
                           r"estates|charter|legg|riform|editt|decret|tass|tribut|dazi|gabell|parlament|statut|"
                           r"ley|leyes|impuesto|loi|lois|impôt|gesetz|steuer|privilèg)", re.I)
    ADVISOR_RECORDS = 60
    KEEP_TURNS = 10               # exchanges kept word for word in a long conversation

    def _recalled(self, about: str, limit: int = 900, seen: set | None = None) -> str:
        if self.memory is None or not about.strip():
            return ""
        lines = self.memory.recall(about, limit_chars=limit)
        if seen is not None:
            # once called up in a scene, a memory is in the conversation already
            lines = [line for line in lines if line not in seen]
            seen.update(lines)
        if not lines:
            return ""
        return ("\n\nTHE COURT RECALLS (older matters that touch this; the rest of the past is in the "
                "chronicle):\n" + "\n".join(f"- {line}" for line in lines))

    def _context_for(self, session: Session, said: str) -> str:
        """What this line of the ruler's calls up: older memories, and the laws if the talk turns to them."""
        out = ""
        recalled = self._recalled(said, seen=session.recalled)
        if recalled:
            out += recalled.strip() + "\n\n"
        if session.diplomatic:
            out += self._land_brief(session, said)
        target = session.snap.target_country
        out += self._war_views(said, session.snap, target.tag if session.diplomatic and target is not None else "",
                               seen=session.recalled)
        if session.codex_later and self._LAW_TALK.search(said):
            out += "WHAT EXISTS IN THIS WORLD (laws and privileges):\n" + session.codex_later + "\n\n"
            session.codex_later = ""
        return out

    def _compact(self, session: Session) -> None:
        """Keep a long conversation lean: past stage directions go, old exchanges become a recap."""
        msgs = session.messages
        last_assistant = max((i for i, m in enumerate(msgs) if m["role"] == "assistant"), default=-1)
        for i, m in enumerate(msgs[1:], start=1):
            if m["role"] == "user" and "\n\nBEATS FOR THIS MOMENT" in m["content"]:
                # the beats and reminders were for that moment only
                m["content"] = m["content"].split("\n\nBEATS FOR THIS MOMENT", 1)[0]
            if m["role"] == "assistant" and i != last_assistant and m["content"].startswith("[how they stand:"):
                # only the latest state of mind matters; older ones are history
                m["content"] = m["content"].split("\n", 1)[-1]
        body = msgs[1:]
        keep = 2 * self.KEEP_TURNS
        if len(body) <= keep + 4 or len(body) % 2:
            return
        first, old, recent = body[:2], body[2:-keep], body[-keep:]
        header = "EARLIER IN THIS CONVERSATION (in brief):"
        lines: list[str] = []
        for m in old:
            text = m["content"]
            if text.startswith(header):
                recap, _, text = text.partition("\n\n\n")
                lines += recap.split("\n")[1:]
            if text.strip():
                who = "The ruler" if m["role"] == "user" else "They"
                text = text.replace("The ruler says: ", "")
                lines.append(f"{who}: {_cap(text, 360)}")
        head = recent[0]
        head["content"] = header + "\n" + "\n".join(lines) + "\n\n\n" + head["content"]
        session.messages = [msgs[0], *first, *recent]

    def _fold_chronicle(self) -> None:
        """Older facts folded into the chronicle of the reign: bounded in size, never lost from the journal."""
        mem = self.memory
        try:
            if mem is None or not mem.needs_summary():
                return
            facts = [f"{e.date}: {e.text}" for e in mem.events_to_summarise()]
            data = self.client.complete_json(prompts.summary_messages(mem.campaign, facts, mem.summary),
                                             prompts.SUMMARY_SCHEMA, schema_name="votc_summary", temperature=0.3,
                                             max_tokens=2500)
            text = _cap(data.get("summary"), 3600)
            if text:
                mem.fold_into_summary(text, mem.events[-1].date if mem.events else "")
                self.log(f"The chronicle of the reign was condensed: {len(facts)} older facts folded in "
                         f"(the journal keeps them all).")
        except Player2Error as exc:
            self.log(f"  the chronicle could not be condensed now: {exc}")
        finally:
            self._folding = False

    # ======================================================= people sent for
    def _find_person(self, snap: Snapshot, name: str):
        low = name.strip().lower()
        people = [p for p in (snap.target_person, snap.heir, snap.ruler, *snap.court) if p is not None and p.name]
        return (next((p for p in people if p.name.lower() == low), None)
                or next((p for p in people if low and (low in p.name.lower() or p.name.lower() in low)), None))

    def _summon(self, session: Session, raws: list[Any]) -> None:
        """The ruler asked for someone: they come in now, later, or cannot come."""
        for raw in [r for r in raws if isinstance(r, dict)][:3]:
            name = _t(raw.get("name"), 60)
            arrives = raw.get("arrives")
            if not name or arrives not in ("now", "later"):
                continue
            person = self._find_person(session.snap, name)
            if person is not None:
                name = person.name
            if person is not None and person.is_ruler:
                continue
            if arrives == "now":
                if name in session.cast:
                    continue
                session.cast.append(name)
                persona = self._ensure_personas(session.snap, [person]) if person is not None else ""
                session.aside = (f"({name} has come in and is in the room now; they take part as the person they "
                                 f"are." + (f" WHO THEY ARE:\n{persona}" if persona else "") + ")")
                if person is None and self.memory is not None:
                    self.memory.remember_person(name, date=session.snap.date, real=False,
                                                note="joined a conversation at the ruler's call")
                self._ui("add_note", f"✦ {name} joins the conversation.")
                self.log(f"  {name} was sent for and joins the conversation")
            else:
                days = max(1, min(120, int(raw.get("days") or 7)))
                about = (f"{name}, sent for by the ruler during \"{session.header.title}\", arrives at court as "
                         f"asked, about the matter discussed then")
                if self.memory is not None:
                    if person is not None:
                        self.memory.plan(session.snap.date, after_days=days, kind="knock", who=person.name,
                                         about=about)
                    else:
                        # Someone who is not at court: their arrival is a scene of its own.
                        self.memory.remember_person(name, date=session.snap.date, real=False,
                                                    note=f"summoned to court by the ruler")
                        self.memory.plan(session.snap.date, after_days=days, kind="story", about=about)
                self._ui("add_note", f"✉ {name} has been sent for and will arrive in about {days} days.")

    # ================================================================== pacts
    _TAG = re.compile(r"^[A-Z][A-Z0-9]{2}$")

    def _known_tag(self, tag: Any) -> str:
        tag = str(tag or "").strip().upper()
        if not self._TAG.match(tag):
            return ""
        if any(c.tag == tag for group in (self.live_picture or {}).values() for c in group):
            return tag
        if self.world is not None and tag in self.world.by_tag:
            return tag
        snap = self.snapshot
        if snap is not None and snap.target_country is not None and snap.target_country.tag == tag:
            return tag
        if snap is not None and snap.tag == tag:
            return tag                   # the ruler's own realm, whatever the last save calls it
        return ""

    def _cname(self, tag: str) -> str:
        for group in (self.live_picture or {}).values():
            for c in group:
                if c.tag == tag:
                    return c.name or tag
        return self.codex.country(tag) if tag else tag

    def _live_sets(self) -> tuple[set[str], dict[str, Any]]:
        live = self.live_picture or {}
        return {c.tag for c in live.get("war", [])}, {c.tag: c for c in live.get("ally", [])}

    _NEEDS_WATCH_TAG = ("ruler_declares_war_on", "ruler_keeps_peace_with", "war_ends")
    # Pacts that outlive one occasion: honoured once, they stay in force for the next.
    # ("ruler_bound": what is left of a pact once the other side has delivered - the
    # ruler's own promise, judged by the AI whenever the ruler acts.)
    _LASTING = ("mutual_defence", "alliance_holds", "ruler_keeps_peace_with", "ruler_must_not_do", "ruler_bound")
    # How a pact event that only reminds the ruler of a deadline begins (see _watch_pacts).
    PACT_REMINDER = "REMINDER:"
    _DEFAULT_DAYS = {"party_must_do": 120, "ruler_must_do": 365}
    _SMALL_DAYS = {"party_must_do": 60, "ruler_must_do": 90}
    _NEEDS_PARTY_TAG = ("alliance_holds", "mutual_defence", "party_declares_war_on")

    def _collect_pacts(self, snap: Snapshot, raws: list[Any]) -> None:
        """Agreements sealed at court: the court will hold both sides to them."""
        mem = self.memory
        if mem is None:
            return
        wars, allies = self._live_sets()
        for raw in [r for r in raws if isinstance(r, dict)][:2]:
            watch = raw.get("watch")
            party = _t(raw.get("party"), 80)
            if watch not in prompts.PACT_WATCHES or not party:
                continue
            kind = raw.get("party_kind") if raw.get("party_kind") in prompts.PACT_PARTIES else "person"
            tag = self._known_tag(raw.get("party_tag")) if kind == "country" else ""
            wtag = self._known_tag(raw.get("watch_tag"))
            if watch in self._NEEDS_WATCH_TAG and not wtag or watch in self._NEEDS_PARTY_TAG and not tag:
                self.log(f"  pact not recorded: {watch} needs a country the game knows")
                continue
            if any(p.get("party") == party and p.get("watch") == watch and p.get("watch_tag", "") == wtag
                   for p in mem.open_pacts().values()):
                continue
            weight = raw.get("weight") if raw.get("weight") in ("small", "great") else (
                "small" if kind == "person" else "great")
            days = int(raw.get("within_days") or 0)
            if not days and watch in self._DEFAULT_DAYS:
                # something owed must come due some day - a small matter soon
                days = self._DEFAULT_DAYS[watch] if weight == "great" else self._SMALL_DAYS[watch]
            title = _t(raw.get("title"), 80) or f"The pact with {party}"
            self._book_note(snap.date, f"Pact sealed with {party}: {title}", weight=2)
            mem.pact_open(snap.date, title=title, party=party, kind=kind, tag=tag,
                          estate=str(raw.get("estate") or ""), watch=watch, watch_tag=wtag,
                          ruler_promise=_t(raw.get("ruler_promise"), 300),
                          party_promise=_t(raw.get("party_promise"), 300),
                          if_kept=_t(raw.get("if_kept"), 300), if_broken=_t(raw.get("if_broken"), 300),
                          weight=weight, due=mem.later(snap.date, days) if days > 0 else 0,
                          armed=bool((watch == "alliance_holds" and tag in allies)
                                     or (watch == "war_ends" and wtag in wars)),
                          known_wars=sorted(wars), holdings=int(snap.n("locations")))
            self.log(f"Pact recorded: {title} with {party} ({watch}{' ' + wtag if wtag else ''}"
                     f"{f', within {days} days' if days else ''})")
            self._ui("add_note", f"✍ Agreement recorded: {title}. The court will hold both sides to it.", "good")

    def _touch_pacts(self, snap: Snapshot, raws: list[Any]) -> None:
        """The AI's judgement: what the ruler just did keeps or breaks a pact."""
        mem = self.memory
        if mem is None:
            return
        for raw in [r for r in raws if isinstance(r, dict)][:3]:
            try:
                pid = int(raw.get("id") or 0)
            except (TypeError, ValueError):
                continue
            pact = mem.open_pacts().get(pid)
            status, why = raw.get("status"), _t(raw.get("why"), 200)
            owed = mem.owed_pacts().get(pid)
            if pact is None and owed is not None and status in ("made_good", "kept", "fulfilled"):
                # a broken promise made good, late: settled - nobody asks for it again
                mem.pact_state(snap.date, pid, "redeemed", note=f"made good late: {why}")
                dropped = mem.end_pact_plans(pid, snap.date)
                self._ui("add_note", f"✔ You made good on your word, late: {owed.get('title')}. "
                                     f"{owed.get('party')} will remember it was kept in the end.", "good")
                self.log(f"Pact made good late: {owed.get('title')}"
                         + (f" ({dropped} pending event(s) about it dropped)" if dropped else ""))
                continue
            if pact is None or status not in ("kept", "broken", "fulfilled", "made_good"):
                continue
            if status == "made_good":
                status = "kept"
            if status == "broken":
                mem.pact_state(snap.date, pid, "broken", note=f"broken by the ruler: {why}")
                self._ui("add_note", f"✖ You broke your word: {pact.get('title')}. {pact.get('party')} will not forget.",
                         "bad")
                self._plan_pact(snap, pid, f"The ruler broke their word: {why}", random.randint(5, 25))
            elif pact.get("watch") == "ruler_must_do" and pact.get("party_promise"):
                mem.pact_state(snap.date, pid, "triggered", note=f"the ruler did their part: {why}")
                self._plan_pact(snap, pid, f"The ruler has done their part ({why}). Now it is the other side's "
                                           f"turn: {pact['party_promise']}", random.randint(5, 30))
            elif status == "fulfilled" or pact.get("watch") == "ruler_must_do":
                mem.pact_state(snap.date, pid, "kept", note=why or "kept")
                self.log(f"Pact kept: {pact.get('title')}")
            else:
                mem.pact_state(snap.date, pid, pact["status"], note=f"kept so far: {why}")

    def _plan_pact(self, snap: Snapshot, pid: int, why: str, days: int) -> None:
        mem = self.memory
        if mem is None:
            return
        mem.plan(snap.date, after_days=max(1, days), kind="pact", about=why, pact=pid)
        self.log(f"A pact comes due: {mem.pacts.get(pid, {}).get('title', '')} - {why}")

    def _watch_pacts(self, snap: Snapshot) -> None:
        """What the game shows - wars, peace, alliances, deadlines - against the promises in force."""
        mem = self.memory
        if mem is None or not self.live_picture:
            return
        wars, allies = self._live_sets()
        today = gamedate.key(snap.date)
        for pid, p in list(mem.open_pacts().items()):
            status, watch = p.get("status"), p.get("watch")
            tag, wtag = p.get("tag", ""), p.get("watch_tag", "")
            due = int(p.get("due") or 0)
            if status == "triggered":
                continue
            if status == "delayed":
                if due and today >= due:
                    stalls = sum(1 for h in p.get("history", []) if "stall" in h)
                    if stalls >= 2:
                        mem.pact_state(snap.date, pid, "broken", note="they stalled once too often")
                        self._plan_pact(snap, pid, f"{p.get('party')} stalled once too often: in all but name they "
                                                   f"have broken the pact.", 2)
                    else:
                        mem.pact_state(snap.date, pid, "triggered", note="the delay they asked for is over")
                        self._plan_pact(snap, pid, "The time they asked for has passed.", 1)
                continue
            fire = ""
            ally = allies.get(tag)
            if watch == "ruler_declares_war_on" and wtag in wars:
                fire = f"The realm is now at war with {self._cname(wtag)}: the moment the pact was made for."
            elif watch == "party_declares_war_on" and ally is not None and ally.foe_tag \
                    and ally.foe_tag != snap.tag and (not wtag or ally.foe_tag == wtag):
                fire = (f"{p.get('party')} is now at war with {ally.foe_name or ally.foe_tag}: they call on the "
                        f"ruler to keep their word and join them.")
            elif watch == "mutual_defence":
                known = set(p.get("known_wars") or [])
                new = wars - known - {tag}
                if new:
                    fire = (f"The realm is at war with {', '.join(self._cname(t) for t in sorted(new))}: the "
                            f"ruler may call on {p.get('party')} to come to their aid, as sworn. (Sworn for "
                            f"defence: if the ruler started this war, {p.get('party')} may fairly say it owes "
                            f"nothing.)")
                elif ally is not None and ally.foe_tag and ally.foe_tag not in known and ally.foe_tag != snap.tag:
                    fire = (f"{p.get('party')} is at war with {ally.foe_name or ally.foe_tag}: they call on the ruler "
                            f"to come to their aid, as sworn. (Sworn for defence: if they started it, the ruler "
                            f"may fairly refuse.)")
                if fire:
                    seen = wars | ({ally.foe_tag} if ally is not None and ally.foe_tag else set())
                    mem.pact_state(snap.date, pid, "open", known_wars=sorted(known | seen))
            elif watch == "war_ends":
                if wtag in wars and not p.get("armed"):
                    mem.pact_state(snap.date, pid, "open", note="the war it was made for has begun", armed=True)
                elif p.get("armed") and wtag not in wars:
                    fire = (f"The war with {self._cname(wtag)} is over: the terms of the pact come due. (The realm "
                            f"had {p.get('holdings', '?')} holdings when it was sealed and has "
                            f"{int(snap.n('locations'))} now.)")
            elif watch == "ruler_keeps_peace_with" and wtag in wars:
                mem.pact_state(snap.date, pid, "broken", note=f"the realm went to war with {self._cname(wtag)}")
                self._plan_pact(snap, pid, f"The realm is at war with {self._cname(wtag)}, against the pact. Who "
                                           f"is to blame - who declared it, and why - weighs on how they react.", 3)
                continue
            elif watch == "alliance_holds":
                if tag in allies and not p.get("armed"):
                    mem.pact_state(snap.date, pid, "open", armed=True)
                elif p.get("armed") and tag not in allies:
                    fire = f"The alliance with {p.get('party')} has ended."
            if not fire and watch == "ruler_must_do" and due and today < due and not p.get("reminded"):
                # before a promise can be broken, the ruler is reminded of it - with time still left
                opened = gamedate.key(p.get("opened", "")) or today
                window = max(5, min(90, round(0.3 * (_key_days(due) - _key_days(opened)))))
                if _key_days(due) - _key_days(today) <= window:
                    mem.pact_state(snap.date, pid, status, note="the deadline draws near and it is not yet done",
                                   reminded=True)
                    self._plan_pact(snap, pid, f"{self.PACT_REMINDER} the deadline ({due // 10000}.{due // 100 % 100}."
                                               f"{due % 100}) draws near and the ruler has not yet done what they "
                                               f"promised: {p.get('ruler_promise') or '-'}. It is not broken yet - "
                                               f"there is still time.", 1)
                    continue
            if not fire and due and today >= due:
                if watch == "ruler_must_do" and any(int(pl.get("pact") or 0) == pid for pl in mem.plans.values()):
                    continue            # its reminder has not been shown yet: never broken unannounced
                if watch == "ruler_must_do":
                    mem.pact_state(snap.date, pid, "broken", note="the deadline passed and the ruler had not done it")
                    self._plan_pact(snap, pid, "The deadline has passed and the ruler has not done what they promised.", 3)
                    continue
                if watch == "party_must_do":
                    fire = "The deadline has come: have they delivered what they promised?"
                elif watch in ("ruler_declares_war_on", "party_declares_war_on", "war_ends"):
                    fire = "The time agreed has passed and the occasion never came: the pact lapses, or must be renewed."
                else:
                    mem.pact_state(snap.date, pid, "kept", note="its term ended with the promise kept")
                    self.log(f"Pact kept to its term: {p.get('title')}")
                    continue
            if fire:
                mem.pact_state(snap.date, pid, "triggered", note=fire)
                self._plan_pact(snap, pid, fire, 1)

    def _pact_enemy(self, pact: dict[str, Any]) -> str:
        """The common enemy of a pact about a war: named, or the partner's current foe."""
        if pact.get("watch_tag"):
            return pact["watch_tag"]
        ally = self._live_sets()[1].get(pact.get("tag", ""))
        return ally.foe_tag if ally is not None and ally.foe_tag else ""

    def _pact_bind(self, snap: Snapshot, pact: dict[str, Any]) -> str:
        enemy = self._pact_enemy(pact) or pact.get("tag", "")
        return f"votc_pact_bind = {{ partner = c:{pact['tag']} enemy = c:{enemy} }}"

    def _pact_due_text(self, snap: Snapshot, pid: int, pact: dict[str, Any], why: str) -> str:
        lines = [f"#{pid} \"{pact.get('title', '')}\" with {pact.get('party', '')} ({pact.get('kind', '')}"
                 + (f", {pact['tag']}" if pact.get("tag") else "") + f"), sealed on {pact.get('opened', '')}.",
                 f"The ruler promised: {pact.get('ruler_promise') or '-'}",
                 f"They promised: {pact.get('party_promise') or '-'}",
                 f"If kept, they said they would: {pact.get('if_kept') or '-'}",
                 f"If broken: {pact.get('if_broken') or '-'}"]
        if pact.get("history"):
            lines.append("Since then: " + " / ".join(pact["history"][-3:]))
        enemy = self._pact_enemy(pact)
        if why.startswith(self.PACT_REMINDER):
            side = ("A REMINDER, not yet a breach: the deadline is near and the ruler has not done it yet. Someone it "
                    "touches (the party, or an officer who knows) reminds the ruler. One choice does it now "
                    "(keeps_pact 'kept'), one asks for patience or explains the delay (keeps_pact 'n/a'), one lets "
                    "it go (keeps_pact 'broken'). Nothing is broken unless the ruler chooses so or the deadline passes.")
        elif pact.get("status") in ("broken", "closed"):
            side = ("The ruler broke their word: the choices are how the ruler answers their reaction (make "
                    "amends, pay, defy, blame another...).")
        elif "call on the ruler" in why or pact.get("watch") == "party_declares_war_on":
            side = ("Now the RULER must keep their side: one choice honours it (for a war: ruler_pact_move "
                    "'ruler_join_war'), at least one refuses or stalls - with what that costs in trust and "
                    "reputation.")
        else:
            side = "The choices are how the ruler answers what they did."
        return prompts.PACT_DUE.format(pact="\n".join(lines), why=why, ruler_side=side,
                                       enemy=self._cname(enemy) if enemy else "the common enemy")

    def _pact_move(self, snap: Snapshot, pact: dict[str, Any], move: str) -> tuple[str, str, bool]:
        """Check the other court's move against the game; (move, bind line, ruler moves possible)."""
        tag = pact.get("tag", "")
        wars, _allies = self._live_sets()
        enemy = self._pact_enemy(pact)
        ruler_ok = bool(tag and enemy)
        if move not in A.PACT_MOVES or not tag:
            return "none", "", ruler_ok
        if move == "join_war" and not (enemy and enemy in wars):
            self.log(f"  pact move dropped: join_war, the realm is not at war with {enemy or 'the enemy'}")
            return "none", "", ruler_ok
        if move == "make_peace" and tag not in wars:
            return "none", "", ruler_ok
        self.log(f"  {pact.get('party')} decides: {move.replace('_', ' ')}")
        return move, self._pact_bind(snap, pact), ruler_ok

    def _pact_said(self, pact: dict[str, Any], move: str) -> str:
        enemy = self._pact_enemy(pact)
        return A.PACT_MOVE_TEXT.get(move, move).format(party=pact.get("party", ""),
                                                       enemy=self._cname(enemy) if enemy else "the enemy")

    def _pact_result(self, snap: Snapshot, pid: int, result: str, move: str, title: str) -> None:
        mem = self.memory
        if mem is None or pid not in mem.pacts:
            return
        note = f"\"{title}\"" + (f": {move.replace('_', ' ')}" if move != "none" else "")
        pact = mem.pacts[pid]
        lasting = pact.get("watch") in self._LASTING
        if result == "honoured" and not lasting and pact.get("ruler_promise")                 and pact.get("watch") in ("party_must_do", "war_ends", "ruler_declares_war_on"):
            # They delivered; the ruler's own promise now binds the ruler.
            mem.pact_state(snap.date, pid, "open", note=f"they kept their word - {note}; the ruler's promise "
                                                        f"still binds", watch="ruler_bound", due=0)
        elif result == "honoured":
            mem.pact_state(snap.date, pid, "open" if lasting else "honoured", note=f"they kept their word - {note}")
        elif result == "refused":
            mem.pact_state(snap.date, pid, "refused", note=f"they broke their word - {note}")
        elif result == "delayed":
            mem.pact_state(snap.date, pid, "delayed", note=f"they stall - {note}", due=mem.later(snap.date, 30))
        elif result == "reacted":
            mem.pact_state(snap.date, pid, "closed", note=f"their answer to a broken promise - {note}")

    def _pact_choice(self, snap: Snapshot, opt: dict[str, Any]) -> None:
        """The ruler's answer to a pact event: kept, broken, or a new promise made."""
        mem = self.memory
        if mem is None:
            return
        pid = int(opt.get("pact") or 0)
        if pid and pid in mem.pacts:
            before = mem.pacts[pid].get("status")
            kp = opt.get("keeps_pact")
            label = opt.get("label", "")
            if kp == "broken" and before in mem.PACT_OPEN:
                mem.pact_state(snap.date, pid, "broken", note=f"the ruler chose: {label}")
                self._plan_pact(snap, pid, f"The ruler did not keep their side: {label}", random.randint(10, 40))
            elif kp == "kept" and before in mem.PACT_OPEN:
                lasting = mem.pacts[pid].get("watch") in self._LASTING
                mem.pact_state(snap.date, pid, "open" if lasting else "kept", note=f"the ruler kept it: {label}")
                if not lasting:
                    mem.end_pact_plans(pid, snap.date)
            elif kp == "kept" and pid in mem.owed_pacts():
                # a broken promise made good, late: settled for good
                mem.pact_state(snap.date, pid, "redeemed", note=f"made good late: {label}")
                mem.end_pact_plans(pid, snap.date)
                self.log(f"Pact made good late: {mem.pacts[pid].get('title')}")
            elif before == "triggered":
                lasting = mem.pacts[pid].get("watch") in self._LASTING
                mem.pact_state(snap.date, pid, "open" if lasting else "closed", note=f"the ruler chose: {label}")
        if opt.get("new_pacts"):
            self._collect_pacts(snap, opt["new_pacts"])

    # ============================================================== variety
    _TONES = ("grim", "comic", "tender", "tense", "uncanny", "mundane", "triumphant", "bittersweet", "scandalous",
              "hopeful", "absurd", "melancholy")
    _CENTRES = ("a noble house", "a commoner, a family or a village", "a cleric or a religious house",
                "a merchant or a guild", "a soldier or a captain", "a woman of the court", "the young",
                "a foreigner in the realm", "the ruler's own family", "an official of the crown",
                "a town and its council", "an artist, a scholar or a physician", "the poor", "an old servant")
    # What stands at the centre of a story in a realm that has grown: the same
    # dice, but a village no longer reaches an emperor's desk unless it burns.
    _CENTRES_GREAT = ("a great noble house or faction", "a governor or viceroy of a distant province",
                      "a subject ruler", "a prince of the church or a great religious order",
                      "a banking house or a trading company", "the army or the fleet as a body",
                      "an ambassador of a great power", "a whole province or people", "the ruler's own family",
                      "a colony or a far frontier", "a minister and his clients", "a great city and its guilds",
                      "an artist, a scholar or an inventor of the court", "a woman of the court")
    _TWISTS = ("a third party steps in", "a motive turns out to be another", "a victory that costs something",
               "a disaster that opens a door", "good news that is also a problem", "a misunderstanding",
               "someone the ruler knows returns, changed")

    def _worn_stories(self) -> str:
        """The kinds of story this campaign has told again and again, and the latest ones."""
        mem = self.memory
        if mem is None or not mem.arcs:
            return ""
        kinds = collections.Counter(str(a.get("kind") or "").strip().lower() for a in mem.arcs.values()
                                    if a.get("kind"))
        worn = [f"{k} ({c} times)" for k, c in kinds.most_common(10) if c >= 3]
        latest = [str(a.get("title") or "") for a in list(mem.arcs.values())[-12:] if a.get("title")]
        settled = [f"{a.get('title', '')}: {(a.get('steps') or [''])[-1][:140]}" for a in list(mem.arcs.values())[-10:]
                   if not a.get("open") and a.get("title")]
        out = ""
        if worn:
            out += ("\n\nKINDS OF STORY THIS CAMPAIGN HAS ALREADY TOLD MANY TIMES (leave them alone unless "
                    "something truly new happens in them): " + "; ".join(worn))
        if latest:
            out += "\nTHE LATEST STORIES (never their twin with new names): " + "; ".join(latest)
        if settled:
            out += ("\nSETTLED - these matters are closed as the ruler decided them; never bring one back as a "
                    "new dilemma (its people may appear only as they now are): " + " | ".join(settled))
        return out

    def _variety(self, snap: Snapshot) -> str:
        """Dice against sameness: a tone and a centre not used lately, sometimes a twist."""
        # Remembered with the campaign (not only this session): after a restart
        # the dice still avoid what the last stories used.
        meta = self.memory.meta if self.memory else {}
        recent = list(meta.get("variety") or getattr(self, "_recent_variety", []))
        tone = random.choice([t for t in self._TONES if t not in recent] or list(self._TONES))
        tier = self._scale_tier(snap)
        centres = (self._CENTRES if tier <= 1 else self._CENTRES_GREAT if tier >= 3
                   else self._CENTRES + self._CENTRES_GREAT[:6])
        centre = random.choice([c for c in centres if c not in recent] or list(centres))
        self._recent_variety = (recent + [tone, centre])[-12:]
        if self.memory:
            self.memory.meta["variety"] = self._recent_variety
        year = (gamedate.parse(snap.date) or (0, 0, 0))[0]
        line = f"THIS TIME, FOR VARIETY: the tone is {tone}; at its centre stands {centre}."
        if random.random() < 0.3:
            line += f" A turn in it: {random.choice(self._TWISTS)}."
        if year:
            line += f" THE AGE: it is {year} - let this decade show."
        line += (" THE SCALE: " + prompts.scale_of(snap.n("locations"), snap.rank, self._great(snap))[0].lower()
                 + " - the matter must be one that reaches a ruler of this size (see THE AGE AND THE SCALE).")
        rooted = self._rooted(snap)
        if rooted:
            line += "\n" + rooted
        return line

    # What a new thing may grow out of, by kind: what the realm has built, what it has lately
    # learnt, its laws, the ways of the age it has taken up, the currents of the world. (The
    # campaign's past is not a seed: every new thing is in line with it anyway - see VARIETY.)
    # Weights of the kinds (only those this realm has count).
    _ROOT_KINDS = {"b": 0.34, "a": 0.26, "l": 0.16, "i": 0.12, "s": 0.12}
    ROOTED_CHANCE = 0.55

    def _realm_material(self, snap: Snapshot) -> list[tuple[str, str]]:
        """(key, words) for what is really in this realm now - read from the game, so that
        what the court talks of changes as the realm and the age do."""
        out: list[tuple[str, str]] = []
        w, cid = self._world_of(snap)
        c = w.countries.get(cid) if w is not None and cid is not None else None
        cat = self.works_catalog
        built = getattr(w, "buildings", None) or {}
        for key, levels in sorted(built.items(), key=lambda kv: -kv[1])[:24]:
            name = ((cat.buildings.get(key) or {}).get("name") if cat is not None else "") or key.replace("_", " ")
            out.append((f"b:{key}", f"what this realm has built: {name} ({levels} in all)"))
        year = (gamedate.parse(snap.date) or (1337, 1, 1))[0]
        if c is not None and getattr(c, "advances", None) and self.codex.advance_ages:
            order = [k for k, _y, _n in prompts.AGES]
            age = w.age if w.age in order else prompts.age_of(year)[0]
            near = set(order[max(0, order.index(age) - 1): order.index(age) + 1])
            for adv in c.advances:
                # only what the game names for the player (technical steps have no name of their own)
                if self.codex.advance_ages.get(adv) in near and adv in self.codex.loc:
                    out.append((f"a:{adv}", f"something this realm has lately learnt or taken up: "
                                            f"{self.codex.word(adv)}"))
        if c is not None:
            laws = {law.key: law for law in self.codex.laws}
            for key, option in c.laws.items():
                law = laws.get(key)
                opt = (law.option_names[law.options.index(option)] if law and option in law.options
                       and law.options.index(option) < len(law.option_names) else self.codex.word(option))
                out.append((f"l:{key}", f"a law in force here: {(law.name if law and law.name else self.codex.word(key))}"
                                        f" - {opt}"))
            for inst in c.institutions:
                out.append((f"i:{inst}", f"a way of the age this realm has taken up: {prompts.readable(inst)}"))
        if w is not None:
            for sit in w.situations:
                if sit in ("data", "database", "id", "type") or len(sit) < 4:
                    continue
                out.append((f"s:{sit}", f"a current of these years in the world: {prompts.readable(sit)}"))
        return out

    def _rooted(self, snap: Snapshot) -> str:
        """Most new things grow out of something really in this realm, not used lately."""
        if random.random() > self.ROOTED_CHANCE:
            return ""
        material = self._realm_material(snap)
        if not material:
            return ""
        meta = self.memory.meta if self.memory else {}
        used = list(meta.get("rooted") or [])
        fresh = [m for m in material if m[0] not in used] or material
        kinds = {}
        for key, words in fresh:
            kinds.setdefault(key[0], []).append((key, words))
        kind = random.choices(list(kinds), weights=[self._ROOT_KINDS.get(k, 0.1) for k in kinds])[0]
        key, words = random.choice(kinds[kind])
        if self.memory:
            self.memory.meta["rooted"] = (used + [key])[-30:]
            self.memory._meta_dirty = True
        return (f"ROOTED IN THIS REALM: a starting point, if it serves - {words}: the people it touches, its "
                f"money, its quarrels, what it is changing here, as this realm and this age really have it, and in "
                f"line with what this campaign has lived through. Never a lecture about it.")

    def _great(self, snap: Snapshot) -> bool:
        w, cid = self._world_of(snap)
        return w is not None and cid is not None and any(c.cid == cid for c in w.great_powers(8))

    def _scale_tier(self, snap: Snapshot) -> int:
        return prompts.scale_tier(snap.n("locations"), snap.rank, self._great(snap))

    # =========================================================== the director
    def _on_pulse(self, snap: Snapshot) -> None:
        if snap.date == self._last_pulse_date:
            # Paused, or sitting in a menu: ask less often.
            self._pulse_gap = min(self._pulse_gap * 1.6, self.cfg.pulse_backoff_max_s)
            return
        first = not self._last_pulse_date
        self._last_pulse_date = snap.date
        self._pulse_gap = self.cfg.pulse_interval_s
        if not first and self.memory is not None:
            # Promises are watched even while the ruler talks: a war declared
            # from the game's own menus counts as much as one declared here.
            self._watch_pacts(snap)
            self._threats_due(snap)
            self._biographer_ages(snap)
            self._century_check(snap)
        if first or self.session is not None or self.busy or self.memory is None:
            return
        if self.battle.live:
            return                  # nothing else comes while the ruler commands a battle
        if self.game_debug is False:
            return                  # it would come without words: the player was told how to fix it
        self._direct(snap)

    # =========================================================== the director
    def _direct(self, snap: Snapshot) -> None:
        """Decide whether something happens now. Often, never constantly.

        In order: a story that is due for its next stage; a follow-up the court
        owes the ruler (the reactions to a decree); otherwise, after a random
        pause, something new - usually a story with choices, sometimes a page of
        chronicle or someone asking for an audience.
        """
        mem = self.memory
        assert mem is not None
        if mem.needs_summary() and not self._folding:
            self._folding = True
            self._submit(self._fold_chronicle)
        # An event the ruler never answered (closed, lost) must not block the court.
        # Events pause the game: a month passing means the question was lost.
        if mem.awaiting_arc:
            arc = mem.arcs.get(mem.awaiting_arc) or {}
            if gamedate.days_between(arc.get("asked", ""), snap.date) > self.UNANSWERED_DAYS:
                mem.arc_step(snap.date, mem.awaiting_arc, text="The crown gave no answer; things took their course.",
                             after_days=30)
        # Room to play: whatever is due waits its turn. After anything the court
        # brought unasked, and after a conversation of the ruler's own, some days
        # pass before the next - what matured meanwhile comes after, one at a time.
        if not self._breathing_room(snap, relaxed=self._new_due(snap)):
            return
        # What the player chose the pace of comes FIRST once its time has come: the
        # follow-ups of the ruler's deeds and the next stages of stories are many in
        # an active reign, and served first they took every turn - new events then
        # came once in years. They wait instead (only a pact falling due does not).
        gone = mem.expire_plans(snap.date, self.PLAN_STALE_DAYS)
        if gone:
            self.log(f"{gone} follow-up(s) waited over half a year: their moment has passed")
        due = mem.due_plan(snap.date)
        # a pact falling due, or a consequence that has waited two months, comes before anything new
        urgent = (due is not None and not mem.awaiting_arc
                  and (due[1].get("kind") == "pact" or mem.overdue_days(due[1], snap.date) >= self.PLAN_OWED_DAYS))
        if not urgent and self._new_due(snap):
            self._bring_new(snap)
            return
        arc_id = mem.due_arc(snap.date)
        if arc_id and not mem.awaiting_arc and not urgent:
            self.log(f"The story goes on: {mem.arcs[arc_id].get('title', '')}")
            self._submit(lambda: self._continue_arc(snap, arc_id))
            mem.meta["last_unprompted"] = snap.date; mem._meta_dirty = True
            return
        if due is not None and not mem.awaiting_arc:
            pid, plan = due
            mem.plan_done(pid, snap.date)
            self.log(f"A follow-up is due: {plan.get('about', '')}")
            mem.meta["last_unprompted"] = snap.date; mem._meta_dirty = True
            self._submit(lambda: self._run_plan(snap, plan))
            return
        if self._war_due(snap):
            mem.last_war_date = snap.date
            self._war_gap = 0
            mem.meta["last_unprompted"] = snap.date; mem._meta_dirty = True
            can_open = not mem.awaiting_arc and len(mem.open_arcs()) < self.cfg.max_open_stories
            if can_open and random.random() < 0.45:
                self.log("New story (the army at war)")
                self._submit(lambda: self._story_event(snap, focus="army"))
            else:
                self.log("A page of the war")
                self._submit(lambda: self._story(snap, "chronicle", war=True))
            return

    def _new_due(self, snap: Snapshot) -> bool:
        """Has the time come for something NEW, at the pace the player chose?

        Counted from the last new thing only: the follow-ups of the ruler's deeds and
        the next stages of stories come on top and never reset this clock. The pause
        is drawn in the chosen range; a troubled realm shortens it."""
        mem = self.memory
        pace = FREQUENCIES.get(self.cfg.event_frequency, FREQUENCIES["normal"])
        if mem is None or pace is None or time.time() < self._new_retry_at:
            return False
        low, high = pace
        if not self._story_gap:
            self._story_gap = random.randint(low, high)
        since = gamedate.days_between(mem.last_new_date, snap.date)
        # a date from another course of events (a save was loaded) counts as long ago
        return not (0 <= since < self._story_gap * (1 - self._pressure(snap)))

    def _bring_new(self, snap: Snapshot) -> None:
        """Something new, unasked. Most new things ask the ruler something: a story with
        choices (and the ruler's own answer in person), or someone at the door asking to be
        received; a page of chronicle, now and then."""
        mem = self.memory
        assert mem is not None
        can_open = not mem.awaiting_arc and len(mem.open_arcs()) < self.cfg.max_open_stories
        upkeep = self._upkeep_due(snap) if can_open else None
        if upkeep is not None and random.random() < self.UPKEEP_CHANCE:
            self.log(f"A measure in force is put to the test: {upkeep['label']}")
            self._submit(lambda: self._new_thing(snap, lambda: self._story_event(
                snap, seed=f"how {upkeep['label']} holds up, years after it was set in force", upkeep=upkeep)))
            return
        roll = random.random()
        # New situations first; someone at the door now and then - and not again while the last
        # few things were audiences already.
        knock_room = snap.court and not self._knocks_crowding()
        if can_open and roll < self.NEW_STORY_SHARE:
            # What kind of story: the business of state, the ruler's daily life, the life of
            # the realm, or the world abroad (envoys, requests, news) when there is a reason.
            focus = random.choices(["government", "daily", "realm", "foreign"],
                                   weights=self._focus_weights(snap))[0]
            names = {"government": "affairs of government", "daily": "daily life",
                     "realm": "life of the realm", "foreign": "affairs from abroad"}
            self.log(f"New story ({names[focus]})")
            self._submit(lambda: self._new_thing(snap, lambda: self._story_event(snap, focus=focus)))
        elif knock_room and (roll < self.NEW_STORY_SHARE + self.NEW_KNOCK_SHARE if can_open else roll < 0.3):
            # a story waits for the ruler (or enough are open): someone comes to the door instead
            self._submit(lambda: self._new_thing(snap, lambda: self._knock(snap), mark="knock"))
        else:
            self.log("A new page of the chronicle")
            rooted = self._rooted(snap)
            self._submit(lambda: self._new_thing(snap, lambda: self._story(snap, "chronicle", rooted=rooted),
                                                 mark="chronicle"))

    # A lasting gain (a standing measure, a policy or an edict of five years and more) is put to the
    # test a year or two after it began, and now and then after that: kept by sound choices, lost by
    # wrong ones (prompts.UPKEEP_TASK).
    UPKEEP_FIRST = (330, 730)      # days after it began before its first test
    UPKEEP_AGAIN = (420, 800)      # days between one test and the next
    UPKEEP_GAP = 150               # at most one such test in this many days, whatever is in force
    UPKEEP_CHANCE = 0.45           # when one is due, the share of new things it takes

    def _lasting(self, snap: Snapshot) -> list[dict[str, Any]]:
        """The lasting gains in force: standing measures, and policies and edicts of 5 years and more."""
        mem = self.memory
        out: list[dict[str, Any]] = []
        if mem is None:
            return out
        for slot, m in sorted((mem.measures or {}).items()):
            gain = MS.GAIN_TEXT.get(m.get("gain", ""), m.get("gain", "")).split(":")[0]
            out.append({"id": f"standing:{slot}:{m.get('date', '')}", "kind": "standing", "slot": int(slot),
                        "date": m.get("date", ""),
                        "label": f"the standing measure \"{m.get('name', '')}\" ({gain})"})
        for p in mem.meta.get("lasting") or []:
            if not p.get("lost") and snap.year and int(p.get("until") or 0) > snap.year:
                origin = f" (from \"{p['origin']}\")" if p.get("origin") else ""
                out.append({**p, "label": f"the measure {p.get('desc', '')}{origin}"})
        return out

    def _upkeep_due(self, snap: Snapshot) -> dict[str, Any] | None:
        """A lasting gain whose time to be tested has come, if any (and none was tested lately)."""
        mem = self.memory
        if mem is None or not snap.date:
            return None
        last = mem.meta.get("upkeep_last", "")
        if last and 0 <= gamedate.days_between(last, snap.date) < self.UPKEEP_GAP:
            return None
        items = self._lasting(snap)
        nxt = {k: v for k, v in (mem.meta.get("upkeep_next") or {}).items() if any(i["id"] == k for i in items)}
        for item in items:
            if item["id"] not in nxt:
                nxt[item["id"]] = mem.later(item.get("date") or snap.date, random.randint(*self.UPKEEP_FIRST))
        if nxt != mem.meta.get("upkeep_next"):
            mem.meta["upkeep_next"] = nxt
            mem._meta_dirty = True
        today = gamedate.key(snap.date)
        due = [i for i in items if int(nxt.get(i["id"], 0)) <= today]
        return random.choice(due) if due else None

    def _upkeep_tested(self, snap: Snapshot, item: dict[str, Any]) -> None:
        mem = self.memory
        if mem is None:
            return
        mem.meta["upkeep_last"] = snap.date
        mem.meta.setdefault("upkeep_next", {})[item["id"]] = mem.later(snap.date, random.randint(*self.UPKEEP_AGAIN))
        mem._meta_dirty = True

    def _lose_lasting(self, snap: Snapshot, item: dict[str, Any]) -> None:
        """The ruler's choice let a lasting gain go: the game takes it away with the choice, the memory here."""
        mem = self.memory
        if mem is None:
            return
        if item.get("kind") == "standing":
            m = (mem.measures or {}).get(int(item.get("slot", 0)))
            if m is not None and f"standing:{item.get('slot')}:{m.get('date', '')}" == item.get("id"):
                mem.measure_end(snap.date, int(item["slot"]))
        else:
            for p in mem.meta.get("lasting") or []:
                if p.get("id") == item.get("id"):
                    p["lost"] = snap.date
            mem._meta_dirty = True
        mem.remember_event(snap.date, "decision", f"Lost: {item.get('label', 'a measure in force')}")
        self.log(f"  the measure is lost: {item.get('label', '')}")

    # What the director brings when something new is due: mostly a new situation with choices,
    # someone asking to be received now and then, a page of chronicle otherwise.
    NEW_STORY_SHARE = 0.72
    NEW_KNOCK_SHARE = 0.18
    KNOCKS_CROWD = 3              # of the last RECENT_KINDS things the court brought, this many audiences are plenty
    RECENT_KINDS = 6

    def _note_kind(self, kind: str) -> None:
        """Remember what the court brought (story, knock, chronicle), for the balance between them."""
        if self.memory is None:
            return
        recent = list(self.memory.meta.get("recent_kinds") or [])
        self.memory.meta["recent_kinds"] = (recent + [kind])[-self.RECENT_KINDS:]
        self.memory._meta_dirty = True

    def _knocks_crowding(self) -> bool:
        recent = list((self.memory.meta if self.memory else {}).get("recent_kinds") or [])
        return recent.count("knock") >= self.KNOCKS_CROWD

    def _new_thing(self, snap: Snapshot, job: Callable[[], None], mark: str = "") -> None:
        """Write something new, and count it as the new thing of its time only if it really
        reached the ruler: a request the AI could not answer (a limit, an error) does not use
        up the turn - it is tried again soon, a little later each time it fails."""
        mem = self.memory
        if mem is None:
            return
        before = len(mem.pages)
        try:
            job()
        finally:
            if self.memory is mem and len(mem.pages) > before:
                low, high = FREQUENCIES.get(self.cfg.event_frequency) or FREQUENCIES["normal"]
                self._story_gap = random.randint(low, high)
                mem.last_new_date = snap.date
                if mark == "chronicle":
                    mem.last_chronicle_date = snap.date
                elif mark == "knock":
                    mem.last_knock_date = snap.date
                mem.meta["last_unprompted"] = snap.date; mem._meta_dirty = True
                self._new_failures = 0
            else:
                self._new_failures += 1
                wait = min(600, 20 * 2 ** self._new_failures)
                self._new_retry_at = time.time() + wait
                self.log(f"  the new event could not be written: tried again in about {wait // 60 or 1} minute(s)")

    # Days of quiet (in game) after something the court brought unasked, and after a
    # conversation of the ruler's own, by the player's pace. Pacts coming due are
    # answered sooner: an ally called to war does not wait a month.
    _BREATHE = {"off": 25, "yearly": 30, "rare": 28, "calm": 22, "normal": 18, "very": 12}
    # A follow-up that has waited this long past its time comes before anything new; one
    # that has waited half a year is let go (its moment has passed). Never a pact's.
    PLAN_OWED_DAYS = 60
    PLAN_STALE_DAYS = 180
    _AFTER_SCENE = {"off": 15, "yearly": 20, "rare": 20, "calm": 15, "normal": 12, "very": 8}

    UNANSWERED_DAYS = 30

    def _breathing_room(self, snap: Snapshot, relaxed: bool = False) -> bool:
        """relaxed: something new is overdue - a week of quiet is enough, so that the pace the
        player chose (20-50 days on Normal) is not stretched by the follow-ups before it."""
        mem = self.memory
        if mem is None:
            return False
        pace = self.cfg.event_frequency if self.cfg.event_frequency in self._BREATHE else "normal"
        since_event = gamedate.days_between(mem.meta.get("last_unprompted", ""), snap.date)
        since_scene = gamedate.days_between(mem.meta.get("last_scene", ""), snap.date)
        due = mem.due_plan(snap.date)
        urgent = due is not None and due[1].get("kind") == "pact"
        need = 5 if urgent else (min(7, self._BREATHE[pace]) if relaxed else self._BREATHE[pace])
        # a date from another course of events (a save was loaded) counts as long ago
        if mem.meta.get("last_unprompted") and 0 <= since_event < need:
            return False
        if mem.meta.get("last_scene") and 0 <= since_scene < (3 if urgent else self._AFTER_SCENE[pace]):
            return False
        return True

    def _knock(self, snap: Snapshot) -> None:
        """Someone asks to be received - only someone with a real reason, chosen for it."""
        mem = self.memory
        if mem is None or not snap.court:
            return
        court = snap.court[:8]
        people = "\n".join(f"{i}. {p.name} - {p.role or p._implied_role() or 'at court'}"
                           + (f", {p.age} years old" if p.age else "")
                           for i, p in enumerate(court, start=1))
        ask = "\n\n".join(x for x in (
            prompts.KNOCK_PLAN_TASK,
            "THE REALM: " + prompts.render_court_view(snap),
            prompts.render_dossier(snap),
            "WHAT THE COURT REMEMBERS:\n" + mem.brief()[:3500],
            "RECENT DOINGS OF THE RULER:\n" + "\n".join(self._recent_doings()),
            "THE PEOPLE AT COURT:\n" + people,
            self._rooted(snap).replace("a starting point, if it serves", "a matter may well come from")
            .replace("Never a lecture about it.", "Only if it gives someone a real reason."),
            CONTINUITY,
            ("AUDIENCES ALREADY ASKED (never the same matter again; the same person only with something new):\n"
             + "\n".join(f"- {p.get('date', '')}: {p.get('title', '')} - {str(p.get('body', ''))[:160]}"
                         for p in [p for p in mem.pages if p.get("kind") == "knock"][-6:]))
            if any(p.get("kind") == "knock" for p in mem.pages) else "",
        ) if x)
        try:
            plan = self.client.complete_json([{"role": "user", "content": ask}], prompts.KNOCK_PLAN_SCHEMA,
                                             schema_name="votc_knock_plan", temperature=0.7, max_tokens=700)
        except Player2Error as exc:
            self.log(f"  no audience: {exc}")
            return
        who = int(plan.get("who") or 0)
        if not plan.get("worth_it") or not 1 <= who <= len(court):
            self.log("Nobody at court has a real reason to ask for an audience now.")
            return
        visit = (f"THIS VISIT: {_t(plan.get('matter'), 400)} Why them: {_t(plan.get('why_them'), 300)} "
                 f"Why now: {_t(plan.get('why_now'), 300)} What the ruler will have to decide or learn: "
                 f"{_t(plan.get('the_choice'), 400)}")
        self.log(f"Someone asks for an audience: {court[who - 1].name} - {_t(plan.get('matter'), 160)}")
        self._story(snap, "knock", who=court[who - 1].name, visit=visit)

    def _news_since(self, since: str, limit: int = 30) -> list[str]:
        """What the court remembers happening after a date, oldest first."""
        mem = self.memory
        if mem is None:
            return []
        k = gamedate.key(since)
        out = [f"{e.date}: {e.text}" for e in mem.events if gamedate.key(e.date) > k]
        for p in mem.pacts.values():
            for h in p.get("history") or []:
                if gamedate.key(h.split(":", 1)[0]) > k:
                    out.append(f"{h} (pact with {p.get('party', '?')}: {p.get('title', '')}, now {p.get('status')})")
        return out[-limit:]

    def _still_open(self, what: str, since: str) -> tuple[str, str]:
        """Before a matter comes back: has what happened since settled or changed it?"""
        news = self._news_since(since)
        if not news:
            return "open", ""
        try:
            data = self.client.complete_json(
                [{"role": "user", "content": prompts.STILL_OPEN_TASK.format(what=what, since=since or "earlier",
                                                                              news="\n".join(news))}],
                prompts.STILL_OPEN_SCHEMA, schema_name="votc_still_open", temperature=0.2, max_tokens=400)
        except Player2Error:
            return "open", ""
        status = data.get("status") if data.get("status") in ("open", "settled", "changed") else "open"
        if status != "open":
            self.log(f"  checked against what happened since: {status} - {_t(data.get('why'), 200)}")
        return status, (_t(data.get("now"), 400) if status == "changed" else _t(data.get("why"), 300))

    def _continue_arc(self, snap: Snapshot, arc_id: int) -> None:
        mem = self.memory
        arc = mem.arcs.get(arc_id) if mem else None
        if arc is None:
            return
        what = f"{arc.get('title', '')}: " + " / ".join(arc.get("steps", [])[-3:])
        status, note = self._still_open(what, arc.get("asked") or arc.get("opened", ""))
        if status == "settled":
            mem.arc_step(snap.date, arc_id, text=f"Settled meanwhile: {note}", after_days=0, close=True)
            self.log(f"  the story ends here: it was settled meanwhile ({arc.get('title', '')})")
            return
        self._story_event(snap, arc_id=arc_id, update=note if status == "changed" else "")

    def _run_plan(self, snap: Snapshot, plan: dict[str, Any]) -> None:
        about = plan.get("about", "")
        # the scene this follow-up brings is one step deeper in the chain
        self._chain = int(plan.get("chain") or 0) + 1
        mem = self.memory
        what = about
        if plan.get("kind") == "pact" and plan.get("pact") and mem is not None:
            pact = mem.pacts.get(int(plan["pact"]))
            if pact is None or pact.get("status") not in mem.PACT_OPEN:
                self.log(f"  not coming: the pact is no longer open ({about[:100]})")
                return
            what = (f"the pact '{pact.get('title', '')}' with {pact.get('party', '?')} (the ruler promised: "
                    f"{pact.get('ruler_promise') or '-'}; they promised: {pact.get('party_promise') or '-'}) - {about}")
        status, note = self._still_open(what, plan.get("planned") or "")
        if status == "settled":
            self.log(f"  not coming: it was settled meanwhile ({about[:100]})")
            if plan.get("kind") == "pact" and plan.get("pact") and mem is not None:
                mem.pact_state(snap.date, int(plan["pact"]), "closed", note=f"settled meanwhile: {note}")
            return
        if status == "changed" and note:
            about = f"{about} - but things have moved: {note}"
        if plan.get("kind") == "pact" and plan.get("pact"):
            self._story_event(snap, pact_id=int(plan["pact"]), pact_why=about)
        elif (plan.get("kind") == "knock" and any(p.name == plan.get("who") for p in snap.court)
              and not self._knocks_crowding()):
            self._story(snap, "knock", about=about, who=plan.get("who", ""))
        elif plan.get("kind") == "chronicle":
            self._story(snap, "chronicle", about=about + " (what came of it - the matter itself is settled "
                                                          "and is not reopened)")
        else:
            self._story_event(snap, seed=about)

    # A story usually ends by its third stage; real developments may carry it to about six,
    # and a truly great matter to eight - never further.
    STORY_USUAL_STAGES = 3
    STORY_LONG_STAGES = 6
    STORY_MAX_STAGES = 8

    def _story_length_note(self, now: int) -> str:
        """How long the story may still run, said to the writer of stage `now`."""
        if now < self.STORY_USUAL_STAGES:
            return ""
        if now == self.STORY_USUAL_STAGES:
            return ("A story normally ENDS about here, at its third stage: write the ending, unless something "
                    "has really developed that cannot be left unresolved (a new party enters, the matter has "
                    "grown, the last choice opened a new front). Then the story may go on - up to about six "
                    "stages in all - and only the choices that truly leave it open continue; the others close "
                    "it. Never keep it open only to have more of it.")
        if now < self.STORY_LONG_STAGES:
            return ("The story has already run past its usual length: it goes on only while a real, new "
                    "development is still unresolved. As soon as the matter is resolved, this is the ending.")
        return (f"This should be the LAST stage (stage {now}): end it, unless it is truly one of the great "
                f"matters of the reign (a war, a succession, an upheaval of the whole realm) and something "
                f"decisive is still open - then it may go on, to stage {self.STORY_MAX_STAGES} at the most.")

    @staticmethod
    def _repeats(arc: dict[str, Any], data: dict[str, Any]) -> bool:
        """Does this new stage only ask again what the story already asked? (two of its three
        choices close to choices already put to the ruler, or the same title again)"""
        def words(text: str) -> set[str]:
            return {w for w in re.findall(r"\w{4,}", (text or "").lower())}

        past = [words(o) for o in arc.get("past_options", []) if o]
        new = [words(str(o.get("label", ""))) for o in (data.get("options") or []) if isinstance(o, dict)]
        # the same button again: most of its words shared with a button already offered
        close = sum(1 for n in new if len(n) >= 2 and any(len(n & p) >= 2 and len(n & p) / len(n | p) >= 0.5
                                                           for p in past))
        title = words(str(data.get("title", "")))
        same_title = any(title and len(title & words(t)) / max(1, len(title)) >= 0.8 for t in arc.get("past_titles", []))
        return close >= 2 or same_title

    def _focus_weights(self, snap: Snapshot) -> list[int]:
        """government, daily, realm, foreign - the business of state first, and the world
        abroad only when there is a reason. (What follows from the ruler's own deeds is
        not drawn here: the AI schedules it as follow-ups.)"""
        doings = self._recent_doings()
        foreign = 8
        live = self.live_picture or {}
        if snap.at_war or live.get("war"):
            foreign += 6
        if live.get("rival") or live.get("lord") or live.get("subj"):
            foreign += 3
        if any(k in d for d in doings for k in ("envoy", "ambasc", "incontro", "visita", "guerra", "war",
                                                  "alliance", "alleanz", "treaty", "tregua")):
            foreign += 8          # what the ruler did abroad brings answers from abroad
        return [40, 16, 18, foreign]

    @staticmethod
    def _pressure(snap: Snapshot) -> float:
        """Troubled realms have more happening in them."""
        p = 0.0
        if snap.at_war:
            p += 0.1
        if snap.n("stability") < 0:
            p += 0.1
        if snap.n("loans") > 0:
            p += 0.05
        p += sum(0.05 for v in snap.estates.values() if 0 < v < 35)
        return min(p, 0.3)

    # stability below, legitimacy below, an estate below, war exhaustion from
    _CRISIS = {"easy": (-50, 15, 10, 15), "normal": (-25, 25, 20, 10),
               "hard": (-10, 35, 30, 7), "very_hard": (0, 45, 35, 5)}

    def _crisis(self, snap: Snapshot) -> bool:
        """Is the realm in the kind of trouble that breeds risings on its own? (Sooner on harder settings.)"""
        stab, legit, estate, weary = self._CRISIS.get(self.cfg.difficulty, self._CRISIS["normal"])
        if snap.n("stability") < stab or (0 < snap.n("legitimacy") < legit):
            return True
        if any(0 < snap.estates.get(e, 100) < estate for e in ("nobles", "clergy", "burghers", "peasants")):
            return True
        return snap.at_war and snap.n("warexhaustion") >= weary

    _TIERS = ("weak", "mild", "severe")
    # A consequence's weight by difficulty: tier -> tier, (bad for the ruler, good for the ruler).
    _WEIGHT = {
        "easy": ({"severe": "mild", "mild": "weak"}, {}),
        "hard": ({"weak": "mild"}, {"severe": "mild"}),
        "very_hard": ({"weak": "mild", "mild": "severe"}, {"severe": "mild", "mild": "weak"}),
    }

    @staticmethod
    def _is_good(action: dict[str, Any]) -> bool:
        kind = action["kind"]
        if kind in ("gold_loss", "manpower_loss", "policy_penalty", "trust_loss", "rival_declare",
                    "progress_resented"):
            return False
        if kind == "war_exhaustion":
            return action.get("sign") == "penalty"
        return action.get("sign", "bonus") != "penalty"

    # ------------------------------------------------------------ balance of consequences
    # The models judged nearly every deed as costly: several prices for one measure, an estate
    # slighted by a tenth for a trifle, the same estate struck again and again, penalties never
    # limited while gains were. These rules keep the AI's judgement, and only its excesses go.
    _ORDER = {A.SLIGHT: 0, "weak": 1, "mild": 2, "severe": 3, A.GRAND: 4}
    _MOOD_KINDS = {"stability", "prestige", "legitimacy", "government_power", "estate", "all_estates"}
    # What a measure that raises money must never cut: the income itself.
    _INCOME_AREAS = {"taxation", "trade_income", "currency", "nobles_taxes", "clergy_taxes", "burghers_taxes",
                     "peasants_taxes"}
    _MONEY_AREAS = _INCOME_AREAS | {"trade", "production", "prosperity"}
    ESTATE_MEMORY_DAYS = 120        # a second blow to the same estate within this is one step softer
    FORCE_MAJEURE_DAYS = 365        # a page of chronicle may weigh more at most once in this long

    def _tier_at_most(self, action: dict[str, Any], top: str) -> dict[str, Any]:
        tier = action.get("tier")
        if tier not in self._ORDER or self._ORDER[tier] <= self._ORDER[top]:
            return action
        if top == A.SLIGHT and action["kind"] not in A.SLIGHT_KINDS:
            top = "weak"
        return {**action, "tier": top}

    def _softer(self, action: dict[str, Any]) -> dict[str, Any]:
        steps = {"severe": "mild", "mild": "weak", "weak": A.SLIGHT if action["kind"] in A.SLIGHT_KINDS else "weak"}
        return {**action, "tier": steps.get(action.get("tier"), action.get("tier"))}

    def _estate_hit_lately(self, estate: str) -> bool:
        mem, snap = self.memory, self.snapshot
        if mem is None or snap is None:
            return False
        return any(h.get("estate") == estate and 0 <= gamedate.days_between(h.get("date", ""), snap.date)
                   <= self.ESTATE_MEMORY_DAYS for h in mem.meta.get("estate_hits") or [])

    def _balanced(self, kind: str, ctx: dict[str, Any], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The consequences the AI chose, kept within reason (see the rules above _ORDER)."""
        dropped: list[str] = []
        acts = list(actions)
        # 1. A page of chronicle is a narration the ruler had no part in: tiny effects, unless it
        #    tells a blow of fate nobody could have prevented (rare: _force_majeure).
        if kind == "chronicle":
            big = bool(ctx.get("force_majeure"))
            kept = []
            for a in acts:
                if a["kind"] in ("edict_short", "edict_long", "great_effort", "societal") and not big:
                    dropped.append(a["kind"])
                    continue
                if a["kind"] in ("policy_bonus", "policy_penalty"):
                    a = {**a, "tier": "weak" if not big else min(a["tier"], "mild", key=self._ORDER.get),
                         "years": a["years"] if int(a["years"]) <= (5 if not big else 10) else ("5" if not big else "10")}
                else:
                    a = self._tier_at_most(a, A.SLIGHT if not big else "mild")
                kept.append(a)
            acts = kept[:3 if big else 2]
        # 2. One move per lever: the same estate, area or currency moved twice is the AI repeating itself.
        seen, kept = set(), []
        for a in acts:
            key = (a["kind"] if a["kind"] not in ("policy_bonus", "policy_penalty") else "policy",
                   a.get("estate", ""), a.get("area", ""))
            if key in seen:
                dropped.append(f"{a['kind']} again")
                continue
            seen.add(key)
            kept.append(a)
        acts = kept
        grave = int(ctx.get("gravity") or 0) >= R.GRAVE
        # 3. The estates: the whole realm's mood falls only for a grave deed; an estate struck
        #    lately is struck more softly now.
        kept = []
        for a in acts:
            if a["kind"] == "all_estates" and a.get("sign") == "penalty" and not grave:
                dropped.append("every estate's displeasure")
                continue
            if a["kind"] == "estate" and a.get("sign") == "penalty" and self._estate_hit_lately(a.get("estate", "")):
                a = self._softer(a)
            kept.append(a)
        acts = kept
        # 4. Statecraft: a realm that submits, a war the realm wants - nobody is angered by it.
        sc = ctx.get("statecraft") or ""
        if sc:
            kept, moods = [], 0
            for a in acts:
                if a["kind"] in self._MOOD_KINDS and not self._is_good(a):
                    if sc in ("submission", "welcome"):
                        dropped.append(f"{a['kind']} penalty ({sc})")
                        continue
                    if sc in ("accepted", "contested"):
                        moods += 1
                        if moods > 1:
                            dropped.append(f"{a['kind']} penalty ({sc})")
                            continue
                        a = self._tier_at_most(a, A.SLIGHT if sc == "accepted" else "weak")
                kept.append(a)
            acts = kept
        # 5. Money that comes in is a gain: a tax, toll or levy never cuts the income itself.
        if ctx.get("brings_money"):
            kept = []
            for a in acts:
                if a["kind"] == "policy_penalty" and a.get("area") in self._INCOME_AREAS:
                    dropped.append(f"{a['area']} penalty on a measure that raises money")
                    continue
                if (a["kind"] == "policy_penalty" and a.get("area") in self._MONEY_AREAS) or a["kind"] == "gold_loss":
                    a = self._tier_at_most(a, "weak")
                kept.append(a)
            acts = kept
        # 6. The net: what the deed costs is in proportion to what it brings. A decree that worked
        #    leaves the realm better off; a grave deed pays in full.
        if not grave and kind != "chronicle":
            goods = [a for a in acts if self._is_good(a)]
            room = {"good": max(1, len(goods)), "mixed": len(goods) + 1}.get(ctx.get("fortune_class", ""),
                                                                            len(goods) + 2)
            top_good = max((self._ORDER.get(a.get("tier"), 1) for a in goods), default=1)
            kept, bads = [], 0
            for a in acts:
                if not self._is_good(a):
                    bads += 1
                    if bads > room:
                        dropped.append(f"{a['kind']} (more prices than gains)")
                        continue
                    if ctx.get("fortune_class") == "good" and self._ORDER.get(a.get("tier"), 0) > top_good:
                        a = self._tier_at_most(a, next(t for t, o in self._ORDER.items() if o == top_good))
                kept.append(a)
            acts = kept
        if dropped:
            self.log("  kept within reason: " + "; ".join(dropped[:6]))
        return acts

    def _force_majeure(self, data: dict[str, Any]) -> bool:
        """Does this page of chronicle tell a blow of fate the court could not prevent - and may it
        weigh more than a narration usually does? At most once a year."""
        if not data.get("beyond_control") or self.memory is None or self.snapshot is None:
            return False
        last = self.memory.meta.get("force_majeure", "")
        if last and 0 <= gamedate.days_between(last, self.snapshot.date) < self.FORCE_MAJEURE_DAYS:
            self.log("  a blow of fate, but one came lately: this page weighs as little as any other")
            return False
        self.memory.meta["force_majeure"] = self.snapshot.date
        self.memory._meta_dirty = True
        return True

    # ------------------------------------------------------------ how the realm takes a war
    _WAR_TALK = re.compile(r"\b(war|wars|attack\w*|invad\w*|conquer\w*|campaign\w*|mobili[sz]\w*|march\w* on|"
                           r"guerr\w*|attacc\w*|invader\w*|invasion\w*|conquist\w*|campagn\w*|mobilit\w*|marciare|"
                           r"krieg\w*|angriff\w*|guerre\w*|attaqu\w*|ataca\w*|ataque\w*)\b", re.I)
    _STATECRAFT = {"declare_war", "press_claim", "white_peace", "form_alliance", "guarantee",
                   "grant_military_access", "swear_truce", "take_submission", "accept_vassalage", "union"}
    _SUBMISSION = {"take_submission", "accept_vassalage", "union"}
    _HARSH_WORKS = {"depopulate", "state_religion", "revoke_privilege", "convert_religion", "convert_culture"}
    _HARSH_POWER = {"kill", "exile", "depose", "revolt", "civil_war", "civil_war_join"}
    _WAR_CAP = {"welcome": 3, "accepted": 4, "contested": 4, "unpopular": 6}

    def _live_country(self, tag: str) -> Any:
        for kind in ("rival", "near", "ally", "war", "gp", "lord", "subj"):
            for c in (self.live_picture or {}).get(kind, []) or []:
                if getattr(c, "tag", "") == tag:
                    return c
        return None

    def _in_live(self, kind: str, tag: str) -> bool:
        return any(getattr(c, "tag", "") == tag for c in (self.live_picture or {}).get(kind, []) or [])

    def _war_view(self, snap: Snapshot, tag: str) -> tuple[str, str]:
        """How the realm would take a war on this court: (level, text). From what the game shows -
        rivalry, an ally's enemy, a broken word, faith, and the realm's own state - never a mood
        the AI imagines. The common people are not against war as such."""
        live = self._live_country(tag)
        tc = snap.target_country if snap.target_country is not None and snap.target_country.tag == tag else None
        name = (tc.name if tc else "") or (getattr(live, "name", "") if live else "") or tag
        n = snap.numbers
        pro, con = [], []
        rival = self._in_live("rival", tag) or bool(tc and n.get("target_rival"))
        if rival:
            pro.append("they are the realm's declared rival")
        foes_of = [a.name for a in (self.live_picture or {}).get("ally", []) or [] if getattr(a, "foe_tag", "") == tag]
        if foes_of:
            pro.append(f"they are at war with our ally {foes_of[0]}")
        if self.memory is not None and any(p.get("tag") == tag and p.get("status") in ("broken", "refused")
                                           for p in self.memory.pacts.values()):
            pro.append("they broke their word to us or refused what they owed")
        other_faith = bool(tc and tc.religion and snap.religion and tc.religion != snap.religion)
        if other_faith:
            pro.append(f"they are of another faith ({tc.religion})")
        if self._in_live("near", tag):
            pro.append("a neighbour: land and ransoms within reach, what the nobles hope for")
        if tc and tc.religion and tc.religion == snap.religion and not rival:
            con.append("they share the realm's faith")
        if snap.n("warexhaustion") >= 5:
            con.append("the realm is already weary of war")
        if snap.n("maxmanpower") > 0 and snap.n("manpower") < 0.3 * snap.n("maxmanpower"):
            con.append("few men are left to raise")
        if snap.n("stability") < 0:
            con.append("the realm is unsettled")
        if snap.at_war:
            con.append("the realm is already at war")
        if snap.n("gold") <= 0 or (snap.n("balance") < 0 and snap.n("gold") < abs(snap.n("balance")) * 6):
            con.append("the chest is nearly empty")
        theirs = (tc.army if tc else 0) or (getattr(live, "army", 0) if live else 0)
        if theirs and snap.n("army") and theirs > 1.5 * snap.n("army"):
            con.append("they are stronger in the field")
        cause = rival or bool(foes_of) or any(p.startswith("they broke") for p in pro)
        if cause:
            level = "welcome" if not con else "accepted" if len(con) <= 2 else "contested"
        elif other_faith or self._in_live("near", tag):
            level = "accepted" if not con else "contested" if len(con) <= 2 else "unpopular"
        else:
            level = "contested" if len(con) <= 1 else "unpopular"
        text = (f"HOW THE REALM WOULD TAKE A WAR ON {name} ({tag}): {prompts.WAR_LEVEL[level]}\n"
                f"  For it: {'; '.join(pro) or 'no grievance the realm knows of'}. "
                f"Against it: {'; '.join(con) or 'nothing in the realm itself'}.")
        return level, text

    def _war_tags(self, text: str, snap: Snapshot, also: str = "") -> list[str]:
        """The courts a talk of war is about: the one named, or the court in the room."""
        tags = [also] if also else []
        low = f" {(text or '').lower()} "
        for kind in ("rival", "near", "war", "ally", "gp", "lord", "subj"):
            for c in (self.live_picture or {}).get(kind, []) or []:
                nm = (getattr(c, "name", "") or "").lower()
                tag = getattr(c, "tag", "")
                if tag and tag != snap.tag and tag not in tags and len(nm) >= 3 and re.search(
                        rf"\b{re.escape(nm)}\b", low):
                    tags.append(tag)
        return tags[:2]

    def _war_views(self, text: str, snap: Snapshot, also: str = "", seen: set | None = None) -> str:
        """The realm's view of the wars this text speaks of - only when it speaks of war."""
        if not self._WAR_TALK.search(text or ""):
            return ""
        out = []
        for tag in self._war_tags(text, snap, also):
            if seen is not None:
                if f"war:{tag}" in seen:
                    continue
                seen.add(f"war:{tag}")
            out.append(self._war_view(snap, tag)[1])
        return ("\n".join(out) + "\n" + prompts.WAR_PEOPLE + "\n\n") if out else ""

    def _gravity_cap(self, snap: Snapshot, kinds: set[str], works: list[Any], power: list[Any],
                     war_tags: list[str]) -> tuple[int, str]:
        """(the most this deed can weigh, its statecraft kind). Ordinary statecraft - a war the realm
        understands, a peace, an alliance, a realm that submits - is never an outrage; a harsh act
        beside it (a massacre, an execution, a faith imposed) is judged as it is."""
        if not kinds & self._STATECRAFT:
            return 10, ""
        if any(isinstance(w, dict) and w.get("kind") in self._HARSH_WORKS for w in works or []) or any(
                isinstance(p, dict) and p.get("kind") in self._HARSH_POWER for p in power or []):
            return 10, ""
        if kinds & self._SUBMISSION:
            return 3, "submission"
        if kinds & {"declare_war", "press_claim"}:
            levels = [self._war_view(snap, t)[0] for t in war_tags if t] or ["contested"]
            level = max(levels, key=lambda lv: self._WAR_CAP[lv])
            return self._WAR_CAP[level], level
        return 3, "accepted"

    def _brings_money(self, words: str) -> bool:
        """Does the ruler raise money (a tax, a toll, a levy) - not lower or abolish one?"""
        return bool(_FLOW_IN.search(words or "")) and not _LOWERING.search(words or "")

    def _weigh(self, action: dict[str, Any]) -> dict[str, Any]:
        """The difficulty makes what hurts the ruler heavier or lighter, and what helps lighter."""
        if action.get("tier") not in self._TIERS or self.cfg.difficulty not in self._WEIGHT:
            return action
        bad, good = self._WEIGHT[self.cfg.difficulty]
        table = good if self._is_good(action) else bad
        return {**action, "tier": table.get(action["tier"], action["tier"])}

    # In a story or a chronicle nobody is "the person spoken to" or "the court
    # abroad", so actions aimed at them would hit whoever was last in the panel.
    _NO_TARGET_IN_STORIES = {"character_modifier", "opinion", "trust_gain", "trust_loss", "favors",
                             "gift_gold", "rival_declare", "rival_drop", "local_control", "local_prosperity",
                             "progress_acclaimed", "progress_resented"}

    def _validated(self, snap: Snapshot, raws: list[Any]) -> list[dict[str, Any]]:
        """A choice's consequences, checked against the catalogue and the budget."""
        out, spent = [], 0
        currencies = _currencies_for(snap)
        for raw in raws or []:
            action, reason = A.validate_action(raw)
            if action is not None:
                action = self._weigh(action)
            if action is None or action["kind"] == "nothing":
                if reason:
                    self.log(f"  dropped: {reason}")
                continue
            if action["kind"] in self._NO_TARGET_IN_STORIES:
                self.log(f"  dropped: {action['kind']} has no target in a story")
                continue
            if action["kind"] in A.CONDITIONAL_CURRENCIES and action["kind"] not in currencies:
                self.log(f"  dropped: {action['kind']} does not exist in this realm")
                continue
            cost = A.action_cost(action) if self._is_good(action) else 0
            if spent + cost > snap.budget:
                self.log(f"  dropped: {action['kind']} costs {cost}")
                continue
            spent += cost
            out.append(action)
            if len(out) >= A.OPTION_SLOTS:
                break
        return self._balanced("choice", {}, out)

    def _recent_doings(self) -> list[str]:
        """What the ruler did lately (decrees, choices, audiences), for stories that follow from it."""
        if self.memory is None:
            return []
        kinds = {"decree", "decision", "story", "scene", "talk", "envoy", "meeting", "visit", "council",
                 "estate", "progress", "measure", "reply", "petition"}
        return [f"{e.date} [{e.kind}]: {e.text}" for e in self.memory.events if e.kind in kinds][-12:]

    def _story_event(self, snap: Snapshot, *, arc_id: int = 0, seed: str = "", focus: str = "",
                     pact_id: int = 0, pact_why: str = "", update: str = "",
                     extra: list[str] | None = None, upkeep: dict[str, Any] | None = None) -> None:
        """Write a situation with three choices and send it to the game (event votc.500).
        upkeep: a lasting gain put to the test (_upkeep_due) - a choice may lose it."""
        mem = self.memory
        if mem is None:
            return
        if upkeep is None and arc_id:
            upkeep = (mem.meta.get("upkeep_arcs") or {}).get(str(arc_id))
        system = prompts.system_prompt(
            language=self.cfg.language, snap=snap,
            codex_digest=self._digest(snap),
            memory_brief=mem.brief(),
            changes_text=prompts.render_changes(diff(self.previous, snap)),
            currencies=_currencies_for(snap),
            world_text=self._world_text(snap),
            realm_profile=self._ensure_realm_profile(snap), difficulty=self.cfg.difficulty,
            summon=False, setting=self._setting(snap), schema=prompts.story_event_schema(),
        )
        task = prompts.text("story") + "\n\n" + prompts.text("story_time")
        arc = mem.arcs.get(arc_id) if arc_id else None
        must_end = False
        # Upheaval needs a cause: the ruler's own doing (a story they are in,
        # the follow-up of their decree) or a realm really in crisis.
        # (a measure put to the test is the ruler's doing, but no cause for a rising)
        provoked = (bool(seed) and upkeep is None) or bool(pact_id) or bool(arc and arc.get("provoked"))
        crisis = self._crisis(snap)
        upheaval_ok = provoked or crisis
        task += ("\n\nREALM IN CRISIS: " + ("yes" if crisis else "no")
                 + ("" if upheaval_ok else " - so no rising, coup or civil war may come of this story now."))
        if arc:
            story = f"{arc.get('title', '')} ({arc.get('kind', '')}): " + " / ".join(arc.get("steps", []))
            elapsed = max(0, gamedate.days_between(arc.get("opened", ""), snap.date))
            span = int(arc.get("span") or 0)
            stages = sum(1 for x in arc.get("steps", []) if x.startswith("The ruler chose") or x.startswith("The ruler answered"))
            now = stages + 1                     # the stage written now
            must_end = bool(span and elapsed >= span * 1.3 and now >= self.STORY_USUAL_STAGES) or (
                now >= self.STORY_MAX_STAGES)
            # Past its usual length a story goes on only for a real reason: it grew out of the ruler's
            # own deeds, or the world has moved since the last stage; past six, both.
            moved = bool(update)
            if (now > self.STORY_USUAL_STAGES and not (provoked or moved)) or (
                    now > self.STORY_LONG_STAGES and not (provoked and moved)):
                if not must_end:
                    self.log(f"  stage {now}: nothing of the ruler's doing or of the world keeps it open - it ends")
                must_end = True
            clock = f"It began {elapsed} days ago" + (
                f"; so far it was expected to take about {span} days in all, {max(0, span - elapsed)} still "
                f"to go - revise that if what happened changes it." if span else ".")
            clock += f" This is stage {now} of the story."
            if must_end:
                clock += (" It has run its course: THIS STAGE ENDS THE STORY - write the ending, and all "
                          "three choices close it ('continues' false).")
            else:
                clock += " " + self._story_length_note(now)
            task += prompts.STORY_CONTINUE.format(story=story, clock=clock)
            asked = [o for o in arc.get("past_options", []) if o]
            if asked:
                task += "\nCHOICES ALREADY PUT TO THE RULER IN THIS STORY: " + "; ".join(asked)
            if update:
                task += ("\n\nIT HAS MOVED SINCE THE LAST STAGE (from what happened meanwhile): " + update
                         + " - the next stage starts from here, not from where it stood.")
        elif seed:
            task += ("\n\nTHIS STORY IS OWED TO THE RULER: it is about " + seed
                     + " - a consequence of what the ruler did earlier (see the memory).")
        if upkeep is not None:
            task += "\n\n" + prompts.UPKEEP_TASK.format(what=upkeep["label"], since=upkeep.get("date") or "long ago")
        pact = mem.pacts.get(pact_id) if pact_id else None
        if pact is not None:
            task += "\n\n" + self._pact_due_text(snap, pact_id, pact, pact_why)
        elif not arc:
            task += "\n\n" + prompts.text("variety") + "\n" + self._variety(snap)
        task += "\n\n" + prompts.text("realism") + "\n\n" + prompts.CHOICE_RULES
        if not arc:
            task += self._worn_stories()
        if focus and not arc and not seed and pact is None:
            task += "\n\n" + prompts.story_focus(focus)
            if focus == "consequence":
                task += "\nRECENT DOINGS OF THE RULER:\n" + "\n".join(self._recent_doings())
        wishes = prompts.player_block("events")
        if wishes:
            task += "\n\n" + wishes
        # What the court remembers of older matters this story touches.
        about = " ".join(x for x in (seed, pact_why, (arc or {}).get("title", ""), " ".join((arc or {}).get("steps", [])[-3:]),
                                     (pact or {}).get("party", ""), (pact or {}).get("ruler_promise", ""),
                                     (pact or {}).get("party_promise", "")) if x)
        task += self._recalled(about)
        if mem.pages:
            task += "\n\nRecent pages (do not repeat their subject): " + "; ".join(p["title"] for p in mem.pages[-10:])
        lands = self._lands_text(snap)
        if lands:
            task += "\n\n" + lands
        if arc:
            # said last, where it is heeded: an ending settles the matter, and the settlement lasts
            task += ("\n\nREMEMBER: every choice that ENDS this story ('continues' false) puts in its actions at "
                     "least one policy_bonus or policy_penalty for 5 to 20 years on what the ending established - "
                     "the lasting mark of how it was settled, gains and costs, as great as the matter was.")
        data = self.client.complete_json([{"role": "system", "content": system}, {"role": "user", "content": task}],
                                         prompts.story_event_schema(), schema_name="votc_story", temperature=0.8)
        if arc and not must_end and self._repeats(arc, data):
            # the same dilemma in other words: once more, with that said; then it ends
            self.log("  the next stage only repeated an old dilemma: asked again")
            again = task + ("\n\nYOUR LAST ATTEMPT ONLY ASKED AGAIN WHAT THE RULER ALREADY ANSWERED. Bring "
                            "something new, or make this stage the ending (all choices 'continues' false).")
            data = self.client.complete_json([{"role": "system", "content": system},
                                              {"role": "user", "content": again}],
                                             prompts.story_event_schema(), schema_name="votc_story", temperature=0.8)
            if self._repeats(arc, data):
                self.log("  still the same dilemma: this stage ends the story")
                must_end = True
        options = [o for o in (data.get("options") or []) if isinstance(o, dict)][:3]
        if len(options) < 3:
            self.log("Story dropped: fewer than three choices.")
            return
        title = _t(data.get("title"), 70) or "Whispers in the Court"
        body = self._narration_only(_cap(data.get("body"), 1800)) or "..."
        self._fill_labels(body, options)
        kept, loc, queues = [], {"votc_arc_title": title, "votc_arc_body": with_gist(data.get("gist"), body)}, []
        # What the other court does about a pact comes due: its own decision, so
        # it rides with every option, whatever the ruler answers.
        pact_move, pact_line, pact_moves_ok = "none", "", False
        if pact is not None:
            pact_move, pact_line, pact_moves_ok = self._pact_move(snap, pact, str(data.get("pact_move") or "none"))
        # The story's length, from its start: re-estimated now from what happened.
        elapsed_now = max(0, gamedate.days_between(arc.get("opened", ""), snap.date)) if arc else 0
        remaining = int(data.get("story_span_days") or 0)
        new_span = (elapsed_now + remaining) if remaining else int((arc or {}).get("span") or 0)
        if arc and new_span and int(arc.get("span") or 0) and abs(new_span - int(arc["span"])) > 7:
            self.log(f"  the story's expected length goes from {arc['span']} to {new_span} days")
        cession_marks: list[str] = []
        integ_marks: list[str] = []
        checked = self._second_look(snap, title, body, [
            (_t(o.get("label"), 80) or self._label_from(o, i), _t(o.get("outcome"), 300),
             self._validated(snap, o.get("actions") or [])) for i, o in enumerate(options, start=1)])
        for n, opt in enumerate(options, start=1):
            acts = checked[n - 1]
            moves = []
            for raw in (opt.get("power_moves") or [])[:2]:
                move, reason = A.validate_power(raw, snap)
                if move and not upheaval_ok and (move["kind"] in A.UPHEAVAL_KINDS
                                                 or (move["kind"] == "kill" and move.get("who_slot") in ("ruler", "heir"))):
                    self.log(f"  move dropped: {move['kind']} without a crisis or a provocation")
                    continue
                if move:
                    moves.append(move)
                elif reason:
                    self.log(f"  power move dropped: {reason}")
            label = _t(opt.get("label"), 80) or self._label_from(opt, n)
            loc[f"votc_arc_opt{n}"] = label
            said = []
            if pact is not None and pact_line:
                said.append(self._pact_said(pact, pact_move))
            if pact is not None and opt.get("ruler_pact_move") in A.RULER_PACT_MOVES and pact_moves_ok:
                said.append(self._pact_said(pact, opt["ruler_pact_move"]))
            cession = None
            for raw in [r for r in (opt.get("territory") or []) if isinstance(r, dict)][:1]:
                cession = self._cession(raw)
            requested = A.requested_text(acts, moves, said)
            if cession is not None:
                requested += f"\n{cession['label']}"
                cession_marks += self._cession_marks([cession], f"o{n}")[1:]
            integ = self._story_integration(opt.get("integrate"))
            if integ is not None:
                requested += f"\n{integ['label']}"
                integ_marks += [f"location:{key} ?= {{ votc_integ_mark = {{ set = o{n} size = {integ['size']} }} }}"
                                for key in integ["places"] if A._token_ok(key)]
            loses = upkeep if upkeep is not None and opt.get("loses_measure") and A.lose_variant(upkeep) else None
            if loses is not None:
                requested += f"\n\u2716 Lost: {upkeep['label']}"
            loc[f"votc_arc_req{n}"] = requested
            queues.append(([A.lose_variant(upkeep)] if loses is not None else [])
                          + ([A.cession_variant(f"o{n}")] if cession is not None else [])
                          + ([A.integrate_variant(f"o{n}")] if integ is not None else [])
                          + self._order_entries(moves) + [A.action_variant(a) for a in acts])
            # a choice that removes the one the story is about settles it
            settles = any(m["kind"] in ("kill", "exile", "depose") for m in moves)
            continues = bool(opt.get("continues")) and not must_end and not settles
            after = max(1, int(opt.get("after_days") or 30))
            if continues and new_span:
                # never schedule the next stage beyond what the story can still last
                after = min(after, max(3, int(new_span * 1.3) - elapsed_now))
            if pact is not None and opt.get("ruler_pact_move") in A.RULER_PACT_MOVES and pact_moves_ok:
                queues[-1].insert(0, A.pact_variant(opt["ruler_pact_move"]))
            if pact_line:
                queues[-1].insert(0, A.pact_variant(pact_move))
            kept.append({"label": label, "outcome": _t(opt.get("outcome"), 300),
                         "wisdom": opt.get("wisdom", 0),
                         "continues": continues, "after_days": after,
                         "pact": pact_id, "keeps_pact": opt.get("keeps_pact") if pact is not None else "n/a",
                         "new_pacts": [p for p in (opt.get("pacts") or []) if isinstance(p, dict)][:1],
                         "later_about": "" if continues else _t(opt.get("later_about"), 300),
                         "later_after_days": int(opt.get("later_after_days") or 0),
                         "acts": acts, "moves": moves, "loses": loses,
                         "cession": f"{cession['label']} - {cession['terms']}" if cession is not None else ""})
        if not arc_id:
            # a story that grew out of the ruler's own deed (a decree's consequence, a pact) may run longer
            arc_id = mem.arc_open(snap.date, title=title, kind=_t(data.get("kind"), 40),
                                  summary=_t(data.get("story_so_far"), 300), span=new_span,
                                  provoked=(bool(seed) and upkeep is None) or bool(pact_id))
            if upkeep is not None:
                arcs = mem.meta.setdefault("upkeep_arcs", {})
                arcs[str(arc_id)] = upkeep
                for old in sorted(arcs, key=int)[:-20]:
                    arcs.pop(old, None)
                self._upkeep_tested(snap, upkeep)
        mem.arc_event(snap.date, arc_id, title=title, body=body, options=kept, span=new_span)
        mem.add_page(snap.date, "story", title, body)
        self._remember(data, snap)
        self.last_scene = {"title": title, "body": body, "kind": "story"}
        self._note_kind("story")
        bind = [pact_line] if pact_line or pact_moves_ok else []
        if pact is not None and not pact_line and pact_moves_ok:
            bind = [self._pact_bind(snap, pact)]
        marks = (["votc_clear_cession_options = yes"] + cession_marks) if cession_marks else []
        if integ_marks:
            marks += ["votc_clear_integ_options = yes"] + integ_marks
        self.mail.send(list(extra or []) + bind + marks + A.option_queue_lines(queues) + ["votc_show_arc = yes"],
                       loc=loc, label="story")
        if pact is not None:
            self._pact_result(snap, pact_id, str(data.get("pact_result") or "none"), pact_move, title)
        why = _t(data.get("reasoning"), 300)
        self.log(f"[story] {title}" + (f" - {why}" if why else ""))

    def _second_look(self, snap: Snapshot, title: str, body: str,
                     choices: list[tuple[str, str, list[dict[str, Any]]]]) -> list[list[dict[str, Any]]]:
        """What each choice does in the game, looked at again against the story and the
        realm before the player sees it: consequences the story does not explain go,
        the others get the size the matter really has. Never adds anything."""
        acts = [list(c[2]) for c in choices]
        if not any(acts):
            return acts
        lines = []
        for i, (label, outcome, xs) in enumerate(choices, start=1):
            lines.append(f"CHOICE {i}: {label}" + (f" - {outcome}" if outcome else ""))
            lines += [f"  {j}. {A.describe_action(a)}" for j, a in enumerate(xs, start=1)] or ["  (nothing)"]
        task = prompts.CONSEQUENCE_CHECK_TASK.format(
            difficulty=prompts.DIFFICULTY.get(self.cfg.difficulty, ""), realm=prompts.render_dossier(snap)[:1800],
            title=title, body=body[:1800], choices="\n".join(lines))
        try:
            data = self.client.complete_json([{"role": "user", "content": task}], prompts.CONSEQUENCE_CHECK_SCHEMA,
                                             schema_name="votc_consequence_check", temperature=0.3, max_tokens=900)
        except Player2Error as exc:
            self.log(f"  second look at the consequences not made ({exc}): kept as written")
            return acts
        before = sum(len(x) for x in acts)
        for verdict in data.get("choices") or []:
            if not isinstance(verdict, dict):
                continue
            i = int(verdict.get("choice") or 0) - 1
            if not 0 <= i < len(acts):
                continue
            keep = {int(k) for k in verdict.get("keep") or [] if str(k).lstrip("-").isdigit()}
            sizes = {int(r.get("number") or 0): r.get("size") for r in verdict.get("resize") or [] if isinstance(r, dict)}
            out = []
            for j, a in enumerate(acts[i], start=1):
                if j not in keep:
                    self.log(f"  second look, choice {i + 1}: dropped {A.describe_action(a)}")
                    continue
                if sizes.get(j) in A.TIERS and a.get("tier") in A.TIERS and sizes[j] != a["tier"]:
                    self.log(f"  second look, choice {i + 1}: {A.describe_action(a)} -> {sizes[j]}")
                    a = dict(a, tier=sizes[j])
                out.append(a)
            acts[i] = out
        self.log(f"  second look at the consequences: {sum(len(x) for x in acts)} of {before} kept")
        return acts

    # A button with no words, or a placeholder where the words should be.
    _BAD_LABEL = re.compile(r"^\s*(?:(?:choice|option|scelta|opzione|choix|opci[oó]n|wahl)\s*\d*|\d+|[.\-…]*)\s*[.:)]?\s*$",
                            re.I)

    def _fill_labels(self, body: str, options: list[dict[str, Any]]) -> None:
        """Every button says what the ruler does: a missing or placeholder label is
        written from the event and what that choice sets in motion."""
        missing = [i for i, o in enumerate(options) if self._BAD_LABEL.match(str(o.get("label") or ""))
                   or len(str(o.get("label") or "").strip()) < 3]
        if not missing:
            return
        self.log(f"  choice {', '.join(str(i + 1) for i in missing)} came without words: writing them")
        outcomes = "\n".join(f"{i + 1}. {_t(o.get('outcome'), 300) or '(no description)'}"
                             for i, o in enumerate(options))
        others = "; ".join(_t(o.get("label"), 60) for i, o in enumerate(options) if i not in missing) or "none"
        try:
            data = self.client.complete_json(
                [{"role": "user", "content": prompts.LABELS_TASK.format(
                    body=body[:1500], outcomes=outcomes, which=", ".join(str(i + 1) for i in missing),
                    others=others, language=prompts.LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language))}],
                prompts.LABELS_SCHEMA, schema_name="votc_labels", temperature=0.5, max_tokens=300)
            labels = [_t(x, 70) for x in (data.get("labels") or [])]
        except Player2Error:
            labels = []
        for j, i in enumerate(missing):
            got = labels[j] if j < len(labels) else ""
            if len(got) >= 3 and not self._BAD_LABEL.match(got):
                options[i]["label"] = got
            else:
                options[i]["label"] = self._label_from(options[i], i + 1)

    @staticmethod
    def _label_from(opt: dict[str, Any], n: int) -> str:
        """Last resort: the button made from what the choice sets in motion."""
        text = _t(opt.get("outcome"), 200)
        first = re.split(r"(?<=[.;:!?])\s|,\s(?:and|but|while|which)\s", text)[0].strip(" .;:")
        if len(first) > 60:
            first = first[:57].rsplit(" ", 1)[0] + "…"
        return first or ("Let it be." if n == 1 else "Not now." if n == 2 else "Another way.")

    def _on_story_choice(self, snap: Snapshot) -> None:
        """The ruler pressed a button of a story event."""
        mem = self.memory
        if mem is None or not mem.awaiting_arc:
            return
        arc_id = mem.awaiting_arc
        arc = mem.arcs.get(arc_id) or {}
        options = arc.get("awaiting") or []
        choice = int(snap.n("agenda"))
        if choice == 9:
            # Answer in person: the side panel opens on the scene.
            last = arc.get("last_event") or {}
            self.last_scene = {"title": last.get("title", ""), "body": last.get("body", ""), "kind": "story"}
            self._reply_arc = arc_id
            self._open_session("reply", snap)
            return
        if not 1 <= choice <= len(options):
            return
        opt = options[choice - 1]
        text = f"The ruler chose: {opt.get('label', '')}. {opt.get('outcome', '')}".strip()
        # A heavy hand of the ruler's own (a purge, an execution, an exile)
        # may later bring a rising: the story counts as provoked from here on.
        provoked = any(m.get("kind") in ("kill", "exile", "depose", "crown", "from_cabinet")
                       for m in opt.get("moves") or [])
        mem.arc_step(snap.date, arc_id, text=text, after_days=int(opt.get("after_days") or 30),
                     close=not opt.get("continues"), provoked=provoked)
        mem.remember_event(snap.date, "story", text)
        mem.judged(snap.date, "story", opt.get("wisdom", 0))
        if opt.get("cession"):
            mem.remember_event(snap.date, "territory", opt["cession"])
            self._book_note(snap.date, f"[agreement] {opt['cession']}", weight=2)
        self._book_note(snap.date, f"{arc.get('title', '')}: {text}",
                        weight=2 if opt.get("moves") or opt.get("acts") else 1)
        self._remember_measures(snap, opt.get("acts") or [], arc.get("title", ""))
        if opt.get("loses"):
            self._lose_lasting(snap, opt["loses"])
        self._pact_choice(snap, opt)
        if opt.get("later_about") and not opt.get("continues"):
            self._plan_followups(snap, [{"kind": "story", "about": opt["later_about"],
                                         "after_days": opt.get("later_after_days") or 60}], opt["later_about"],
                                 aftermath=True)
        if any(m.get("kind") == "civil_war_join" for m in opt.get("moves") or []):
            # The civil war starts with this click; its rebel side becomes a
            # country a moment later. Look for it until the switch happens.
            self._join_rebels = {"from": snap.tag, "tries": 0, "next": time.time() + 3}
            leader = next((m.get("who") for m in opt.get("moves") or [] if m.get("kind") == "civil_war_join"), "")
            mem.remember_event(snap.date, "story",
                               f"The player took the rebels' side in the civil war"
                               + (f", led by {leader}" if leader else "")
                               + f"; {snap.ruler.name if snap.ruler else 'the ruler'} stays at the head of the crown, now the enemy.")
            self.log("You chose the rebels: looking for their country to switch to it.")
        self.log(f"Choice: {opt.get('label', '')} - the story " + ("goes on" if opt.get("continues") else "ends"))

    @staticmethod
    def _narration_only(body: str) -> str:
        """Cut any block of suggested replies or options a model appended to a narration."""
        cut = re.search(r"(?im)^\s*(suggeriment\w*|risposte( possibili| suggerite)?|possibili risposte|opzioni|"
                        r"scelte|suggestions?|possible replies|options)\b[^\n]*:", body)
        return body[:cut.start()].rstrip() if cut else body

    def _story(self, snap: Snapshot, kind: str, show_in_panel: bool = False, *,
               about: str = "", who: str = "", war: bool = False, visit: str = "",
               news: str = "", extra: list[str] | None = None, rooted: str = "") -> None:
        import copy
        work = copy.copy(snap)
        slot = 0
        if kind == "knock":
            slot = next((i for i, p in enumerate(snap.court, start=1) if who and p.name == who), 0)
            slot = slot or random.randint(1, len(snap.court))
            work.target_person = snap.court[slot - 1]
        system = prompts.system_prompt(
            language=self.cfg.language, snap=work,
            codex_digest=self._digest(snap),
            memory_brief=self.memory.brief() if self.memory else "",
            changes_text=prompts.render_changes(diff(self.previous, snap)),
            currencies=_currencies_for(snap),
            world_text=self._world_text(snap),
            realm_profile=self._ensure_realm_profile(snap), difficulty=self.cfg.difficulty,
            personas=self._ensure_personas(work, [work.target_person]) if kind == "knock" else "",
            summon=False, setting=self._setting(snap), schema=prompts.story_schema(),
        )
        task = prompts.text("audience") if kind == "knock" else prompts.text("chronicle")
        scale_name, _how, scale_who = prompts.scale_of(snap.n("locations"), snap.rank, self._great(snap))
        task += (f"\n\nTHE SCALE: {scale_name.lower()}. " + (
            f"Whoever asks to be received is someone who could reach a ruler of this size ({scale_who}), with a "
            f"matter of that size." if kind == "knock" else
            "The page tells something that matters at this size of realm - in a great realm, a province, a "
            "people, a fleet or a faction; in a lordship, a village or a family. In a great realm a single "
            "village appears only as the sign of something that runs through a whole province, and the page "
            "says so."))
        if war:
            # A page of the war: the war report is in the state papers above.
            task = prompts.text("war_chronicle")
        task += self._recalled(" ".join(x for x in (about, who) if x))
        if visit:
            task += "\n\n" + visit
        if news:
            task += "\n\n" + news
        if rooted and not about and not war:
            task += "\n\n" + rooted
        if kind == "chronicle" and not about:
            task += "\n\n" + CONTINUITY
        if about:
            task += ("\n\nTHIS SCENE IS OWED TO THE RULER: it is about " + about
                     + " - a consequence of what the ruler did earlier (see the memory). Show how it "
                       "is being lived now, by named people.")
        wishes = prompts.player_block("events")
        if wishes:
            task += "\n\n" + wishes
        if kind == "chronicle" and self.memory and self.memory.pages:
            written = "; ".join(p["title"] for p in self.memory.pages[-12:])
            task += f"\n\nPages already written (do not repeat their subject): {written}"
        data = self.client.complete_json([{"role": "system", "content": system}, {"role": "user", "content": task}],
                                         prompts.story_schema(), schema_name=f"votc_{kind}")
        title = _t(data.get("title"), 70) or ("From the War" if war else "From the Chronicle" if kind == "chronicle"
                                              else "Someone asks for an audience")
        body = self._narration_only(_cap(data.get("body"), 2000)) or "..."
        self._remember(data, snap)
        # a scene a follow-up brought carries how deep in the chain it is, to the talk it opens
        chain = getattr(self, "_chain", 0) if about else 0
        self._chain = 0
        self.last_scene = {"title": title, "body": body, "kind": kind, "chain": chain}
        self._note_kind(kind if kind in ("knock", "chronicle") else "story")
        if kind == "chronicle" and self.memory:
            self.memory.add_page(snap.date, kind, title, body)
            self._book_note(snap.date, f"In the realm: {title}", weight=0)
        if show_in_panel:
            self._ui("set_busy", "")
            self._ui("add_line", f"{title}  ·  {snap.date}", body)
            self._ui("set_hub", [("chronicle_new", "\u270E  Another page"), ("back", "\u21A9  Back")])
            return
        if kind == "knock":
            script = [f"votc_in_knocker_p{slot} = yes", "votc_show_knock = yes"]
            loc = {"votc_knk_title": title, "votc_knk_body": with_gist(data.get("gist"), body)}
            # Remembered: when the ruler receives them, THEY came to the ruler.
            if self.memory:
                self.memory.add_page(snap.date, "knock", title,
                                     f"[{work.target_person.name}] {body}")
        else:
            session = Session(kind="chronicle", snap=snap, header=Header())
            session.balance = {"force_majeure": self._force_majeure(data)}
            self._collect_actions(session, data)
            if session.acts:
                session.acts = self._second_look(snap, title, body, [
                    ("(no choice: this is what happened, as the page tells it)", "", session.acts)])[0]
            script = (list(extra or []) + A.queue_lines([A.action_variant(a) for a in session.acts], prefix="c")
                      + ["votc_show_chronicle = yes"])
            loc = {"votc_chr_title": title, "votc_chr_body": with_gist(data.get("gist"), body),
                   "votc_chr_requested": A.requested_text(session.acts, [])}
            self._remember_measures(snap, session.acts, title)
        self.mail.send(script, loc=loc, label=kind)
        self.log(f"[{kind}] {title}")

    # ================================================================ helpers
    def _pending_petition(self, name: str, date: str) -> dict[str, str] | None:
        """Did this person ask for an audience lately? Then they came to the ruler."""
        if self.memory is None or not name:
            return None
        received = [e.date for e in self.memory.events if e.kind == "petition_received" and e.text == name]
        for page in reversed(self.memory.pages[-40:]):
            if page.get("kind") != "knock":
                continue
            if received and gamedate.key(received[-1]) >= gamedate.key(page.get("date", "")):
                return None                     # already received for that request
            if gamedate.days_between(page.get("date", ""), date) > 120:
                return None
            body = page.get("body", "")
            if body.startswith(f"[{name}] "):
                return {"title": page.get("title", ""), "body": body[len(name) + 3:]}
        return None

    def _remember_measures(self, snap: Snapshot, acts: list[dict[str, Any]], origin: str = "") -> None:
        """Lasting measures go into memory with their end, so later pages can show their effects.
        A lasting GAIN is kept apart too: years on it is put to the test (_upkeep_due).
        An estate's satisfaction lowered is noted too: a second blow soon after is softer (_balanced)."""
        if self.memory is None:
            return
        lasting = []
        for a in acts:
            years = {"edict_short": 5, "edict_long": 15}.get(a["kind"]) or (
                int(a.get("years") or 0) if a["kind"] == "policy_bonus" else 0)
            if years < 5 or not snap.year or a.get("edict") == "votc_edict_debasement":
                continue
            item = {"kind": "edict" if a["kind"].startswith("edict") else "policy", "date": snap.date,
                    "until": snap.year + years, "desc": A.describe_action(a).lstrip("\u2726 "),
                    "origin": _t(origin, 80)}
            item.update({"edict": a["edict"]} if item["kind"] == "edict" else {"area": a["area"], "tier": a["tier"]})
            item["id"] = f"{item['kind']}:{item.get('edict') or item['area'] + ':' + item['tier']}:{snap.date}"
            lasting.append(item)
        if lasting:
            self.memory.meta["lasting"] = ((self.memory.meta.get("lasting") or []) + lasting)[-30:]
            self.memory._meta_dirty = True
        hits = [{"estate": a.get("estate", ""), "date": snap.date} for a in acts
                if a.get("kind") == "estate" and a.get("sign") == "penalty"]
        if hits:
            self.memory.meta["estate_hits"] = ((self.memory.meta.get("estate_hits") or []) + hits)[-24:]
            self.memory._meta_dirty = True
        for a in acts:
            years = {"edict_short": 5, "edict_long": 15}.get(a["kind"]) or int(a.get("years") or 0)
            if years and snap.year:
                self.memory.remember_event(snap.date, "measure",
                                           f"In force until {snap.year + years}: {A.describe_action(a)}")

    def _remember(self, data: dict[str, Any], snap: Snapshot) -> None:
        if self.memory is None:
            return
        note = _t(data.get("memory_note"), 300)
        if note:
            self.memory.remember_event(snap.date, "scene", note)
        thread = _t(data.get("thread"), 120)
        if thread:
            self.memory.open_thread(key=thread.lower()[:40], title=thread, date=snap.date)
        for person in (data.get("people") or [])[:3]:
            if isinstance(person, dict) and _t(person.get("name"), 60):
                self.memory.remember_person(
                    _t(person.get("name"), 60), date=snap.date, role=_t(person.get("role"), 60),
                    real=not bool(person.get("invented")), standing=_t(person.get("standing"), 120))

    def _ui(self, method: str, *args) -> None:
        if self.drawer is not None:
            self.drawer.post(method, *args)

    def _submit(self, job: Callable[[], None]) -> None:
        self._work.put(job)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._work.get(timeout=0.3)
            except queue.Empty:
                continue
            self.busy = True
            try:
                job()
            except Player2Error as exc:
                self.log(f"Problem with {self.client.NAME}: {exc}")
                self._ui("set_busy", "")
                self._ui("add_note", f"{self.client.NAME} does not answer: {exc}", "bad")
            except Exception:
                self.log("Errore:\n" + traceback.format_exc())
                self._ui("set_busy", "")
            finally:
                self.busy = False


# Words with which a ruler decides - in English and in the languages players
# most often write in. Only a cue to ask the referee, who judges for itself.
# Machines no age of this game (1337 to the 19th century) has known, in the languages
# players most often write in: the referee may never find them "possible".
_NEVER_INVENTED = re.compile(
    r"\b(jets?|aeroplan\w*|airplan\w*|aircraft|helicopt\w*|machine[- ]?guns?|nuclear|atomic|missiles?|lasers?|"
    r"computers?|internet|telephon\w*|televis\w*|radio|aere[oi]|elicotter\w*|mitragliatric\w*|nuclear[ei]|"
    r"atomic[ahe]|missil[ei]|telefon\w*|avi[oó]n\w*|helic[oó]pter\w*|ametralladora\w*|nucl[eé]ai?re?s?|"
    r"misil\w*|avions?|h[ée]licopt[èe]re\w*|mitrailleuse\w*|flugzeug\w*|hubschrauber\w*|maschinengewehr\w*)\b",
    re.I)
# Words of the supernatural and words aimed at the machine: the referee is pointed at them.
_SUPERNATURAL = re.compile(
    r"\b(demon\w*|devils?|satan\w*|spells?|sorcer\w*|witchcraft|magic\w*|curses?|dragons?|necroman\w*|"
    r"demon[ie]|diavol\w*|incantesim\w*|stregoner\w*|magi[ac]\w*|maledizion\w*|drag(?:o|hi)|"
    r"demonio\w*|hechiz\w*|brujer\w*|d[ée]mons?|sortil[èe]ge\w*|sorcellerie|d[äa]mon\w*|zauber\w*|hexerei)\b",
    re.I)
_TO_THE_MACHINE = re.compile(
    r"(ignore (all |your |the |previous )*(instructions|rules)|as an ai|system prompt|game master|developer mode|"
    r"the rules (say|allow)|you (must|have to) (accept|agree|grant)|ignora (le |tutte le )*(istruzioni|regole)|"
    r"sei un'?\s?ia|le regole (dicono|permettono)|devi (accettare|concedere))", re.I)


# The quiet line under a conversation, and the small note when an order is heard: what is
# said takes effect only when the ruler dismisses them, in the event that follows.
CLOSE_HINT = "\u25c7 What is decided here takes effect when you press Dismiss - in the event that follows."
NOTED_NOTE = "\u25c7 Noted. It will be weighed when you dismiss them, and comes with the event that follows."

_FAKE_NOTE = re.compile(r"\[\s*(?:system|sys|admin|developer|dev|gm|game ?master|narrator|referee|note|ooc|"
                        r"sistema|narratore|arbitro|nota)\b[^\]]*\]|</?\s*(?:system|assistant|user)\s*>|```|#{3,}",
                        re.I)


def _clean_words(text: str) -> str:
    """The ruler's words as one line of their own speech: no line breaks to fake another
    speaker's line, no bracketed "system" notes, no straight quotes to close the quotation."""
    text = _FAKE_NOTE.sub(" ", text or "")
    text = re.sub(r"\s*[\r\n]+\s*", " / ", text).replace('"', "\u201d")
    return re.sub(r"[ \t]{2,}", " ", text).strip(" /")


# Said to what the court brings unasked (chronicles, audiences): new, yet in line with the past.
CONTINUITY = ("IN LINE WITH WHAT HAS HAPPENED: whatever the matter, it happens in the realm this campaign has "
              "made (see the memory) - the mood after its wars and decrees, who rose and fell, old debts and "
              "grudges. Let that colour it and link to it where it fits, often without naming any past event and "
              "never retelling it; the matter itself is new.")


def _key_days(key: int) -> int:
    """A gamedate key (yyyymmdd) as a count of days, months taken as 30 - for distances only."""
    return key // 10000 * 360 + (key // 100 % 100) * 30 + key % 100


def _reality_hints(words: str) -> str:
    """A pointer for the referee when the ruler's words hold what REALITY FIRST is about."""
    notes = []
    m = _NEVER_INVENTED.search(words or "")
    if m:
        notes.append(f'the ruler speaks of "{m.group(0)}", which no age of this world has known')
    m = _SUPERNATURAL.search(words or "")
    if m:
        notes.append(f'the ruler speaks of "{m.group(0)}": if it is meant as a real power, it exists only as belief')
    if _TO_THE_MACHINE.search(words or ""):
        notes.append("the ruler's words speak to the game or to you, not to anyone in the world: they are nothing")
    return (("\n\nREALITY CHECK: " + "; ".join(notes) + " (see REALITY FIRST).") if notes else "") + _money_hints(words)


# Money that comes or goes every month - said in the ruler's words - and money that moves once.
_FLOW_IN = re.compile(
    r"\b(tax(?:es)?|tolls?|dut(?:y|ies)|levy|levies|tithes?|tariffs?|excise|rents?|fees?|exemptions?|"
    r"tass[ae]|impost[ae]|dazi?o?|gabell[ae]|pedaggi?o?|decim[ae]|tribut[io]|esenzion[ei]|affitt[io]|canon[ei]|"
    r"dogan[ae]|accis[ae]|impuestos?|aranceles?|peajes?|diezmos?|imp[ôo]ts?|taxes?|p[ée]ages?|d[îi]mes?|octrois?|"
    r"steuer(?:n)?|z[öo]lle?|abgaben?|zehnte?n?)\b", re.I)
_FLOW_OUT = re.compile(
    r"\b(wages?|salar(?:y|ies)|stipends?|subsid(?:y|ies)|upkeep|garrisons?|pensions?|"
    r"stipendi?o?|salari?o?|soldo|paga\s+(?:ai|dei|alle|delle)|sussidi?o?|pensioni?|mantenimento|guarnigion[ei]|"
    r"salarios?|sueldos?|subsidios?|gages|soldes?|l[öo]hne?)\b", re.I)


_LOWERING = re.compile(
    r"\b(lower\w*|reduc\w*|abolish\w*|cut\w*|remov\w*|exempt\w*|waiv\w*|abbass\w*|ridu\w*|aboli\w*|togli\w*|"
    r"esent\w*|elimin\w*|condon\w*|sospend\w*|bajar\w*|rebaj\w*|suprim\w*|baisse\w*|supprim\w*|senk\w*|"
    r"abschaff\w*|erlass\w*)\b", re.I)


def _money_hints(words: str) -> str:
    """When the ruler speaks of money that comes or goes every month, say so last - where it is heeded:
    the models kept turning a new tax into a few coins at once."""
    notes = []
    m = _FLOW_IN.search(words or "")
    if m:
        notes.append(f'the ruler speaks of "{m.group(0)}": money that comes EVERY MONTH, not once. A new or raised '
                     f'tax, toll, duty or rent, or an exemption ended, is a standing measure (gain "revenue", with '
                     f'its burden on trade, prosperity or those who pay) - or, if the ruler set an end, a '
                     f'policy_bonus on taxation, trade income or that estate\'s taxes for those years; a tax lowered '
                     f'or an exemption granted is the same the other way. A one-time gold_gain only for what is '
                     f'collected ONCE (stalls sold, a fine, a special levy raised a single time)')
    m = _FLOW_OUT.search(words or "")
    if m:
        notes.append(f'the ruler speaks of "{m.group(0)}": money that goes out EVERY MONTH - a standing measure '
                     f'that costs, or a policy for years; a gold_loss only for what is paid once')
    return ("\n\nMONEY - ITS SHAPE: " + "; ".join(notes) + " (see THE SHAPE OF A CONSEQUENCE).") if notes else ""


_DECISIVE = re.compile(
    r"\b(yes|fine|very well|agreed|so be it|do it|do so|make it so|see to it|i order|i command|i decree|i grant|"
    r"i forbid|i refuse|i promise|i swear|i accept|i reject|pay|send|give|grant|arrest|hang|execute|exile|"
    r"dismiss|raise|lower|build|repair|fund|forgive|pardon|declare|approve|deny|refuse|"
    r"s[iì]|va bene|d'accordo|cos[iì] sia|fallo|fatelo|ordino|comando|decreto|concedo|proibisco|"
    r"rifiuto|prometto|giuro|accetto|paga|pagate|manda|mandate|arrestate|impiccate|esiliate|alzate|"
    r"abbassate|costruite|riparate|perdono|dichiaro|approvo|nego|dimezzate|raddoppiate|togliete|"
    r"(?:that|this|it) is an order|(?:è|e') un ordine)\b", re.I)


# Models sometimes slip a token of another script into Italian prose
# ("daたちの"). Nothing in a European court's speech is written in these.
_FOREIGN_SCRIPT = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯฀-๿]+")


_OPENING_ADDRESS = re.compile(
    r"^\s*(?:sire|maest[àa]|vostra maest[àa]|vostra altezza|altezza|mio signore|mio re|signore|"
    r"senher rei|senhor rei|mossenyor|monsignore|my lord|your majesty)\s*[,!:]\s*", re.I)

_ADDRESS = re.compile(
    r",\s*(?:sire|my lord|my liege|your majesty|majesty|your grace|your highness|my king|my queen|"
    r"maest[àa]|mio signore|signore|monsignore|mossenyor|senher)\b(?=[\s.,;:!?]|$)", re.I)

_SLIP = re.compile(
    r"\b(?:no[,—-]+\s*(?:forgive me|wait|sorry|I mean)|forgive me,? that was|where was I|what was I saying|"
    r"I had (?:it|them|the \w+) here|I've lost my place|scusate,? (?:era|no)|dove ero)\b", re.I)

_GESTURE_STOP = frozenset("""with from then into onto before after while their his her them they this that
    looks glances turns speaks says answers again still once more hand hands head face eyes""".split())


_PHRASE_STOP = frozenset("""the a an and or but of to in on at for with by from as is are was were be been it its
    this that these those i you he she we they me him her us them my your his our their not no so if then than
    will would shall should can could may might must have has had do does did""".split())


def _shared_phrases(text: str, earlier: list[str]) -> list[str]:
    """Pet phrases this speaker already used: short formulas set off by punctuation
    (', God willing.', 'By my faith,', ', as you know,') - not the subject they talk about."""
    def formulas(t: str) -> set[str]:
        out = set()
        for frag in re.split(r"[,.;:!?\u2014()\"]+", t.lower()):
            words = re.findall(r"[a-zà-ÿ']+", frag)
            if 2 <= len(words) <= 4 and any(w not in _PHRASE_STOP for w in words):
                out.add(" ".join(words))
        return out
    seen = set().union(*(formulas(e) for e in earlier)) if earlier else set()
    return sorted(formulas(text) & seen)[:2]


def _gesture_words(gesture: str) -> set[str]:
    """The motions and objects of a gesture ('papers', 'brow', 'rubs'), to keep them from coming back."""
    words = re.findall(r"[a-zà-ÿ]{4,}", gesture.lower())
    return {w.rstrip("s") for w in words if w not in _GESTURE_STOP}


def _drop_address(text: str) -> str:
    text = _ADDRESS.sub("", text)
    m = _OPENING_ADDRESS.match(text)
    if m:
        rest = text[m.end():]
        text = rest[:1].upper() + rest[1:]
    return text


_STOCK_GESTURE = re.compile(
    r"senza allegria|non raggiung\w* gli occhi|nocch\w*|mascella|brivido|trattien\w* il respiro"
    r"|without (?:mirth|humou?r)|reach(?:es)? (?:his|her) eyes|knuckles|jaw", re.I)


# What the ruler may say that should hit the listener before any business.
_PERSONAL = re.compile(
    r"\b(die|dying|dead|death|my time|meet (?:the|my) lord|when i(?:'m| am) gone|my grave|ill\b|illness|sick|"
    r"fever|love|in love|marry|afraid|fear|betray|traitor|forgive|farewell|goodbye|last wish|my son|my daughter|"
    r"my wife|my husband|morir|morto|morte|malat|amore|tradit|perdon|addio|mio figlio|mia figlia|mia moglie)\b",
    re.I)

# The helpful assistant's voice, which nobody at a court ever had.
_ASSISTANT = [
    (re.compile(r"^(?:[^.!?]{0,30}[,.]\s*)?i (?:hear|understand|see|honou?r|respect|appreciate|share) "
                r"(?:you|your|the|this|that|what)", re.I), "acknowledging first"),
    (re.compile(r"\b(?:a (?:wise|noble|worthy|fair|good) (?:thought|point|purpose|aim|idea|question))\b", re.I),
     "praising the ruler's idea"),
    (re.compile(r"\byou are (?:right|wise) to\b|\bthat is (?:wise|well said)\b", re.I), "validating"),
    (re.compile(r"\bi (?:will|shall) (?:do as you ask|honou?r your (?:wish|will|request))\b", re.I),
     "tidy obedience"),
    (re.compile(r"\b(?:ti (?:capisco|ascolto)|comprendo|onoro)\b", re.I), "acknowledging first"),
]
# A writer's balance, in any speech: "I will X, but I will not Y"; "too great for A; too great for B".
_BALANCED = [
    re.compile(r"\bI (?:will|shall|would) [^.;!?]{3,80}, but I (?:will|shall|would) not\b", re.I),
    re.compile(r"\btoo \w+ for [^;.!?]{2,60}[;,] [^.!?]{0,50}\btoo \w+ for\b", re.I),
    re.compile(r"\b(?:is|are) not [^,.;]{2,40}, but\b", re.I),
]


# Speech that reads as written prose - the marks novelists' editors strike out,
# in the languages players write in. A turn that shows them is said again.
_OPENS_WITH_SUMUP = re.compile(
    r"^\s*(?:"
    # Italian
    r"cos[iì] (?:l'\w+|il \w+|la \w+|lo \w+|i \w+|le \w+) (?:[eè]|sono|resta|restano) \w+|"
    r"cos[iì] (?:[eè]|siamo|resta) (?:deciso|chiaro|inteso|stabilito|d'accordo)|questo ci (?:avvicina|porta)|"
    r"ebbene|orbene|la vostra (?:proposta|offerta|parola|richiesta|lettera) (?:giunge|ci trova|mi trova)|"
    # English
    r"so (?:it is|that is|the matter is|the thing is) (?:settled|agreed|clear|decided)|this brings us|"
    r"then it is settled|your (?:proposal|offer|word) (?:comes|reaches|finds) (?:us|me)|"
    # Spanish, French, German
    r"as[ií] (?:queda|est[aá]) (?:claro|decidido|acordado)|votre (?:proposition|offre) (?:arrive|nous trouve)|"
    r"ainsi (?:c'est|il est) (?:entendu|d[ée]cid[ée])|so ist es (?:beschlossen|klar|abgemacht))", re.I)
_BOOKISH = re.compile(r"\b(?:ebbene|orbene|invero|altres[iì]|codest[oaie]|siffatt[oaie]|giung[ea]|"
                      r"verily|thus it is|henceforth|forsooth)\b", re.I)
_LEGAL = re.compile(r"\b(?:purch[eé]|affinch[eé]|qualora|provided that|so long as|in such a way that|"
                    r"siempre que|pourvu que)\b", re.I)
_TRIAD = re.compile(r"\b[^\W\d_]{3,}, [^\W\d_]{3,},? (?:e|ed|and|y|et) [^\W\d_]{3,}\b", re.I)
_BALANCED_IT = re.compile(r"\bnon (?:[eè]|sono|era) [^,.;!?]{2,40}[,.] (?:ma|[eè]) ", re.I)


_ENGLISH_WORDS = frozenset(("the", "and", "of", "to", "is", "you", "your", "we", "it", "that", "in", "for",
                            "with", "my", "our", "this", "not", "be", "are", "have", "will", "what", "they",
                            "his", "her", "was", "but", "if", "lord", "sire"))


def _wrong_language(lines: list, language: str) -> bool:
    """Did lines meant to be in another language come out in English?"""
    if not language or language.lower().startswith("en"):
        return False
    words = [w for l in lines if isinstance(l, dict)
             for w in re.findall(r"[a-zA-Z']+", f"{l.get('text', '')} {l.get('gesture', '')}".lower())]
    if len(words) < 12:
        return False
    return sum(w in _ENGLISH_WORDS for w in words) / len(words) > 0.18


def _written_voice(lines: list) -> str:
    """Why a turn reads as written prose rather than speech, or ''."""
    texts = [str(l.get("text", "")) for l in lines if isinstance(l, dict) and str(l.get("text", "")).strip()]
    if not texts:
        return ""
    joined = " ".join(texts)
    if _OPENS_WITH_SUMUP.search(texts[0]):
        return "it opens by summing up where things stand"
    if _BOOKISH.search(joined):
        return "bookish, archaic words"
    if _BALANCED_IT.search(joined):
        return "a balanced 'not X, but Y'"
    if joined.count(";") >= 2:
        return "semicolons - a written sentence"
    weak = []
    words = len(joined.split())
    sentences = max(1, len(re.findall(r"[.!?]+(?:\s|$)", joined)))
    if words >= 40 and words / sentences > 24:
        weak.append("long written sentences")
    if len(re.findall(r"\s[\u2014\u2013-]\s", joined)) >= 3:
        weak.append("dashes strung through the speech")
    if len(_LEGAL.findall(joined)) >= 2:
        weak.append("the clauses of a contract")
    if len(_TRIAD.findall(joined)) >= 2:
        weak.append("lists of three")
    if joined.count(";") == 1:
        weak.append("a semicolon")
    return ", ".join(weak) if len(weak) >= 2 else ""


def _assistant_voice(lines: list) -> str:
    """Why a turn sounds like an assistant, or '' - on the first speech, where it shows most."""
    texts = [str(l.get("text", "")) for l in lines if isinstance(l, dict)]
    if not texts:
        return ""
    first = texts[0].strip()
    for pattern, why in _ASSISTANT:
        if pattern.search(first):
            return why
    for text in texts:
        if any(p.search(text) for p in _BALANCED):
            return "a writer's balanced antithesis"
    # the 'yes - though' hedge: agreement, then a careful caveat, in one speech
    if re.match(r"^(?:yes|very well|then i|i will|i shall|as you (?:wish|say))\b", first, re.I) and \
            re.search(r",\s*(?:though|but first|but i must|yet i must)\b", first, re.I):
        return "agreeing with a careful caveat"
    return ""


# Older personas asked for "one verbal habit" and for anecdotes: models then
# repeated the habit in every answer and dropped the anecdotes into business.
_TAG_SENTENCE = re.compile(
    r"[^.!?\n]*(?:verbal habit|catch-?phrase|pet phrase|signature (?:word|phrase)|favou?rite (?:word|phrase|saying)"
    r"|habit is [\"“'‘])[^.!?\n]*[.!?]?[\"”’']?\s*", re.I)


def _persona_for_actor(text: str) -> str:
    out = []
    for line in (text or "").split("\n"):
        if line.startswith(("DETAILS:", "SAYS LIKE:")):
            continue
        if line.startswith("PRIVATE:"):
            line = "BACKGROUND (never told; it only shapes how they see things): " + line[8:].strip()
        line = re.sub(r"\s{2,}", " ", _TAG_SENTENCE.sub(" ", line)).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def _cast_name(speaker: str, cast: list[str]) -> str:
    """One name per person all scene long: models append a role ("..., re di Cagliari")
    or shorten the name ("Pere IV"). The name of the person in the scene is kept."""
    base = speaker.split(",")[0].strip()
    low = base.lower()
    for name in cast:
        n = name.lower()
        if low and (low == n or (len(low) >= 4 and (n.startswith(low) or low.startswith(n)
                                                     or low in n or n in low))):
            return name
    # "Friedrich" or "the grand master Friedrich" for "Grandmaster Friedrich von Hohenzollern":
    # a personal name the label shares with exactly one person of the scene.
    words = {w for w in re.findall(r"[^\W\d_]{4,}", low)}
    hits = [name for name in cast if words & {w for w in re.findall(r"[^\W\d_]{4,}", name.lower())
                                                  if w not in _TITLE_WORDS}]
    if len(hits) == 1:
        return hits[0]
    # Only a title ("il grand maestro dell'Ordine"): the principal of the scene.
    if cast and words and words <= _TITLE_WORDS:
        return cast[0]
    return base or speaker


_TITLE_WORDS = frozenset("""grandmaster grand master king queen duke duchess count countess prince princess
    lord lady emperor empress sultan pope bishop archbishop cardinal abbot baron marquis doge ruler order
    knights hospitaller templar re regina duca duchessa conte contessa principe principessa signore signora
    imperatore sultano papa vescovo arcivescovo cardinale abate barone marchese maestro granmaestro
    grandmaestro ordine cavalieri ospedale della dell delle degli""".split())


def _bare_gesture(gesture: str, speaker: str) -> str:
    """'Bartolommeo Chiavelli, the noble lord of..., leans forward' -> 'Leans forward'.

    The panel already shows who speaks, with their role; models keep putting
    the name and a description in front of the gesture anyway. When the
    description cannot be cut cleanly the gesture is dropped: it was optional."""
    if not gesture or not speaker:
        return gesture[:160]
    # every leading part of the name, longest first: "Pere IV il Cerimonioso", "Pere IV", "Pere"
    words = speaker.split()
    names = [" ".join(words[:i]) for i in range(len(words), 0, -1)]
    for name in names:
        if not gesture.lower().startswith(name.lower()):
            continue
        rest = gesture[len(name):]
        if rest.startswith(","):
            m = re.match(r",([^,]{0,160}),\s+(?=[a-z]+s\b)", rest)
            if not m:
                return ""
            rest = rest[m.end():]
        rest = rest.strip()
        return (rest[:1].upper() + rest[1:])[:160] if rest else ""
    return gesture[:160]


def _clean(text: str) -> str:
    return re.sub(r"  +", " ", _FOREIGN_SCRIPT.sub("", text))


_MOODS = (
    "bored, would rather be elsewhere",
    "distracted by a private worry that has nothing to do with this",
    "tired and short with everyone",
    "knows something about this and is keeping it back",
    "irritable today, for reasons nobody asks about",
    "in a good mood, jokes, not entirely serious",
    "eager to be noticed by the ruler, says a little too much",
    "proud of something they did lately, and wants it noticed",
    "worried about their own place at court",
    "has a favour to ask, and is waiting for the moment",
    "annoyed with someone else in the room",
)


def _moods_of_the_day(cast: list[str]) -> dict[str, str]:
    """Not everyone in the room is equally present. Decided by dice, kept for the scene."""
    if not cast:
        return {}
    if len(cast) == 1:
        return {cast[0]: random.choice(_MOODS)} if random.random() < 0.4 else {}
    picked = random.sample(cast, k=min(len(cast), random.choice((1, 1, 2))))
    return dict(zip(picked, random.sample(_MOODS, len(picked))))


# What the ruler said decides how long the answer is; dice only vary the
# ordinary case. (Dice alone cut explanations the ruler had asked for.)
_ASKS_FOR_DETAIL = re.compile(
    r"spieg|raccont|dettagl|descriv|illustr|approfond|parlami|dimmi (tutto|di più|di piu|come|perch|cosa|chi|quali)"
    r"|perch[eé]|come mai|in che modo|che cosa è successo|cosa è successo|cos'è successo|com'è andata"
    r"|cosa sai|che sai|che ne pensi|cosa ne pensi|cosa pensi|resocont|rapporto|situazione|consiglio|consigli"
    r"|quali sono|chi sono|chi è|cosa proponi|che proponi|piano|strategia|esponi|riferisci|aggiornami"
    r"|explain|tell me|describe|\bwhy\b|\bhow\b|what happened|detail|report|what do you think|advise"
    r"|explica|cuént|cuent[ae]me|por qu[ée]|c[óo]mo|detall|inform[ae]"                      # Spanish, Portuguese
    r"|expliqu|racont|pourquoi|comment|d[ée]tail|rapport"                                  # French
    r"|erkl[äa]r|erz[äa]hl|warum|wieso|weshalb|\bwie\b|bericht|einzelheit"                  # German
    r"|conta-me|porqu[eê]|detalh|relat[óo]rio", re.I)


def _length_for(said: str) -> str:
    """full | short | normal, from the ruler's words."""
    said = said.strip()
    if _ASKS_FOR_DETAIL.search(said) or (len(said) > 220 and "?" in said):
        return "full"
    if len(said) < 30 and "?" not in said:
        return "short"
    return "normal"


def _beats(session: Any, *, opening: bool = False, said: str = "") -> str:
    """Stage directions for this moment, so that the scene is not symmetric by default."""
    need = "normal" if opening else _length_for(said)
    if need == "full":
        length = ("a REAL, FULL answer, about 500-1000 characters: the ruler asked to explain or to "
                  "hear an account (see HOW LONG). Still spoken, in their own voice; their mood colours "
                  "how they tell it, it does not make them skip it")
    elif need == "short":
        length = ("short, in kind with what the ruler said: a few words to two sentences, unless that "
                  "person truly has something of their own to add")
    elif opening:
        length = random.choice(["two or three sentences", "three to five sentences"])
    else:
        length = random.choices(["one or two sentences", "two to four sentences",
                                 "a short paragraph - they have something at stake"],
                                weights=[35, 45, 20])[0]
    lines = [f"BEATS FOR THIS MOMENT (follow them): the main answer is {length}.",
             "Play the plan in \"inner\": each speaker's tactic shows in what they DO with their words, and a "
             "specific particular beats a general matter (see THE CRAFT)."]
    if opening and session.kind in prompts._PURPOSE_UNKNOWN_MODES:
        lines.append("They do not know yet why the ruler has come: they may wonder or ask, never say it as known.")
    if session.turns == 0 and len(session.cast) > 1:
        lines.append("ENTER LATE: they were in the middle of something between themselves when the ruler "
                     "spoke - it colours their first words; no ceremony, no agenda.")
    if said and _PERSONAL.search(said):
        lines.append("The ruler has just said something that touches them personally (a death, an illness, love, "
                     "fear, a betrayal, a farewell): they react to THAT first, as the person they are and as their "
                     "bond with the ruler allows - the matter can wait (see THE BIGGEST THING FIRST).")
    if need == "full" and len(session.cast) > 1:
        lines.append("The one asked gives the full answer; the others, if they speak, say a line.")
    if len(session.cast) > 1:
        most = random.choices([1, 2, 3], weights=[55, 33, 12])[0] if not opening else random.choice([2, 3])
        lines.append(f"At most {most} of those present speak; the others stay silent or are ignored.")
    if said and session.turns > 0 and len(said) >= 25:
        # said last, where it is heeded: the model's habit is to object to everything
        lines.append("WEIGH WHAT THE RULER SAID ON ITS MERITS, as these people: if it is sound and harms "
                     "nothing they care about, they agree (perhaps with a detail or a condition) and move on; "
                     "they object only for a real reason of their own or of the realm, and never repeat an "
                     "objection the ruler has already answered.")
    if session.moods and session.turns == 0:
        # said once: from here on "inner" carries how they feel, and it moves
        lines.append("TODAY: " + "; ".join(f"{n} is {m}" for n, m in session.moods.items())
                     + ". Show it once; after that it only colours them.")
    lines.append("SOUND: said, not written - short sentences, plain words, start with the thing itself, no "
                 "crafted or quotable line, no sum-up (see HOW IT SOUNDS).")
    if session.cast:
        lines.append("SPEAKER NAMES - write each exactly so, every time, never a title in its place: "
                     + "; ".join(f'"{n}"' for n in session.cast) + ".")
    lines += _scene_record(session)
    return "\n".join(lines)


def _short_role(role: str) -> str:
    """'re di Cagliari e signore della corte che riceve Petru II' -> 're di Cagliari'."""
    role = re.split(r",|;|\(| e | and | who | che | qui | que | der | which ", role.strip(), maxsplit=1)[0].strip()
    return role if len(role) <= 40 else role[:40].rsplit(" ", 1)[0]


_QUESTION = re.compile(r"[^.!?]*\?")


def _scene_record(session: Any) -> list[str]:
    """What this scene has already used up: said to the model, so it reaches for something new."""
    out = []
    # A question the ruler has since answered is not asked again: they move on.
    asked = []
    for who, said in session.spoken.items():
        qs = [q.strip() for q in _QUESTION.findall(said[-1] if said else "") if len(q.strip()) > 12]
        if qs:
            asked.append(f'{who.split()[0]} asked "{qs[-1][:140]}"')
    if asked and session.turns > 0:
        out.append("Already asked, and the ruler has answered since: " + "; ".join(asked[:3]) + ". Nobody asks "
                   "it again in other words: they take the answer and move - accept with a condition, raise the "
                   "price, counter-offer, refuse, threaten, or ask for time.")
    if session.slips:
        out.append(f"{', '.join(session.slips)} already lost the thread or corrected themselves: nobody does that "
                   f"again in this scene.")
    if session.used_props:
        out.append("Gestures and objects already used - never again in this scene: "
                   + ", ".join(sorted(session.used_props)[:12]) + ". Most speeches need no gesture at all.")
    if session.turns - session.addressed_turn <= 2:
        out.append("A title was used a moment ago: no \"Sire\", \"my lord\" or \"Majesty\" this time.")
    if session.worn:
        out.append("Already said more than once - never again in this scene: " + ", ".join(session.worn) + ".")
    if session.openings:
        out.append("Do not begin a speech the way recent ones began (" + "; ".join(
            f'"{o}..."' for o in session.openings[-4:]) + ").")
    return out

_WORD = re.compile(r"[^\W\d_]{5,}", re.U)


_COMMON = {"ruler", "court", "courts", "their", "where", "which", "would", "whether", "about", "answer",
           "result", "motion", "there", "these", "those", "after", "before", "under", "other", "still", "request"}


def _similar(a: str, b: str) -> bool:
    """Two descriptions of the same matter: a good part of the telling words of the shorter one recur."""
    wa = set(_WORD.findall((a or "").lower())) - _COMMON
    wb = set(_WORD.findall((b or "").lower())) - _COMMON
    if len(wa) < 2 or len(wb) < 2:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.3


def _cap(value: Any, limit: int) -> str:
    """Trim prose at a sentence end, never mid-word, when a model overruns."""
    text = _clean(value.strip()) if isinstance(value, str) else ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(".\n"))
    return cut[: end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0] + "…"


def _t(value: Any, limit: int) -> str:
    return _clean(value.strip())[:limit] if isinstance(value, str) else ""


def _gov_group(snap: Snapshot) -> str:
    gov = (snap.government or "").lower()
    for group, hints in (("monarchy", ("monarch", "monarchi", "regno", "kingdom")),
                         ("republic", ("republic", "repubblic")),
                         ("theocracy", ("theocra", "teocra")),
                         ("tribe", ("trib",))):
        if any(h in gov for h in hints):
            return group
    return ""


def _currencies_for(snap: Snapshot) -> set[str]:
    hay = f"{snap.religion} {snap.government}".lower()
    return {c for c, hints in CURRENCY_HINTS.items() if any(h in hay for h in hints)}
