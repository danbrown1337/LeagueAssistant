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
