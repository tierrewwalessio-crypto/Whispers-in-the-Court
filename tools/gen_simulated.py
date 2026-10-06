"""Generate the SIMULATED institutions: what a decree does when the game itself would not allow it.

Run from the repository root:  python tools/gen_simulated.py [path to EU5]

A decree may order a government reform, a law or an estate privilege the game does not
allow this realm (another government's reform, a law of a later age, an estate it does
not have). (Buildings are not simulated: one the game does not allow is not built.) Forcing the game's own object on
the realm leaves the state in a shape the game does not expect, so the mod carries out
the order its own way: a modifier with the same effects, by decree, that lasts until it
is revoked.

Each simulated object copies the numeric effects of the game's own one (its
country_modifier) - values, or the game's named
values of main_menu/common/script_values. Switches ("= yes": is_pope, allow_guild_hall,
blocked_from_conversion...) are never copied: they are what makes the real object
special, and exactly what must not be forced.

Writes:
  mod/.../main_menu/common/static_modifiers/votc_simulated.txt
  mod/.../main_menu/localization/english/votc_simulated_l_english.yml
  tools/court_brain/courtbrain/simulated.py   (which objects have a simulation)
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "mod" / "WhispersInTheCourt"
OUT = MOD / "main_menu" / "common" / "static_modifiers" / "votc_simulated.txt"
LOC = MOD / "main_menu" / "localization" / "english" / "votc_simulated_l_english.yml"
PY = ROOT / "tools" / "court_brain" / "courtbrain" / "simulated.py"
GAME = Path(sys.argv[1] if len(sys.argv) > 1 else r"F:/SteamLibrary/steamapps/common/Europa Universalis V") / "game"

# prefix of the simulated modifier, by kind of object
PREFIX = {"reform": "votc_simr_", "policy": "votc_simp_", "privilege": "votc_simv_"}

_TOP = re.compile(r"^([a-z][a-z0-9_]*)\s*=\s*\{(.*?)^\}", re.S | re.M)
_LINE = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*=\s*(-?[0-9.]+|[a-z][a-z0-9_]*)\s*(?:#.*)?$", re.M)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _block(body: str, name: str, indent: str = r"\t") -> str:
    """The first `name = { ... }` block of body (balanced braces)."""
    m = re.search(r"(?m)^" + indent + name + r"\s*=\s*\{", body)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(body) and depth:
        depth += {"{": 1, "}": -1}.get(body[i], 0)
        i += 1
    return body[m.end():i - 1]


def _flat(block: str) -> str:
    """Only the block's own lines: nested blocks (potential_trigger, conditions) removed."""
    out, depth = [], 0
    for ch in block:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def modifier_types() -> dict[str, str]:
    """modifier key -> its category (country, location...), from the game's definitions."""
    out: dict[str, str] = {}
    for f in (GAME / "main_menu" / "common" / "modifier_type_definitions").glob("*.txt"):
        for m in _TOP.finditer(_read(f)):
            cat = re.search(r"category\s*=\s*(\w+)", m.group(2))
            out[m.group(1)] = cat.group(1) if cat else ""
    return out


def named_values() -> set[str]:
    """Named numbers a static modifier may use (main_menu script values that are plain numbers)."""
    names: set[str] = set()
    for f in (GAME / "main_menu" / "common" / "script_values").glob("*.txt"):
        names.update(re.findall(r"(?m)^([a-z][a-z0-9_]*)\s*=\s*-?[0-9.]+\s*(?:#.*)?$", _read(f)))
    return names


def effects(block: str, types: dict[str, str], names: set[str], want: tuple[str, ...]) -> list[tuple[str, str]]:
    out = []
    for key, value in _LINE.findall(_flat(block)):
        if value in ("yes", "no") or key not in types:
            continue
        if types[key] not in want and types[key]:
            continue
        if not re.fullmatch(r"-?[0-9.]+", value) and value not in names:
            continue
        out.append((key, value))
    return out


def main() -> None:
    types, names = modifier_types(), named_values()
    common = GAME / "in_game" / "common"
    sims: dict[str, dict[str, list[tuple[str, str]]]] = {k: {} for k in PREFIX}
    country = ("country",)
    for f in sorted((common / "government_reforms").glob("*.txt")):
        for m in _TOP.finditer(_read(f)):
            eff = effects(_block(m.group(2), "country_modifier"), types, names, country)
            if eff:
                sims["reform"][m.group(1)] = eff
    for f in sorted((common / "estate_privileges").glob("*.txt")):
        for m in _TOP.finditer(_read(f)):
            eff = effects(_block(m.group(2), "country_modifier"), types, names, country)
            if eff:
                sims["privilege"][m.group(1)] = eff
    for f in sorted((common / "laws").glob("*.txt")):
        for law in _TOP.finditer(_read(f)):
            for opt in re.finditer(r"(?m)^\t([a-z][a-z0-9_]*)\s*=\s*\{", law.group(2)):
                body = _block(law.group(2)[opt.start():], opt.group(1))
                eff = effects(_block(body, "country_modifier", r"\t\t"), types, names, country)
                if eff:
                    sims["policy"][opt.group(1)] = eff

    lines = ["\ufeff# Whispers in the Court - SIMULATED institutions. GENERATED by tools/gen_simulated.py: edit that.",
             "# What a decree does when the game itself does not allow this realm the real thing:",
             "# the same numeric effects, by decree, until revoked (courtbrain/works.py).", ""]
    loc = ["\ufeffl_english:", ""]
    for kind, table in sims.items():
        category = "country"
        for key, eff in sorted(table.items()):
            name = PREFIX[kind] + key
            lines.append(f"{name} = {{")
            lines.append(f"\tgame_data = {{ category = {category} }}")
            lines += [f"\t{k} = {v}" for k, v in eff]
            lines.append("}")
            loc.append(f' STATIC_MODIFIER_NAME_{name}:0 "${key}$ (by decree)"')
            loc.append(f' STATIC_MODIFIER_DESC_{name}:0 "The Crown has ordered it in its own way, where the '
                       f'realm could not have it as such (Whispers in the Court)."')
        lines.append("")
    io.open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
    io.open(LOC, "w", encoding="utf-8", newline="\n").write("\n".join(loc) + "\n")
    py = ['"""Which game objects have a simulated version (GENERATED by tools/gen_simulated.py)."""', "",
          "from __future__ import annotations", ""]
    for kind, table in sims.items():
        plural = {"reform": "REFORMS", "policy": "POLICIES", "privilege": "PRIVILEGES"}[kind]
        py.append(f"{plural}: frozenset[str] = frozenset({{")
        py += [f'    "{k}",' for k in sorted(table)]
        py.append("})")
        py.append("")
    io.open(PY, "w", encoding="utf-8", newline="\n").write("\n".join(py))
    print("simulated:", {k: len(v) for k, v in sims.items()})


if __name__ == "__main__":
    main()
