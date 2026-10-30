import json

from fastapi.testclient import TestClient

from leagueassistant import server
from tests.test_core import AHRI, DARIUS, JINX, LEE, THRESH, YASUO, FakeUGG


class FakeDDragon:
    version, patch = "16.19.1", "16.19"
    champions = {c: f"Champ{c}" for c in (AHRI, YASUO, LEE, THRESH, JINX, DARIUS)}
    _champion_data = {c: {"id": f"Champ{c}"} for c in champions}

    def champion_name(self, cid):
        return self.champions.get(cid, f"#{cid}")

    def champion_icon(self, cid):
        return None

    runes, spells = {}, {}

    def rune_icon(self, rid):
        return None

    def runes_in_tree(self, runes, tree):
        return []

    def item_name(self, iid):
        return f"Item{iid}"

    def item_icon(self, iid):
        return None

    def spell_icon(self, sid):
        return None

    def item_cost(self, iid):
        return 0

    def base_item(self, iid):
        return iid

    def upgraded_from(self, iid):
        base = self.item(iid).get("specialRecipe")
        return [base] if base else []

    def spell_list(self):
        return {4: {"id": "SummonerFlash", "name": "Flash", "cooldown": [300]},
                14: {"id": "SummonerDot", "name": "Ignite", "cooldown": [180]}}

    def item(self, iid):
        return {}

    def all_items(self):
        return {}

    def champion(self, cid):
        return {}


def make_client(monkeypatch, token="secret", data_dir=None):
    monkeypatch.setenv("AGENT_TOKEN", token)
    if data_dir:
        monkeypatch.setenv("LEAGUEASSISTANT_DATA", str(data_dir))
    else:
        monkeypatch.setenv("LEAGUEASSISTANT_LOG", "0")
    app = server.create_app()
    hub = app.state.hub
    monkeypatch.setattr(hub.stats, "get", lambda: (FakeDDragon(), FakeUGG("16.19")))
    return TestClient(app), hub


