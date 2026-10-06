"""Turning the world read from a save into words the model can use.

The context lines the game sends on every click say how the realm stands
right now. This adds what they cannot carry: the laws actually in force and
which option each one is set to, the ruler's and heir's traits, the realm's
bonds (overlord, subjects, allies, rivals, wars), the great powers of the
world, and what has actually happened - the events that fired, by name.

It also says who a foreign court is when the player addresses one, and what
the save knows about a person of the player's court (their traits), which is
what a persona is written from.
"""

from __future__ import annotations

from typing import Any

from .codex import Codex
from .worldsave import World


def _name_of(world: World, codex: Codex, cid: int) -> str:
    return codex.country(world.tag(cid))


def _person(world: World, codex: Codex, char_id: int) -> str:
    ch = world.characters.get(char_id)
    if ch is None:
        return ""
    traits = ", ".join(codex.word(t) for t in ch.traits) or "no notable traits"
    return f"{codex.word(ch.first_name)} (adm {ch.adm}/dip {ch.dip}/mil {ch.mil}; {traits})"


def neighbours_brief(world: World | None, codex: Codex, geo: Any, tag: str, limit: int = 6) -> str:
    """The realms that share this realm's own lands (its main regions), with their size
    against it - real neighbours to fear or court, so nobody has to invent them."""
    if world is None or geo is None or not world.owners:
        return ""
    cid = world.by_tag.get(tag)
    if cid is None:
        return ""
    index, parent = geo.index, geo.parent

    def region(n: int) -> str:
        loc = index[n - 1] if 0 < n <= len(index) else ""
        return parent.get(parent.get(parent.get(loc, ""), ""), "")

    size: dict[int, int] = {}
    by_region: dict[str, dict[int, int]] = {}
    for n, owner in world.owners.items():
        size[owner] = size.get(owner, 0) + 1
        r = region(n)
        if r:
            by_region.setdefault(r, {})[owner] = by_region.setdefault(r, {}).get(owner, 0) + 1
    mine = size.get(cid, 0)
    if not mine:
        return ""
    home = [r for r, owners in by_region.items() if owners.get(cid, 0) >= mine * 0.15]
    near: dict[int, int] = {}
    for r in home:
        for owner, k in by_region[r].items():
            if owner != cid and k >= 2:
                near[owner] = near.get(owner, 0) + k
    if not near:
        return ""

    def weight(other: int) -> str:
        q = size.get(other, 0) / mine
        return ("far greater" if q >= 3 else "greater" if q >= 1.4 else "about its size" if q >= 0.7
                else "smaller" if q >= 0.25 else "much smaller")

    shown = sorted(near, key=lambda o: -near[o])[:limit]
    return "  sharing its lands: " + ", ".join(f"{_name_of(world, codex, o)} ({weight(o)})" for o in shown)


def realm_brief(world: World | None, codex: Codex, tag: str, geo: Any = None) -> str:
    """Everything the save says about one realm."""
    if world is None:
        return ""
    cid = world.by_tag.get(tag)
    if cid is None:
        return ""
    c = world.countries[cid]
    out = [f"{codex.country(tag)} - {codex.word(c.government)}, {codex.word(c.rank)}"]
    if c.parliament:
        out.append(f"  governed with a {codex.word(c.parliament)}")
    if c.ruler:
        out.append(f"  ruler: {_person(world, codex, c.ruler)}")
        crowns = [o for o, oc in world.countries.items() if o != cid and oc.ruler == c.ruler]
        if crowns:
            out.append("  the same person also rules " + ", ".join(_name_of(world, codex, o) for o in crowns[:4])
                       + " (a personal union: one ruler wearing several crowns, never two different people)")
    if c.heir and c.heir != c.ruler:
        out.append(f"  heir: {_person(world, codex, c.heir)}")
    if c.laws:
        laws = [f"{codex.word(law)}: {codex.word(opt)}" for law, opt in list(c.laws.items())[:14]]
        out.append("  laws in force: " + "; ".join(laws))
    over = world.overlord_of(cid)
    if over:
        out.append(f"  SUBJECT ({codex.word(over[1])}) of {_name_of(world, codex, over[0])}")
    subs = world.subjects_of(cid)
    if subs:
        out.append("  subjects: " + ", ".join(f"{_name_of(world, codex, s)} ({codex.word(k)})" for s, k in subs[:10]))
    allies = world.allies_of(cid)
    if allies:
        out.append("  allies: " + ", ".join(_name_of(world, codex, a) for a in allies[:10]))
    rivals = world.rivals.get(cid, [])
    if rivals:
        out.append("  rivals: " + ", ".join(_name_of(world, codex, r) for r in rivals[:6]))
    for war in world.wars_of(cid)[:4]:
        side = "attacking" if cid in war.attackers else "defending"
        enemies = war.defenders if cid in war.attackers else war.attackers
        out.append(f"  AT WAR ({side}) against " + ", ".join(_name_of(world, codex, e) for e in enemies[:6]))
    if not world.wars_of(cid):
        out.append("  at peace: no war")
    near = neighbours_brief(world, codex, geo, tag)
    if near:
        out.append(near)
    return "\n".join(out)


