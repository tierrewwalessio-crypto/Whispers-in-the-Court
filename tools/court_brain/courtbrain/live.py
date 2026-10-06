"""The world around the realm, as the game reports it on every heartbeat.

A save is the complete picture, but it is written every twenty minutes at
best. In between, the mod reports a smaller picture with every heartbeat
(tools/gen_live.py): who is at war with the realm, its allies, rivals,
subjects and overlord, the great powers and the neighbours - each with its
ruler and its main enemy. Comparing two of these pictures is how the court
learns, within a minute or two, that a war broke out next door or that a
king died, and the model hears it before the next save exists.
"""

from __future__ import annotations

from .world import LiveCountry

LIST_TITLES = {
    "war": "AT WAR WITH THE REALM",
    "ally": "ALLIES",
    "rival": "RIVALS",
    "subj": "SUBJECTS",
    "lord": "OVERLORD",
    "gp": "THE GREAT POWERS",
    "near": "NEIGHBOURS",
}


def _index(live: dict[str, list[LiveCountry]]) -> dict[str, LiveCountry]:
    out: dict[str, LiveCountry] = {}
    for group in live.values():
        for c in group:
            out.setdefault(c.tag, c)
    return out


def _tags(live: dict[str, list[LiveCountry]], name: str) -> dict[str, LiveCountry]:
    return {c.tag: c for c in live.get(name, [])}


def news(old: dict[str, list[LiveCountry]], new: dict[str, list[LiveCountry]], realm: str) -> list[str]:
    """What changed in the world between two pictures, as dated-log lines."""
    if not old or not new:
        return []
    out: list[str] = []
    realm = realm or "the realm"

    def added_removed(name: str, on_add: str, on_remove: str) -> None:
        before, after = _tags(old, name), _tags(new, name)
        for tag in after.keys() - before.keys():
            out.append(on_add.format(c=after[tag].name, realm=realm))
        for tag in before.keys() - after.keys():
            out.append(on_remove.format(c=before[tag].name, realm=realm))

    added_removed("war", "WAR: {c} and {realm} are now at war", "PEACE: the war between {c} and {realm} is over")
    added_removed("ally", "{c} is now allied with {realm}", "the alliance between {realm} and {c} has ended")
    added_removed("rival", "{c} and {realm} are now rivals", "{c} and {realm} are no longer rivals")
    added_removed("subj", "{c} became a subject of {realm}", "{c} is no longer a subject of {realm}")
    added_removed("lord", "{realm} now answers to {c} as overlord", "{realm} no longer answers to {c}")
    # Great-power standing and borders shift slowly and noisily; only the
    # clear cases are worth a line.
    before_gp, after_gp = _tags(old, "gp"), _tags(new, "gp")
    if len(before_gp) >= 8 and len(after_gp) >= 8:
        for tag in after_gp.keys() - before_gp.keys():
            out.append(f"{after_gp[tag].name} now counts among the great powers")
    before_near, after_near = _tags(old, "near"), _tags(new, "near")
    known_after = _index(new)
    for tag in before_near.keys() - after_near.keys():
        if tag not in known_after and len(before_near) < 6:
            out.append(f"{before_near[tag].name} no longer borders {realm}")

    # Per country: a new ruler, a new war, a peace.
    before_all, after_all = _index(old), _index(new)
    for tag, now in after_all.items():
        was = before_all.get(tag)
        if was is None:
            continue
        if was.ruler and now.ruler and was.ruler != now.ruler:
            out.append(f"in {now.name}, {now.ruler} now rules in place of {was.ruler}")
        # Only the main enemy is reported, so a change from one enemy to
        # another says too little to be news; war and peace are clear.
        if now.foe_tag and not was.foe_tag:
            out.append(f"WAR: {now.name} is at war with {now.foe_name}")
        elif was.foe_tag and not now.foe_tag:
            out.append(f"PEACE: {now.name} is no longer at war (it was fighting {was.foe_name})")
    # The same war seen from both sides is one piece of news.
    seen: set[frozenset] = set()
    unique: list[str] = []
    for line in out:
        if line.startswith("WAR: ") and " is at war with " in line:
            a, _, b = line[5:].partition(" is at war with ")
            pair = frozenset((a, b))
            if pair in seen:
                continue
            seen.add(pair)
        unique.append(line)
    return unique


def render(live: dict[str, list[LiveCountry]], realm: str = "") -> str:
    """The live picture as a section of the system prompt."""
    if not live:
        return ""
    lines: list[str] = []
    for name, title in LIST_TITLES.items():
        group = live.get(name) or []
        if not group:
            if name == "war":
                lines.append(f"{title}: nobody - {realm or 'the realm'} is at peace")
            continue
        parts = []
        for c in group:
            bit = c.name
            if c.ruler:
                bit += f" (ruled by {c.ruler})"
            if c.foe_name and name != "war":
                bit += f", at war with {c.foe_name}"
            parts.append(bit)
        lines.append(f"{title}: " + "; ".join(parts))
    return "\n".join(lines)
