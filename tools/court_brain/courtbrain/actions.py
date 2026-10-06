"""The action vocabulary, mirrored from the mod.

This module is the middleware's half of the contract written in
mod/WhispersInTheCourt/in_game/common/scripted_effects/votc_action_effects.txt
and votc_diplomacy_effects.txt. It does three jobs:

* it builds the JSON schema the model must answer in, so the model cannot
  name an effect that does not exist;
* it re-validates whatever came back, because a schema is a request and not
  a guarantee;
* it renders the survivors into the handful of script lines the run file is
  allowed to contain.

The mod clamps everything a second time. Neither layer trusts the other, and
the game is the one that gets the last word.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

# ----------------------------------------------------------------------
# Vocabulary - every value here exists in the mod's script
# ----------------------------------------------------------------------

TIERS = ("weak", "mild", "severe")
SIGNS = ("bonus", "penalty")
# Below weak: the small change a small thing makes (a narration, a petty grievance). Only on
# these levers, and numbered at the end of the queue (votc_values.txt: *_slight_*).
SLIGHT = "slight"
SLIGHT_KINDS = ("stability", "prestige", "legitimacy", "government_power", "estate")

ESTATE_TYPES = (
    "nobles_estate",
    "clergy_estate",
    "burghers_estate",
    "peasants_estate",
    "tribes_estate",
    "dhimmi_estate",
    "cossacks_estate",
)

EDICTS = (
    "votc_edict_trade_charter",
    "votc_edict_road_ordinance",
    "votc_edict_muster",
    "votc_edict_noble_confirmation",
    "votc_edict_pious_endowment",
    "votc_edict_chancery_reform",
    "votc_edict_kings_justice",
    "votc_edict_patronage_letters",
    "votc_edict_toleration",
    "votc_edict_debasement",
    "votc_edict_granaries",
    "votc_edict_summon_estates",
)

EDICT_NOTES = {
    "votc_edict_trade_charter": "protects merchants; trade and burgher taxes",
    "votc_edict_road_ordinance": "roads, building speed, control",
    "votc_edict_muster": "levies raised faster, morale recovery, army tradition",
    "votc_edict_noble_confirmation": "courts the nobility; their power and levies",
    "votc_edict_pious_endowment": "favours the church; conversion and influence",
    "votc_edict_chancery_reform": "bureaucracy, cabinet and legislative efficiency",
    "votc_edict_kings_justice": "assizes ride out; unrest and separatism fall",
    "votc_edict_patronage_letters": "research, literacy, institution growth",
    "votc_edict_toleration": "tolerance of heathens and heretics; less separatism",
    "votc_edict_debasement": "short money now, inflation and lost production later",
    "votc_edict_granaries": "famine relief at the crown's expense",
    "votc_edict_summon_estates": "parliament support and estate satisfaction",
}

# One lever of the economy or the state, pulled for some years
# (tools/gen_policies.py generates the modifiers behind them).
POLICY_AREAS = {
    "taxation": "how much the crown can tax the estates",
    "production": "output of fields, mines and workshops",
    "trade": "merchants' reach and the pull of the realm's markets",
    "prosperity": "the slow growth or decline of the realm's wealth",
    "currency": "the soundness of the coin (a bonus lowers inflation)",
    "army_upkeep": "what the army costs to keep",
    "navy_upkeep": "what the fleet costs to keep",
    "building_upkeep": "what the realm's buildings cost to keep",
    "construction": "how fast things get built",
    "control": "the crown's grip on its provinces",
    "manpower": "how many men the realm can raise",
    "food": "food produced and stored",
    "morale": "the fighting spirit of the army",
    "discipline": "order in the ranks, drill, obedience",
    "siegecraft": "skill and will in taking and holding strongholds",
    "army_supply": "food, fodder and care that keep the men alive on campaign",
    "army_speed": "how fast the army moves",
    "morale_recovery": "how fast the men find their heart again",
}
# The first eighteen areas each have one numbered consequence per duration, and those
# numbers must never move (a save may hold them in its queue). Every area added since has
# one per strength and sign: the queue gives it its years in a variable (votc_queue_years).
LEGACY_AREAS = tuple(POLICY_AREAS)
POLICY_AREAS.update({
    "conversion": "how fast the realm's people take up the state faith",
    "heretic_conversion": "how fast heretics return to the state faith",
    "tolerance_heretics": "how heretics are treated and how content they are",
    "tolerance_heathens": "how those of other religions are treated and how content they are",
    "state_faith": "the zeal and reach of the state church",
    "assimilation": "how fast other cultures of the realm take up the state culture",
    "cultural_influence": "the pull of the realm's culture and traditions",
    "learning": "schools, schoolmasters, books: the realm's literacy",
    "population_growth": "how fast the realm's people grow in number",
    "social_mobility": "how easily people rise to better trades and ranks",
    "colonization": "settlers sent overseas, the reach and upkeep of colonies",
    "exploration": "how fast expeditions chart unknown lands",
    "diplomacy": "the realm's name abroad and the envoys it can send",
    "espionage": "spies and informers abroad",
    "subject_loyalty": "how loyal vassals and subject states are",
    "sailors": "how many sailors the realm can find",
    "naval_morale": "the fighting spirit of the fleet",
    "shipbuilding": "how fast ships are built",
    "research": "how fast new knowledge and techniques are mastered",
    "institutions": "how fast new institutions (printing, the Renaissance...) take root",
    "integration": "how fast new lands are made part of the realm",
    "separatism": "talk of breaking away (a bonus means less of it)",
    "rebellion": "the growth of rebels (a bonus means fewer)",
    "legitimacy": "the ruler's right to rule, month by month",
    "prestige": "the crown's standing, month by month",
    "cabinet": "how well the cabinet and the ministers work",
    "court_costs": "what the court costs (a bonus means cheaper)",
    "crown_power": "the Crown's weight against the estates",
    "nobles_favour": "how content the nobles are, lastingly",
    "clergy_favour": "how content the clergy is, lastingly",
    "burghers_favour": "how content the burghers are, lastingly",
    "peasants_favour": "how content the commoners are, lastingly",
    "trade_income": "what trade brings into the treasury",
    "recruitment": "how fast levies and regiments are raised",
    "fortifications": "the upkeep and garrisons of the realm's forts",
    "mercenaries": "what hired companies cost",
})
# Added after the great efforts were numbered: numbered after them (never renumber).
LATE_AREAS = ("nobles_taxes", "clergy_taxes", "burghers_taxes", "peasants_taxes")
POLICY_AREAS.update({
    "nobles_taxes": "how much of the nobles' wealth the Crown may take",
    "clergy_taxes": "how much of the church's wealth the Crown may take",
    "burghers_taxes": "how much of the towns' wealth the Crown may take",
    "peasants_taxes": "how much of the commoners' wealth the Crown may take",
})
COMPACT_AREAS = tuple(a for a in POLICY_AREAS if a not in LEGACY_AREAS)
# The same areas by theme, for the prompts: whatever a measure aims at, the lever is here.
AREA_GROUPS = {
    "money and the economy": ("taxation", "production", "trade", "trade_income", "prosperity", "currency",
                              "construction", "building_upkeep", "court_costs", "food"),
    "faith": ("conversion", "heretic_conversion", "tolerance_heretics", "tolerance_heathens", "state_faith"),
    "culture and society": ("assimilation", "cultural_influence", "learning", "population_growth",
                            "social_mobility"),
    "the state and order": ("control", "integration", "separatism", "rebellion", "legitimacy", "prestige",
                            "cabinet", "crown_power"),
    "the estates' lasting favour and what the Crown takes from each": (
        "nobles_favour", "clergy_favour", "burghers_favour", "peasants_favour",
        "nobles_taxes", "clergy_taxes", "burghers_taxes", "peasants_taxes"),
    "knowledge": ("research", "institutions"),
    "colonies and exploration": ("colonization", "exploration"),
    "abroad": ("diplomacy", "espionage", "subject_loyalty"),
    "the army": ("manpower", "recruitment", "army_upkeep", "fortifications", "mercenaries"),
    "the sea": ("sailors", "naval_morale", "shipbuilding", "navy_upkeep"),
    "an army at war (1-2 years)": ("morale", "discipline", "siegecraft", "army_supply", "army_speed",
                                   "morale_recovery"),
}
ARMY_AREAS =("morale", "discipline", "siegecraft", "army_supply", "army_speed", "morale_recovery")
# 1 and 2 years: the length of a campaign, for matters of the army at war.
POLICY_YEARS = ("1", "2", "5", "10", "20")

SOCIETAL_VALUES = (
    "traditionalist_vs_innovative",
    "mysticism_vs_jurisprudence",
    "centralization_vs_decentralization",
    "spiritualist_vs_humanist",
    "belligerent_vs_conciliatory",
    "serfdom_vs_free_subjects",
    "capital_economy_vs_traditional_economy",
    "mercantilism_vs_free_trade",
    "aristocracy_vs_plutocracy",
    "outward_vs_inward",
    "offensive_vs_defensive",
    "land_vs_naval",
    "quality_vs_quantity",
    "individualism_vs_communalism",
)

SOCIETAL_DIRECTIONS = (
    "tiny_move_to_left",
    "tiny_move_to_right",
    "minor_move_to_left",
    "minor_move_to_right",
)

CHARACTER_MODIFIERS = (
    "votc_char_trusted_counsel",
    "votc_char_slighted",
    "votc_char_kings_word",
)

OPINION_MODIFIERS = (
    "votc_opinion_warm_words",
    "votc_opinion_generous_audience",
    "votc_opinion_cold_audience",
    "votc_opinion_insulting_audience",
    "votc_opinion_royal_promise",
    "votc_opinion_broken_promise",
)

# Currencies that only exist for some governments or faiths. Offering them
# to the model unconditionally would produce karma in a Catholic kingdom, so
# the prompt builder filters these by what the realm actually uses.
CONDITIONAL_CURRENCIES = {
    "devotion": "religions with devotion",
    "karma": "religions with karma",
    "horde_unity": "hordes",
    "republican_tradition": "republics",
}


@dataclass(frozen=True)
class Spec:
    effect: str
    params: tuple[str, ...]
    cost_band: str
    summary: str


# kind -> how to render it. The parameter names match the mod's $args$.
ACTIONS: dict[str, Spec] = {
    "stability": Spec("votc_act_stability", ("tier", "sign"), "tier", "the realm steadies or wobbles"),
    "prestige": Spec("votc_act_prestige", ("tier", "sign"), "tier", "the crown's standing"),
    "legitimacy": Spec("votc_act_legitimacy", ("tier", "sign"), "tier", "the ruler's right to rule"),
    "government_power": Spec("votc_act_government_power", ("tier", "sign"), "tier", "the crown's reach"),
    "army_tradition": Spec("votc_act_army_tradition", ("tier", "sign"), "tier", "the army's pride"),
    "war_exhaustion": Spec("votc_act_war_exhaustion", ("tier", "sign"), "tier",
                           "weariness of war (penalty = relief)"),
    "devotion": Spec("votc_act_devotion", ("tier", "sign"), "tier", "piety"),
    "karma": Spec("votc_act_karma", ("tier", "sign"), "tier", "karma"),
    "horde_unity": Spec("votc_act_horde_unity", ("tier", "sign"), "tier", "unity of the horde"),
    "republican_tradition": Spec("votc_act_republican_tradition", ("tier", "sign"), "tier",
                                 "republican tradition"),
    "gold_gain": Spec("votc_act_gold_gain", ("tier",), "tier", "money into the treasury"),
    "gold_loss": Spec("votc_act_gold_loss", ("tier",), "tier", "money out of the treasury"),
    "manpower_gain": Spec("votc_act_manpower_gain", ("tier",), "tier", "recruits found"),
    "manpower_loss": Spec("votc_act_manpower_loss", ("tier",), "tier", "recruits lost"),
    "estate": Spec("votc_act_estate", ("estate", "tier", "sign"), "tier",
                   "one order of the realm is pleased or slighted"),
    "all_estates": Spec("votc_act_all_estates", ("sign",), "mild", "the whole realm's mood"),
    "edict_short": Spec("votc_act_edict_short", ("edict",), "edict", "a five year edict"),
    "edict_long": Spec("votc_act_edict_long", ("edict",), "edict", "a fifteen year edict"),
    "policy_bonus": Spec("votc_act_policy", ("area", "tier", "years"), "policy",
                         "a lasting GAIN in one area of the economy or the state (see POLICIES)"),
    "policy_penalty": Spec("votc_act_policy", ("area", "tier", "years"), "policy",
                           "a lasting COST in one area of the economy or the state (see POLICIES)"),
    "societal": Spec("votc_act_societal", ("type", "direction"), "societal",
                     "the realm drifts on one of its values"),
    "character_modifier": Spec("votc_act_char_modifier", ("modifier",), "char_modifier",
                               "the person you spoke to is marked by it"),
    "opinion": Spec("votc_act_opinion", ("modifier",), "opinion",
                    "how the foreign court now regards you"),
    "trust_gain": Spec("votc_act_trust_gain", (), "opinion", "they trust you more"),
    "trust_loss": Spec("votc_act_trust_loss", (), "opinion", "they trust you less"),
    "favors": Spec("votc_act_favors_gain", (), "opinion", "they owe you favours"),
    "gift_gold": Spec("votc_act_gift_gold", (), "gift", "a purse sent to their court"),
    "rival_declare": Spec("votc_act_rival_declare", (), "rival", "name them a rival"),
    "rival_drop": Spec("votc_act_rival_drop", (), "rival", "stop treating them as a rival"),
    "local_control": Spec("votc_act_local_control", ("tier", "sign"), "tier",
                          "the crown's grip on the place you are standing in"),
    "local_prosperity": Spec("votc_act_local_prosperity", ("tier", "sign"), "tier",
                             "the fortunes of the place you are standing in"),
    "progress_acclaimed": Spec("votc_act_progress_acclaimed", (), "mild",
                               "the realm remembers the visit warmly"),
    "progress_resented": Spec("votc_act_progress_resented", (), "weak",
                              "the realm remembers the visit badly"),
    "nothing": Spec("votc_act_nothing", (), "none", "nothing changes but the mood"),
    "great_effort": Spec("votc_act_great_effort", ("domain",), "none",
                         "RARE - the whole realm rises to an exceptional effort (see GREAT EFFORTS)"),
}

# Great efforts: once in a long while, beyond what a realm usually musters (see GREAT_RULES).
GREAT_DOMAINS = {
    "army": "every levy raised at once and larger than the orders owe, for two years (they go home when "
            "disbanded); the fields lack hands and the peasants suffer",
    "treasury": "gifts, loans and pledges pour into the chest: twice a large sum; nobles and burghers remember it",
    "works": "the whole realm builds as one for two years, at the Crown's cost and the villages' labour",
    "faith": "a great revival for two years: conversion and the church's reach soar, tolerance of others falls",
}
GREAT_RULES = """
GREAT EFFORTS ("great_effort", domain army | treasury | works | faith) - rare
Once in a long while a realm does more than it usually can: raises armies beyond
its usual levies, fills its chest from its people's pledges, builds as one, or is
swept by a revival. Only when ALL of these hold:
- the moment is truly exceptional: the realm's survival, an invasion, a holy
  war, a great cause the people believe in - never an ordinary war, never
  because the ruler simply wants more;
