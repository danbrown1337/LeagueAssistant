"""Turns U.gg stats and League client state into views.

The view functions return plain dicts (sent to the web UI as JSON). The
format_* functions turn those into text for the terminal commands.
"""

from __future__ import annotations

import time

from .ddragon import DDragon
from .lcu import ChampSelectState
from .ugg import UGG, Build, Matchup

LIVE_POSITIONS = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "adc", "UTILITY": "support"}
QUEUES = {
    400: "Normal Draft", 420: "Ranked Solo/Duo", 430: "Normal Blind", 440: "Ranked Flex",
    450: "ARAM", 490: "Quickplay", 700: "Clash", 900: "URF", 1700: "Arena", 1900: "URF",
}


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def rate(wins: int, games: int) -> float | None:
    return round(wins / games, 4) if games else None


# --- Building blocks ---------------------------------------------------------

def champ_ref(cid: int, dd: DDragon) -> dict | None:
    if not cid:
        return None
    return {"id": cid, "name": dd.champion_name(cid), "icon": dd.champion_icon(cid)}


def item_ref(iid: int, dd: DDragon) -> dict:
    return {"id": iid, "name": dd.item_name(iid), "icon": dd.item_icon(iid)}


def build_view(b: Build, dd: DDragon) -> dict:
    def tree(tid):
        return {
            "id": tid, "name": dd.runes.get(tid), "icon": dd.rune_icon(tid),
            "runes": [{"id": r, "name": dd.runes.get(r), "icon": dd.rune_icon(r)} for r in dd.runes_in_tree(b.runes, tid)],
        }

    later = []
    for slot in b.later_items[:3]:
        best = sorted(slot, key=lambda o: o[2], reverse=True)[:3]
        later.append([{**item_ref(i, dd), "winRate": rate(w, m), "games": m} for i, w, m in best if m])
    return {
        "champion": champ_ref(b.champion_id, dd),
        "role": b.role,
        "winRate": rate(b.wins, b.matches),
        "games": b.matches,
        "runes": {
            "primary": tree(b.rune_trees[0]),
            "secondary": tree(b.rune_trees[1]),
            "shards": [dd.runes.get(s, str(s)) for s in b.shards],
        },
        "spells": [{"id": s, "name": dd.spells.get(s, str(s)), "icon": dd.spell_icon(s)} for s in b.spells],
        "starting": [item_ref(i, dd) for i in b.starting_items],
        "core": [item_ref(i, dd) for i in b.core_items],
        "later": later,
        "skills": {"priority": list(b.skill_priority), "order": b.skill_order},
    }


def counter_views(picks: list[Matchup], dd: DDragon) -> list[dict]:
    return [{**champ_ref(m.opponent_id, dd), "winRate": rate(m.wins, m.matches), "games": m.matches} for m in picks]


# --- Champ select ------------------------------------------------------------

def champ_select_view(state: ChampSelectState, ugg: UGG, dd: DDragon,
                      pool: set[int] | None = None, min_matches: int = 100) -> dict:
    my_role = state.my_role or (ugg.main_role(state.my_champion) if state.my_champion else None)
    enemy_roles = ugg.assign_roles(state.enemies) if state.enemies else {}
    role_of = {cid: role for role, cid in enemy_roles.items()}
    lane_enemy = enemy_roles.get(my_role) if my_role else None

    view = {
        "myRole": my_role,
        "me": champ_ref(state.my_champion, dd),
        "locked": state.my_locked,
        "laneOpponent": champ_ref(lane_enemy, dd) if lane_enemy else None,
        "team": [{**(champ_ref(p["champion"], dd) or {}), "role": p["role"], "locked": p["locked"], "me": p["me"]}
                 for p in state.my_team],
        "enemies": [{**champ_ref(c, dd), "role": role_of.get(c)} for c in state.enemies],
        "bans": [champ_ref(b, dd) for b in state.bans],
        "timer": {"phase": state.timer_phase, "endsAt": int(time.time() * 1000) + state.time_left_ms},
        "counters": [],
        "matchup": None,
        "build": None,
        "errors": [],
    }

    if lane_enemy:
        unavailable = set(state.bans) | set(state.enemies) | set(state.allies.values())
        available = (pool or set(dd.champions)) - unavailable
        try:
            view["counters"] = counter_views(ugg.counters(lane_enemy, my_role, min_matches=min_matches, pool=available), dd)
        except LookupError as e:
            view["errors"].append(str(e))

    if state.my_champion:
        try:
            view["build"] = build_view(ugg.build(state.my_champion, my_role), dd)
            if lane_enemy:
                m = ugg.matchup(state.my_champion, lane_enemy, my_role)
                if m and m.matches:
                    view["matchup"] = {"winRate": rate(m.wins, m.matches), "games": m.matches}
        except LookupError as e:
            view["errors"].append(str(e))
    return view


