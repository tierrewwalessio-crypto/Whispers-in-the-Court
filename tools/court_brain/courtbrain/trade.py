"""Trade in goods: what the realm really has to sell, and what a deal really brings and costs.

A court may agree to sell a good to another realm, or to buy one from it, for some years.
Such a deal is a flow of money every month - never a sum at once - and it is balanced here,
the same way every time:

* the realm can only sell what it has: a raw material its lands bring out (the save's
  "raw_material" of the player's locations) or a good its workshops make (the buildings it
  has, and what the game says each makes);
* what it brings follows the good's price in the game, how much of it the realm has, how
  much of it the deal takes and the terms agreed - shown as a lasting income for its years
  (votc_pol_income<band>_bonus_<tier>, the same bands the decrees use);
* it costs what selling that good away really costs: gold and silver sold as metal are not
  minted at home (the mint yields less); iron, copper, saltpetre, horses and arms leave the
  armies dearer to keep; timber and naval stores the ships; food the granaries; anything
  else the realm's own workshops, which lose it;
* buying is the other way round: a monthly expense for its years, and the benefit of the good.
"""

from __future__ import annotations

import difflib
from typing import Any

from . import actions as A

PRECIOUS = {"goods_gold", "silver"}
ARMS = {"iron", "copper", "tin", "lead", "saltpeter", "horses", "weaponry", "firearms", "cannons"}
NAVAL = {"lumber", "naval_supplies"}
VOLUME = {1: 0.3, 2: 0.6, 3: 1.0}                  # share of the realm's output the deal takes
TERMS = {"cheap": 0.75, "fair": 1.0, "dear": 1.3}    # the price agreed, for the seller
SCALE = 0.5                                          # gold a month per unit of price, per place
TIER_OF_VOLUME = {1: "weak", 2: "weak", 3: "mild"}   # what the deal costs at home, or gives


def _cls(good: str, info: dict[str, Any]) -> str:
    if good in PRECIOUS:
        return "precious"
    if good in ARMS:
        return "arms"
    if good in NAVAL:
        return "naval"
    if info.get("file", "").endswith("food"):
        return "food"
    return "other"


# what it costs at home to sell a good of each class away, or what it gives to buy one: an area
AREA_OF = {"arms": "army_upkeep", "naval": "shipbuilding", "food": "food", "other": "production"}


def realm_goods(world: Any, codex: Any) -> dict[str, dict[str, int]]:
    """good -> {"places": lands that bring it out, "workshops": levels of buildings that make it}."""
    out: dict[str, dict[str, int]] = {}
    for good, n in (getattr(world, "raw_goods", None) or {}).items():
        out.setdefault(good, {"places": 0, "workshops": 0})["places"] += n
    for building, levels in (getattr(world, "buildings", None) or {}).items():
        for good in (codex.building_goods.get(building) or []):
            out.setdefault(good, {"places": 0, "workshops": 0})["workshops"] += levels
    return out


def realm_goods_text(world: Any, codex: Any) -> str:
    goods = realm_goods(world, codex)
    if not goods:
        return ""
    lands = [f"{codex.word(g)} ({v['places']} place{'s' if v['places'] > 1 else ''})"
             for g, v in sorted(goods.items(), key=lambda kv: -kv[1]["places"]) if v["places"]]
    shops = [codex.word(g) for g, v in sorted(goods.items(), key=lambda kv: -kv[1]["workshops"]) if v["workshops"]]
    return ("WHAT THE REALM HAS TO TRADE (from the game - nothing else is its to sell): its lands bring out "
            + (", ".join(lands) or "nothing of note") + "; its workshops make "
            + (", ".join(shops) or "nothing of note") + ". A good the realm lacks can only be bought.")


def schema() -> dict[str, Any]:
    return {
        "type": "array", "maxItems": 2,
        "description": "Trade agreed now, for years (see TRADE IN GOODS). Usually empty.",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["direction", "good", "volume", "terms", "years"],
            "properties": {
                "direction": {"type": "string", "enum": ["export", "import"]},
                "good": {"type": "string", "description": "The good, by its name in the game."},
                "volume": {"type": "integer", "minimum": 1, "maximum": 3,
                           "description": "1 a little of it, 2 a good part, 3 most of what there is."},
                "terms": {"type": "string", "enum": ["cheap", "fair", "dear"],
                          "description": "The price agreed, from the SELLER's side: cheap, fair or dear."},
                "years": {"type": "integer", "minimum": 1, "maximum": 20},
            },
        },
    }


