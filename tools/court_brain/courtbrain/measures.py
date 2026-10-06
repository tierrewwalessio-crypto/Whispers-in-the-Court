"""Standing measures: policies the ruler keeps in force until they revoke them.

A literacy drive, a permanent market watch, a new levy on salt. The AI names the measure
and says which lever it moves and how hard; everything that makes it balanced is decided
here, the same way every time (the mod's modifiers: tools/gen_standing.py):

* nothing lasting is free - a measure that helps is paid, every month, with a share of
  the Crown's tax income set by its strength (3 / 6 / 10%), which grows with the realm;
* except a measure meant to make money (taxes, trade, production, the coin): it would be
  absurd for it to cost the very income it raises - it pays for itself, and a heavier
  take from the people (taxes) weighs on prosperity instead, one step lighter than it;
* a new levy ("revenue") brings a share of the tax income instead (3 / 6 / 9%), and always
  weighs on someone (a burden one step lighter than the levy; the realm's prosperity when
  the AI named none);
* a burden may be added to any measure when it would really weigh on something else;
* four can stand at once, one per slot, each revoked on its own; a new measure on a lever
  that already has one replaces it (a drive made larger, or smaller).
"""

from __future__ import annotations

import re
from typing import Any

from . import actions as A

SLOTS = 4
TIER = {1: "weak", 2: "mild", 3: "severe"}
TIER_WORD = {"weak": "minor", "mild": "moderate", "severe": "major"}
COST_PCT = {"weak": 3, "mild": 6, "severe": 10}          # of the tax income, every month
REVENUE_PCT = {"weak": 3, "mild": 6, "severe": 9}        # tools/gen_standing.py: revenue
LIGHTER = {"weak": "weak", "mild": "weak", "severe": "mild"}
# Measures meant to make money pay for themselves; those that take more from the people weigh on them.
INCOME_GAINS = {"taxation", "trade_income", "trade", "production", "currency",
                "nobles_taxes", "clergy_taxes", "burghers_taxes", "peasants_taxes"}
TAKING_GAINS = {"taxation", "nobles_taxes", "clergy_taxes", "burghers_taxes", "peasants_taxes"}
# The levers (every policy area, as the mod's modifiers have them) and one of the measures' own.
BURDENS = list(A.POLICY_AREAS)
GAINS = BURDENS + ["revenue"]
GAIN_TEXT = {**A.POLICY_TEXT, "revenue": "a new levy: more tax income"}

RULES = """
STANDING MEASURES ("measures") - what the ruler keeps in force until they revoke it
Only when the RULER plainly orders something lasting with no end ("from now on",
"for as long as I reign", "every year", "a permanent...") - a literacy drive, a
standing school of gunners, a permanent watch on the markets, a new levy on salt.
A new tax, toll, duty, fee or rent, or the end of an exemption, is lasting BY
NATURE even when the ruler did not say "from now on": it is a standing measure
("revenue") unless the ruler set an end to it. Lowering a tax or granting an
exemption is the other way round (end the levy in force, or a policy_penalty on
taxation for years). Something for a set number of years is a policy
(policy_bonus) instead.
- "gain": the lever it really moves, and "strength" 1-3 (minor, moderate, major).
- Nothing lasting is free: a measure that helps is paid EVERY MONTH with a share of
  the Crown's tax income set by its strength (3%, 6%, 10%) - the game grows it with
  the realm, and it shows in the budget. Say "money": "costs".
- A measure meant to MAKE money (on taxation, trade, trade_income, production, the
  coin or an estate's taxes) pays for itself: it never costs the income it raises.
- A new levy ("gain": "revenue", "money": "brings") brings a share of the tax
  income instead (3%, 6%, 9%), and always weighs on someone: say on what in
  "burden".
- "burden": what else it really weighs on, if anything (schoolmasters taken from
  the fields: production; a salt levy: prosperity or control). Often none.
- A real start-up cost (buildings, the first hiring) goes in "actions" as a one-time
  gold loss; the monthly share is added by the game itself.
- To revoke one in force: "action": "end" with its name (see MEASURES IN FORCE).
- Four at most; a new one on a lever that already has one replaces it.
""".strip()


def schema() -> dict[str, Any]:
    return {
        "type": "array", "maxItems": 2,
        "description": "Standing measures started or revoked by the ruler's order. Usually empty. See STANDING MEASURES.",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "name", "gain", "strength", "money", "burden", "burden_strength"],
            "properties": {
                "action": {"type": "string", "enum": ["start", "end"]},
                "name": {"type": "string", "description": ("A short name, in the language of the scene (e.g. 'The "
                                                           "Literacy Drive'); for 'end', the name of one in force.")},
                "gain": {"type": "string", "enum": GAINS + ["none"]},
                "strength": {"type": "integer", "minimum": 1, "maximum": 3},
                "money": {"type": "string", "enum": ["costs", "brings", "none"]},
                "burden": {"type": "string", "enum": BURDENS + ["none"]},
                "burden_strength": {"type": "integer", "minimum": 1, "maximum": 3},
            },
        },
    }


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w{4,}", (text or "").lower())}