- the RULER calls for it in plain words;
- the realm is behind its ruler: see EXCEPTIONAL EFFORT in THE CHARACTER OF
  THIS REALM. If it says the realm cannot rise to it now, it does not happen,
  however the ruler insists: say why in the story (the lords stay home, the
  towns keep their purses shut).
It is great but not endless: it lasts two years, it has its price, and a realm
can manage it once in ten years at most. The size of it is the realm's own - a
county rising in arms is still a county.
""".strip()
# Set by Court Brain: "" when the realm could rise to a great effort now, else why not.
GREAT_GATE: Any = None

ENUMS: dict[str, tuple[str, ...]] = {
    "tier": TIERS,
    "sign": SIGNS,
    "estate": ESTATE_TYPES,
    "edict": EDICTS,
    "type": SOCIETAL_VALUES,
    "direction": SOCIETAL_DIRECTIONS,
    "modifier": CHARACTER_MODIFIERS + OPINION_MODIFIERS,
    "area": tuple(POLICY_AREAS),
    "years": POLICY_YEARS,
    "domain": tuple(GREAT_DOMAINS),
}

# Earned only by a decree that truly works (see app._decree): a great advantage on any
# lever, and a lasting income for the Crown in bands by the realm's size. EARNED is set
# while such a decree is weighed - what it allows, and how much of it is left - and is
# None everywhere else, so nothing else can reach them.
GRAND = "grand"
INCOME_BANDS = (0.5, 1, 2, 4, 8, 15, 30, 60)          # tools/gen_policies.py
INCOME_AREAS = tuple(f"income{n}" for n in range(1, len(INCOME_BANDS) + 1))
EARNED_TIERS = ("weak", "mild", "severe", "grand")
EARNED_YEARS = ("5", "10", "20")
EARNED: dict[str, Any] | None = None


def income_band(taxbase: float) -> int:
    """The band whose small income is about 4% of the realm's monthly tax base."""
    want = max(0.5, taxbase * 0.04)
    return min(range(len(INCOME_BANDS)), key=lambda i: abs(math.log(INCOME_BANDS[i] / want))) + 1