RULES = """
TRADE IN GOODS ("trade") - a deal to sell a good to the other realm, or to buy one from it,
for some years. It is a FLOW of money every month for those years, never a sum at once
(a single cargo sold today is a one-time gold_gain instead).
- The realm can SELL only what WHAT THE REALM HAS TO TRADE lists: never a good it does not
  bring out or make. A good it lacks it can only BUY.
- "volume": how much of it the deal takes; "terms": the price agreed, as the seller would
  call it; "years": how long it runs. Court Brain turns it into the income or expense the
  game's prices give, and into its real cost: gold or silver sold as metal leaves the mint
  poorer, arms and iron leave the armies dearer to keep, timber and naval stores the ships,
  food the granaries, anything else the realm's own workshops. Buying gives the good's
  benefit for a monthly expense.
- No pact for the same deal: a pact is only for what is owed LATER beyond it.
""".strip()


def find_good(what: str, codex: Any) -> str:
    want = " ".join(str(what or "").lower().replace("_", " ").split())
    if not want:
        return ""
    names = {g: codex.word(g).lower() for g in codex.goods_info}
    for g, n in names.items():
        if want in (g.replace("_", " "), n) or want == n.rstrip("s"):
            return g
    close = difflib.get_close_matches(want, list(names.values()), n=1, cutoff=0.8)
    return next((g for g, n in names.items() if close and n == close[0]), "")


def _nearest_income(amount: float) -> tuple[int, str, float]:
    """(band, tier, gold a month) of the lasting income or expense nearest to amount."""
    mult = {"weak": 1, "mild": 2, "severe": 3, "grand": 4}
    best = min(((b, t, base * m) for b, base in enumerate(A.INCOME_BANDS, start=1) for t, m in mult.items()),
               key=lambda x: abs(x[2] - amount))
    return best


def validate(raw: Any, *, codex: Any, world: Any, taxbase: float) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(raw, dict):
        return None, ""
    direction = raw.get("direction")
    good = find_good(raw.get("good", ""), codex)
    if direction not in ("export", "import"):
        return None, ""
    if not good:
        return None, f"no good called {raw.get('good')!r} in this world"
    info = codex.goods_info.get(good, {})
    name = codex.word(good)
    volume = max(1, min(3, int(raw.get("volume") or 1)))
    years = max(1, min(20, int(raw.get("years") or 10)))
    terms = raw.get("terms") if raw.get("terms") in TERMS else "fair"
    price = float(info.get("price") or 1.0)
    cls = _cls(good, info)
    tier = TIER_OF_VOLUME[volume]
    if direction == "export":
        have = realm_goods(world, codex).get(good)
        units = (have["places"] + 0.5 * have["workshops"]) if have else 0
        if units <= 0:
            return None, f"the realm has no {name} to sell - it neither brings it out nor makes it"
        amount = price * units * VOLUME[volume] * TERMS[terms] * SCALE
        amount = min(amount, max(0.5, 0.12 * taxbase * volume))       # never more than the realm could bear
        band, itier, gold = _nearest_income(amount)
        lines = [f"add_country_modifier = {{ modifier = votc_pol_income{band}_bonus_{itier} years = {years} "
                 f"mode = add_and_extend }}"]
        if cls == "precious":
            lines.append(f"add_country_modifier = {{ modifier = votc_trade_mint_less_{volume} years = {years} "
                         f"mode = add_and_extend }}")
            cost = "the mint yields less"
        else:
            lines.append(f"add_country_modifier = {{ modifier = votc_pol_{AREA_OF[cls]}_penalty_{tier} "
                         f"years = {years} mode = add_and_extend }}")
            cost = {"arms": "the armies are dearer to keep", "naval": "ships are dearer to build",
                    "food": "less food at home", "other": "the realm's own workshops have less of it"}[cls]
        label = (f"Trade: {name} sold abroad for {years} years - about {gold:g} gold a month; at home, {cost}")
    else:
        amount = price * 2 * VOLUME[volume] * TERMS[terms] * SCALE
        amount = min(amount, max(0.5, 0.12 * taxbase * volume))
        band, itier, gold = _nearest_income(amount)
        lines = [f"add_country_modifier = {{ modifier = votc_trade_expense{band}_{itier} years = {years} "
                 f"mode = add_and_extend }}"]
        if cls == "precious":
            lines.append(f"add_country_modifier = {{ modifier = votc_trade_mint_more_{volume} years = {years} "
                         f"mode = add_and_extend }}")
            gain = "the mint has more metal to strike"
        else:
            lines.append(f"add_country_modifier = {{ modifier = votc_pol_{AREA_OF[cls]}_bonus_{tier} "
                         f"years = {years} mode = add_and_extend }}")
            gain = {"arms": "the armies are cheaper to keep", "naval": "ships are cheaper to build",
                    "food": "more food at home", "other": "the realm's workshops have more to work with"}[cls]
        label = f"Trade: {name} bought abroad for {years} years - about {gold:g} gold a month; at home, {gain}"
    return {"direction": direction, "good": good, "name": name, "volume": volume, "years": years,
            "terms": terms, "gold": gold, "lines": lines, "label": label}, ""