# --- In game -----------------------------------------------------------------

def _player_names(p: dict) -> set[str]:
    return {n for n in (p.get("riotId"), p.get("riotIdGameName"), p.get("summonerName")) if n}


def _live_champion_id(p: dict, dd: DDragon) -> int:
    raw = p.get("rawChampionName", "")
    slug = raw.rsplit("_", 1)[-1] if raw else ""
    for cid, c in dd._champion_data.items():
        if c["id"] == slug:
            return cid
    try:
        return dd.champion_id(p.get("championName", ""))
    except KeyError:
        return 0


def _objectives(events: list[dict], team_of: dict[str, str]) -> dict[str, dict[str, int]]:
    out = {t: {"towers": 0, "inhibitors": 0, "dragons": 0, "barons": 0, "heralds": 0, "grubs": 0} for t in ("ORDER", "CHAOS")}
    other = {"ORDER": "CHAOS", "CHAOS": "ORDER"}
    for e in events:
        name = e.get("EventName")
        if name in ("TurretKilled", "InhibKilled"):
            # Structure names say whose it was: Turret_T1_... belongs to ORDER (team 1).
            structure = e.get("TurretKilled") or e.get("InhibKilled") or ""
            owner = "ORDER" if "_T1_" in structure else "CHAOS" if "_T2_" in structure else None
            if owner:
                out[other[owner]]["towers" if name == "TurretKilled" else "inhibitors"] += 1
        elif name in ("DragonKill", "BaronKill", "HeraldKill", "HordeKill"):
            team = team_of.get(e.get("KillerName", ""))
            if team:
                key = {"DragonKill": "dragons", "BaronKill": "barons", "HeraldKill": "heralds", "HordeKill": "grubs"}[name]
                out[team][key] += 1
    return out


def live_view(game: dict, dd: DDragon, ugg: UGG | None = None, my_build: dict | None = None) -> dict:
    active = game.get("activePlayer", {})
    my_names = _player_names(active)
    players = game.get("allPlayers", [])
    my_team = next((p.get("team") for p in players if _player_names(p) & my_names), "ORDER")
    team_of = {n: p.get("team") for p in players for n in _player_names(p)}

    views = []
    for p in players:
        s = p.get("scores", {})
        items = [i for i in p.get("items", []) if i.get("itemID")]
        views.append({
            **(champ_ref(_live_champion_id(p, dd), dd) or {"name": p.get("championName")}),
            "player": p.get("riotIdGameName") or p.get("summonerName"),
            "team": p.get("team"),
            "ally": p.get("team") == my_team,
            "me": bool(_player_names(p) & my_names),
            "role": LIVE_POSITIONS.get(p.get("position") or ""),
            "level": p.get("level", 0),
            "kills": s.get("kills", 0), "deaths": s.get("deaths", 0), "assists": s.get("assists", 0),
            "cs": s.get("creepScore", 0), "wardScore": round(s.get("wardScore", 0)),
            "dead": p.get("isDead", False), "respawn": round(p.get("respawnTimer", 0)),
            "items": [item_ref(i["itemID"], dd) for i in items if i.get("slot", 0) < 6],
            "itemGold": sum((i.get("price") or dd.item_cost(i["itemID"])) * i.get("count", 1) for i in items),
        })

    allies = [v for v in views if v["ally"]]
    enemies = [v for v in views if not v["ally"]]
    # Ranked games report positions; other modes may not, so guess from U.gg.
    if ugg and enemies and not any(e["role"] for e in enemies) and len(enemies) == 5:
        try:
            guess = ugg.assign_roles([e["id"] for e in enemies if e.get("id")])
            role_of = {cid: r for r, cid in guess.items()}
            for e in enemies:
                e["role"] = role_of.get(e.get("id"))
        except LookupError:
            pass
    me = next((v for v in views if v["me"]), None)
    lane = next((e for e in enemies if me and me["role"] and e["role"] == me["role"]), None)

    objectives = _objectives((game.get("events") or {}).get("Events", []), team_of)
    enemy_team = "CHAOS" if my_team == "ORDER" else "ORDER"

    def totals(team_views, team):
        return {
            "kills": sum(v["kills"] for v in team_views),
            "itemGold": sum(v["itemGold"] for v in team_views),
            **objectives[team],
        }

    progress = None
    if me and my_build:
        owned = {i["id"] for i in me["items"]}
        progress = [{**i, "owned": i["id"] in owned} for i in my_build["core"]]

    return {
        "gameTime": game.get("gameData", {}).get("gameTime", 0),
        "mode": game.get("gameData", {}).get("gameMode"),
        "me": me,
        "gold": round(active.get("currentGold", 0)),
        "laneOpponent": lane,
        "allies": allies,
        "enemies": enemies,
        "score": {"ally": totals(allies, my_team), "enemy": totals(enemies, enemy_team)},
        "build": my_build,
        "buildProgress": progress,
    }


