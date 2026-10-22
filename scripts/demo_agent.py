"""Plays a fake game against the server so you can try the dashboard without League.

    python scripts/demo_agent.py --server http://localhost:8080 --token SECRET

Goes lobby -> champ select (picks arrive one by one) -> a sped-up game -> post game with lobby ranks.
"""

from __future__ import annotations

import argparse
import random
import time

import requests

AHRI, YASUO, LEE, THRESH, JINX, DARIUS, ORNN, VI, LULU, CAITLYN = 103, 157, 64, 412, 222, 122, 516, 254, 117, 51
SLUGS = {AHRI: "Ahri", YASUO: "Yasuo", LEE: "LeeSin", THRESH: "Thresh", JINX: "Jinx", DARIUS: "Darius",
         ORNN: "Ornn", VI: "Vi", LULU: "Lulu", CAITLYN: "Caitlyn"}
ME = {"gameName": "DemoPlayer", "tagLine": "NA1", "profileIconId": 29, "summonerLevel": 312}

ALLIES = [(ORNN, "TOP"), (VI, "JUNGLE"), (AHRI, "MIDDLE"), (CAITLYN, "BOTTOM"), (LULU, "UTILITY")]
ENEMIES = [(DARIUS, "TOP"), (LEE, "JUNGLE"), (YASUO, "MIDDLE"), (JINX, "BOTTOM"), (THRESH, "UTILITY")]
LCU_POS = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "middle", "BOTTOM": "bottom", "UTILITY": "utility"}
# Item path per champion over the game (ids from Data Dragon).
ITEMS = {
    AHRI: [1056, 2003, 3118, 3020, 3100, 3089, 3157], YASUO: [1055, 6673, 3006, 3031, 6333, 3026],
    ORNN: [1054, 3068, 3047, 3075, 3065], VI: [1103, 6692, 3111, 3071, 6333],
    CAITLYN: [1055, 3031, 3006, 3094, 3036], LULU: [3865, 3504, 3158, 3107],
    DARIUS: [1054, 6631, 3047, 3053, 3742], LEE: [1102, 6692, 3111, 3071, 6333],
    JINX: [1055, 3031, 3006, 3094, 3085], THRESH: [3865, 3190, 3117, 3109],
}


def history(n=8):
    games = []
    for i in range(n):
        champ = random.choice([AHRI, AHRI, 7, 99, 134])
        win = random.random() < 0.55
        games.append({
            "gameCreation": int((time.time() - 3600 * (i * 5 + 2)) * 1000),
            "gameDuration": random.randint(1300, 2200), "queueId": random.choice([420, 420, 440, 450]),
            "participants": [{"championId": champ, "stats": {
                "win": win, "kills": random.randint(1, 14), "deaths": random.randint(0, 9), "assists": random.randint(2, 15),
                "totalMinionsKilled": random.randint(140, 260), "neutralMinionsKilled": random.randint(0, 12),
                **{f"item{k}": v for k, v in enumerate([3118, 3020, 3100, 3089, 3157, 0])},
            }}],
        })
    return games


def champ_select(step: int) -> dict:
    """Step 0: nothing picked. Each step reveals one more pick per team."""
    my_team, actions = [], []
    for cell, (champ, pos) in enumerate(ALLIES):
        picked = step > cell
        hovering = cell == 2 and step == 2
        my_team.append({"cellId": cell, "assignedPosition": LCU_POS[pos],
                        "championId": champ if picked else 0,
                        "championPickIntent": champ if hovering else 0})
        actions.append({"actorCellId": cell, "type": "pick", "completed": picked})
    their = [{"cellId": 5 + i, "championId": c if step > i else 0} for i, (c, _) in enumerate(ENEMIES)]
    return {
        "localPlayerCellId": 2, "myTeam": my_team, "theirTeam": their, "actions": [actions],
        "bans": {"myTeamBans": [238, 55, 91], "theirTeamBans": [555, 84, 145]},
        "timer": {"phase": "BAN_PICK" if step < 5 else "FINALIZATION", "adjustedTimeLeftInPhase": 30000},
    }


