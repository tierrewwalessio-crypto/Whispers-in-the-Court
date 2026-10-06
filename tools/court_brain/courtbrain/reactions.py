"""How the realm and the world answer what the ruler does.

Every decision is weighed for how grave it is (the referee and the chancery
say so with a "gravity" from 0 to 10). An ordinary act costs what the
catalogue of small consequences says. A grave one - a massacre, a sacrilege,
a betrayal, a tyranny - is judged again here, as the realm would really take
it: every estate, the common people, the council, the army, the Church and the
courts abroad. Nothing here is a fixed recipe: the AI says who reacts and how,
from the realm as it is (how loyal, how strong, how pious, how the estates
stand) and from the difficulty; this module turns that into the game's own
effects, at any strength the game has (up to the heaviest), and rolls the
dice for what is uncertain (a plot against the ruler's life).

What it can bring: the realm's mood (stability, legitimacy, prestige,
government power, war exhaustion), each estate's anger, money and men lost,
risings and civil war, an attempt to kill or depose the ruler, the anger of
foreign courts (outrage, a broken alliance, a cause for war, war itself),
people fleeing or dying, and what comes later (plots, ultimatums, famine,
unrest) as events. It is only ever run when the act is grave: an ordinary
decision costs nothing extra, and nothing is shown.
"""

from __future__ import annotations

import random
from typing import Any

from . import actions as A

GRAVE = 5                     # from here on, the realm and the world are asked how they take it
# A rising, a plot on the ruler's life or throne, a foreign court going to war over it: only for an
# outrage (a massacre, a sacrilege, a betrayal, a tyranny). A bitterly contested deed (5-6) costs the
# realm's mood, never its unity.
OUTRAGE = 7
LEVELS = ("weak", "mild", "severe", "extreme", "radical", "ultimate")
MOODS = {
    "stability": "add_stability = stability_{l}_penalty",
    "legitimacy": "add_legitimacy = legitimacy_{l}_penalty",
    "prestige": "add_prestige = prestige_{l}_penalty",
    "government_power": "add_government_power = government_power_{l}_penalty",
    "army_tradition": "add_army_tradition = army_tradition_{l}_penalty",
    "war_exhaustion": "add_war_exhaustion = war_exhaustion_{l}_bonus",
}
MOOD_TEXT = {"stability": "the realm's stability", "legitimacy": "the ruler's legitimacy", "prestige": "prestige",
             "government_power": "the Crown's grip", "army_tradition": "the army's pride",
             "war_exhaustion": "war-weariness", "estate": "an estate's loyalty", "all_estates": "every estate's loyalty",
             "gold": "the treasury", "manpower": "the realm's manpower"}
MONEY_MULT = {"weak": 1, "mild": 1, "severe": 1, "extreme": 2, "radical": 3, "ultimate": 4}
ABROAD = ("outrage", "horror", "break_alliance", "casus_belli", "declare_war")


