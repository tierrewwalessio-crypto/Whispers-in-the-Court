"""Court Brain as a single program: WhispersInTheCourt.exe.

The player downloads one file and starts it, as with other mod launchers.
Inside it (built by tools/build_exe.py with PyInstaller) are Python, Court
Brain and the mod itself. At every start the program:

- installs the mod into EU5's mod folder, or updates it when the copy there
  differs from the one it carries (a fingerprint of the files is kept next
  to the installed mod, in .votc_bundle). It installs its own copy even when
  the player also subscribed on the Steam Workshop: only this copy carries
  what depends on the player's game (the hidden debug boxes), so it is named
  "Whispers in the Court (USE THIS)" in the launcher;
- keeps its settings in %LOCALAPPDATA%\\WhispersInTheCourt\\config.json (the
  folder of a downloaded exe may not be writable);
- can start EU5 through Steam with -debug_mode, which the bridge needs.

Run from the sources (python -m courtbrain) everything works the same, with
the mod taken from the repository and config.json beside the package.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

EU5_APP_ID = "3450310"
MARK = ".votc_bundle"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def mod_source() -> Path:
    """The mod this Court Brain carries."""
    if frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "mod" / "WhispersInTheCourt"
    return Path(__file__).resolve().parents[3] / "mod" / "WhispersInTheCourt"


def config_path() -> Path:
    if frozen():
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "WhispersInTheCourt" / "config.json"
    return Path(__file__).resolve().parents[1] / "config.json"


def fingerprint(folder: Path) -> str:
    h = hashlib.sha1()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        h.update(path.relative_to(folder).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def signature(cfg) -> str:
    """The carried mod plus the game files the installation copies (they change with EU5 updates)."""
    h = hashlib.sha1(fingerprint(mod_source()).encode())
    h.update(f"language:{cfg.game_language}".encode())
    if cfg.game_dir:
        from .battleview import FILE as BATTLE_FILE
        try:
            st = (Path(cfg.game_dir) / "game" / BATTLE_FILE).stat()
            h.update(f"{BATTLE_FILE}:{st.st_size}:{int(st.st_mtime)}".encode())
        except OSError:
            pass
    if cfg.hide_debug_tooltips and cfg.game_dir:
        from .hidedebug import FILES
        for rel in FILES:
            f = Path(cfg.game_dir) / "game" / rel
            try:
                st = f.stat()
                h.update(f"{rel}:{st.st_size}:{int(st.st_mtime)}".encode())
            except OSError:
                h.update(rel.encode())
    return h.hexdigest()


def install_mod(cfg, log: Callable[[str], None]) -> bool:
    """Copy the carried mod into EU5's mod folder, with what depends on this player's game."""
    source = mod_source()
    if not source.is_dir():
        log(f"WARNING: the mod to install is missing ({source}).")
        return False
    if not cfg.user_dir or not cfg.user_path.is_dir():
        log("WARNING: EU5's folder in Documents was not found. Start the game once, then reopen "
            "Whispers in the Court. If it still is not found, set \"user_dir\" in "
            f"{config_path()} to the folder that holds EU5's logs, mod and save games.")
        return False
    target = cfg.user_path / "mod" / "WhispersInTheCourt"
    _remove_old_copies(cfg, log)
    try:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    except OSError as exc:
        log(f"WARNING: the mod could not be installed: {exc}. Close EU5 and reopen Whispers in the Court.")
        return False
    _mark_name(target)
    languages = _localize(target, cfg)
    if cfg.game_language != "english":
        log(f"EU5 runs in {cfg.game_language}: the mod's texts are installed for it too.")
    from .names import NameRegistry, PlaceNames
    NameRegistry(cfg.state_dir).write_loc(target)      # names chosen in earlier games
    PlaceNames(cfg.state_dir).write_loc(target)        # places renamed in earlier games
    if cfg.game_dir:
        from .battleview import install as battle_button
        if not battle_button(Path(cfg.game_dir), target):
            log("The battle window of this game version was not recognised: take command of a battle from the "
                "army's own \"Command the Battle\" ability.")
    if cfg.hide_debug_tooltips and cfg.game_dir:
        from .hidedebug import install as hide_debug
        done = hide_debug(Path(cfg.game_dir), target)
        if done:
            log(f"Purple debug boxes hidden ({sum(n for _, n in done)} conditions in {len(done)} interface "
                "files, taken from your version of the game).")
    try:
        (target / MARK).write_text(signature(cfg), encoding="utf-8")
    except OSError:
        pass
    log(f"Mod installed in {target}. In the EU5 launcher, enable \"{INSTALLED_NAME}\" "
        "(not the Workshop copy, if you have one).")
    return True


# The copy this program installs is the complete one: its name says so in the
# launcher, beside a Workshop subscription that lacks the per-game parts.
INSTALLED_NAME = "Whispers in the Court (USE THIS)"


def _localize(target: Path, cfg) -> list[str]:
    """The mod ships its texts in English. The game only reads the files of the
    language it runs in, so the English files are copied for every language EU5
    has (the interface stays in English), and the bridge's reload command names
    the language actually in use - a reload of the wrong language never brings
    the scene's words in."""
    from .config import GAME_LANGUAGES
    root = target / "main_menu" / "localization"
    english = root / "english"
    command = cfg.reload_command
    done = []
    for lang in GAME_LANGUAGES:
        folder = root / lang
        folder.mkdir(parents=True, exist_ok=True)
        for src in english.glob("*_l_english.yml"):
            text = src.read_text(encoding="utf-8-sig")
            if lang != "english":
                text = text.replace("l_english:", f"l_{lang}:", 1)
            text = text.replace("reload switchlanguage english", command)
            (folder / src.name.replace("_l_english.yml", f"_l_{lang}.yml")).write_bytes(
                b"\xef\xbb\xbf" + text.encode("utf-8"))
        done.append(lang)
    return done


