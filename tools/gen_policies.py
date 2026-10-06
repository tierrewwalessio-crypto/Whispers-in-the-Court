"""Generate the POLICY modifiers: what a decree or a reform really changes.

Run from the repository root:  python tools/gen_policies.py

An edict (votc_modifiers.txt) is a fixed package. A policy is one lever of
the economy or the state - taxes, production, trade, upkeep, control... -
pulled one way or the other, at one of three strengths, for a number of
years. A decree about tolls can become "trade: mild bonus, control: weak
penalty, 10 years"; a reckless one can cost the realm for twenty.

Every modifier key below exists in vanilla EU5 1.3 static modifiers (see
docs/eu5_modifiers_static.txt), and the magnitudes are taken from the range
vanilla itself uses: WEAK is about an ordinary event modifier, SEVERE about
the strongest vanilla gives for a comparable cause.

Writes:
  mod/.../main_menu/common/static_modifiers/votc_policies.txt
  mod/.../main_menu/localization/english/votc_policies_l_english.yml
and prints the area table for courtbrain/actions.py (POLICY_AREAS).
"""

from __future__ import annotations

import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "mod" / "WhispersInTheCourt"
MODIFIERS = MOD / "main_menu" / "common" / "static_modifiers" / "votc_policies.txt"
LOC = MOD / "main_menu" / "localization" / "english" / "votc_policies_l_english.yml"

# area -> (Italian name, what it means for the model, [(modifier key, weak value for a BONUS)])
# A bonus is always good for the realm; a penalty is the same values negated.
# Tier multipliers: weak 1, mild 2, severe 3.
AREAS = {
    "taxation": ("Taxation", "how much the crown can tax the estates",
                 [("global_estate_max_tax", 0.03)]),
    "production": ("Production", "output of fields, mines and workshops",
                   [("global_production_efficiency", 0.03)]),
    "trade": ("Trade", "merchants' reach and the pull of the realm's markets",
              [("global_merchant_power", 0.05), ("global_trade_center_power", 0.03)]),
    "prosperity": ("Prosperity", "the slow growth or decline of the realm's wealth",
                   [("global_monthly_prosperity", 0.0005)]),
    "currency": ("Coinage", "the soundness of the coin (a bonus lowers inflation)",
                 [("monthly_inflation", -0.0005), ("minting_income_factor", 0.03)]),
    "army_upkeep": ("Army upkeep", "what the army costs to keep",
                    [("army_maintenance_efficiency", 0.05)]),
    "navy_upkeep": ("Fleet upkeep", "what the fleet costs to keep",
                    [("navy_maintenance_efficiency", 0.05)]),
    "building_upkeep": ("Building upkeep", "what the realm's buildings cost to keep",
                        [("building_upkeep_efficiency", 0.05)]),
    "construction": ("Construction", "how fast things get built",
                     [("global_construction_speed", 0.05)]),
    "control": ("Control", "the crown's grip on its provinces",
                [("global_monthly_control", 0.0005)]),
    "manpower": ("Levies", "how many men the realm can raise",
                 [("global_manpower_modifier", 0.05)]),
    "food": ("Food supply", "food produced and stored",
             [("global_monthly_food_modifier", 0.05)]),
    # the army at war (values as the game's own advances and laws use them)
    "morale": ("Army morale", "the fighting spirit of the army",
               [("land_morale_modifier", 0.05)]),
    "discipline": ("Discipline", "order in the ranks, drill, obedience",
                   [("discipline", 0.025)]),
    "siegecraft": ("Siegecraft", "skill and will in taking and holding strongholds",
                   [("siege_ability", 0.05)]),
    "army_supply": ("Army supply", "food, fodder and care that keep the men alive on campaign",
                    [("land_unit_attrition", -0.05)]),
    "army_speed": ("Marching speed", "how fast the army moves",
                   [("army_movement_speed", 0.05)]),
    "morale_recovery": ("Morale recovery", "how fast the men find their heart again",
                        [("land_morale_recovery", 0.01)]),
}