# --- Match history -----------------------------------------------------------

def lcu_match_view(game: dict, dd: DDragon) -> dict:
    p = game["participants"][0]
    s = p["stats"]
    return {
        "champion": champ_ref(p["championId"], dd),
        "win": bool(s.get("win")),
        "kills": s.get("kills", 0), "deaths": s.get("deaths", 0), "assists": s.get("assists", 0),
        "cs": s.get("totalMinionsKilled", 0) + s.get("neutralMinionsKilled", 0),
        "items": [item_ref(s[f"item{n}"], dd) for n in range(6) if s.get(f"item{n}")],
        "duration": game.get("gameDuration", 0),
        "queue": QUEUES.get(game.get("queueId"), f"Queue {game.get('queueId')}"),
        "playedAt": game.get("gameCreation"),
    }


# --- Post game ranks ---------------------------------------------------------

SOLO, FLEX = "RANKED_SOLO_5x5", "RANKED_FLEX_SR"
TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND"]
APEX_TIERS = ["MASTER", "GRANDMASTER", "CHALLENGER"]
DIVISIONS = ["IV", "III", "II", "I"]


def rank_value(tier: str | None, division: str | None = None, lp: int = 0) -> float | None:
    """A rank on one scale for averaging: Iron IV 0 LP = 0, each division +1, Master 0 LP = 28."""
    tier = (tier or "").upper()
    if tier in APEX_TIERS:
        # Master+ is one LP ladder; Grandmaster and Challenger are just its top.
        return 28 + max(lp, 0) / 400
    if tier not in TIERS:
        return None  # NONE, UNRANKED or empty
    div = DIVISIONS.index(division) if division in DIVISIONS else 0
    return TIERS.index(tier) * 4 + div + min(max(lp, 0), 99) / 100


def rank_label(value: float) -> str:
    if value >= 28:
        return "Master+"
    return f"{TIERS[int(value // 4)].title()} {DIVISIONS[int(value % 4)]}"


def _entry_rank(tier, division, lp, queue, source) -> dict | None:
    value = rank_value(tier, division, lp or 0)
    if value is None:
        return None
    tier = tier.upper()
    label = tier.title() + (f" {lp} LP" if tier in APEX_TIERS else f" {division}" if division in DIVISIONS else "")
    return {"tier": tier.lower(), "label": label, "value": round(value, 2),
            "queue": "flex" if queue == FLEX else "solo", "source": source}


def player_rank(stats: dict | None, queue: str = SOLO) -> dict | None:
    """The best guess at the rank a player plays at in `queue`.

    Their current rank in that queue, else their other ranked queue, else
    last season's. `source` says which one was used.
    """
    if not stats:
        return None
    by_queue = stats.get("queueMap") or {q.get("queueType"): q for q in stats.get("queues") or []}
    other = FLEX if queue == SOLO else SOLO
    for q, source in ((queue, "current"), (other, "other queue")):
        e = by_queue.get(q) or {}
        rank = _entry_rank(e.get("tier"), e.get("division"), e.get("leaguePoints"), q, source)
        if rank:
            return {**rank, "wins": e.get("wins", 0), "losses": e.get("losses", 0)}
    e = by_queue.get(queue) or {}
    for tier, division in ((e.get("previousSeasonEndTier"), e.get("previousSeasonEndDivision")),
                           (stats.get("highestPreviousSeasonEndTier"), stats.get("highestPreviousSeasonEndDivision"))):
        rank = _entry_rank(tier, division, 0, queue, "last season")
        if rank:
            return rank
    return None


def average_rank(ranks: list[dict | None]) -> dict | None:
    values = [r["value"] for r in ranks if r]
    if not values:
        return None
    avg = sum(values) / len(values)
    return {"label": rank_label(avg), "value": round(avg, 2), "ranked": len(values), "players": len(ranks)}