def schema() -> dict[str, Any]:
    level = {"type": "string", "enum": list(LEVELS)}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["gravity", "who", "realm", "uprising", "attempt_on_ruler", "abroad", "people", "followups",
                     "summary"],
        "properties": {
            "gravity": {"type": "integer", "minimum": 0, "maximum": 10},
            "who": {"type": "array", "maxItems": 8, "description": "Hidden, English: each group and how it takes it.",
                    "items": {"type": "object", "additionalProperties": False,
                              "required": ["group", "reaction"],
                              "properties": {"group": {"type": "string"}, "reaction": {"type": "string"}}}},
            "realm": {"type": "array", "maxItems": 8, "items": {
                "type": "object", "additionalProperties": False, "required": ["what", "estate", "level"],
                "properties": {
                    "what": {"type": "string", "enum": list(MOODS) + ["estate", "all_estates", "gold", "manpower"]},
                    "estate": {"type": "string", "enum": ["", *A.ESTATE_TYPES]},
                    "level": level}}},
            "uprising": {"type": "object", "additionalProperties": False,
                         "required": ["kind", "estate", "regions", "leader"],
                         "properties": {
                             "kind": {"type": "string", "enum": ["none", "revolt", "civil_war"]},
                             "estate": {"type": "string", "enum": ["", *A.REBEL_ESTATES]},
                             "regions": {"type": "string", "enum": ["", *A.REBEL_REGIONS]},
                             "leader": {"type": "string", "description": "A real person of the court who leads it, "
                                                                          "or ''."}}},
            "attempt_on_ruler": {"type": "object", "additionalProperties": False, "required": ["kind", "by", "chance"],
                                 "properties": {
                                     "kind": {"type": "string", "enum": ["none", "assassination", "deposition"]},
                                     "by": {"type": "string"},
                                     "chance": {"type": "integer", "minimum": 0, "maximum": 90,
                                                "description": "Honest chance it succeeds now."}}},
            "abroad": {"type": "array", "maxItems": 4, "items": {
                "type": "object", "additionalProperties": False, "required": ["tag", "does"],
                "properties": {"tag": {"type": "string", "description": "The country's tag from the papers."},
                               "does": {"type": "string", "enum": list(ABROAD)}}}},
            "people": {"type": "array", "maxItems": 3, "description": "People dying or fleeing because of it.",
                       "items": {"type": "object", "additionalProperties": False,
                                 "required": ["where", "scope", "who", "share"],
                                 "properties": {"where": {"type": "string"},
                                                "scope": {"type": "string",
                                                          "enum": ["location", "province", "area", "region", "realm"]},
                                                "who": {"type": "string", "description": "a pop type, faith or culture, "
                                                                                          "or '' for everyone"},
                                                "share": {"type": "integer", "minimum": 1, "maximum": 3}}}},
            "followups": {"type": "array", "maxItems": 3, "items": {
                "type": "object", "additionalProperties": False, "required": ["after_days", "kind", "who", "about"],
                "properties": {"after_days": {"type": "integer", "minimum": 3, "maximum": 180},
                               "kind": {"type": "string", "enum": ["audience", "story", "chronicle"]},
                               "who": {"type": "string"}, "about": {"type": "string"}}}},
            "summary": {"type": "string", "description": "English, one or two sentences: how the realm and the "
                                                          "world took it."},
        },
    }


TASK = """
HOW DOES THE REALM, AND THE WORLD, TAKE WHAT THE RULER HAS JUST DONE?
You are not the court and not the ruler: you are the realm itself, all of it,
judging a real deed of its master. Be as true as history would be. No fixed
recipe: the most plausible answer for THIS deed, in THIS realm, now.

THE DEED: {deed}

WHAT IS ALREADY APPLIED FOR IT: {applied}

Weigh:
- How grave it is for people of this age, faith and culture (gravity 0-10:
  5 bitterly contested, 7 an outrage, 9 a crime that shakes the realm, 10 an
  atrocity beyond reckoning).
- Everyone, each from their own interest and conscience: the common people,
  the peasants, the burghers, the nobles, the clergy, the council and the
  officials who must carry it out, the army, the Church, the neighbours and
  the great courts - and the victims and their kin. Who obeys, who drags
  their feet, who flees, who rises, who plots, who writes to Rome, who sees
  their chance.
- How strong they are, and how strong the Crown is (THE REALM NOW): a feared,
  loved, well-armed ruler can survive what a weak one cannot; an angry,
  strong estate rises, a broken one grumbles.
- {difficulty} Whatever the difficulty, a deed of gravity 8 or more is never
  cheap: it brings the realm to the edge - risings, a civil war, a blade in
  the dark, foreign outrage - as it really would.
- What is already applied counts: add only what is still missing, sized to
  the deed. The realm's losses go in "realm" (up to "ultimate" for the
  unthinkable); a rising in "uprising"; a plot on the ruler's life or throne
  in "attempt_on_ruler" with its honest chance; foreign courts in "abroad"
  (only real tags from the papers: a co-religionist, a neighbour, the head of
  the faith, a rival who sees the chance); people dying or fleeing in
  "people"; what comes later in "followups" (a plot uncovered, an
  ultimatum, a famine, a flight of families, a bishop's interdict).
- A small or merely unpopular deed: gravity below 5, and leave everything
  else empty.
- Ordinary statecraft is not an outrage: a war on a rival or with a real
  cause, a peace, an alliance, a realm that submits or joins by agreement, a
  tax or a reform. Its price is what war or the measure itself costs, and
  the grumbling of those who pay - gravity below 5. Only a deed that truly
  outrages (gravity 7 and more) brings risings, plots on the ruler, or
  foreign courts taking up arms over it; a contested one (5-6) costs the
  realm's mood and some goodwill abroad, nothing more.

THE REALM NOW:
{realm}

{papers}
""".strip()