# Every other part of the realm a decree, a reform or a standing measure can move.
# Added after the first eighteen (whose consequence numbers must never move - see
# courtbrain/actions.py): the queue gives them their years in a variable instead of
# one numbered consequence per duration. Values: WEAK about the lower quarter of what
# vanilla EU5 1.3 gives for the same modifier, so that SEVERE (x3) stays within what
# vanilla gives for its strongest comparable causes (tools/calibrate: advances, laws,
# policies, privileges, events).
NEW_AREAS = {
    # faith
    "conversion": ("Conversion", "how fast the realm's people take up the state faith",
                   [("global_pop_conversion_speed_modifier", 0.08)]),
    "heretic_conversion": ("Conversion of heretics", "how fast heretics return to the state faith",
                           [("global_heretic_pop_conversion_speed_modifier", 0.1)]),
    "tolerance_heretics": ("Tolerance of heretics", "how heretics of the realm are treated and how content they are",
                           [("tolerance_heretic", 0.5)]),
    "tolerance_heathens": ("Tolerance of heathens", "how those of other religions are treated and how content they are",
                           [("tolerance_heathen", 0.5)]),
    "state_faith": ("Fervour of the state faith", "the zeal and reach of the state church",
                    [("tolerance_own", 0.5), ("monthly_religious_influence", 0.05)]),
    # culture
    "assimilation": ("Assimilation", "how fast other cultures of the realm take up the state culture",
                     [("global_pop_assimilation_speed_modifier", 0.06)]),
    "cultural_influence": ("Cultural influence", "the pull of the realm's culture and traditions",
                           [("cultural_influence_modifier", 0.05), ("cultural_tradition_modifier", 0.05)]),
    # society
    "learning": ("Learning", "schools, schoolmasters, books: the realm's literacy",
                 [("global_monthly_literacy", 0.01), ("global_max_literacy", 2.5)]),
    "population_growth": ("Population growth", "how fast the realm's people grow in number",
                          [("global_population_growth", 0.0001)]),
    "social_mobility": ("Social mobility", "how easily people rise to better trades and ranks",
                        [("global_pop_promotion_speed_modifier", 0.05)]),
    # colonies and exploration
    "colonization": ("Colonization", "settlers sent overseas, the reach and upkeep of colonies",
                     [("colonial_migration_size_modifier", 0.1), ("colonial_range_modifier", 0.1),
                      ("colonial_maintenance_efficiency", 0.1)]),
    "exploration": ("Exploration", "how fast expeditions chart unknown lands",
                    [("exploration_mission_speed_modifier", 0.1)]),
    # abroad
    "diplomacy": ("Diplomacy", "the realm's name abroad and the envoys it can send",
                  [("diplomatic_reputation", 0.5), ("monthly_diplomats", 0.05)]),
    "espionage": ("Espionage", "spies and informers abroad",
                  [("spy_network_construction", 0.1)]),
    "subject_loyalty": ("Loyalty of subjects", "how loyal vassals and subject states are",
                        [("subject_loyalty", 3)]),
    # the sea
    "sailors": ("Sailors", "how many sailors the realm can find",
                [("global_sailors_modifier", 0.05)]),
    "naval_morale": ("Naval morale", "the fighting spirit of the fleet",
                     [("naval_morale_modifier", 0.05)]),
    "shipbuilding": ("Shipbuilding", "how fast ships are built",
                     [("ship_build_speed", 0.05)]),
    # knowledge
    "research": ("Research", "how fast new knowledge and techniques are mastered",
                 [("research_speed_modifier", 0.03)]),
    "institutions": ("Institutions", "how fast new institutions (printing, the Renaissance...) take root",
                     [("global_institution_growth_modifier", 0.05)]),
    # the state and order
    "integration": ("Integration", "how fast new lands are made part of the realm",
                    [("global_integration_speed_modifier", 0.08)]),
    "separatism": ("Separatism", "talk of breaking away (a bonus means less of it)",
                   [("global_separatism", -0.03)]),
    "rebellion": ("Rebellion", "the growth of rebels (a bonus means fewer)",
                  [("monthly_rebel_growth", -0.0005), ("pop_join_rebel_threshold", 0.03)]),
    "legitimacy": ("Legitimacy", "the ruler's right to rule, month by month",
                   [("monthly_legitimacy", 0.05)]),
    "prestige": ("Prestige", "the crown's standing, month by month",
                 [("monthly_prestige", 0.03)]),
    "cabinet": ("The cabinet", "how well the cabinet and the ministers work",
                [("country_cabinet_efficiency", 0.03)]),
    "court_costs": ("Court expenses", "what the court costs (a bonus means cheaper)",
                    [("court_spending_efficiency", 0.05)]),
    "crown_power": ("Power of the Crown", "the Crown's weight against the estates",
                    [("global_crown_estate_power", 0.05)]),
    # the estates' lasting mood
    "nobles_favour": ("Favour of the nobility", "how content the nobles are, lastingly",
                      [("nobles_estate_target_satisfaction", 0.03)]),
    "clergy_favour": ("Favour of the clergy", "how content the clergy is, lastingly",
                      [("clergy_estate_target_satisfaction", 0.03)]),
    "burghers_favour": ("Favour of the burghers", "how content the burghers are, lastingly",
                        [("burghers_estate_target_satisfaction", 0.03)]),
    "peasants_favour": ("Favour of the commoners", "how content the commoners are, lastingly",
                        [("peasants_estate_target_satisfaction", 0.03)]),
    # money and arms
    "trade_income": ("Trade income", "what trade brings into the treasury",
                     [("trade_income", 0.03)]),
    "recruitment": ("Recruitment", "how fast levies and regiments are raised",
                    [("global_levy_recruitment_speed_modifier", 0.05), ("regiment_recruit_speed", 0.05)]),
    "fortifications": ("Fortifications", "the upkeep and garrisons of the realm's forts",
                       [("fort_maintenance_efficiency", 0.1), ("global_garrison_size_modifier", 0.05)]),
    "mercenaries": ("Mercenaries", "what hired companies cost",
                    [("mercenary_maintenance_efficiency", 0.05)]),
    # the Crown's share of one estate's wealth (vanilla: 0.05 - 0.15 for comparable causes)
    "nobles_taxes": ("Taxes on the nobility", "how much of the nobles' wealth the Crown may take",
                     [("nobles_estate_max_tax", 0.04)]),
    "clergy_taxes": ("Taxes on the clergy", "how much of the church's wealth the Crown may take",
                     [("clergy_estate_max_tax", 0.04)]),
    "burghers_taxes": ("Taxes on the burghers", "how much of the towns' wealth the Crown may take",
                       [("burghers_estate_max_tax", 0.04)]),
    "peasants_taxes": ("Taxes on the commoners", "how much of the commoners' wealth the Crown may take",
                       [("peasants_estate_max_tax", 0.04)]),
}
ALL_AREAS = {**AREAS, **NEW_AREAS}

