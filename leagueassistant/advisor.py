"""Turns U.gg stats and League client state into views.

The view functions return plain dicts (sent to the web UI as JSON). The
format_* functions turn those into text for the terminal commands.
"""

from __future__ import annotations

import time

import requests

from . import itemizer
from .objectives import objectives_view
from .spells import spells_view
from .ddragon import DDragon
from .lcu import ChampSelectState
from .ugg import UGG, Build, Matchup

LIVE_POSITIONS = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "adc", "UTILITY": "support"}
QUEUES = {
    400: "Normal Draft", 420: "Ranked Solo/Duo", 430: "Normal Blind", 440: "Ranked Flex",
    450: "ARAM", 490: "Quickplay", 700: "Clash", 900: "URF", 1700: "Arena", 1900: "URF", 2400: "ARAM: Mayhem",
}


MODE_NAMES = {
    "CLASSIC": "Summoner's Rift", "ARAM": "ARAM", "CHERRY": "Arena", "URF": "URF", "ARURF": "ARURF",
    "ONEFORALL": "One for All", "NEXUSBLITZ": "Nexus Blitz", "ULTBOOK": "Ultimate Spellbook", "STRAWBERRY": "Swarm",
}


def game_mode(queue: dict | None = None, live: dict | None = None, session: dict | None = None) -> dict:
    """Which game mode this is. `kind` is sr (U.gg ranked data fits), aram, or other (no U.gg data)."""
    mode = ((live or {}).get("gameData") or {}).get("gameMode") or (queue or {}).get("gameMode")
    if not mode and session and session.get("benchEnabled"):
        mode = "ARAM"  # an older agent that doesn't send the queue
    mode = (mode or "CLASSIC").upper()
    qid = (queue or {}).get("id")
    desc = (queue or {}).get("description") or ""
    # ARAM: Mayhem plays like ARAM (U.gg uses its ARAM builds for it) plus augments.
    mayhem = qid == 2400 or "mayhem" in desc.lower() or mode == "KIWI"
    kind = "aram" if mayhem else {"CLASSIC": "sr", "ARAM": "aram"}.get(mode, "other")
    label = "ARAM: Mayhem" if mayhem else QUEUES.get(qid) or desc or MODE_NAMES.get(mode, mode.title())
    return {"kind": kind, "gameMode": mode, "queueId": qid, "label": label, "mayhem": mayhem}


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


is_boots = itemizer.is_boots


NO_BOOTS = {"Yuumi"}  # champions that never buy boots, for when U.gg's boots stats can't be read
# The usual boots when U.gg's boots stats can't be read: Ionian for supports, else by how the champion builds.
GUESS_BOOTS = {"support": 3158, "ap": 3020, "adc": 3006, "ad": 3047, "tank": 3047}
MIN_BOOTS_SHARE = 0.25  # add boots only when at least this share of games buy that pair


def _boots_position(cores: list, dd: DDragon) -> int:
    """Where boots usually go in the build order, from U.gg's 3-item cores."""
    weighted = [(i, games) for items, _, games in cores for i, iid in enumerate(items) if is_boots(iid, dd)]
    total = sum(g for _, g in weighted)
    return round(sum(i * g for i, g in weighted) / total) if total else 1


def full_build_view(champion_id: int, role: str | None, ugg: UGG, dd: DDragon, aram: bool = False) -> dict:
    """The U.gg build, with boots filled in when the core has none.

    U.gg's overview file only has the most common 3-item core, which often
    leaves boots out (Miss Fortune), and for some ARAM champions it has no
    boots at all. Its builds file has the boots players buy, so we add the
    most common pair where its item orders usually put them. Champions that
    mostly skip boots (Yuumi) stay without.
    """
    view = build_view(ugg.aram_build(champion_id) if aram else ugg.build(champion_id, role), dd)
    if any(is_boots(i["id"], dd) for i in view["core"]):
        return view
    try:
        choices, cores = ugg.boots(champion_id, None if aram else view["role"], aram=aram)
    except (LookupError, requests.RequestException):
        choices, cores = None, []
    if choices:
        total = sum(g for _, _, g in choices) or 1
        boots = sorted(((i, w, g) for i, w, g in choices if i and is_boots(i, dd)), key=lambda c: -c[2])
        if boots and boots[0][2] / total >= MIN_BOOTS_SHARE:
            iid, wins, games = boots[0]
            pos = min(_boots_position(cores, dd), len(view["core"]))
            view["core"].insert(pos, {**item_ref(iid, dd), "added": True})
            view["bootsOptions"] = [{**item_ref(i, dd), "share": round(g / total, 3), "winRate": rate(w, g)}
                                    for i, w, g in boots[:3]]
            view["bootsNote"] = (f"U.gg lists boots separately: {dd.item_name(iid)} in {round(games / total * 100)}% "
                                 f"of games.")
        return view
    champ = dd.champion(champion_id)
    if not choices and not aram and champ.get("id") not in NO_BOOTS:
        # Couldn't read U.gg's boots stats: use the usual boots rather than leaving them out,
        # marked so it's clear it's a guess.
        kind = "support" if view["role"] == "support" else itemizer.play_style(view, dd, champ.get("tags", []))
        iid = GUESS_BOOTS[kind]
        if dd.item(iid):
            view["core"].insert(min(1, len(view["core"])), {**item_ref(iid, dd), "added": True})
            view["bootsNote"] = (f"Couldn't read U.gg's boots stats, so {dd.item_name(iid)} is the usual pick "
                                 f"for this kind of champion.")
        return view
    if aram:
        # No boots data: borrow the boots from the Summoner's Rift build.
        try:
            sr = ugg.build(champion_id)
        except LookupError:
            return view
        for n, iid in enumerate(sr.core_items):
            if is_boots(iid, dd):
                view["core"].insert(min(n, len(view["core"])), {**item_ref(iid, dd), "added": True})
                view["bootsNote"] = "Boots added from the Summoner's Rift build: U.gg's ARAM data has none for this champion."
                break
    return view