def find(active: dict[int, dict[str, Any]], name: str, gain: str = "") -> int:
    """The slot of the measure in force the ruler means: by its name, else by its lever."""
    mine = _words(name)
    best, score = 0, 0.0
    for slot, m in active.items():
        other = _words(m.get("name", ""))
        s = len(mine & other) / len(mine | other) if mine and other else 0.0
        if gain and gain == m.get("gain"):
            s += 0.5
        if s > score:
            best, score = slot, s
    return best if score >= 0.34 else 0


def validate(raw: Any, active: dict[int, dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(raw, dict):
        return None, "not a measure"
    name = " ".join(str(raw.get("name") or "").split())[:60]
    gain = str(raw.get("gain") or "none")
    if raw.get("action") == "end":
        slot = find(active, name, gain)
        if not slot:
            return None, f"no standing measure called {name!r} is in force"
        return {"action": "end", "slot": slot, "name": active[slot].get("name", name),
                "label": f"The standing measure \"{active[slot].get('name', name)}\" is revoked"}, ""
    if gain not in GAINS:
        return None, "a standing measure needs the lever it moves"
    tier = TIER[max(1, min(3, int(raw.get("strength") or 1)))]
    burden = str(raw.get("burden") or "none")
    btier = TIER[max(1, min(3, int(raw.get("burden_strength") or 1)))]
    if burden not in BURDENS:
        burden = "none"
    if raw.get("money") == "brings" and gain not in INCOME_GAINS:
        gain = "revenue"                            # money that comes in is a levy, whatever lever was named
    if gain == "revenue":
        money = "brings"
        if burden == "none":
            burden = "prosperity"                   # a levy always weighs on someone
        btier = min(btier, LIGHTER[tier], key=list(TIER.values()).index)
    elif gain in INCOME_GAINS:
        money = "none"                              # it pays for itself
        if gain in TAKING_GAINS and burden == "none":
            burden, btier = "prosperity", LIGHTER[tier]
    else:
        money = "costs"                             # nothing lasting is free
    same = next((s for s, m in active.items() if m.get("gain") == gain), 0)
    slot = same or next((s for s in range(1, SLOTS + 1) if s not in active), 0)
    if not slot:
        return None, f"{SLOTS} standing measures are already in force: one must be revoked first"
    m = {"action": "start", "slot": slot, "name": name or GAIN_TEXT[gain].split(":")[0].capitalize(),
         "gain": gain, "tier": tier, "money": money, "burden": burden, "btier": btier, "replaces": bool(same)}
    m["label"] = label(m)
    return m, ""


def label(m: dict[str, Any]) -> str:
    if m.get("action") == "end":
        return f"The standing measure \"{m['name']}\" is revoked"
    what = "a new levy" if m["gain"] == "revenue" else GAIN_TEXT[m["gain"]].split(":")[0]
    money = (f"brings {REVENUE_PCT[m['tier']]}% more tax income" if m["money"] == "brings" else
             "pays for itself" if m["money"] == "none" else
             f"costs {COST_PCT[m['tier']]}% of the tax income every month")
    burden = (f"; it weighs on {A.POLICY_TEXT.get(m['burden'], m['burden']).split(':')[0].split(' (')[0]} "
              f"({TIER_WORD[m['btier']]})" if m["burden"] != "none" else "")
    return (f"Standing measure \"{m['name']}\": {what} ({TIER_WORD[m['tier']]}), until revoked - it {money}"
            f"{burden}")


def script(m: dict[str, Any]) -> list[str]:
    """The game's side: clear the slot, then the measure's pieces (permanent modifiers)."""
    slot = m["slot"]
    lines = [f"votc_standing_clear_{slot} = yes"]
    if m.get("action") == "end":
        return lines
    lines.append(f"votc_standing_gain = {{ slot = {slot} area = {m['gain']} tier = {m['tier']} }}")
    if m["money"] == "costs":
        lines.append(f"votc_standing_cost = {{ slot = {slot} tier = {m['tier']} }}")
    if m["burden"] != "none":
        lines.append(f"votc_standing_burden = {{ slot = {slot} area = {m['burden']} tier = {m['btier']} }}")
    return lines


def brief(active: dict[int, dict[str, Any]]) -> str:
    """The measures in force, for the court and the referee."""
    if not active:
        return ""
    return "MEASURES IN FORCE (standing, until the ruler revokes them):\n" + "\n".join(
        f"  - {label(m).removeprefix('Standing measure ')} (since {m.get('date', '?')})"
        for _s, m in sorted(active.items()))