TIERS = {"weak": 1, "mild": 2, "severe": 3}
TIER_EN = {"weak": "minor", "mild": "moderate", "severe": "major"}

# Earned only by a decree that truly works (a triumph, or a plan with a real vision):
# a great advantage on any lever, and a lasting income for the Crown. Bonus only.
GRAND = ("grand", 4)
# monthly gold for a "weak" income, by the realm's size (Court Brain picks the band)
INCOME_BANDS = (0.5, 1, 2, 4, 8, 15, 30, 60)
INCOME_TIERS = {"weak": 1, "mild": 2, "severe": 3, "grand": 4}
INCOME_EN = {"weak": "a small", "mild": "a moderate", "severe": "a large", "grand": "a great"}


def _fmt(v: float) -> str:
    s = f"{v:.5f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def modifiers_text() -> str:
    out = [
        "﻿# Whispers in the Court - POLICIES. GENERATED by tools/gen_policies.py: edit that, not this.",
        "#",
        "# votc_pol_<area>_<bonus|penalty>_<weak|mild|severe>, applied for a number of",
        "# years by votc_act_policy (votc_action_effects.txt).",
        "",
    ]
    for area, (_it, _note, keys) in ALL_AREAS.items():
        for sign in ("bonus", "penalty"):
            for tier, mult in TIERS.items():
                out.append(f"votc_pol_{area}_{sign}_{tier} = {{")
                out.append("\tgame_data = { category = country }")
                for key, weak in keys:
                    v = weak * mult * (1 if sign == "bonus" else -1)
                    out.append(f"\t{key} = {_fmt(v)}")
                out.append("}")
        out.append("")
    out.append("# Earned: a great advantage (bonus only), by votc_act_policy_earned.")
    for area, (_it, _note, keys) in ALL_AREAS.items():
        out.append(f"votc_pol_{area}_bonus_{GRAND[0]} = {{")
        out.append("\tgame_data = { category = country }")
        for key, weak in keys:
            out.append(f"\t{key} = {_fmt(weak * GRAND[1])}")
        out.append("}")
    out.append("")
    out.append("# Earned: a lasting income for the Crown, in bands by the realm's size.")
    for band, base in enumerate(INCOME_BANDS, start=1):
        for tier, mult in INCOME_TIERS.items():
            out.append(f"votc_pol_income{band}_bonus_{tier} = {{")
            out.append("\tgame_data = { category = country }")
            out.append(f"\tmonthly_gold_income = {_fmt(base * mult)}")
            out.append("}")
    return "\n".join(out)


