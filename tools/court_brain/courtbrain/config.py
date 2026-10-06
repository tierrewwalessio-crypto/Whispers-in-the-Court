"""Configuration and path discovery for Court Brain.

Everything that depends on where things live on this machine is resolved
here, once, so the rest of the package never guesses at a path.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# EU5 writes its user data under Documents/Paradox Interactive/Europa
# Universalis V. On a machine where OneDrive has taken over the Documents
# folder the real directory is the OneDrive one and the local one is an
# empty decoy, so both are probed and the one that actually holds logs wins.
_USER_DIR_CANDIDATES = (
    "OneDrive/Documents/Paradox Interactive/Europa Universalis V",
    "Documents/Paradox Interactive/Europa Universalis V",
    "OneDrive/Documenti/Paradox Interactive/Europa Universalis V",
    "Documenti/Paradox Interactive/Europa Universalis V",
)

DEFAULT_CONFIG_NAME = "config.json"


def _home() -> Path:
    return Path(os.path.expanduser("~"))


_EU5 = Path("Paradox Interactive") / "Europa Universalis V"


def _known_documents() -> list[Path]:
    """Where Windows itself says the Documents folder is - moved to another drive,
    redirected to OneDrive under any name, or in any language. The game asks
    Windows the same question, so this is where it writes its folder."""
    out: list[Path] = []
    if os.name != "nt":
        return out
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD),
                        ("d4", ctypes.c_ubyte * 8)]

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        buf = ctypes.c_wchar_p()
        shell = ctypes.windll.shell32
        if shell.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0 and buf.value:
            out.append(Path(buf.value))
        ctypes.windll.ole32.CoTaskMemFree(buf)
    except (OSError, AttributeError, ValueError):
        pass
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value, _kind = winreg.QueryValueEx(k, "Personal")
        out.append(Path(os.path.expandvars(value)))
    except (OSError, ImportError):
        pass
    return out


def _user_dir_candidates() -> list[Path]:
    home = _home()
    out = [docs / _EU5 for docs in _known_documents()]
    out += [home / rel for rel in _USER_DIR_CANDIDATES]
    # OneDrive under another name ("OneDrive - Personal", a company's) and
    # Documents under another name, for any setup Windows did not report.
    for drive in [home, *home.glob("OneDrive*")]:
        for name in ("Documents", "Documenti", "Dokumente", "Documentos", "Documenten", "Dokumenty", "Mes documents"):
            out.append(drive / name / _EU5)
    seen, unique = set(), []
    for p in out:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def discover_user_dir() -> Path | None:
    """The EU5 user directory: the one with a logs/ folder in it."""
    found: list[tuple[int, Path]] = []
    for p in _user_dir_candidates():
        if not p.is_dir():
            continue
        # Score by how much of a real user directory this looks like.
        score = 0
        if (p / "logs").is_dir():
            score += 4
        if (p / "mod").is_dir():
            score += 2
        if (p / "pdx_settings.json").is_file():
            score += 2
        if (p / "save games").is_dir():
            score += 1
        found.append((score, p))
    if not found:
        return None
    found.sort(key=lambda t: t[0], reverse=True)
    return found[0][1]


# The languages EU5 ships; pdx_settings.json names the one in use ("l_spanish").
GAME_LANGUAGES = ("english", "french", "german", "spanish", "russian", "polish", "braz_por", "simp_chinese",
                  "japanese", "korean", "turkish")


def discover_game_language(user_dir: str | Path) -> str:
    """The language EU5 runs in, from its own settings; '' if it is the default (English)."""
    try:
        data = json.loads((Path(user_dir) / "pdx_settings.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    raw = str(((data.get("System") or {}) if isinstance(data, dict) else {}).get("language") or "")
    lang = raw.removeprefix("l_").strip().lower()
    return lang if lang in GAME_LANGUAGES else ""


def discover_game_dir() -> Path | None:
    """The EU5 install directory, by walking Steam's library list."""
    steam_roots = [
        Path(r"C:/Program Files (x86)/Steam"),
        Path(r"C:/Program Files/Steam"),
        _home() / ".steam/steam",
    ]
    libraries: list[Path] = []
    for root in steam_roots:
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if not vdf.is_file():
            continue
        libraries.append(root)
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith('"path"'):
                parts = line.split('"')
                if len(parts) >= 4:
                    libraries.append(Path(parts[3].replace("\\\\", "/")))
    for lib in libraries:
        candidate = lib / "steamapps" / "common" / "Europa Universalis V"
        if (candidate / "game" / "in_game").is_dir():
            return candidate
    return None