def _mark_name(target: Path) -> None:
    meta = target / ".metadata" / "metadata.json"
    try:
        text = meta.read_text(encoding="utf-8-sig")
        text = re.sub(r'("name"\s*:\s*)"[^"]*"', lambda m: m.group(1) + '"' + INSTALLED_NAME + '"', text, count=1)
        meta.write_text(text, encoding="utf-8")
    except OSError:
        pass


# Where earlier versions of the mod installed themselves, under other names.
OLD_FOLDERS = ("VoicesOfTheCourt",)
MOD_ID = "votc_eu5_court_voices"


def _remove_old_copies(cfg, log: Callable[[str], None]) -> None:
    """An earlier copy of this mod under its old name would show up twice in the launcher."""
    for name in OLD_FOLDERS:
        old = cfg.user_path / "mod" / name
        meta = old / ".metadata" / "metadata.json"
        try:
            if meta.is_file() and MOD_ID in meta.read_text(encoding="utf-8-sig", errors="replace"):
                shutil.rmtree(old)
                log(f"Removed the old copy of the mod ({old.name}): it is now \"Whispers in the Court\".")
        except OSError:
            pass


def ensure_mod(cfg, log: Callable[[str], None]) -> None:
    """Install the mod if it is missing, update it if it is not the one carried here."""
    source = mod_source()
    if not source.is_dir() or not cfg.user_dir:
        if not cfg.user_dir:
            log("WARNING: EU5's folder in Documents was not found. Start the game once, then reopen "
                "Whispers in the Court. If it still is not found, set \"user_dir\" in "
                f"{config_path()} to the folder that holds EU5's logs, mod and save games.")
        return
    target = cfg.user_path / "mod" / "WhispersInTheCourt"
    try:
        installed = (target / MARK).read_text(encoding="utf-8").strip()
    except OSError:
        installed = ""
    if installed and installed == signature(cfg):
        log("The installed mod is up to date.")
        return
    if target.exists() and _running("eu5.exe"):
        # Replacing the mod under a running game leaves it with the old scripts in memory, its file
        # watcher broken, and Court Brain sending orders only the new mod knows: it crashes. The
        # update waits until the game is closed (Start EU5 does it first).
        log("WARNING: EU5 is open with an older version of the mod. It is not replaced while the game runs - "
            "that makes it crash. Close EU5, then start it with the Start EU5 button (the mod is updated "
            "first), or reopen Whispers in the Court with the game closed.")
        return
    log("Updating the mod in EU5's folder…" if target.exists() else "Installing the mod in EU5's folder…")
    install_mod(cfg, log)