def loc_text() -> str:
    out = ["﻿l_english:", ""]
    for area, (it, _note, _keys) in ALL_AREAS.items():
        for sign in ("bonus", "penalty"):
            for tier in TIERS:
                label = f"{it}: {TIER_EN[tier]} {'advantage' if sign == 'bonus' else 'disadvantage'}"
                out.append(f' STATIC_MODIFIER_NAME_votc_pol_{area}_{sign}_{tier}:0 "{label}"')
                out.append(f' STATIC_MODIFIER_DESC_votc_pol_{area}_{sign}_{tier}:0 '
                           f'"The effect of a decision taken at court (Whispers in the Court)."')
    for area, (it, _note, _keys) in ALL_AREAS.items():
        out.append(f' STATIC_MODIFIER_NAME_votc_pol_{area}_bonus_{GRAND[0]}:0 "{it}: great advantage"')
        out.append(f' STATIC_MODIFIER_DESC_votc_pol_{area}_bonus_{GRAND[0]}:0 '
                   f'"The fruit of a reform that truly worked (Whispers in the Court)."')
    for band in range(1, len(INCOME_BANDS) + 1):
        for tier in INCOME_TIERS:
            out.append(f' STATIC_MODIFIER_NAME_votc_pol_income{band}_bonus_{tier}:0 '
                       f'"New revenue: {INCOME_EN[tier]} lasting income"')
            out.append(f' STATIC_MODIFIER_DESC_votc_pol_income{band}_bonus_{tier}:0 '
                       f'"Revenue created by a decree that truly worked (Whispers in the Court)."')
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    io.open(MODIFIERS, "w", encoding="utf-8", newline="\n").write(modifiers_text())
    io.open(LOC, "w", encoding="utf-8", newline="\n").write(loc_text())
    print(f"wrote {len(ALL_AREAS) * 6} policy modifiers")
    print("POLICY_AREAS = {")
    for area, (_it, note, _keys) in ALL_AREAS.items():
        print(f'    "{area}": "{note}",')
    print("}")