def test_agent_needs_token(monkeypatch):
    client, _ = make_client(monkeypatch)
    assert client.post("/api/agent", json={}).status_code == 401
    assert client.post("/api/agent", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_champ_select_snapshot_produces_counters(monkeypatch):
    client, hub = make_client(monkeypatch)
    snap = {
        "clientConnected": True,
        "phase": "ChampSelect",
        "champSelect": {
            "localPlayerCellId": 0,
            "myTeam": [{"cellId": 0, "championId": 0, "assignedPosition": "middle"}],
            "theirTeam": [{"championId": YASUO}],
        },
        "pickable": [AHRI, DARIUS],
    }
    r = client.post("/api/agent", json=snap, headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    state = client.get("/api/state").json()
    assert state["agentOnline"] and state["phase"] == "ChampSelect"
    cs = state["champSelect"]
    assert cs["myRole"] == "mid"
    assert cs["laneOpponent"]["id"] == YASUO
    assert [c["id"] for c in cs["counters"]] == [AHRI]


def eog_snapshot(phase="EndOfGame"):
    def player(champ, puuid):
        return {"puuid": puuid, "championId": champ, "riotIdGameName": puuid,
                "stats": {"CHAMPIONS_KILLED": 3, "NUM_DEATHS": 1, "ASSISTS": 5, "MINIONS_KILLED": 150}}

    def solo(tier, division):
        return {"queueMap": {"RANKED_SOLO_5x5": {"tier": tier, "division": division, "leaguePoints": 0}}}

    game = {
        "gameId": 42, "queueType": "RANKED_SOLO_5x5", "localPlayer": {"puuid": "me"},
        "teams": [
            {"isPlayerTeam": False, "isWinningTeam": False, "players": [player(YASUO, "foe")]},
            {"isPlayerTeam": True, "isWinningTeam": True, "players": [player(AHRI, "me"), player(LEE, "mate")]},
        ],
    }
    ranks = {"me": solo("GOLD", "II"), "mate": solo("PLATINUM", "II"), "foe": solo("EMERALD", "IV")}
    return {"clientConnected": True, "phase": phase, "postGame": {"game": game, "ranks": ranks}}


def test_post_game_shows_ranks_until_next_champ_select(monkeypatch):
    client, _ = make_client(monkeypatch)
    auth = {"Authorization": "Bearer secret"}
    client.post("/api/agent", json=eog_snapshot(), headers=auth)
    pg = client.get("/api/state").json()["postGame"]
    assert pg["ranked"] and pg["queue"] == "solo"
    ally, enemy = pg["teams"]
    assert ally["ally"] and ally["win"] and not enemy["ally"]
    assert [p["rank"]["label"] for p in ally["players"]] == ["Gold II", "Platinum II"]
    assert ally["players"][0]["me"]
    assert ally["average"]["label"] == "Platinum IV"  # halfway between Gold II and Platinum II
    assert pg["average"]["ranked"] == 3

    client.post("/api/agent", json={"clientConnected": True, "phase": "Lobby"}, headers=auth)
    assert client.get("/api/state").json()["postGame"] is not None
    client.post("/api/agent", json={"clientConnected": True, "phase": "ChampSelect"}, headers=auth)
    assert client.get("/api/state").json()["postGame"] is None


AUTH = {"Authorization": "Bearer secret"}
ARAM_QUEUE = {"id": 450, "gameMode": "ARAM", "mapId": 12}


def test_aram_champ_select_ranks_bench_and_skips_lanes(monkeypatch):
    client, _ = make_client(monkeypatch)
    snap = {
        "clientConnected": True, "phase": "ChampSelect", "queue": ARAM_QUEUE,
        "champSelect": {
            "localPlayerCellId": 0, "benchEnabled": True,
            "myTeam": [{"cellId": 0, "championId": AHRI}, {"cellId": 1, "championId": YASUO}],
            "theirTeam": [], "benchChampions": [{"championId": JINX}],
        },
    }
    client.post("/api/agent", json=snap, headers=AUTH)
    state = client.get("/api/state").json()
    assert state["mode"]["label"] == "ARAM"
    cs = state["champSelect"]
    assert cs["mode"]["kind"] == "aram" and cs["laneOpponent"] is None and cs["counters"] == []
    assert cs["build"]["role"] == "aram" and cs["build"]["winRate"] == 0.52
    assert [(o["id"], o["source"]) for o in cs["aramOptions"]] == [(JINX, "bench"), (AHRI, "you"), (YASUO, "teammate")]


def live_player(champ, team, me=False):
    name = "Me" if me else f"P{champ}"
    return {"championName": f"Champ{champ}", "rawChampionName": f"game_character_displayname_Champ{champ}",
            "riotId": f"{name}#1", "riotIdGameName": name, "team": team, "position": "", "scores": {}, "items": []}


def test_aram_game_uses_aram_build_and_no_lanes(monkeypatch):
    client, _ = make_client(monkeypatch)
    enemies = [YASUO, LEE, THRESH, JINX, DARIUS]
    live = {
        "activePlayer": {"riotId": "Me#1", "riotIdGameName": "Me"},
        "allPlayers": [live_player(AHRI, "ORDER", me=True)] + [live_player(c, "CHAOS") for c in enemies],
        "gameData": {"gameMode": "ARAM", "gameTime": 300},
    }
    client.post("/api/agent", json={"clientConnected": True, "phase": "InProgress", "live": live}, headers=AUTH)
    g = client.get("/api/state").json()["live"]
    assert g["mode"]["kind"] == "aram"
    assert g["laneOpponent"] is None
    assert all(e["role"] is None for e in g["enemies"])  # no Summoner's Rift lane guesses
    assert g["build"]["role"] == "aram"


def test_modes_without_ugg_data_show_no_build(monkeypatch):
    client, _ = make_client(monkeypatch)
    snap = {"clientConnected": True, "phase": "ChampSelect", "queue": {"id": 1700, "gameMode": "CHERRY"},
            "champSelect": {"localPlayerCellId": 0, "myTeam": [{"cellId": 0, "championId": AHRI}], "theirTeam": []}}
    client.post("/api/agent", json=snap, headers=AUTH)
    cs = client.get("/api/state").json()["champSelect"]
    assert cs["build"] is None and cs["errors"] == ["U.gg has no builds for Arena."]


def test_finished_games_are_saved(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, data_dir=tmp_path)
    client.post("/api/agent", json=eog_snapshot(), headers=AUTH)
    client.post("/api/agent", json=eog_snapshot(), headers=AUTH)
    [line] = (tmp_path / "games.jsonl").read_text().splitlines()
    rec = json.loads(line)
    assert rec["gameId"] == 42 and rec["win"] and rec["lobbyAverage"]["ranked"] == 3
    # The estimate leaves you out: the two other players are Platinum II and Emerald IV.
    assert rec["mmrEstimate"]["ranked"] == 2 and rec["mmrEstimate"]["label"] == "Platinum I"
    state = client.get("/api/state").json()
    assert state["postGame"]["mmr"]["games"] == 1 and state["mmr"][0]["label"] == "Platinum I"


def test_browser_friendly_routes(monkeypatch):
    client, _ = make_client(monkeypatch)
    r = client.get("/api/agent", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/"
    for path in ("/", "/healthz", "/api/state"):
        assert client.head(path).status_code == 200, path


def test_mayhem_champ_select_and_game_show_augments(monkeypatch):
    client, _ = make_client(monkeypatch)
    q = {"id": 2400, "gameMode": "ARAM"}
    snap = {"clientConnected": True, "phase": "ChampSelect", "queue": q,
            "champSelect": {"localPlayerCellId": 0, "benchEnabled": True, "theirTeam": [],
                            "myTeam": [{"cellId": 0, "championId": AHRI}]}}
    client.post("/api/agent", json=snap, headers=AUTH)
    cs = client.get("/api/state").json()["champSelect"]
    assert cs["mode"]["label"] == "ARAM: Mayhem" and cs["augments"]["prismatic"][0] == "Tripleshot"
    assert cs["build"]["role"] == "aram"

    live = {"activePlayer": {"riotId": "Me#1", "riotIdGameName": "Me"},
            "allPlayers": [live_player(AHRI, "ORDER", me=True), live_player(YASUO, "CHAOS")],
            "gameData": {"gameMode": "ARAM", "gameTime": 60}}
    client.post("/api/agent", json={"clientConnected": True, "phase": "InProgress", "queue": q, "live": live}, headers=AUTH)
    assert client.get("/api/state").json()["live"]["augments"]["gold"] == ["Magic Missile"]
    # Plain ARAM: no augments.
    client.post("/api/agent", json={"clientConnected": True, "phase": "InProgress", "queue": ARAM_QUEUE, "live": live}, headers=AUTH)
    assert client.get("/api/state").json()["live"]["augments"] is None


def spell_game(t, items=()):
    def p(champ, team, me=False):
        d = live_player(champ, team, me)
        d["items"] = [{"itemID": i, "slot": n} for n, i in enumerate(items)] if me else []
        d["summonerSpells"] = {
            "summonerSpellOne": {"displayName": "Flash", "rawDisplayName": "GeneratedTip_SummonerSpell_SummonerFlash_DisplayName"},
            "summonerSpellTwo": {"displayName": "Ignite", "rawDisplayName": "GeneratedTip_SummonerSpell_SummonerDot_DisplayName"},
        }
        return d
    return {"activePlayer": {"riotId": "Me#1", "riotIdGameName": "Me"},
            "allPlayers": [p(AHRI, "ORDER", me=True), p(YASUO, "CHAOS")],
            "gameData": {"gameMode": "CLASSIC", "gameTime": t}}


def test_summoner_spell_timers(monkeypatch):
    client, _ = make_client(monkeypatch)
    post = lambda t: client.post("/api/agent", json={"clientConnected": True, "phase": "InProgress", "live": spell_game(t)}, headers=AUTH)
    post(600)
    spells = client.get("/api/state").json()["live"]["spells"]
    foe = next(p for p in spells if not p["ally"])
    assert [(s["name"], s["cooldown"], s["usedAt"]) for s in foe["spells"]] == [("Flash", 300, None), ("Ignite", 180, None)]

    # Tap the enemy's Flash at 10:05; it survives the next update, and tapping again undoes it.
    assert client.post("/api/spells", json={"player": foe["key"], "slot": "summonerSpellOne", "at": 605}).status_code == 200
    post(610)
    foe = next(p for p in client.get("/api/state").json()["live"]["spells"] if not p["ally"])
    assert foe["spells"][0]["usedAt"] == 605
    client.post("/api/spells", json={"player": foe["key"], "slot": "summonerSpellOne", "at": None})
    assert next(p for p in client.get("/api/state").json()["live"]["spells"] if not p["ally"])["spells"][0]["usedAt"] is None

    assert client.post("/api/spells", json={"player": "Nobody#0", "slot": "summonerSpellOne", "at": 1}).status_code == 400

    # A new game clears the timers.
    client.post("/api/spells", json={"player": foe["key"], "slot": "summonerSpellTwo", "at": 615})
    post(20)
    foe = next(p for p in client.get("/api/state").json()["live"]["spells"] if not p["ally"])
    assert foe["spells"][1]["usedAt"] is None


class BootsDD(FakeDDragon):
    def item(self, iid):
        return {3009: {"name": "Boots of Swiftness", "tags": ["Boots"], "gold": {"total": 1000}}}.get(iid, {})


def test_boots_are_kept_after_the_quest_slot_hides_them(monkeypatch):
    client, hub = make_client(monkeypatch)
    monkeypatch.setattr(hub.stats, "get", lambda: (BootsDD(), FakeUGG("16.19")))
    post = lambda t, items: client.post("/api/agent", json={"clientConnected": True, "phase": "InProgress",
                                                            "live": spell_game(t, items)}, headers=AUTH)
    my_items = lambda: [i["id"] for i in client.get("/api/state").json()["live"]["me"]["items"]]
    post(600, [3009])
    assert my_items() == [3009]
    post(900, [])            # quest done: the game stops listing the boots
    assert my_items() == [3009]
    post(20, [])             # a new game starts without them
    assert my_items() == []


class ShopDD(FakeDDragon):
    ITEMS = {
        1001: {"name": "Boots", "tags": ["Boots"], "gold": {"total": 300, "base": 300, "purchasable": True}, "maps": {"11": True}},
        3009: {"name": "Boots of Swiftness", "tags": ["Boots"], "from": ["1001"],
               "gold": {"total": 1000, "base": 700, "purchasable": True}, "maps": {"11": True}},
        3006: {"name": "Berserker's Greaves", "tags": ["Boots"], "from": ["1001"],
               "gold": {"total": 1100, "base": 800, "purchasable": True}, "maps": {"11": True}},
        1036: {"name": "Long Sword", "tags": ["Damage"], "gold": {"total": 350, "base": 350, "purchasable": True}, "maps": {"11": True}},
    }

    def item(self, iid):
        return self.ITEMS.get(iid, {})

    def all_items(self):
        return self.ITEMS

    def item_cost(self, iid):
        return self.ITEMS.get(iid, {}).get("gold", {}).get("total", 0)

    def item_name(self, iid):
        return self.ITEMS.get(iid, {}).get("name", f"#{iid}")


def test_quest_slot_boots_are_worked_out_from_gold(monkeypatch):
    client, hub = make_client(monkeypatch)
    monkeypatch.setattr(hub.stats, "get", lambda: (ShopDD(), FakeUGG("16.19")))

    def post(t, gold, items=()):
        g = spell_game(t, items)
        g["activePlayer"]["currentGold"] = gold
        client.post("/api/agent", json={"clientConnected": True, "phase": "InProgress", "live": g}, headers=AUTH)
        return client.get("/api/state").json()["live"]

    post(600, 1400)
    post(602, 1100)                     # 300 gone, nothing new to see: plain Boots into the boots slot
    live = post(604, 1050, [1036])      # a visible Long Sword (350) shouldn't count as boots
    assert live["boots"] == {**live["boots"], "id": 1001, "source": "gold"}
    live = post(700, 350)               # 700 gone: Boots upgraded to Swiftness in that slot
    assert live["boots"]["id"] == 3009
    assert 3009 in [i["id"] for i in live["me"]["items"]]

    # You can correct it from the dashboard.
    assert client.post("/api/boots", json={"id": 3006}).status_code == 200
    assert post(702, 360)["boots"] == {**post(702, 360)["boots"], "id": 3006, "source": "you"}
    assert client.post("/api/boots", json={"id": 1036}).status_code == 400  # not boots