def steam_app_id(game_dir: str) -> str:
    """EU5's Steam id, from the library manifest beside the game when it can be read."""
    if game_dir:
        steamapps = Path(game_dir).parents[1]
        folder = Path(game_dir).name
        for manifest in steamapps.glob("appmanifest_*.acf"):
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(r'"installdir"\s+"' + re.escape(folder) + '"', text):
                m = re.search(r'"appid"\s+"(\d+)"', text)
                if m:
                    return m.group(1)
    return EU5_APP_ID


DEBUG_HELP = ("In Steam: right-click Europa Universalis V > Properties > Launch Options, write -debug_mode, "
              "then start the game as usual.")


def _running(image: str) -> bool:
    """Is a program with this file name running? (Windows only; False elsewhere.)"""
    # Read as bytes: tasklist writes in the console's own code page, which on a
    # Windows in another language is not UTF-8 (the output then came back as None
    # and "Start EU5" failed). The image name itself is always plain ASCII.
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"], capture_output=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return image.lower().encode("ascii") in (out.stdout or b"").lower()


def launch_game(cfg, log: Callable[[str], None]) -> None:
    """Start EU5 with -debug_mode, which the mod needs to load its texts.

    The game's own executable is started directly, with the option on its command
    line: Steam's steam://run/<id>//<options> link asks the player to confirm, and
    the option is easily lost on the way (testers' games started without it). Steam
    must be running first, or the game restarts itself through Steam without it."""
    if _running("eu5.exe"):
        log("EU5 is already running. If it was not started with -debug_mode, close it and press "
            "Start EU5 again. " + DEBUG_HELP)
        return
    ensure_mod(cfg, log)            # an update that waited for the game to close goes in now
    exe = Path(cfg.game_dir or ".") / "binaries" / "eu5.exe"
    if cfg.game_dir and exe.is_file() and os.name == "nt":
        if not _running("steam.exe"):
            log("Starting Steam first…")
            try:
                os.startfile("steam://open/main")      # noqa: S606 - Steam's own protocol
            except OSError:
                pass
            for _ in range(60):
                if _running("steam.exe"):
                    time.sleep(8)                      # let Steam sign in before the game asks it
                    break
                time.sleep(1)
        try:
            subprocess.Popen([str(exe), "-debug_mode"], cwd=str(exe.parent), close_fds=True,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            log("Starting EU5 with -debug_mode.")
            return
        except OSError as exc:
            log(f"Could not start {exe} ({exc}); trying through Steam.")
    url = f"steam://run/{steam_app_id(cfg.game_dir)}//-debug_mode/"
    try:
        os.startfile(url)                       # noqa: S606 - Steam's own protocol
        log("Starting EU5 with -debug_mode through Steam (if Steam asks to confirm the option, accept).")
    except (AttributeError, OSError) as exc:
        log(f"WARNING: could not start EU5 ({exc}). Start it yourself with -debug_mode. " + DEBUG_HELP)


_MUTEX = None


def single_instance() -> bool:
    """True if no other Court Brain runs: two would read and write the same files."""
    global _MUTEX
    if not hasattr(sys, "getwindowsversion"):
        return True
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _MUTEX = kernel32.CreateMutexW(None, False, "Local\\WhispersInTheCourt.CourtBrain")
    return ctypes.get_last_error() != 183                     # ERROR_ALREADY_EXISTS