COSTS = {
    "slight": 2, "weak": 5, "mild": 12, "severe": 30,
    "edict": 25, "privilege": 35, "opinion": 10, "societal": 20, "char_modifier": 15,
    "war": 60, "peace": 45, "pact": 40, "subjugate": 100,
    "cb": 25, "truce": 20, "gift": 15, "rival": 10,
    "none": 0,
    "policy_bonus_weak": 10, "policy_bonus_mild": 20, "policy_bonus_severe": 35, "policy_bonus_grand": 50,
    "policy_penalty_weak": 5, "policy_penalty_mild": 5, "policy_penalty_severe": 5,
}

# ----------------------------------------------------------------------
# Staged outcomes - the ones the player has to click
# ----------------------------------------------------------------------

STAGE_KINDS = {
    "none": 0,
    "declare_war": 1,
    "white_peace": 2,
    "form_alliance": 3,
    "break_alliance": 4,
    "guarantee": 5,
    "grant_military_access": 6,
    "press_claim": 7,
    "swear_truce": 8,
    "take_submission": 9,
    "accept_vassalage": 10,
    "union": 11,
}

STAGE_NOTES = {
    "declare_war": "war is declared on them, using the casus belli you name",
    "white_peace": "every war against them ends, with a five year truce",
    "form_alliance": "a formal alliance is sworn",
    "break_alliance": "the alliance is broken - they will remember it",
    "guarantee": "their independence is guaranteed",
    "grant_military_access": "their armies may cross your land",
    "press_claim": "you gain a casus belli against them, to use or not",
    "swear_truce": "a five year mutual truce",
    "take_submission": "they become your vassal - only from a war you are winning",
    "accept_vassalage": ("they AGREE to come under your crown as a vassal, keeping their own government "
                         "and laws - by treaty, without war; a weaker realm that seeks your protection or "
                         "is won over (a free town's submission, a lordship buying safety)"),
    "union": ("they AGREE to become part of your realm outright: their lands become yours and their state "
              "ends - by treaty, without war; only a much smaller realm that truly consents"),
}

STAGE_COST = {
    "none": "none", "declare_war": "war", "white_peace": "peace",
    "form_alliance": "pact", "break_alliance": "pact", "guarantee": "pact",
    "grant_military_access": "pact", "press_claim": "cb", "swear_truce": "truce",
    "take_submission": "subjugate",
    "accept_vassalage": "subjugate", "union": "subjugate",
}

CASUS_BELLI = {
    "cb_war_from_event": 1,
    "cb_insulted_us": 2,
    "cb_claim_throne": 3,
    "cb_humiliate": 4,
    "cb_religious_conformance": 5,
    "cb_trade_conflict": 6,
    "cb_border_war": 7,
    "cb_subjugation": 8,
}

CASUS_BELLI_NOTES = {
    "cb_war_from_event": "a quarrel that got out of hand - the neutral choice",
    "cb_insulted_us": "honour; they slighted you",
    "cb_claim_throne": "a dynastic claim on their throne",
    "cb_humiliate": "to shame them, nothing more",
    "cb_religious_conformance": "faith",
    "cb_trade_conflict": "trade and the sea",
    "cb_border_war": "a disputed border",
    "cb_subjugation": "to make them bend the knee",
}


# ----------------------------------------------------------------------
# Validation and rendering
# ----------------------------------------------------------------------

def _token_ok(value: Any) -> bool:
    """Paradox script tokens only. Anything else never reaches the run file."""
    if not isinstance(value, str) or not value:
        return False
    return all(c.isalnum() or c == "_" for c in value)


def validate_action(obj: Any) -> tuple[dict[str, Any] | None, str]:
    """Returns (clean action, reason it was rejected)."""
    if not isinstance(obj, dict):
        return None, "not an object"
    kind = obj.get("kind")
    if kind not in ACTIONS:
        return None, f"unknown action {kind!r}"
    spec = ACTIONS[kind]
    clean: dict[str, Any] = {"kind": kind}
    if spec.effect == "votc_act_policy" and (obj.get("tier") == GRAND or obj.get("area") == "income"):
        return _validate_earned(kind, obj)
    if obj.get("tier") == SLIGHT:
        if kind in SLIGHT_KINDS:
            return _validate_slight(kind, obj)
        obj = {**obj, "tier": "weak"}               # the smallest this lever has
    if spec.effect == "votc_act_policy":
        obj = dict(obj)
        if obj.get("tier") not in TIERS:
            obj["tier"] = "weak"
        if obj.get("area") in ARMY_AREAS:
            # the state of an army on campaign: it lasts a campaign, not a decade
            obj["years"] = obj.get("years") if obj.get("years") in ("1", "2") else "1"
        elif obj.get("years") not in POLICY_YEARS:
            obj["years"] = "10"
    for param in spec.params:
        value = obj.get(param)
        if not _token_ok(value):
            return None, f"{kind}: {param} is missing or not a plain token"
        allowed = ENUMS.get(param)
        if allowed and value not in allowed:
            return None, f"{kind}: {param}={value!r} is not in the catalogue"
        clean[param] = value
    # A character modifier must be a character modifier, not an opinion one.
    if kind == "character_modifier" and clean["modifier"] not in CHARACTER_MODIFIERS:
        return None, "character_modifier: that modifier belongs to a foreign court"
    if kind == "opinion" and clean["modifier"] not in OPINION_MODIFIERS:
        return None, "opinion: that modifier belongs to a person, not a court"
    if kind == "great_effort":
        why = GREAT_GATE() if callable(GREAT_GATE) else "the realm's state is not known"
        if why:
            return None, f"a great effort is beyond the realm now: {why}"
    return clean, ""


