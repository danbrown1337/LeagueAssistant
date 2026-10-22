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


def make_client(monkeypatch, token="secret"):
    monkeypatch.setenv("AGENT_TOKEN", token)
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
