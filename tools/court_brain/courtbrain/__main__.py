"""Command line entry point.

    python -m courtbrain              start Court Brain (side panel + bridge)
    python -m courtbrain --check      is everything installed and reachable?
    python -m courtbrain --install    copy the mod into the EU5 mod folder
    python -m courtbrain --saves      which memory belongs to which save file
    python -m courtbrain --no-panel   run without the side panel (log only)
"""

from __future__ import annotations

import argparse
import shutil
import sys
import threading
from pathlib import Path

from . import config as config_mod
from . import gamelogs
from .app import CourtBrain
from .codex import build as build_codex
from .player2 import Player2Client


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def mod_errors(logs_dir: Path, kept: Path | None = None) -> list[str]:
    """What the game said about this mod in its last session (also what was kept
    from its error log before Court Brain had the game clear it)."""
    return gamelogs.mod_errors(logs_dir, kept)


def cmd_check(cfg: config_mod.Config) -> int:
    print("Whispers in the Court - checking the installation\n")
    ok = True
    print(f"EU5 user folder     : {cfg.user_dir or 'NOT FOUND'}")
    print(f"EU5 installation    : {cfg.game_dir or 'NOT FOUND'}")
    print(f"Mod                 : {cfg.mod_dir or 'NOT FOUND'}")
    print(f"Player2             : {cfg.player2_base}\n")

    if not cfg.user_dir or not cfg.user_path.is_dir():
        ok = False
        print("  [!] EU5's user folder was not found: set user_dir in config.json.")
    if not cfg.mod_path.is_dir():
        ok = False
        print("  [!] The mod is not installed: run  py -3 -m courtbrain --install")
    elif not (cfg.mod_path / "in_game" / "gui" / "votc_bridge.gui").is_file():
        ok = False
        print("  [!] The installed mod is an old version: run  py -3 -m courtbrain --install")
    else:
        print("  [ok] Mod installed.")

    health = Player2Client(cfg.player2_base, cfg.player2_game_key, timeout=10).health()
    if health:
        print(f"  [ok] Player2 answers (version {health.get('client_version', '?')}).")
    else:
        ok = False
        print("  [!] Player2 does not answer: open the Player2 app and log in.")

    if cfg.game_dir:
        codex = build_codex(cfg.game_dir, cfg.game_language)
        print(f"  [ok] Reading the game files: {len(codex.laws)} laws, {len(codex.tags)} countries.")

    history = cfg.user_path / "console_history.txt"
    if history.is_file():
        text = history.read_text(encoding="utf-8", errors="replace")
        if "votc_say " in text:
            print("  [ok] The mod's bridge has written at least once: the channel works.")
        else:
            print("  [ ] The bridge has not written yet: start a game with the mod enabled.")

    errors = mod_errors(cfg.logs_path, cfg.state_dir / gamelogs.KEPT_NAME)
    if errors:
        ok = False
        print(f"\n  [!] In the last session the game reported {len(errors)} problems with the mod:")
        for line in errors[:15]:
            print(f"      {line[:190]}")
    elif cfg.logs_path.is_dir():
        print("  [ok] No errors from the mod in the last game session.")

    print("\nAll good." if ok else "\nSomething needs fixing (see above).")
    return 0 if ok else 1

def cmd_install(cfg: config_mod.Config) -> int:
    from .bundle import _running, install_mod
    if _running("eu5.exe"):
        # The same rule as ensure_mod: a mod replaced under a running game crashes it.
        print("EU5 is open: close it first. Replacing the mod while the game runs makes it crash.")
        return 1
    return 0 if install_mod(cfg, print) else 1