def _validate_slight(kind: str, obj: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    clean, why = validate_action({**obj, "tier": "weak"})
    return ({**clean, "tier": SLIGHT}, "") if clean else (None, why)


def _validate_earned(kind: str, obj: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """A great advantage or a lasting income: only what the decree earned, never more."""
    earned = EARNED or {}
    area, tier = obj.get("area"), obj.get("tier")
    if kind != "policy_bonus":
        if area == "income":
            return None, "a lasting income is always a gain"
        return validate_action({**obj, "tier": "severe"})      # no great tier for a price
    if area == "income":
        top = earned.get("income")
        if not top or earned.get("income_left", 0) <= 0:
            return None, "a lasting income comes only from a decree that truly works"
        if tier not in EARNED_TIERS:
            tier = "weak"
        tier = EARNED_TIERS[min(EARNED_TIERS.index(tier), EARNED_TIERS.index(top))]
        earned["income_left"] -= 1
        area = f"income{earned.get('band', 1)}"
    else:
        if area not in POLICY_AREAS:
            return None, f"policy_bonus: area={area!r} is not in the catalogue"
        if not earned.get("grand") or earned.get("grand_left", 0) <= 0:
            # more than the decree earned: the largest ordinary gain
            return validate_action({**obj, "tier": "severe"})
        earned["grand_left"] -= 1
    if area in ARMY_AREAS:
        years = obj.get("years") if obj.get("years") in ("1", "2") else "2"
    else:
        years = obj.get("years") if obj.get("years") in EARNED_YEARS else "10"
    return {"kind": kind, "area": area, "tier": tier, "years": years}, ""


def render_action(action: dict[str, Any]) -> str:
    spec = ACTIONS[action["kind"]]
    if spec.effect == "votc_act_policy":
        sign = "bonus" if action["kind"] == "policy_bonus" else "penalty"
        return (f"votc_act_policy = {{ area = {action['area']} sign = {sign} "
                f"tier = {action['tier']} years = {action['years']} }}")
    if not spec.params:
        return f"{spec.effect} = yes"
    args = " ".join(f"{p} = {action[p]}" for p in spec.params)
    return f"{spec.effect} = {{ {args} }}"


def action_cost(action: dict[str, Any]) -> int:
    spec = ACTIONS[action["kind"]]
    if spec.cost_band == "policy":
        sign = "bonus" if action["kind"] == "policy_bonus" else "penalty"
        return COSTS.get(f"policy_{sign}_{action.get('tier')}", 0)
    band = action.get("tier", spec.cost_band) if spec.cost_band == "tier" else spec.cost_band
    return COSTS.get(band, 0)


def validate_stage(obj: Any) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(obj, dict):
        return None, "not an object"
    kind = obj.get("kind")
    if kind not in STAGE_KINDS:
        return None, f"unknown outcome {kind!r}"
    if kind == "none":
        return None, ""
    try:
        slot = int(obj.get("slot", 0))
    except (TypeError, ValueError):
        return None, "slot is not a number"
    if slot not in (1, 2, 3, 4):
        return None, f"slot {slot} is outside 1-4"
    cb = obj.get("casus_belli") or "cb_war_from_event"
    if cb not in CASUS_BELLI:
        return None, f"unknown casus belli {cb!r}"
    return {"slot": slot, "kind": kind, "casus_belli": cb}, ""


def render_stage(stage: dict[str, Any]) -> str:
    kind_id = STAGE_KINDS[stage["kind"]]
    cb_id = CASUS_BELLI[stage["casus_belli"]]
    return f"votc_stage = {{ slot = {stage['slot']} kind = {kind_id} cb = {cb_id} }}"


def stage_cost(stage: dict[str, Any]) -> int:
    return COSTS.get(STAGE_COST.get(stage["kind"], "none"), 0)


# ----------------------------------------------------------------------
# The JSON schema handed to the model
# ----------------------------------------------------------------------

def action_schema(earned: bool = False) -> dict[str, Any]:
    """One object shape covering every action.

    Strict json_schema mode requires every declared property to be listed as
    required, so optional parameters are modelled as "" and dropped during
    validation rather than omitted. `earned` (decrees only) adds the great tier and
    the lasting income, which the decree may earn.
    """
    if earned:
        schema = action_schema()
        props = schema["properties"]
        props["tier"] = {"type": "string", "enum": ["", SLIGHT, *TIERS, GRAND]}
        props["area"] = {"type": "string", "enum": ["", *POLICY_AREAS, "income"]}
        return schema
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "tier", "sign", "estate", "edict", "type", "direction", "modifier",
                     "area", "years", "domain"],
        "properties": {
            "kind": {"type": "string", "enum": list(ACTIONS.keys())},
            "tier": {"type": "string", "enum": ["", SLIGHT, *TIERS]},
            "sign": {"type": "string", "enum": ["", *SIGNS]},
            "estate": {"type": "string", "enum": ["", *ESTATE_TYPES]},
            "edict": {"type": "string", "enum": ["", *EDICTS]},
            "type": {"type": "string", "enum": ["", *SOCIETAL_VALUES]},
            "direction": {"type": "string", "enum": ["", *SOCIETAL_DIRECTIONS]},
            "modifier": {
                "type": "string",
                "enum": ["", *CHARACTER_MODIFIERS, *OPINION_MODIFIERS],
            },
            "area": {"type": "string", "enum": ["", *POLICY_AREAS]},
            "years": {"type": "string", "enum": ["", *POLICY_YEARS]},
            "domain": {"type": "string", "enum": ["", *GREAT_DOMAINS]},
        },
    }


def stage_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["slot", "kind", "casus_belli"],
        "properties": {
            "slot": {
                "type": "integer",
                "minimum": 0,
                "maximum": 4,
                "description": (
                    "Which option this outcome hangs on, counting the options you "
                    "listed from 1: 1 is the first option, 2 the second, and so on. "
                    "Use 0 only together with kind \"none\"."
                ),
            },
            "kind": {"type": "string", "enum": list(STAGE_KINDS.keys())},
            "casus_belli": {"type": "string", "enum": list(CASUS_BELLI.keys())},
        },
    }