def world_brief(world: World | None, codex: Codex, tag: str, *, max_events: int = 14) -> str:
    if world is None:
        return ""
    out = [f"(as the state papers stood on {world.date})"]
    mine = realm_brief(world, codex, tag)
    if mine:
        out.append("YOUR REALM IN FULL:")
        out.append(mine)
    powers = world.great_powers(8)
    if powers:
        out.append("THE GREAT POWERS OF THE WORLD: " + ", ".join(codex.country(p.tag) for p in powers))
    active = [w for w in world.wars if tag not in [world.tag(x) for x in w.attackers + w.defenders]]
    if active:
        wars = []
        for war in active[:6]:
            a = codex.country(world.tag(war.attackers[0]))
            d = codex.country(world.tag(war.defenders[0]))
            wars.append(f"{a} against {d}")
        out.append("WARS ELSEWHERE: " + "; ".join(wars))

    cid = world.by_tag.get(tag)
    own = [e for e in world.events if e.country == cid]
    if own:
        out.append("WHAT HAS HAPPENED IN YOUR REALM (most recent last):")
        for e in own[-max_events:]:
            title = codex.event_title(e.key)
            if title:
                out.append(f"  {e.date}: {title}")
    powers_ids = {world.by_tag.get(p.tag) for p in powers}
    abroad = [e for e in world.events if e.country in powers_ids and codex.event_title(e.key)]
    if abroad:
        out.append("NEWS FROM THE GREAT COURTS:")
        seen = set()
        for e in reversed(abroad):
            title = codex.event_title(e.key)
            key = (e.country, title)
            if key in seen:
                continue
            seen.add(key)
            out.append(f"  {e.date}: {codex.country(world.tag(e.country))} - {title}")
            if len(seen) >= 8:
                break
    return "\n".join(out)


TITLES = {
    "count", "countess", "duke", "duchess", "king", "queen", "prince", "princess", "lord", "lady",
    "heir", "emperor", "empress", "sultan", "khan", "doge", "pope", "grand", "archduke", "marquis",
    "conte", "contessa", "duca", "duchessa", "re", "regina", "principe", "principessa", "signore",
    "erede", "imperatore", "sir", "don", "dona", "donna", "dom", "doña",
}


def _facts(codex: Codex, ch) -> str:
    bits = [f"skills adm {ch.adm} dip {ch.dip} mil {ch.mil}"]
    traits = ", ".join(codex.word(t) for t in ch.traits)
    if traits:
        bits.append("traits: " + traits)
    if ch.estate:
        bits.append("of the " + codex.word(ch.estate))
    if ch.birth:
        bits.append("born " + ch.birth)
    return "; ".join(bits)


def person_facts(world: World | None, codex: Codex, tag: str, name: str, *,
                 is_ruler: bool = False, is_heir: bool = False) -> str:
    """Traits and standing of a named person of a realm, if the save has them.

    Rulers and heirs are taken from the realm's government record, which is
    exact. Anyone else is matched on the given name only, loosely, because the
    game may show a translated form ("Sighinolfo" for "Siginulf").
    """
    if world is None or not name:
        return ""
    import difflib
    cid = world.by_tag.get(tag)
    country = world.countries.get(cid) if cid is not None else None
    if country is not None and (is_ruler or is_heir):
        ch = world.characters.get(country.ruler if is_ruler else country.heir)
        return _facts(codex, ch) if ch else ""
    tokens = [t for t in name.replace(",", " ").split() if t]
    while tokens and tokens[0].lower() in TITLES:
        tokens = tokens[1:]
    if not tokens:
        return ""
    given = tokens[0].lower()
    best, best_score = None, 0.0
    for ch in world.characters.values():
        if cid is not None and ch.country != cid:
            continue
        candidate = codex.word(ch.first_name).lower()
        if not candidate:
            continue
        score = difflib.SequenceMatcher(None, candidate, given).ratio()
        if score > best_score:
            best, best_score = ch, score
    return _facts(codex, best) if best is not None and best_score >= 0.7 else ""