def _shift(level: str, by: int) -> str:
    i = LEVELS.index(level) if level in LEVELS else 1
    return LEVELS[max(0, min(len(LEVELS) - 1, i + by))]


def build(data: dict[str, Any], *, snap: Any, world: Any, difficulty: str, known_tag: Any,
          validate_work: Any, cap: int = 10, war_on: frozenset[str] = frozenset()) -> dict[str, Any]:
    """The AI's judgement -> the game's effects. Returns lines (applied with the outcome),
    orders (power moves, queued with it), followups, notes for the panel and a summary.
    cap: the most this deed can weigh (ordinary statecraft is never an outrage, see app);
    war_on: the courts the ruler is going to war with by this very deed."""
    gravity = max(0, min(10, int(data.get("gravity") or 0), cap))
    out: dict[str, Any] = {"gravity": gravity, "lines": [], "orders": [], "followups": [], "notes": [],
                           "summary": str(data.get("summary") or "")[:400], "roll": ""}
    if gravity < GRAVE:
        return out
    # difficulty: lighter on easy, heavier on hard - but never light for an atrocity
    shift = {"easy": -1, "normal": 0, "hard": 1, "very_hard": 1}.get(difficulty, 0)
    if shift < 0 and gravity >= 8:
        shift = 0
    seen = set()
    for r in [x for x in data.get("realm") or [] if isinstance(x, dict)][:8]:
        what, level = str(r.get("what") or ""), _shift(str(r.get("level") or "mild"), shift)
        key = (what, r.get("estate"))
        if key in seen:
            continue
        seen.add(key)
        if what in MOODS:
            out["lines"].append(MOODS[what].format(l=level))
        elif what == "estate" and r.get("estate") in A.ESTATE_TYPES:
            e = r["estate"]
            out["lines"].append(f"if = {{ limit = {{ country_has_estate = estate_type:{e} }} add_estate_satisfaction = "
                                f"{{ type = estate_type:{e} value = estate_satisfaction_{level}_penalty }} }}")
        elif what == "all_estates":
            out["lines"].append(f"add_all_estate_satisfaction = {{ value = estate_satisfaction_{level}_penalty }}")
        elif what in ("gold", "manpower"):
            base = "severe" if LEVELS.index(level) >= 2 else level
            value = f"votc_{what}_{base}"
            effect = "add_gold" if what == "gold" else "add_manpower"
            out["lines"].append(f"{effect} = {{ value = {value} multiply = -{MONEY_MULT[level]} }}")
        else:
            continue
        name = A.ESTATE_TEXT.get(r.get("estate"), "an estate") if what == "estate" else MOOD_TEXT.get(what, what)
        out["notes"].append(f"{name}: {level}")

    up = data.get("uprising") or {}
    if isinstance(up, dict) and up.get("kind") in ("revolt", "civil_war") and gravity >= OUTRAGE:
        move, why = A.validate_power({"kind": up["kind"], "estate": up.get("estate"), "regions": up.get("regions"),
                                      "who": up.get("leader") or ""}, snap)
        if move is None and up.get("leader"):
            move, why = A.validate_power({"kind": up["kind"], "estate": up.get("estate"),
                                          "regions": up.get("regions"), "who": ""}, snap)
        if move is not None:
            out["orders"].append(move)
            out["notes"].append(move["label"])

    at = data.get("attempt_on_ruler") or {}
    ruler = snap.ruler.name if getattr(snap, "ruler", None) and snap.ruler.name else ""
    if isinstance(at, dict) and at.get("kind") in ("assassination", "deposition") and ruler and gravity >= OUTRAGE:
        factor = {"easy": 0.8, "normal": 1.0, "hard": 1.2, "very_hard": 1.4}.get(difficulty, 1.0)
        chance = max(3, min(75, int((int(at.get("chance") or 0)) * factor)))
        hit = random.random() * 100 < chance
        by = str(at.get("by") or "conspirators")[:80]
        if hit:
            kind = "kill" if at["kind"] == "assassination" else "depose"
            raw = {"kind": kind, "who": ruler}
            if kind == "kill":
                raw["reason"] = "assassination"
            move, _why = A.validate_power(raw, snap)
            if move is not None:
                out["orders"].append(move)
                out["notes"].append(f"{'the ruler is murdered' if kind == 'kill' else 'the ruler is deposed'} ({by})")
            out["roll"] = f"the {at['kind']} by {by} SUCCEEDED ({chance}%)"
        else:
            out["followups"].append({"after_days": random.randint(5, 25), "kind": "story", "who": "",
                                     "about": f"a plot against the ruler's {'life' if at['kind'] == 'assassination' else 'throne'} "
                                              f"by {by}, set off by the ruler's deed, is uncovered in time - or almost"})
            out["roll"] = f"the {at['kind']} by {by} failed or was not tried yet ({chance}%)"

    for a in [x for x in data.get("abroad") or [] if isinstance(x, dict)][:4]:
        tag, does = str(a.get("tag") or "").upper().strip(), str(a.get("does") or "")
        if not tag or tag == getattr(snap, "tag", "") or does not in ABROAD or not known_tag(tag):
            continue
        if tag in war_on and does in ("declare_war", "casus_belli", "break_alliance"):
            continue                    # the ruler is going to war with them already
        if does in ("declare_war", "break_alliance") and gravity < OUTRAGE:
            continue                    # nobody takes up arms over a contested deed
        c = f"c:{tag}"
        if does in ("outrage", "horror"):
            mod = "votc_opinion_outrage" if does == "outrage" else "votc_opinion_horror"
            out["lines"].append(f"{c} ?= {{ add_opinion = {{ target = root modifier = {mod} }} }}")
        elif does == "break_alliance":
            out["lines"].append(f"if = {{ limit = {{ exists = {c} is_allied_with = {{ target = {c} }} }} remove_relation = "
                                f"{{ first = this second = {c} type = relation_type:alliance }} }}")
        elif does == "casus_belli":
            out["lines"].append(f"{c} ?= {{ add_casus_belli = {{ target = root type = casus_belli:cb_insulted_us }} }}")
        elif does == "declare_war":
            out["lines"].append(f"if = {{ limit = {{ exists = {c} {c} = {{ NOT = {{ is_at_war_with = root }} "
                                f"NOT = {{ has_truce_with = root }} }} }} {c} = {{ declare_war_with_cb = "
                                f"{{ target = root type = casus_belli:cb_insulted_us }} }} }}")
        out["notes"].append(f"{tag}: {does.replace('_', ' ')}")

    for p in [x for x in data.get("people") or [] if isinstance(x, dict)][:3]:
        # Flight and deaths the deed sets off, beyond what it ordered itself: never more than
        # about 15 in 100 - the killing the ruler ordered is the ruler's own work, counted apart.
        work, _why = validate_work({"kind": "depopulate", "what": p.get("who") or "", "where": p.get("where") or "realm",
                                    "scope": p.get("scope") or "realm", "to": "",
                                    "size": min(2, int(p.get("share") or 1))})
        if work is not None:
            from .works import script
            out["lines"] += script(work)
            out["notes"].append(work["label"])

    for f in [x for x in data.get("followups") or [] if isinstance(x, dict)][:3]:
        out["followups"].append(f)
    return out