def catalogue_text(*, currencies: set[str] | None = None, diplomatic: bool = False) -> str:
    """The catalogue, written out for the system prompt.

    Only the parts that apply are shown: a Catholic kingdom is never told
    about karma, and a court conversation is never told how to declare war.
    """
    lines: list[str] = ["ACTIONS (kind -> what it does):"]
    for kind, spec in ACTIONS.items():
        if kind in CONDITIONAL_CURRENCIES and (currencies is None or kind not in currencies):
            continue
        if not diplomatic and kind in (
            "opinion", "trust_gain", "trust_loss", "favors", "gift_gold",
            "rival_declare", "rival_drop",
        ):
            continue
        params = ", ".join(spec.params) if spec.params else "no parameters"
        lines.append(f"  {kind} ({params}): {spec.summary}")
    lines.append("")
    lines.append("tier: slight | weak | mild | severe   (slight: a petty matter, a page of chronicle - only on")
    lines.append("      stability, prestige, legitimacy, government_power and estate; severe is rare and expensive)")
    lines.append("sign: bonus | penalty")
    lines.append("")
    lines.append("EDICTS:")
    for edict in EDICTS:
        lines.append(f"  {edict}: {EDICT_NOTES[edict]}")
    lines.append("")
    lines.append("GREAT EFFORT DOMAINS (kind great_effort):")
    for domain, note in GREAT_DOMAINS.items():
        lines.append(f"  {domain}: {note}")
    lines.append(GREAT_RULES)
    lines.append("")
    lines.append("POLICIES (kinds policy_bonus / policy_penalty: area + tier + years 1|2|5|10|20). How a measure")
    lines.append("of ANY kind really changes the game - taxes and tolls, but also faith, culture, schools,")
    lines.append("colonies, diplomacy, the fleet, order, the estates. Whatever the measure aims at, put a")
    lines.append("policy_bonus on THAT area; where it really bites, ONE price that fits (a policy_penalty, gold,")
    lines.append("stability or an estate) - for a sound measure smaller than its gain. The army-at-war areas last")
    lines.append("1 or 2 years - a campaign.")
    for group, areas in AREA_GROUPS.items():
        lines.append(f"  {group}: " + "; ".join(f"{a} ({POLICY_AREAS[a]})" for a in areas))
    if diplomatic:
        lines.append("")
        lines.append("OUTCOMES THAT NEED THE PLAYER'S CLICK (staged against an option slot):")
        for kind, note in STAGE_NOTES.items():
            lines.append(f"  {kind}: {note}")
        lines.append("")
        lines.append("CASUS BELLI (for declare_war and press_claim):")
        for cb, note in CASUS_BELLI_NOTES.items():
            lines.append(f"  {cb}: {note}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Offers: heavy outcomes the player confirms in the side panel
# ----------------------------------------------------------------------

# The ruler's orders (mod: votc_order_effects.txt). No court influence, but
# every order that takes effect costs the realm what the real act would.
OFFER_EFFECTS = {
    "declare_war": "votc_order_war = {{ cb = {cb} }}",
    "white_peace": "votc_order_peace = yes",
    "form_alliance": "votc_order_alliance = yes",
    "break_alliance": "votc_order_break_alliance = yes",
    "guarantee": "votc_order_guarantee = yes",
    "grant_military_access": "votc_order_access = yes",
    "press_claim": "votc_order_claim = {{ cb = {cb} }}",
    "swear_truce": "votc_order_truce = yes",
    "take_submission": "votc_order_submission = yes",
}
# Orders added later: numbered at the end of the queue (never renumber).
OFFER_EFFECTS_LATE = {
    "accept_vassalage": "votc_order_vassalage = yes",
    "union": "votc_order_union = yes",
}
ALL_OFFERS = {**OFFER_EFFECTS, **OFFER_EFFECTS_LATE}

# What each order costs the realm when it takes effect, for the panel.
ORDER_COST_TEXT = {
    "declare_war": "a little stability and prestige (none against a rival; more without a real cause)",
    "white_peace": "prestige",
    "form_alliance": "government power",
    "break_alliance": "prestige, stability and that court's trust",
    "guarantee": "government power",
    "grant_military_access": "prestige",
    "press_claim": "government power",
    "swear_truce": "prestige",
    "take_submission": "a little government power - and the realm takes pride in it (prestige, stability)",
    "accept_vassalage": "a little government power - and the realm takes pride in it (prestige, stability)",
    "union": "a little government power - and the realm takes pride in it (prestige, stability)",
    "change_government": "50 stability and 25 legitimacy, as in the game (and 20 years before another change)",
    "change_rank": "government power, in exchange for prestige",
}

# Why an order cannot be given, for the player.
REFUSAL_TEXT = {
    "declare_war": "you are allied, already at war or under a truce with them",
    "white_peace": "you are not at war with them",
    "form_alliance": "you are already allied or at war with them",
    "break_alliance": "you are not allied with them",
    "guarantee": "you are at war with them",
    "grant_military_access": "you are at war with them",
    "swear_truce": "you are at war with them or a truce is already in force",
    "take_submission": "submission only comes out of a war you are winning",
    "accept_vassalage": "you are at war with them: submission now comes only with the peace",
    "union": "you are at war with them: submission now comes only with the peace",
}

def validate_offer(obj: Any, *, allied: bool, at_war: bool, truce: bool,
                   budget: int) -> tuple[dict[str, Any] | None, str]:
    """Filter offers the situation cannot support before they become buttons.

    The mod re-checks all of this when the effect runs; doing it here as well
    means the player is never shown a button that would only refuse itself.
    """
    if not isinstance(obj, dict):
        return None, "not an object"
    kind = obj.get("kind")
    if kind not in ALL_OFFERS:
        return None, f"unknown outcome {kind!r}"
    cb = obj.get("casus_belli") or "cb_war_from_event"
    if cb not in CASUS_BELLI:
        cb = "cb_war_from_event"
    label = str(obj.get("label") or "").strip()[:90] or STAGE_NOTES.get(kind, kind)
    rules = {
        "declare_war": (not allied and not at_war and not truce, "allied, already at war, or under truce"),
        "white_peace": (at_war, "not at war with them"),
        "form_alliance": (not allied and not at_war, "already allied or at war"),
        "break_alliance": (allied, "not allied"),
        "guarantee": (not at_war, "at war with them"),
        "grant_military_access": (not at_war, "at war with them"),
        "swear_truce": (not at_war and not truce, "at war or already under truce"),
        "take_submission": (at_war, "submission only comes out of a war"),
        "accept_vassalage": (not at_war, "at war with them: submission comes only with the peace"),
        "union": (not at_war, "at war with them: submission comes only with the peace"),
        "press_claim": (True, ""),
    }
    ok, why = rules.get(kind, (True, ""))
    if not ok:
        return None, f"{kind}: {why}"
    cost = COSTS.get(STAGE_COST.get(kind, "none"), 0)
    if cost > budget:
        return None, f"{kind}: costs {cost}, only {budget} influence left"
    return {"kind": kind, "casus_belli": cb, "label": label, "cost": cost}, ""


def render_offer(offer: dict[str, Any]) -> str:
    return ALL_OFFERS[offer["kind"]].format(cb=CASUS_BELLI[offer["casus_belli"]])


def render_orders(offers: list[dict[str, Any]]) -> list[str]:
    """The ruler's own decisions: no court influence, a real cost, the engine's checks."""
    return [render_offer(o) for o in offers]


# ----------------------------------------------------------------------
# The queue: consequences wait inside the game until the player confirms
# ----------------------------------------------------------------------
#
# Nothing is applied behind the player's back any more. The run file only
# PARKS each consequence in a numbered slot (votc_queue); the "Cosi sia"
# option of the outcome event applies them (votc_apply_queue), so the game's
# own tooltip on that button lists exactly what changes - numbers, modifiers
# and their duration. tools/gen_queue.py writes the mod side from variants()
# below, so both sides always agree on the numbering. Regenerate the mod
# (python tools/gen_queue.py) whenever the catalogue changes.

QUEUE_SLOTS = 6

# Changes to the state itself (mod: votc_institution_effects.txt).
NAME_SLOTS = 100
GOVERNMENTS = ("monarchy", "republic", "theocracy", "tribe", "steppe_horde")
RANKS = ("rank_county", "rank_duchy", "rank_kingdom", "rank_empire")
GOVERNMENT_TEXT = {"monarchy": "monarchy", "republic": "republic", "theocracy": "theocracy",
                   "tribe": "tribe", "steppe_horde": "steppe horde"}
RANK_TEXT = {"rank_county": "county", "rank_duchy": "duchy", "rank_kingdom": "kingdom", "rank_empire": "empire"}
INSTITUTION_KINDS = ("rename_country", "change_government", "change_rank")

# Power over people and over the realm's unity.
KILL_REASONS = ("execution", "assassination", "fever", "vanished")
REBEL_REGIONS = ("far", "outer", "foreign_culture", "other_faith", "low_control")
REBEL_ESTATES = ("nobles_estate", "clergy_estate", "burghers_estate", "peasants_estate")
POWER_KINDS = ("kill", "depose", "crown", "heir", "regent", "to_cabinet", "from_cabinet", "exile",
               "revolt", "civil_war", "civil_war_join")
# Moves that overturn the realm: never out of nowhere (see app._crisis).
UPHEAVAL_KINDS = ("revolt", "civil_war", "civil_war_join", "depose", "crown")
REGION_TEXT = {
    "far": "in the regions far from the capital",
    "outer": "in the lands outside the capital's area",
    "foreign_culture": "in the lands of another culture",
    "other_faith": "in the lands of another faith",
    "low_control": "in the lands where the Crown holds little control",
}
KILL_TEXT = {"execution": "executed", "assassination": "assassinated", "fever": "dead of a fever",
             "vanished": "vanished"}


def power_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "who", "reason", "estate", "regions", "label"],
        "properties": {
            "kind": {"type": "string", "enum": list(POWER_KINDS)},
            "who": {"type": "string", "description": ("Exact name of a person in the context (the ruler, the "
                                                      "heir, someone of the court, the person spoken to). For a "
                                                      "revolt or civil war: its leader, or empty.")},
            "reason": {"type": "string", "enum": ["", *KILL_REASONS]},
            "estate": {"type": "string", "enum": ["", *REBEL_ESTATES]},
            "regions": {"type": "string", "enum": ["", *REBEL_REGIONS]},
            "label": {"type": "string", "description": "Short button text, max 70 characters."},
        },
    }


def validate_power(obj: Any, snap: Any) -> tuple[dict[str, Any] | None, str]:
    """Check a power move against the people the game actually reported."""
    if not isinstance(obj, dict) or obj.get("kind") not in POWER_KINDS:
        return None, "not a power move"
    kind = obj["kind"]
    people = [p for p in [snap.ruler, snap.heir, snap.target_person, *snap.court] if p and p.slot]
    who = str(obj.get("who") or "").strip().lower()
    person = next((p for p in people if p.name.lower() == who), None)
    if person is None and who:
        person = next((p for p in people if who in p.name.lower() or p.name.lower() in who), None)
    out: dict[str, Any] = {"kind": kind, "casus_belli": "cb_war_from_event", "cost": 0}
    if kind in ("revolt", "civil_war", "civil_war_join"):
        if obj.get("estate") not in REBEL_ESTATES or obj.get("regions") not in REBEL_REGIONS:
            return None, f"{kind}: estate and regions are needed"
        out.update(estate=obj["estate"], regions=obj["regions"])
        # A rising against the crown is never led by the one who wears it.
        if person is not None and not person.is_ruler:
            out.update(who=person.name, who_slot=person.slot)
        elif kind == "civil_war_join":
            return None, "civil_war_join: the rebels need a leader other than the ruler"
    else:
        if person is None:
            return None, f"{kind}: nobody called {obj.get('who')!r} is at court"
        if kind == "depose" and not person.is_ruler:
            return None, "depose: that person does not rule"
        if kind in ("crown", "heir", "regent", "exile") and person.is_ruler:
            return None, f"{kind}: that person already rules"
        if kind == "kill":
            out["reason"] = obj.get("reason") if obj.get("reason") in KILL_REASONS else "execution"
        out.update(who=person.name, who_slot=person.slot)
    out["label"] = str(obj.get("label") or "").strip()[:80] or describe_power(out)
    return out, ""


def power_variant(move: dict[str, Any]) -> int:
    kind = move["kind"]
    if kind == "kill":
        key = ("power", "kill", move.get("reason", "execution"))
    elif kind in ("revolt", "civil_war", "civil_war_join"):
        key = ("power", kind, f"{move['regions']}:{move['estate']}")
    else:
        key = ("power", kind, "")
    return _ids().get(key, 0)