def _game_queue(game: dict) -> tuple[str, bool]:
    """The ranked queue to compare players in, and whether the game itself was ranked."""
    queue_type = game.get("queueType")
    if queue_type in (SOLO, FLEX):
        return queue_type, True
    queue_id = game.get("queueId") or (game.get("queue") or {}).get("id")
    if queue_id in (420, 440):
        return (SOLO if queue_id == 420 else FLEX), True
    return SOLO, False


def post_game_view(game: dict, ranks: dict[str, dict | None], dd: DDragon) -> dict:
    """Both teams from the end of game screen with each player's rank and the team averages."""
    queue, ranked = _game_queue(game)
    my_puuid = (game.get("localPlayer") or {}).get("puuid")
    teams = []
    for t in game.get("teams") or []:
        players = []
        for p in t.get("players") or []:
            s = p.get("stats") or {}
            players.append({
                **(champ_ref(p.get("championId") or 0, dd) or {"name": p.get("championName")}),
                "player": p.get("riotIdGameName") or p.get("summonerName") or ("Bot" if p.get("botPlayer") else ""),
                "me": bool(my_puuid) and p.get("puuid") == my_puuid,
                "kills": s.get("CHAMPIONS_KILLED", 0), "deaths": s.get("NUM_DEATHS", 0), "assists": s.get("ASSISTS", 0),
                "cs": s.get("MINIONS_KILLED", 0) + s.get("NEUTRAL_MINIONS_KILLED", 0),
                "rank": player_rank(ranks.get(p.get("puuid")), queue),
            })
        mine = bool(t.get("isPlayerTeam")) or any(p["me"] for p in players)
        teams.append({"ally": mine, "win": bool(t.get("isWinningTeam")), "players": players,
                      "average": average_rank([p["rank"] for p in players])})
    teams.sort(key=lambda t: not t["ally"])
    return {
        "gameId": game.get("gameId"),
        "queue": "flex" if queue == FLEX else "solo",
        "ranked": ranked,
        "teams": teams,
        "average": average_rank([p["rank"] for t in teams for p in t["players"]]),
    }


# --- Text output for the terminal --------------------------------------------

def format_build(b: Build, dd: DDragon) -> str:
    v = build_view(b, dd)
    names = lambda xs: ", ".join(x["name"] for x in xs)
    tree = lambda t: f"{t['name']}: {names(t['runes'])}"
    lines = [
        f"{v['champion']['name']} {v['role']}: {pct(b.win_rate)} win rate over {b.matches:,} games",
        f"  Runes:    {tree(v['runes']['primary'])}",
        f"            {tree(v['runes']['secondary'])}",
        f"  Shards:   {', '.join(v['runes']['shards'])}",
        f"  Spells:   {names(v['spells'])}",
        f"  Start:    {names(v['starting'])}",
        f"  Core:     {names(v['core'])}",
    ]
    for n, slot in enumerate(v["later"], start=4):
        lines.append(f"  Item {n}:   " + " / ".join(f"{o['name']} ({pct(o['winRate'])})" for o in slot))
    lines.append(f"  Skills:   {' > '.join(b.skill_priority)}   ({' '.join(b.skill_order[:9])} ...)")
    return "\n".join(lines)


def format_counters(enemy_id: int, role: str, picks: list[Matchup], dd: DDragon) -> str:
    if not picks:
        return f"No counter data for {dd.champion_name(enemy_id)} {role}."
    lines = [f"Best picks into {dd.champion_name(enemy_id)} {role}:"]
    for m in picks:
        lines.append(f"  {dd.champion_name(m.opponent_id):<14} {pct(m.win_rate)}  ({m.matches:,} games)")
    return "\n".join(lines)


def format_match(m: dict) -> str:
    return (f"{'Win ' if m['win'] else 'Loss'}  {m['champion']['name']:<14} "
            f"{m['kills']}/{m['deaths']}/{m['assists']}  {m['duration'] // 60} min  {m['queue']}")


def format_riot_match(match: dict, puuid: str) -> str:
    info = match["info"]
    p = next(x for x in info["participants"] if x["puuid"] == puuid)
    return (f"{'Win ' if p['win'] else 'Loss'}  {p['championName']:<14} {p['kills']}/{p['deaths']}/{p['assists']}  "
            f"{info['gameDuration'] // 60} min  {QUEUES.get(info['queueId'], info['queueId'])}")