def live(t: float) -> dict:
    players = []
    events = []
    progress = t / 1800
    for team, roster in (("ORDER", ALLIES), ("CHAOS", ENEMIES)):
        for i, (champ, pos) in enumerate(roster):
            bought = ITEMS[champ][: 1 + int(progress * len(ITEMS[champ]))]
            rnd = random.Random(champ)
            dead = rnd.random() < 0.3 and int(t) % 90 < 20
            players.append({
                "championName": SLUGS[champ], "rawChampionName": f"game_character_displayname_{SLUGS[champ]}",
                "riotId": f"{'DemoPlayer#NA1' if champ == AHRI else SLUGS[champ] + 'Main#' + str(1000 + champ)}",
                "riotIdGameName": "DemoPlayer" if champ == AHRI else SLUGS[champ] + "Main",
                "team": team, "position": pos, "level": min(18, 1 + int(t / 100)),
                "isDead": dead, "respawnTimer": 14.0 if dead else 0,
                "scores": {"kills": int(progress * rnd.randint(2, 12)), "deaths": int(progress * rnd.randint(1, 8)),
                           "assists": int(progress * rnd.randint(2, 14)), "creepScore": int(t / 60 * (7 if pos not in ("UTILITY",) else 1.2)),
                           "wardScore": progress * 20},
                "items": [{"itemID": iid, "slot": s, "count": 1, "displayName": ""} for s, iid in enumerate(bought)],
            })
    if t > 400:
        events.append({"EventName": "DragonKill", "KillerName": "ViMain"})
    if t > 700:
        events.append({"EventName": "TurretKilled", "TurretKilled": "Turret_T2_C_05_A", "KillerName": "DemoPlayer"})
    if t > 900:
        events.append({"EventName": "DragonKill", "KillerName": "LeeSinMain"})
        events.append({"EventName": "TurretKilled", "TurretKilled": "Turret_T1_L_03_A", "KillerName": "DariusMain"})
    if t > 1300:
        events.append({"EventName": "BaronKill", "KillerName": "OrnnMain"})
    return {
        "activePlayer": {"riotId": "DemoPlayer#NA1", "riotIdGameName": "DemoPlayer", "currentGold": 400 + (t * 7) % 1600},
        "allPlayers": players, "events": {"Events": events},
        "gameData": {"gameMode": "CLASSIC", "gameTime": t},
    }


def post_game() -> dict:
    """The end of game scoreboard and everyone's ranked stats, as the agent sends them."""
    tiers = [("GOLD", "I", 62), ("PLATINUM", "IV", 10), ("GOLD", "II", 35), (None, None, 0), ("SILVER", "I", 88),
             ("PLATINUM", "III", 41), ("GOLD", "I", 5), ("EMERALD", "IV", 0), ("GOLD", "III", 77), ("PLATINUM", "IV", 54)]
    teams, ranks = [], {}
    for team_id, roster in ((100, ALLIES), (200, ENEMIES)):
        players = []
        for champ, _ in roster:
            puuid = f"puuid-{champ}"
            rnd = random.Random(champ)
            players.append({
                "puuid": puuid, "championId": champ,
                "riotIdGameName": "DemoPlayer" if champ == AHRI else SLUGS[champ] + "Main",
                "stats": {"CHAMPIONS_KILLED": rnd.randint(2, 12), "NUM_DEATHS": rnd.randint(1, 8),
                          "ASSISTS": rnd.randint(2, 14), "MINIONS_KILLED": rnd.randint(30, 260)},
            })
            tier, div, lp = tiers[len(ranks)]
            solo = {"queueType": "RANKED_SOLO_5x5", "tier": tier or "NONE", "division": div or "NA", "leaguePoints": lp,
                    "wins": rnd.randint(20, 90), "losses": rnd.randint(20, 90),
                    "previousSeasonEndTier": "SILVER" if tier is None else "", "previousSeasonEndDivision": "II"}
            ranks[puuid] = {"queueMap": {"RANKED_SOLO_5x5": solo}}
        teams.append({"teamId": team_id, "isPlayerTeam": team_id == 100, "isWinningTeam": team_id == 100, "players": players})
    return {"game": {"gameId": int(time.time()), "queueType": "RANKED_SOLO_5x5", "localPlayer": {"puuid": f"puuid-{AHRI}"},
                     "teams": teams}, "ranks": ranks}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://localhost:8080")
    p.add_argument("--token")
    p.add_argument("--speed", type=float, default=30, help="Game seconds per real second")
    p.add_argument("--loop", action="store_true", help="Repeat forever")
    args = p.parse_args()
    http = requests.Session()
    if args.token:
        http.headers["Authorization"] = f"Bearer {args.token}"
    hist = history()

    def send(phase, **extra):
        r = http.post(args.server.rstrip("/") + "/api/agent",
                      json={"clientConnected": True, "phase": phase, "summoner": ME, "history": hist, **extra}, timeout=60)
        r.raise_for_status()

    while True:
        print("Lobby"); send("Lobby"); time.sleep(4)
        print("Ready check"); send("ReadyCheck"); time.sleep(2)
        for step in range(6):
            print(f"Champ select step {step}")
            send("ChampSelect", champSelect=champ_select(step), pickable=[AHRI, 7, 99, 134, 4, 1, 61, 103, 517, 711, 126, 245])
            time.sleep(4)
        t = 0.0
        while t < 1800:
            send("InProgress", live=live(t))
            time.sleep(2)
            t += 2 * args.speed
        print("End of game")
        hist = [{**history(1)[0], "participants": [{"championId": AHRI, "stats": {
            "win": True, "kills": 9, "deaths": 3, "assists": 11, "totalMinionsKilled": 241, "neutralMinionsKilled": 8,
            "item0": 3118, "item1": 3020, "item2": 3100, "item3": 3089, "item4": 3157}}],
            "gameCreation": int(time.time() * 1000), "gameDuration": 1800, "queueId": 420}] + hist[:7]
        send("EndOfGame", postGame=post_game()); time.sleep(8)
        if not args.loop:
            break


if __name__ == "__main__":
    main()