def describe_power(move: dict[str, Any]) -> str:
    k, who = move["kind"], move.get("who", "")
    if k == "kill":
        return f"☠ {who}: {KILL_TEXT.get(move.get('reason', ''), 'killed')}"
    if k == "civil_war_join":
        lead = f", led by {who}" if who else ""
        return (f"⚔ Civil war: the {ESTATE_TEXT.get(move['estate'], move['estate'])} take up arms "
                f"{REGION_TEXT.get(move['regions'], move['regions'])}{lead}, against the Crown. "
                f"You leave the Crown and take command of the rebel side: the ruler remains your enemy.")
    if k in ("revolt", "civil_war"):
        what = "Civil war" if k == "civil_war" else "A revolt brewing"
        lead = f", led by {who}" if who else ""
        return (f"⚔ {what}: the {ESTATE_TEXT.get(move['estate'], move['estate'])} take up arms "
                f"{REGION_TEXT.get(move['regions'], move['regions'])}{lead}")
    return {
        "depose": f"♛ {who} is deposed",
        "crown": f"♛ {who} takes the throne",
        "heir": f"♛ {who} becomes designated heir",
        "regent": f"♛ {who} becomes regent",
        "to_cabinet": f"✦ {who} joins the cabinet",
        "from_cabinet": f"✦ {who} leaves the cabinet",
        "exile": f"✦ {who} is exiled",
    }.get(k, k)

def institution_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "name", "adjective", "government", "rank", "label"],
        "properties": {
            "kind": {"type": "string", "enum": list(INSTITUTION_KINDS)},
            "name": {"type": "string", "description": ("rename_country: the bare name, WITHOUT any title of "
                                                       "rank or government - 'Trinacria', not 'Kingdom of "
                                                       "Trinacria': the game adds 'Kingdom of...' itself.")},
            "adjective": {"type": "string", "description": "rename_country: the adjective (e.g. 'Trinacrian')."},
            "government": {"type": "string", "enum": ["", *GOVERNMENTS]},
            "rank": {"type": "string", "enum": ["", *RANKS]},
            "label": {"type": "string", "description": "The button, max 70 characters."},
        },
    }


def validate_institution(obj: Any, *, current_name: str = "") -> tuple[dict[str, Any] | None, str]:
    if not isinstance(obj, dict) or obj.get("kind") not in INSTITUTION_KINDS:
        return None, "not a change of the state"
    kind = obj["kind"]
    out: dict[str, Any] = {"kind": kind, "casus_belli": "cb_war_from_event", "cost": 0}
    if kind == "rename_country":
        name = " ".join(str(obj.get("name") or "").split())
        # The game puts the title of rank and government in front by itself.
        name = re.sub(r"^(?:(?:Sacro\s+)?(?:Regno|Ducato|Granducato|Contea|Principato|Marca|Impero|Repubblica|"
                      r"Serenissima\s+Repubblica|Signoria|Sultanato|Califfato|Khanato|Emirato)\s+(?:di|d'|del|della|dei)\s*|"
                      r"(?:Holy\s+)?(?:Kingdom|Duchy|Grand\s+Duchy|County|Principality|March|Empire|Republic|"
                      r"Sultanate|Caliphate|Khanate|Emirate)\s+of\s+)", "", name, flags=re.I)[:40]
        adj = " ".join(str(obj.get("adjective") or "").split())[:30]
        if len(name) < 3 or name.lower() == current_name.strip().lower():
            return None, "rename_country: no new name"
        out.update(name=name, adjective=adj or name)
        default = f"The realm takes the name {name}"
    elif kind == "change_government":
        if obj.get("government") not in GOVERNMENTS:
            return None, "change_government: unknown form"
        out["government"] = obj["government"]
        default = f"The state becomes a {GOVERNMENT_TEXT[obj['government']]}"
    else:
        if obj.get("rank") not in RANKS:
            return None, "change_rank: unknown rank"
        out["rank"] = obj["rank"]
        default = f"Proclaim the state a {RANK_TEXT[obj['rank']]}"
    out["label"] = str(obj.get("label") or "").strip()[:80] or default
    return out, ""
# What another court may do about a pact with the ruler (the partner's own
# decision, applied with whatever option the ruler picks).
PACT_MOVES = ("join_war", "declare_war", "break_alliance", "goodwill", "alliance", "make_peace", "casus_belli",
              "pays")
# ...and what the ruler may do to keep their own side (a choice of the event).
RULER_PACT_MOVES = ("ruler_join_war",)


PACT_MOVE_TEXT = {
    "join_war": "{party} joins your war against {enemy} (their own decision)",
    "declare_war": "{party} declares war on you (their own decision)",
    "break_alliance": "{party} breaks its alliance with you (their own decision)",
    "goodwill": "{party} thinks better of you: opinion and trust rise",
    "alliance": "{party} allies with you (their own decision)",
    "make_peace": "{party} makes peace with you, with a truce (their own decision)",
    "casus_belli": "You gain a just cause for war against {party}",
    "pays": "{party} pays what it owed: gold for your treasury",
    "ruler_join_war": "You join {party}'s war against {enemy}",
}


def pact_variant(move: str) -> int:
    return _ids().get(("pact", move, ""), 0)


# Story events (votc.500): three choices, each with its own slots.
OPTION_COUNT = 3
OPTION_SLOTS = 4

ORDER_CB_KINDS = ("declare_war", "press_claim")


def _param_values(kind: str, param: str) -> tuple[str, ...]:
    if param == "modifier":
        return CHARACTER_MODIFIERS if kind == "character_modifier" else OPINION_MODIFIERS
    if param == "area":
        return LEGACY_AREAS           # the later areas are numbered at the end (see COMPACT_AREAS)
    return ENUMS[param]


