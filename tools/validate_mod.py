#!/usr/bin/env python3
"""Static checks on the Whispers in the Court mod, without launching EU5.

Starting the game to find out you typed `looking_for` instead of
`looking_for_a` costs five minutes. This costs a second. It is not a parser
for Paradox script - the engine is the only authority on that - but it
catches the mistakes that actually happen:

  * unbalanced braces
  * a votc_* effect that is called but never defined
  * a $parameter$ a caller never passes
  * a localisation key referenced from script that no .yml defines
  * a token used as a statement that vanilla's own script never uses,
    which is usually a misremembered effect or trigger name

The last check works by comparing against a vocabulary harvested from the
game's own common/ and events/ folders, so it stays correct across patches:
re-run with --refresh-vocabulary after the game updates.

    python tools/validate_mod.py
    python tools/validate_mod.py --game "F:/SteamLibrary/.../Europa Universalis V"
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOD = ROOT / "mod" / "WhispersInTheCourt"
VOCAB = ROOT / "docs" / "eu5_script_vocabulary.txt"

# Structural keywords and our own argument names, which are never engine
# tokens and must not be reported as unknown.
# Tokens the executable documents but vanilla's own script never uses, so the
# vocabulary harvested from game files cannot vouch for them. Each one was
# read out of the effect documentation embedded in eu5.exe.
FROM_ENGINE_DOCS = {
    "debug_log",   # "Log a string to the error log when this effect executes"
    "error_log",   # the same documentation entry names both
    # defined in main_menu/common/modifier_type_definitions/00_modifier_types.txt
    # (category country, percent) though no vanilla script uses it yet
    "global_heretic_pop_conversion_speed_modifier",
}

CONTROL = {
    "if", "else", "else_if", "limit", "trigger", "effect", "option", "name",
    "value", "add", "subtract", "multiply", "divide", "min", "max", "round",
    "floor", "ceiling", "modifier", "type", "target", "first", "second",
    "third", "desc", "title", "immediate", "after", "hidden_effect", "and",
    "or", "not", "nor", "nand", "trigger_if", "trigger_else", "custom_tooltip",
    "random", "random_list", "chance", "years", "months", "days", "mode",
    "potential", "allow", "cooldown", "price", "select_trigger", "column",
    "visible", "enabled", "data", "source", "source_flags", "target_flag",
    "looking_for_a", "cache_targets", "on_actions", "events", "fallback",
    "outcome", "orphan", "category", "interface_lock", "hide_portraits",
    "fire_only_once", "major", "namespace", "game_data", "yearly_decay",
    "estate_preferences", "law_category", "law_gov_group", "save_scope_as",
    "saved_scopes", "is_shown", "is_valid", "ai_is_valid", "ai_chance",
    "ai_frequency", "ai_tick", "ai_tick_frequency", "ai_will_do",
    "message", "show_message", "show_message_to_target", "sound",
    "on_own_nation", "on_other_nation", "use_enroute", "block_when_at_war",
    "show_in_gui_list", "scope", "slot", "kind", "cb", "seq", "nopt", "who",
    "where", "key", "a1", "a2", "a3", "topic", "agenda", "mode", "choice",
    "errand", "tier", "sign", "estate", "edict", "direction", "c", "cbvar",
    "val", "cost", "modifier", "level",
}

_TOKEN = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*\??=")
_DEF = re.compile(r"^([a-z][a-z0-9_]*)\s*=\s*\{")
# A script value can also be a bare number: "votc_cost_mild = 12".
_SCALAR_DEF = re.compile(r"^([a-z][a-z0-9_]*)\s*=\s*-?[0-9.]+\s*$")
_ARG = re.compile(r"\$([a-z0-9_]+)\$")
_LOC_DEF = re.compile(r'^\s*([A-Za-z0-9_.\-]+):\s*\d*\s*"')
_LOC_REF = re.compile(r"\b(votc_[a-z0-9_]+)\b")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        # Paradox has no block comments and no escaping, so this is exact
        # enough: anything after an unquoted # is a comment.
        in_quote = False
        cut = len(line)
        for i, ch in enumerate(line):
            if ch == '"':
                in_quote = not in_quote
            elif ch == "#" and not in_quote:
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


def script_files() -> list[Path]:
    return sorted(
        p for p in MOD.rglob("*.txt")
        if "localization" not in p.parts
    )


def loc_files() -> list[Path]:
    return sorted(MOD.rglob("*.yml"))


# ----------------------------------------------------------------------

def check_braces(problems: list[str]) -> None:
    for path in script_files() + loc_files():
        if path.suffix == ".yml":
            continue
        text = strip_comments(read(path))
        depth = 0
        for lineno, line in enumerate(text.splitlines(), 1):
            depth += line.count("{") - line.count("}")
            if depth < 0:
                problems.append(f"{path.relative_to(ROOT)}:{lineno}: a closing brace too many")
                break
        else:
            if depth != 0:
                problems.append(
                    f"{path.relative_to(ROOT)}: {depth} brace(s) left open at end of file"
                )


def collect_definitions() -> tuple[set[str], dict[str, set[str]]]:
    defined: set[str] = set()
    args: dict[str, set[str]] = defaultdict(set)
    for path in script_files():
        text = strip_comments(read(path))
        current = ""
        depth = 0
        for line in text.splitlines():
            if depth == 0:
                m = _DEF.match(line)
                if m:
                    current = m.group(1)
                    defined.add(current)
                else:
                    scalar = _SCALAR_DEF.match(line)
                    if scalar:
                        defined.add(scalar.group(1))
            if current:
                for arg in _ARG.findall(line):
                    args[current].add(arg)
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                depth = 0
                current = ""
    return defined, args


def check_calls(problems: list[str], defined: set[str], args: dict[str, set[str]]) -> None:
    called: dict[str, list[tuple[Path, int, set[str]]]] = defaultdict(list)
    for path in script_files():
        text = strip_comments(read(path))
        for lineno, line in enumerate(text.splitlines(), 1):
            m = _TOKEN.match(line)
            if not m:
                continue
            token = m.group(1)
            if not token.startswith("votc_"):
                continue
            passed = set(re.findall(r"\b([a-z0-9_]+)\s*=", line.split("=", 1)[1])) if "{" in line else set()
            called[token].append((path, lineno, passed))

    for token, sites in called.items():
        if token in defined:
            continue
        # A definition line is also a "call" by this crude test; skip those.
        real = [s for s in sites if True]
        if real:
            path, lineno, _ = real[0]
            problems.append(
                f"{path.relative_to(ROOT)}:{lineno}: calls {token}, which nothing defines"
            )

    for token, sites in called.items():
        needed = args.get(token, set())
        if not needed:
            continue
        for path, lineno, passed in sites:
            if passed and not needed.issubset(passed):
                missing = ", ".join(sorted(needed - passed))
                problems.append(
                    f"{path.relative_to(ROOT)}:{lineno}: {token} needs {missing}"
                )


def check_localisation(problems: list[str]) -> None:
    defined: set[str] = set()
    for path in loc_files():
        # EU5 silently ignores a localisation file without the UTF-8 BOM:
        # every key in it then shows as its raw name in the game.
        if not path.read_bytes().startswith(b"\xef\xbb\xbf"):
            problems.append(f"{path.relative_to(ROOT)}: missing UTF-8 BOM - the game would ignore this file")
        for line in read(path).splitlines():
            m = _LOC_DEF.match(line)
            if m:
                defined.add(m.group(1))

    # Keys the script points at: event titles/descs/option names and the
    # keys handed to the logging effects.
    referenced: dict[str, tuple[Path, int]] = {}
    for path in script_files():
        text = strip_comments(read(path))
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not any(
                stripped.startswith(prefix)
                for prefix in ("title =", "desc =", "name =", "votc_log", "key =")
            ):
                continue
            for key in _LOC_REF.findall(line):
                if key.startswith(("votc_ctx", "votc_req", "votc_evt", "votc_ack",
                                   "votc_dlg", "votc_cnc", "votc_chr", "votc_knk",
                                   "votc_prg", "votc_env", "votc_ntc")):
                    referenced.setdefault(key, (path, lineno))

    for key, (path, lineno) in sorted(referenced.items()):
        if key not in defined:
            problems.append(
                f"{path.relative_to(ROOT)}:{lineno}: localisation key {key} is not defined"
            )

    # Interactions and actions need a name and a _desc.
    for folder, suffix in (
        ("character_interactions", "_desc"),
        ("country_interactions", "_desc"),
        ("generic_actions", "_desc"),
    ):
        root = MOD / "in_game" / "common" / folder
        for path in root.glob("*.txt"):
            for line in strip_comments(read(path)).splitlines():
                m = _DEF.match(line)
                if not m:
                    continue
                key = m.group(1)
                for wanted in (key, key + suffix):
                    if wanted not in defined:
                        problems.append(
                            f"{path.relative_to(ROOT)}: {wanted} has no localisation"
                        )


def check_vocabulary(problems: list[str], defined: set[str], game_dir: Path | None) -> None:
    vocab = load_vocabulary(game_dir)
    if not vocab:
        problems.append(
            "No vanilla vocabulary available; skipping the unknown-token check. "
            "Run with --game <EU5 install> or --refresh-vocabulary."
        )
        return
    unknown: dict[str, tuple[Path, int]] = {}
    for path in script_files():
        text = strip_comments(read(path))
        for lineno, line in enumerate(text.splitlines(), 1):
            m = _TOKEN.match(line)
            if not m:
                continue
            token = m.group(1)
            if token in vocab or token in CONTROL or token in defined:
                continue
            if token in FROM_ENGINE_DOCS:
                continue
            if token.startswith("votc_"):
                continue
            unknown.setdefault(token, (path, lineno))
    for token, (path, lineno) in sorted(unknown.items()):
        problems.append(
            f"{path.relative_to(ROOT)}:{lineno}: '{token}' does not appear anywhere in "
            "vanilla script - check the spelling"
        )


def load_vocabulary(game_dir: Path | None) -> set[str]:
    if game_dir:
        harvest_vocabulary(game_dir)
    if not VOCAB.is_file():
        return set()
    out: set[str] = set()
    for line in VOCAB.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 2:
            out.add(parts[1])
        elif len(parts) == 1:
            out.add(parts[0])
    return out


def harvest_vocabulary(game_dir: Path) -> None:
    """Rebuild the vocabulary from the installed game."""
    roots = [
        game_dir / "game" / "in_game" / "common",
        game_dir / "game" / "in_game" / "events",
        game_dir / "game" / "main_menu" / "common",
    ]
    counts: dict[str, int] = defaultdict(int)
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.txt"):
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                m = _TOKEN.match(line)
                if m:
                    counts[m.group(1)] += 1
    if not counts:
        return
    VOCAB.parent.mkdir(parents=True, exist_ok=True)
    VOCAB.write_text(
        "\n".join(f"{n} {tok}" for tok, n in sorted(counts.items(), key=lambda kv: -kv[1])),
        encoding="utf-8",
    )
    print(f"Vocabulary refreshed: {len(counts)} tokens from {game_dir}")


# ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the VotC mod without launching EU5.")
    parser.add_argument("--game", help="EU5 install directory, to refresh the vocabulary")
    parser.add_argument("--refresh-vocabulary", action="store_true")
    args = parser.parse_args(argv)

    if not MOD.is_dir():
        print(f"Cannot find the mod at {MOD}")
        return 2

    game_dir = Path(args.game) if args.game else None
    if args.refresh_vocabulary and not game_dir:
        sys.path.insert(0, str(ROOT / "tools" / "court_brain"))
        from courtbrain.config import discover_game_dir  # noqa: PLC0415
        found = discover_game_dir()
        game_dir = Path(found) if found else None

    problems: list[str] = []
    check_braces(problems)
    defined, arg_map = collect_definitions()
    check_calls(problems, defined, arg_map)
    check_localisation(problems)
    check_vocabulary(problems, defined, game_dir)

    files = len(script_files()) + len(loc_files())
    if problems:
        print(f"{len(problems)} problem(s) in {files} files:\n")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"{files} files checked, nothing to report.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