def _yield_to_the_game() -> None:
    """Run below normal priority: the game gets the CPU first whenever both want it (reading a
    save, writing prompts). Court Brain's own work only waits a little longer."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
    except (AttributeError, OSError):
        pass


def cmd_run(cfg: config_mod.Config, *, panel: bool) -> int:
    _yield_to_the_game()
    from . import prompts
    from .bundle import config_path, ensure_mod, launch_game
    instructions = config_path().parent / "instructions.json"
    prompts.load_custom(instructions)                 # the player's own instructions for the AI
    brain = CourtBrain(cfg)
    if not panel:
        try:
            brain.run_forever()
        except KeyboardInterrupt:
            brain.stop()
        return 0

    from .drawer import Callbacks, Drawer
    from .ledger import Ledger
    drawer = Drawer(
        Callbacks(
            on_send=brain.on_send,
            on_offer=brain.on_offer,
            on_close=brain.on_close,
            on_hub=brain.on_hub,
            on_suggest=brain.on_suggest,
            listen_start=lambda: brain.client.listen_start(30.0),
            listen_stop=brain.client.listen_stop,
        ),
        game_dir=cfg.game_dir,
        cache_dir=cfg.state_dir,
    )
    brain.drawer = drawer
    # The Court Brain window: settings, status and the record of the court.
    ledger = Ledger(drawer.root, drawer.art, cfg=cfg, log_file=cfg.state_dir / "court_brain.log",
                    data_dir=cfg.state_dir, instructions_file=instructions, status=brain.status,
                    on_frequency=brain.set_frequency, on_launch=lambda: launch_game(cfg, brain.log),
                    save_option=config_mod.set_option, on_provider=brain.set_provider)

    def log(message: str) -> None:
        print(message, flush=True)
        ledger.log(message)

    brain.log = log
    drawer.on_error = log
    # A launcher's work first: the mod in place and up to date, and what the
    # game said about it last time.
    ensure_mod(cfg, log)
    kept = cfg.state_dir / gamelogs.KEPT_NAME
    errors = mod_errors(cfg.logs_path, kept)
    if errors:
        log(f"WARNING: in the last session the game reported {len(errors)} problems with the mod:")
        for line in errors[:8]:
            log("  " + line[:190])
    try:
        kept.unlink()           # told once; a new session keeps its own
    except OSError:
        pass

    def start() -> None:
        threading.Thread(target=brain.run_forever, name="court-brain", daemon=True).start()

    if cfg.setup_done:
        start()
    else:
        # The first start: the choices come before the court speaks.
        ledger.show_setup(first=True, on_done=start)
    try:
        drawer.run()
    except KeyboardInterrupt:
        pass
    finally:
        brain.stop()
    return 0

def cmd_saves(cfg: config_mod.Config) -> int:
    """Every save file, with the campaign and the point of memory it carries."""
    from .memory import CampaignStore
    from .savesindex import SavesIndex

    store = CampaignStore(cfg.state_dir)
    index = SavesIndex(cfg.user_path / "save games", cfg.state_dir / "saves_index.json")
    saves = index.refresh()
    print(f"Saves in {cfg.user_path / 'save games'}: {len(saves)}\n")
    memories: dict[int, object] = {}
    for info in saves:
        where = f"{info.date or '?':>10}  {info.file}"
        if not info.text:
            print(f"  {where}\n      binary (a game without -debug_mode): no memory attached")
            continue
        if not info.campaign:
            print(f"  {where}\n      {info.playthrough_name or '?'}: no court memory (saved before this version of the mod)")
            continue
        mem = memories.get(info.campaign)
        if mem is None:
            mem = memories[info.campaign] = store.open(info.campaign)
        mem.checkout(info.head)
        print(f"  {where}\n      campaign {mem.campaign} - the court remembers up to "
              f"{mem.head_date() or 'the beginning'}: {len(mem.events)} facts, {len(mem.pages)} chronicle "
              f"pages, {len(mem.people)} people")
    return 0

def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(prog="courtbrain", description="Court Brain - Whispers in the Court for EU5.")
    parser.add_argument("--config", help="path of config.json")
    parser.add_argument("--check", action="store_true", help="check the installation")
    parser.add_argument("--install", action="store_true", help="install the mod into EU5's mod folder")
    parser.add_argument("--saves", action="store_true", help="show the memory attached to each save")
    parser.add_argument("--no-panel", action="store_true", help="run without the side panel")
    parser.add_argument("--language", help="the language the AI writes in (English, Italiano, ...)")
    args = parser.parse_args(argv)

    cfg = config_mod.load(args.config)
    if args.language:
        cfg.language = args.language
    if args.check:
        return cmd_check(cfg)
    if args.install:
        return cmd_install(cfg)
    if args.saves:
        return cmd_saves(cfg)
    from .bundle import single_instance
    if not single_instance():
        if sys.stdout is None:
            import tkinter.messagebox as mb
            mb.showinfo("Whispers in the Court", "Whispers in the Court is already open.")
        else:
            print("Court Brain is already running.")
        return 0
    try:
        return cmd_run(cfg, panel=not args.no_panel)
    except Exception:
        # Started without a console (pyw), a crash would otherwise be silent.
        import traceback
        report = traceback.format_exc()
        try:
            (cfg.state_dir / "court_brain_crash.log").write_text(report, encoding="utf-8")
        except OSError:
            pass
        if sys.stdout is None:
            import tkinter.messagebox as mb
            mb.showerror("Whispers in the Court", "Court Brain stopped because of an error:\n\n" + report[-1500:])
        raise


if __name__ == "__main__":
    raise SystemExit(main())