def variants() -> list[tuple[tuple[str, ...], str]]:
    """Every consequence the game can queue: (key, the script line that applies it)."""
    import itertools
    out: list[tuple[tuple[str, ...], str]] = []
    for kind, spec in ACTIONS.items():
        if kind in ("nothing", "great_effort"):      # great efforts are numbered at the end
            continue
        values = [_param_values(kind, p) for p in spec.params]
        for combo in itertools.product(*values):
            action = {"kind": kind, **dict(zip(spec.params, combo))}
            out.append((("act", kind, *combo), render_action(action)))
    for kind in OFFER_EFFECTS:
        cbs = CASUS_BELLI if kind in ORDER_CB_KINDS else {"cb_war_from_event": 1}
        for cb in cbs:
            out.append((("order", kind, cb), render_offer({"kind": kind, "casus_belli": cb})))
    # Changes to the state itself (appended last: earlier numbers never move).
    for n in range(1, NAME_SLOTS + 1):
        out.append((("order", "rename_country", str(n)), f"votc_order_rename = {{ n = {n} }}"))
    for g in GOVERNMENTS:
        out.append((("order", "change_government", g), f"votc_order_government = {{ type = {g} }}"))
    for r in RANKS:
        out.append((("order", "change_rank", r), f"votc_order_rank = {{ rank = {r} }}"))
    # Power over people and the realm's unity (votc_power_effects.txt). The
    # person is bound to the slot when the mail is written (votc_bind_target).
    for reason in KILL_REASONS:
        out.append((("power", "kill", reason), f"votc_order_kill = {{ t = votc_tgt_$slot$ reason = {reason} }}"))
    for kind in ("depose", "crown", "heir", "regent", "to_cabinet", "from_cabinet", "exile"):
        out.append((("power", kind, ""), f"votc_order_{kind} = {{ t = votc_tgt_$slot$ }}"))
    for mode in ("revolt", "civil_war"):
        for crit in REBEL_REGIONS:
            for estate in REBEL_ESTATES:
                out.append((("power", mode, f"{crit}:{estate}"),
                            f"votc_order_{mode}_{crit} = {{ estate = {estate} t = votc_tgt_$slot$ }}"))
    # The same civil war, with the ruler going over to the rebels (appended last).
    for crit in REBEL_REGIONS:
        for estate in REBEL_ESTATES:
            out.append((("power", "civil_war_join", f"{crit}:{estate}"),
                        f"votc_order_civil_war_{crit} = {{ estate = {estate} t = votc_tgt_$slot$ }} "
                        f"votc_join_rebels_request = yes"))
    # What another court does about a pact (votc_pact_effects.txt; appended last).
    for move in PACT_MOVES[:4]:
        out.append((("pact", move, ""), f"votc_pact_{move} = yes"))
    for move in PACT_MOVES[4:] + RULER_PACT_MOVES:
        out.append((("pact", move, ""), f"votc_pact_{move} = yes"))
    # Territory changing hands (votc_cession_effects.txt; appended last): the
    # locations are marked when the mail is written, the order applies one set.
    out.append((("order", "cession", "q"), "votc_order_cessions = { set = q }"))
    for n in range(1, OPTION_COUNT + 1):
        out.append((("order", "cession", f"o{n}"),
                    f"votc_order_cessions = {{ set = o{n} }} votc_clear_cession_options = yes"))
    # The policy areas added later (appended last): one consequence per strength and
    # sign; its years are the slot's own variable, written with it (votc_queue_years).
    for kind, sign in (("policy_bonus", "bonus"), ("policy_penalty", "penalty")):
        for area in COMPACT_AREAS:
            if area in LATE_AREAS:
                continue
            for tier in TIERS:
                out.append((("act", kind, area, tier, "y"),
                            f"votc_act_policy = {{ area = {area} sign = {sign} tier = {tier} "
                            f"years = var:votc_qy$slot$ }}"))
    # Great efforts (appended last).
    for domain in GREAT_DOMAINS:
        out.append((("act", "great_effort", domain), f"votc_act_great_effort = {{ domain = {domain} }}"))
    # The estates' taxes (appended last).
    for kind, sign in (("policy_bonus", "bonus"), ("policy_penalty", "penalty")):
        for area in LATE_AREAS:
            for tier in TIERS:
                out.append((("act", kind, area, tier, "y"),
                            f"votc_act_policy = {{ area = {area} sign = {sign} tier = {tier} "
                            f"years = var:votc_qy$slot$ }}"))
    # What a decree that truly worked earns (appended last): a great advantage on any
    # lever, and a lasting income in bands by the realm's size.
    for area in POLICY_AREAS:
        out.append((("act", "policy_bonus", area, GRAND, "e"),
                    f"votc_act_policy_earned = {{ area = {area} tier = {GRAND} years = var:votc_qy$slot$ }}"))
    for area in INCOME_AREAS:
        for tier in EARNED_TIERS:
            out.append((("act", "policy_bonus", area, tier, "e"),
                        f"votc_act_policy_earned = {{ area = {area} tier = {tier} years = var:votc_qy$slot$ }}"))
    # Peaceful submission by agreement (appended last).
    for kind in OFFER_EFFECTS_LATE:
        out.append((("order", kind, "cb_war_from_event"), render_offer({"kind": kind,
                                                                       "casus_belli": "cb_war_from_event"})))
    # A story choice that binds a place closer to the realm (votc_integration_effects.txt; appended last).
    for n in range(1, OPTION_COUNT + 1):
        out.append((("order", "integrate", f"o{n}"), f"votc_order_integrate = {{ set = o{n} }}"))
    # The slight tier, below weak (appended last).
    for kind in SLIGHT_KINDS:
        estates = ESTATE_TYPES if kind == "estate" else ("",)
        for estate in estates:
            for sign in SIGNS:
                action = {"kind": kind, "tier": SLIGHT, "sign": sign, **({"estate": estate} if estate else {})}
                key = ("act", kind, *(action[p] for p in ACTIONS[kind].params))
                out.append((key, render_action(action)))
    # A lasting gain lost when it is put to the test (app._upkeep_due; appended last): a standing
    # measure revoked, a policy's or an edict's modifier taken away.
    for n in range(1, 5):
        out.append((("order", "standing_end", str(n)), f"votc_standing_clear_{n} = yes"))
    for area in POLICY_AREAS:
        for tier in (*TIERS, GRAND):
            out.append((("act", "lose_policy", area, tier), f"votc_lose_policy = {{ area = {area} tier = {tier} }}"))
    for area in INCOME_AREAS:
        for tier in EARNED_TIERS:
            out.append((("act", "lose_policy", area, tier), f"votc_lose_policy = {{ area = {area} tier = {tier} }}"))
    for edict in EDICTS:
        out.append((("act", "lose_edict", edict), f"remove_country_modifier = {edict}"))
    return out


def lose_variant(item: dict[str, Any]) -> int:
    """The consequence that takes a lasting gain away (see app._lasting)."""
    if item.get("kind") == "standing":
        return _ids().get(("order", "standing_end", str(item.get("slot", 0))), 0)
    if item.get("kind") == "edict":
        return _ids().get(("act", "lose_edict", item.get("edict", "")), 0)
    return _ids().get(("act", "lose_policy", item.get("area", ""), item.get("tier", "")), 0)


def integrate_variant(set_: str) -> int:
    return _ids().get(("order", "integrate", set_), 0)


def cession_variant(set_: str) -> int:
    return _ids().get(("order", "cession", set_), 0)


_VARIANT_IDS: dict[tuple[str, ...], int] | None = None


def _ids() -> dict[tuple[str, ...], int]:
    global _VARIANT_IDS
    if _VARIANT_IDS is None:
        _VARIANT_IDS = {key: i + 1 for i, (key, _line) in enumerate(variants())}
    return _VARIANT_IDS


def action_variant(action: dict[str, Any]) -> Any:
    """The consequence's number - or, for a policy area added later, (number, "", years):
    the years travel in the slot's own variable."""
    spec = ACTIONS[action["kind"]]
    if spec.effect == "votc_act_policy" and (action.get("tier") == GRAND or action.get("area") in INCOME_AREAS):
        vid = _ids().get(("act", action["kind"], action["area"], action["tier"], "e"), 0)
        return (vid, "", int(action["years"])) if vid else 0
    if spec.effect == "votc_act_policy" and action.get("area") in COMPACT_AREAS:
        vid = _ids().get(("act", action["kind"], action["area"], action["tier"], "y"), 0)
        return (vid, "", int(action["years"])) if vid else 0
    return _ids().get(("act", action["kind"], *(action[p] for p in spec.params)), 0)


def order_variant(offer: dict[str, Any]) -> int:
    kind = offer["kind"]
    if kind in POWER_KINDS:
        return power_variant(offer)
    if kind == "rename_country":
        return _ids().get(("order", kind, str(offer.get("slot", 0))), 0)
    if kind == "change_government":
        return _ids().get(("order", kind, offer.get("government", "")), 0)
    if kind == "change_rank":
        return _ids().get(("order", kind, offer.get("rank", "")), 0)
    cb = offer["casus_belli"] if offer["kind"] in ORDER_CB_KINDS else "cb_war_from_event"
    return _ids().get(("order", offer["kind"], cb), 0)


def _entries(items: list[Any]) -> list[tuple[int, str, int]]:
    """(number, person slot, years) - a bare number, (number, who), or (number, who, years)."""
    out = []
    for it in items:
        vid, who, years = (tuple(it) + ("", 0))[:3] if isinstance(it, tuple) else (it, "", 0)
        if vid:
            out.append((vid, who or "", int(years or 0)))
    return out


def _slot_lines(slot: str, vid: int, who: str, years: int) -> list[str]:
    lines = []
    if who:
        lines.append(f"votc_bind_target = {{ slot = {slot} who = {who} }}")
    if years:
        lines.append(f"votc_queue_years = {{ slot = {slot} years = {years} }}")
    lines.append(f"votc_queue = {{ slot = {slot} id = {vid} }}")
    return lines


def queue_lines(ids: list[Any], *, prefix: str = "") -> list[str]:
    """ids: variant numbers, or (number, person slot[, years]) for moves that name someone
    or policies whose years travel with them.

    prefix "c" writes the chronicle's own slots (votc_qc1...), used by votc.300.
    """
    lines = ["votc_queue_clear_chronicle = yes" if prefix == "c" else "votc_queue_clear = yes"]
    for n, (vid, who, years) in enumerate(_entries(ids)[:QUEUE_SLOTS], start=1):
        lines += _slot_lines(f"{prefix}{n}", vid, who, years)
    return lines


# -- saying in words what was asked of the game ---------------------------------

