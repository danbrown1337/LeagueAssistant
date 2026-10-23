"""Objective timers from the in-game event feed.

Only timers that follow from what the game reports: when an objective was
killed and how long it takes to come back. Respawn times have been stable
for years; first spawns change between seasons, so only Dragon's is assumed.
All times are game seconds.
"""

from __future__ import annotations

DRAGON_FIRST = 5 * 60
DRAGON_RESPAWN = 5 * 60
ELDER_RESPAWN = 6 * 60  # also the wait for the first Elder after a team claims its soul
BARON_RESPAWN = 6 * 60
INHIB_RESPAWN = 5 * 60
SOUL_DRAGONS = 4

DRAGON_NAMES = {"Fire": "Infernal", "Water": "Ocean", "Earth": "Mountain", "Air": "Cloud",
                "Chemtech": "Chemtech", "Hextech": "Hextech", "Elder": "Elder"}
LANES = {"L": "top", "C": "mid", "R": "bot"}


def _inhib(structure: str) -> tuple[str, str] | None:
    """Barracks_T1_L1 -> (ORDER, top)."""
    parts = structure.split("_")
    if len(parts) < 3 or parts[1] not in ("T1", "T2"):
        return None
    return ("ORDER" if parts[1] == "T1" else "CHAOS"), LANES.get(parts[2][:1], parts[2])


def objectives_view(events: list[dict], team_of: dict[str, str], my_team: str, game_time: float) -> dict:
    """Dragons per team, soul, and timers [{key, label, at, side}] where `at` is the game time it's up."""
    side = lambda team: None if team is None else ("ally" if team == my_team else "enemy")
    dragons = {"ally": [], "enemy": []}
    soul = None
    last_dragon = last_baron = None
    inhibs: dict[tuple[str, str], float] = {}

    for e in sorted(events, key=lambda e: e.get("EventTime", 0)):
        name, t = e.get("EventName"), e.get("EventTime", 0)
        if name == "DragonKill":
            kind = e.get("DragonType") or ""
            s = side(team_of.get(e.get("KillerName", "")))
            if kind != "Elder" and s:
                dragons[s].append(DRAGON_NAMES.get(kind, kind or "Dragon"))
                if soul is None and len(dragons[s]) >= SOUL_DRAGONS:
                    soul = s
            last_dragon = (t, kind)
        elif name == "BaronKill":
            last_baron = t
        elif name == "InhibKilled":
            key = _inhib(e.get("InhibKilled") or "")
            if key:
                inhibs[key] = t + INHIB_RESPAWN
        elif name == "InhibRespawned":
            key = _inhib(e.get("InhibRespawned") or "")
            if key:
                inhibs.pop(key, None)

    timers = []
    if soul or (last_dragon and last_dragon[1] == "Elder"):
        at = last_dragon[0] + ELDER_RESPAWN if last_dragon else DRAGON_FIRST
        timers.append({"key": "dragon", "label": "Elder Dragon", "at": at, "side": None})
    else:
        at = last_dragon[0] + DRAGON_RESPAWN if last_dragon else DRAGON_FIRST
        timers.append({"key": "dragon", "label": "Dragon", "at": at, "side": None})
    if last_baron is not None:
        timers.append({"key": "baron", "label": "Baron", "at": last_baron + BARON_RESPAWN, "side": None})
    for (owner, lane), at in sorted(inhibs.items(), key=lambda kv: kv[1]):
        if at > game_time:
            mine = side(owner) == "ally"
            timers.append({"key": f"inhib-{owner}-{lane}", "at": at, "side": side(owner),
                           "label": f"{'Your' if mine else 'Their'} {lane} inhibitor"})

    point = None
    if not soul:
        point = next((s for s in ("ally", "enemy") if len(dragons[s]) == SOUL_DRAGONS - 1), None)
    return {"dragons": dragons, "soul": soul, "soulPoint": point, "timers": timers}