def augments_view(champion_id: int, ugg: UGG) -> dict | None:
    """U.gg's ARAM: Mayhem augment ranking for a champion, or None if it has none."""
    try:
        return ugg.mayhem_augments(champion_id)
    except (LookupError, requests.RequestException, ValueError):
        return None


def counter_views(picks: list[Matchup], dd: DDragon) -> list[dict]:
    return [{**champ_ref(m.opponent_id, dd), "winRate": rate(m.wins, m.matches), "games": m.matches} for m in picks]


# --- Champ select ------------------------------------------------------------

def champ_select_view(state: ChampSelectState, ugg: UGG, dd: DDragon,
                      pool: set[int] | None = None, min_matches: int = 100, mode: dict | None = None) -> dict:
    mode = mode or game_mode()
    if mode["kind"] != "sr":
        return _no_lanes_champ_select_view(state, ugg, dd, mode)
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
        "mode": mode,
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
            view["build"] = full_build_view(state.my_champion, my_role, ugg, dd)
            if lane_enemy:
                m = ugg.matchup(state.my_champion, lane_enemy, my_role)
                if m and m.matches:
                    view["matchup"] = {"winRate": rate(m.wins, m.matches), "games": m.matches}
        except LookupError as e:
            view["errors"].append(str(e))
    return view


def aram_options(champions: list[tuple[int, str]], ugg: UGG, dd: DDragon) -> list[dict]:
    """ARAM win rates for the champions you could end up on, best first. `champions` is (id, source)."""
    out, seen = [], set()
    for cid, source in champions:
        if not cid or cid in seen:
            continue
        seen.add(cid)
        try:
            b = ugg.aram_build(cid)
        except LookupError:
            continue
        out.append({**champ_ref(cid, dd), "source": source, "winRate": rate(b.wins, b.matches), "games": b.matches})
    out.sort(key=lambda o: o["winRate"] or 0, reverse=True)
    return out


def _no_lanes_champ_select_view(state: ChampSelectState, ugg: UGG, dd: DDragon, mode: dict) -> dict:
    # ARAM and the other modes have no lanes, so no lane opponent or counters.
    view = {
        "myRole": None,
        "me": champ_ref(state.my_champion, dd),
        "locked": state.my_locked,
        "laneOpponent": None,
        "team": [{**(champ_ref(p["champion"], dd) or {}), "role": None, "locked": p["locked"], "me": p["me"]}
                 for p in state.my_team],
        "enemies": [champ_ref(c, dd) for c in state.enemies],
        "bans": [champ_ref(b, dd) for b in state.bans],
        "bench": [champ_ref(c, dd) for c in state.bench],
        "timer": {"phase": state.timer_phase, "endsAt": int(time.time() * 1000) + state.time_left_ms},
        "counters": [],
        "matchup": None,
        "build": None,
        "aramOptions": [],
        "augments": None,
        "errors": [],
        "mode": mode,
    }
    if mode["kind"] != "aram":
        view["errors"].append(f"U.gg has no builds for {mode['label']}.")
        return view
    if state.my_champion and mode.get("mayhem"):
        view["augments"] = augments_view(state.my_champion, ugg)
    if state.my_champion:
        try:
            view["build"] = full_build_view(state.my_champion, None, ugg, dd, aram=True)
        except LookupError as e:
            view["errors"].append(str(e))
    team = [(p["champion"], "you" if p["me"] else "teammate") for p in state.my_team]
    view["aramOptions"] = aram_options(sorted(team, key=lambda t: t[1] != "you") + [(c, "bench") for c in state.bench], ugg, dd)
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