def discover_player2_port() -> int:
    """Player2 writes its port to APPDATA/game.player2.client/api.port.

    The file is removed when the app exits cleanly, so its absence is also a
    hint that Player2 is not running - but we still fall back to the
    documented default rather than refusing to try.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        f = Path(appdata) / "game.player2.client" / "api.port"
        try:
            return int(f.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
    return 4315


@dataclass
class Config:
    # --- where things are -------------------------------------------------
    user_dir: str = ""
    game_dir: str = ""
    mod_dir: str = ""          # <user_dir>/mod/WhispersInTheCourt

    # --- Player2 ----------------------------------------------------------
    player2_host: str = "127.0.0.1"
    player2_port: int = 0      # 0 = discover
    player2_game_key: str = "voices-of-the-court-eu5"
    model_temperature: float = 0.85
    # Generous on purpose: the model's hidden reasoning counts against it.
    max_tokens: int = 3000
    request_timeout_s: float = 120.0
    health_ping_s: float = 60.0

    # --- the AI that writes ---------------------------------------------------
    # "player2" (default), or a cloud model with the player's own key: "gemini", "mistral",
    # "openrouter" (any of hundreds of models through one key, paid by credits or free ones).
    ai_provider: str = "player2"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"   # free key: the most requests a day
    mistral_api_key: str = ""
    mistral_model: str = "mistral-large-latest"
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-3.1-flash-lite"   # the model Court Brain's prompts are tuned on

    # --- voice ------------------------------------------------------------
    tts_enabled: bool = False
    tts_voice_id: str = ""

    # --- the words --------------------------------------------------------
    # Language the AI writes in (characters, events, the advisor), in the
    # player's own words: "English", "Italiano", "Deutsch"... Chosen at the
    # first start. The interface of the mod and of Court Brain is English.
    language: str = "English"
    # How hard the court is: easy, normal (the mod as designed), hard, very_hard.
    difficulty: str = "normal"
    # False until the player has gone through the first-start settings.
    setup_done: bool = False
    # The localisation folder the mod writes into. Must match the language
    # EU5 itself is running in, because that is the file the game reloads.
    game_language: str = "english"

    # --- the console channel ----------------------------------------------
    # Virtual-key name for the key that opens the EU5 console. OEM_3 is the
    # key below Esc on a US layout; on an Italian layout that physical key is
    # OEM_5. Run `python -m courtbrain --test-console` to find yours.
    console_key: str = "OEM_3"
    reload_loc_command: str = ""   # empty = build it from game_language
    run_file_name: str = "votc_in.txt"
    # Seconds to wait after focusing the game before typing. Too short and
    # the first keystroke lands before the window is ready.
    focus_settle_s: float = 0.20
    key_delay_s: float = 0.02

    # --- pacing -----------------------------------------------------------
    # Wall-clock seconds between heartbeat pulses while the game is running.
    # The pulse is skipped entirely when the in-game date has not moved,
    # which is what happens while the game is paused or a popup is open.
    # Short: the director only decides at a pulse, and at high speed a minute
    # of real time is months of game time.
    pulse_interval_s: float = 20.0
    pulse_backoff_max_s: float = 40.0
    # Minimum in-game days between two unprompted chronicles / knocks. The
    # mod enforces its own budget; this stops the *narration* from becoming
    # wallpaper.
    # Unprompted events: a new one comes after a random pause of this many
    # in-game days (re-rolled each time), with this chance per heartbeat once
    # the pause is over. Stories already under way come back on their own date.
    # How often the court brings something NEW on its own: off, rare, normal,
    # frequent, very (see FREQUENCIES). It never limits the consequences of
    # what the ruler does: those come when the AI decides they would.
    event_frequency: str = "normal"
    max_open_stories: int = 4
    chronicle_min_days: int = 25
    knock_min_days: int = 60
    chronicle_chance: float = 0.55
    knock_chance: float = 0.30

    # --- safety -----------------------------------------------------------
    # Hard ceiling on actions per scene, mirroring
    # votc_max_actions_per_scene in the mod. The mod clamps anyway; this
    # keeps a bad response from even being written to disk.
    max_actions_per_scene: int = 3
    dry_run: bool = False      # generate and log, but never touch the game

    # --- interface ---------------------------------------------------------
    overlay: bool = True       # the small typing/voice window
    # Hide the purple "DEBUG INFO" boxes -debug_mode adds to tooltips
    # (see hidedebug.py). Show them in game with the console command
    # "effect set_global_variable = votc_show_debug".
    hide_debug_tooltips: bool = True
    # Have the game empty its own logs (Log.ClearAll) when debug.log grows past 120 MB. Off: in a
    # session where the game ran it, EU5 never loaded a new text of Court Brain again.
    clear_game_logs: bool = False

    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def user_path(self) -> Path:
        return Path(self.user_dir)

    @property
    def logs_path(self) -> Path:
        return self.user_path / "logs"

    @property
    def run_path(self) -> Path:
        return self.user_path / "run"

    @property
    def mod_path(self) -> Path:
        return Path(self.mod_dir)

    @property
    def dynamic_loc_path(self) -> Path:
        return (
            self.mod_path
            / "main_menu"
            / "localization"
            / self.game_language
            / f"votc_dynamic_l_{self.game_language}.yml"
        )

    @property
    def player2_base(self) -> str:
        port = self.player2_port or discover_player2_port()
        return f"http://{self.player2_host}:{port}/v1"

    @property
    def reload_command(self) -> str:
        if self.reload_loc_command:
            return self.reload_loc_command
        # "Reload localization files and switch language" - switching to the
        # language already in use is a no-op that forces the reload, and it
        # is the one reload subcommand whose name we can read straight out
        # of the executable.
        return f"reload switchlanguage {self.game_language}"

    @property
    def state_dir(self) -> Path:
        return self.user_path / "court_brain"


def _default_config_path() -> Path:
    from .bundle import config_path
    return config_path()


def load(path: str | os.PathLike[str] | None = None) -> Config:
    """Load config.json, filling in anything missing by discovery."""
    cfg_path = Path(path) if path else _default_config_path()
    data: dict[str, Any] = {}
    if cfg_path.is_file():
        data = json.loads(cfg_path.read_text(encoding="utf-8"))

    known = {f for f in Config.__dataclass_fields__}
    extra = {k: v for k, v in data.items() if k not in known}
    cfg = Config(**{k: v for k, v in data.items() if k in known})
    cfg.extra.update(extra)

    if not cfg.user_dir:
        found = discover_user_dir()
        if found:
            cfg.user_dir = str(found)
    # The mod's texts are loaded in the language the game runs in: follow the game,
    # unless the player set another language here on purpose.
    if cfg.user_dir and cfg.game_language in ("", "english"):
        cfg.game_language = discover_game_language(cfg.user_dir) or "english"
    if not cfg.game_dir:
        found = discover_game_dir()
        if found:
            cfg.game_dir = str(found)
    if not cfg.mod_dir and cfg.user_dir:
        cfg.mod_dir = str(Path(cfg.user_dir) / "mod" / "WhispersInTheCourt")
    if not cfg.player2_port:
        cfg.player2_port = discover_player2_port()
    return cfg


def set_option(key: str, value: Any, path: str | os.PathLike[str] | None = None) -> None:
    """Change one setting in config.json, leaving everything else as the player wrote it."""
    cfg_path = Path(path) if path else _default_config_path()
    data: dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    data[key] = value
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def save(cfg: Config, path: str | os.PathLike[str] | None = None) -> Path:
    cfg_path = Path(path) if path else _default_config_path()
    data = asdict(cfg)
    data.pop("extra", None)
    cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return cfg_path


def problems(cfg: Config) -> list[str]:
    """Human-readable reasons Court Brain cannot run yet."""
    out: list[str] = []
    if not cfg.user_dir or not cfg.user_path.is_dir():
        out.append(
            "EU5 user directory not found. Set \"user_dir\" in config.json to the "
            "folder that contains logs/ and mod/ (usually under Documents/Paradox "
            "Interactive/Europa Universalis V)."
        )
    elif not cfg.logs_path.is_dir():
        out.append(f"No logs/ folder in {cfg.user_dir}. Start EU5 once first.")
    if not cfg.mod_dir or not cfg.mod_path.is_dir():
        out.append(
            f"Mod not installed at {cfg.mod_dir or '<unset>'}. Copy "
            "mod/WhispersInTheCourt there."
        )
    elif not cfg.dynamic_loc_path.is_file():
        out.append(
            f"Missing {cfg.dynamic_loc_path}. The mod's localisation folder must "
            f"match game_language ({cfg.game_language!r})."
        )
    return out