TIER_TEXT = {"slight": "slight", "weak": "minor", "mild": "moderate", "severe": "major", "grand": "great"}
ESTATE_TEXT = {
    "nobles_estate": "Nobility", "clergy_estate": "Clergy", "burghers_estate": "Burghers",
    "peasants_estate": "Commoners", "tribes_estate": "Tribes", "dhimmi_estate": "Dhimmi",
    "cossacks_estate": "Cossacks",
}
POLICY_TEXT = {
    "taxation": "Taxation", "production": "Production", "trade": "Trade",
    "prosperity": "Prosperity", "currency": "Coinage", "army_upkeep": "Army upkeep",
    "navy_upkeep": "Fleet upkeep", "building_upkeep": "Building upkeep",
    "construction": "Construction", "control": "Control of the land", "manpower": "Levies",
    "food": "Food supply", "morale": "Army morale", "discipline": "Discipline", "siegecraft": "Siegecraft",
    "army_supply": "Army supply", "army_speed": "Marching speed", "morale_recovery": "Morale recovery",
    "conversion": "Conversion", "heretic_conversion": "Conversion of heretics",
    "tolerance_heretics": "Tolerance of heretics", "tolerance_heathens": "Tolerance of heathens",
    "state_faith": "Fervour of the state faith", "assimilation": "Assimilation",
    "cultural_influence": "Cultural influence", "learning": "Learning", "population_growth": "Population growth",
    "social_mobility": "Social mobility", "colonization": "Colonization", "exploration": "Exploration",
    "diplomacy": "Diplomacy", "espionage": "Espionage", "subject_loyalty": "Loyalty of subjects",
    "sailors": "Sailors", "naval_morale": "Naval morale", "shipbuilding": "Shipbuilding", "research": "Research",
    "institutions": "Institutions", "integration": "Integration", "separatism": "Separatism",
    "rebellion": "Rebellion", "legitimacy": "Legitimacy", "prestige": "Prestige", "cabinet": "The cabinet",
    "court_costs": "Court expenses", "crown_power": "Power of the Crown", "nobles_favour": "Favour of the nobility",
    "clergy_favour": "Favour of the clergy", "burghers_favour": "Favour of the burghers",
    "peasants_favour": "Favour of the commoners", "trade_income": "Trade income", "recruitment": "Recruitment",
    "fortifications": "Fortifications", "mercenaries": "Mercenaries",
    "nobles_taxes": "Taxes on the nobility", "clergy_taxes": "Taxes on the clergy",
    "burghers_taxes": "Taxes on the burghers", "peasants_taxes": "Taxes on the commoners",
    "income1": "New revenue (a lasting income for the Crown)",
    "income2": "New revenue (a lasting income for the Crown)",
    "income3": "New revenue (a lasting income for the Crown)",
    "income4": "New revenue (a lasting income for the Crown)",
    "income5": "New revenue (a lasting income for the Crown)",
    "income6": "New revenue (a lasting income for the Crown)",
    "income7": "New revenue (a lasting income for the Crown)",
    "income8": "New revenue (a lasting income for the Crown)",
}
_CURRENCY_TEXT = {
    "stability": "Stability", "prestige": "Prestige", "legitimacy": "Legitimacy",
    "government_power": "Government power", "army_tradition": "Army tradition",
    "devotion": "Devotion", "karma": "Karma", "horde_unity": "Horde unity",
    "republican_tradition": "Republican tradition",
}
ORDER_TEXT = {
    "declare_war": "Declaration of war", "white_peace": "White peace and a 5-year truce",
    "form_alliance": "Alliance", "break_alliance": "Alliance broken",
    "guarantee": "Guarantee of independence", "grant_military_access": "Military access granted",
    "press_claim": "Casus belli obtained", "swear_truce": "5-year truce",
    "take_submission": "Submission as a vassal",
}
_LOC: dict[str, str] | None = None


def _loc(key: str) -> str:
    """The names of the mod's own modifiers, read from its localisation."""
    global _LOC
    if _LOC is None:
        import re
        from pathlib import Path
        _LOC = {}
        from .bundle import mod_source
        base = mod_source() / "main_menu" / "localization" / "english"
        for f in base.glob("votc_*l_english.yml"):
            try:
                for m in re.finditer(r'^ ([\w.]+):0 "(.*)"$', f.read_text(encoding="utf-8-sig"), re.M):
                    _LOC[m.group(1)] = m.group(2)
            except OSError:
                pass
    return _LOC.get(f"STATIC_MODIFIER_NAME_{key}") or _LOC.get(key) or key


def describe_action(action: dict[str, Any]) -> str:
    k = action["kind"]
    tier = TIER_TEXT.get(action.get("tier", ""), "")
    up = action.get("sign") == "bonus"
    if k in _CURRENCY_TEXT:
        return f"{_CURRENCY_TEXT[k]}: {tier} {'gain' if up else 'loss'}"
    if k == "war_exhaustion":
        return f"War exhaustion: {tier} {'increase' if up else 'relief'}"
    if k in ("gold_gain", "gold_loss"):
        return f"Treasury: {'income' if k == 'gold_gain' else 'expense'} ({tier})"
    if k in ("manpower_gain", "manpower_loss"):
        return f"Manpower: {tier} {'gain' if k == 'manpower_gain' else 'loss'}"
    if k == "estate":
        return f"{ESTATE_TEXT.get(action['estate'], action['estate'])}: satisfaction {'rises' if up else 'falls'} ({tier})"
    if k == "all_estates":
        return f"All estates: satisfaction {'rises' if up else 'falls'}"
    if k in ("edict_short", "edict_long"):
        return f"Edict \"{_loc(action['edict'])}\" for {5 if k == 'edict_short' else 15} years"
    if k in ("policy_bonus", "policy_penalty"):
        return (f"✦ {POLICY_TEXT.get(action['area'], action['area'])}: "
                f"{tier} {'advantage' if k == 'policy_bonus' else 'disadvantage'} for {action['years']} year{'s' if action['years'] != '1' else ''}")
    if k == "societal":
        left, _, right = action["type"].partition("_vs_")
        side = left if action["direction"].endswith("left") else right
        size = "tiny" if action["direction"].startswith("tiny") else "small"
        return f"Societal values: a {size} push towards \"{side.replace('_', ' ')}\""
    if k == "character_modifier":
        return f"The person you spoke to gains \"{_loc(action['modifier'])}\""
    if k == "opinion":
        return f"That court's opinion: \"{_loc(action['modifier'])}\""
    simple = {
        "trust_gain": "That court's trust: rises", "trust_loss": "That court's trust: falls",
        "favors": "That court owes you favours", "gift_gold": "A gift of gold to that court",
        "rival_declare": "Declared rivals", "rival_drop": "Rivalry dropped",
        "progress_acclaimed": "The progress is remembered fondly (modifier, 5 years)",
        "progress_resented": "The progress is remembered badly (modifier, 5 years)",
    }
    if k in simple:
        return simple[k]
    if k == "great_effort":
        return {"army": "The realm rises in arms: every levy raised, larger than usual (2 years)",
                "treasury": "The realm pours its pledges into the Crown's chest",
                "works": "The realm builds as one (2 years)",
                "faith": "A great revival of the faith (2 years)"}.get(action.get("domain", ""), "A great effort")
    if k in ("local_control", "local_prosperity"):
        what = "Control" if k == "local_control" else "Prosperity"
        return f"{what} of the place visited: {tier} {'gain' if up else 'loss'}"
    return k


def describe_order(offer: dict[str, Any]) -> str:
    kind = offer["kind"]
    if kind in POWER_KINDS:
        return describe_power(offer)
    if kind == "rename_country":
        return f"✦ New name of the state: {offer.get('name', '')} (adjective: {offer.get('adjective', '')})"
    if kind == "change_government":
        return f"✦ New form of government: {GOVERNMENT_TEXT.get(offer.get('government', ''), '')}"
    if kind == "change_rank":
        return f"✦ New rank: {RANK_TEXT.get(offer.get('rank', ''), '')}"
    return f"⚔ {ORDER_TEXT.get(offer['kind'], offer['kind'])} (the ruler's order)"

_NO_GLYPH = re.compile(r"^[☀-➿\s]+")


def requested_text(actions: list[dict[str, Any]], orders: list[dict[str, Any]], extra: list[str] | None = None) -> str:
    """The tooltip line under the game's own list: what the AI asked for."""
    items = list(extra or []) + [describe_order(o) for o in orders] + [describe_action(a) for a in actions]
    # The game's fonts have no stars or swords: they would show as empty boxes.
    items = [_NO_GLYPH.sub("", i) for i in items]
    if not items:
        return "Whispers in the Court: no consequence requested from the game."
    return ("Whispers in the Court asked the game for:\n" + "\n".join(f"• {i}" for i in items)
            + "\n(Above: what the game really applies when you press.)")

def option_queue_lines(options: list[list[int]]) -> list[str]:
    """Park each choice's consequences in its own slots (votc_qo<choice>_<n>)."""
    lines = ["votc_queue_clear_options = yes"]
    for o, ids in enumerate(options[:OPTION_COUNT], start=1):
        for n, (vid, who, years) in enumerate(_entries(ids)[:OPTION_SLOTS], start=1):
            lines += _slot_lines(f"o{o}_{n}", vid, who, years)
    return lines