def live_view(game: dict, dd: DDragon, ugg: UGG | None = None, my_build: dict | None = None,
              mode: dict | None = None, spell_uses: dict | None = None) -> dict:
    active = game.get("activePlayer", {})
    my_names = _player_names(active)
    players = game.get("allPlayers", [])
    my_team = next((p.get("team") for p in players if _player_names(p) & my_names), "ORDER")
    team_of = {n: p.get("team") for p in players for n in _player_names(p)}

    views = []
    for p in players:
        s = p.get("scores", {})
        items = [{**i, "itemID": dd.base_item(i["itemID"])} for i in p.get("items", []) if i.get("itemID")]
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
            # Every item but the trinket. Not "slots 0-5": boots sit outside those six slots now,
            # and skipping them made the app think you never bought boots.
            "items": [{**item_ref(i["itemID"], dd), "slot": i.get("slot")} for i in items
                      if "Trinket" not in dd.item(i["itemID"]).get("tags", [])],
            "itemGold": sum((i.get("price") or dd.item_cost(i["itemID"])) * i.get("count", 1) for i in items),
        })

    allies = [v for v in views if v["ally"]]
    enemies = [v for v in views if not v["ally"]]
    mode = mode or game_mode(live=game)
    # Ranked games report positions; other Summoner's Rift games may not, so guess from U.gg.
    if mode["kind"] == "sr" and ugg and enemies and not any(e["role"] for e in enemies) and len(enemies) == 5:
        try:
            guess = ugg.assign_roles([e["id"] for e in enemies if e.get("id")])
            role_of = {cid: r for r, cid in guess.items()}
            for e in enemies:
                e["role"] = role_of.get(e.get("id"))
        except LookupError:
            pass
    me = next((v for v in views if v["me"]), None)
    lane = None
    if mode["kind"] == "sr":
        lane = next((e for e in enemies if me and me["role"] and e["role"] == me["role"]), None)

    events = (game.get("events") or {}).get("Events", [])
    objectives = _objectives(events, team_of)
    game_time = game.get("gameData", {}).get("gameTime", 0)
    timers = objectives_view(events, team_of, my_team, game_time) if mode["kind"] == "sr" else None
    enemy_team = "CHAOS" if my_team == "ORDER" else "ORDER"

    def totals(team_views, team):
        return {
            "kills": sum(v["kills"] for v in team_views),
            "itemGold": sum(v["itemGold"] for v in team_views),
            **objectives[team],
        }

    progress = None
    if me and my_build:
        owned = itemizer.owned_with_upgrades((i["id"] for i in me["items"]), dd)
        # Any finished boots fill the build's boots slot; show the ones you bought.
        my_boots = next((i for i in me["items"] if itemizer.is_finished_boots(i["id"], dd)), None)
        progress = []
        for i in my_build["core"]:
            if is_boots(i["id"], dd) and my_boots and i["id"] not in owned:
                progress.append({**my_boots, "owned": True})
            else:
                progress.append({**i, "owned": i["id"] in owned})

    advice = None
    if me and my_build:
        advice = itemizer.advise(me, enemies, my_build, dd, gold=round(active.get("currentGold", 0)),
                                 game_time=game.get("gameData", {}).get("gameTime", 0), aram=mode["kind"] == "aram")

    return {
        "gameTime": game.get("gameData", {}).get("gameTime", 0),
        "mode": mode,
        "me": me,
        "gold": round(active.get("currentGold", 0)),
        "laneOpponent": lane,
        "allies": allies,
        "enemies": enemies,
        "score": {"ally": totals(allies, my_team), "enemy": totals(enemies, enemy_team)},
        "objectives": timers,
        "spells": spells_view(players, views, spell_uses or {}, dd),
        "augments": augments_view(me["id"], ugg) if ugg and me and me.get("id") and mode.get("mayhem") else None,
        "build": my_build,
        "buildProgress": progress,
        "advice": advice,
    }


# --- Match history -----------------------------------------------------------

def lcu_match_view(game: dict, dd: DDragon) -> dict:
    p = game["participants"][0]
    s = p["stats"]
    return {
        "gameId": game.get("gameId"),
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
        # Everyone but you: who matchmaking put you with, the basis of the MMR estimate.
        "lobbyEstimate": average_rank([p["rank"] for t in teams for p in t["players"] if not p["me"]]),
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
