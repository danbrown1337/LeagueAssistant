import dataclasses
import json
from pathlib import Path

from leagueassistant import advisor, lcu
from leagueassistant.ugg import UGG, Matchup, parse_build, shrunk_win_rate

HERE = Path(__file__).parent

AHRI, YASUO, LEE, THRESH, JINX, DARIUS = 103, 157, 64, 412, 222, 122


def test_parse_build():
    b = parse_build(AHRI, "mid", json.loads((HERE / "ahri_mid_overview.json").read_text()))
    assert b.matches > b.wins > 0
    assert b.rune_trees == (8100, 8200)
    assert len(b.runes) == 6
    assert len(b.core_items) == 3
    assert len(b.skill_order) == 18
    assert sorted(b.skill_priority) == ["E", "Q", "W"]


def test_parse_lockfile():
    creds = lcu.parse_lockfile("LeagueClient:1234:54321:s3cret:https\n")
    assert (creds.port, creds.password) == (54321, "s3cret")


def test_parse_champ_select():
    session = {
        "localPlayerCellId": 2,
        "myTeam": [
            {"cellId": 0, "championId": DARIUS, "assignedPosition": "top"},
            {"cellId": 2, "championId": 0, "championPickIntent": AHRI, "assignedPosition": "middle"},
            {"cellId": 3, "championId": 0, "assignedPosition": "bottom"},
        ],
        "theirTeam": [{"championId": YASUO}, {"championId": 0}],
        "bans": {"myTeamBans": [LEE, 0], "theirTeamBans": []},
    }
    s = lcu.parse_champ_select(session)
    assert s.my_champion == AHRI
    assert s.my_role == "mid"
    assert s.allies == {"top": DARIUS}
    assert s.enemies == [YASUO]
    assert s.bans == [LEE]


class FakeUGG(UGG):
    ROLE_GAMES = {
        YASUO: {"top": 300, "jungle": 0, "mid": 700, "adc": 0, "support": 0},
        LEE: {"top": 10, "jungle": 900, "mid": 20, "adc": 0, "support": 5},
        THRESH: {"top": 0, "jungle": 0, "mid": 0, "adc": 0, "support": 1000},
        JINX: {"top": 0, "jungle": 0, "mid": 5, "adc": 1000, "support": 0},
        DARIUS: {"top": 900, "jungle": 50, "mid": 30, "adc": 0, "support": 0},
        AHRI: {"top": 0, "jungle": 0, "mid": 1000, "adc": 0, "support": 0},
    }

    def role_matches(self, champion_id):
        return self.ROLE_GAMES[champion_id]

    # ARAM (wins, games) per champion.
    ARAM = {AHRI: (520, 1000), YASUO: (450, 1000), JINX: (560, 1000)}

    def aram_build(self, champion_id):
        if champion_id not in self.ARAM:
            raise LookupError(f"No ARAM build data for champion {champion_id}")
        b = parse_build(champion_id, "aram", json.loads((HERE / "ahri_mid_overview.json").read_text()))
        wins, games = self.ARAM[champion_id]
        return dataclasses.replace(b, wins=wins, matches=games)

    def build(self, champion_id, role=None):
        return parse_build(champion_id, role or "mid", json.loads((HERE / "ahri_mid_overview.json").read_text()))

    def boots(self, champion_id, role=None, aram=False):
        raise LookupError("no boots data in tests")

    def mayhem_augments(self, champion_id):
        return {"prismatic": ["Tripleshot", "Giant Slayer"], "gold": ["Magic Missile"], "silver": ["Blunt Force"]}

    def matchups(self, champion_id, role=None):
        # Yasuo's results in mid: loses a lot to Ahri, Thresh mid is off-role.
        return [Matchup(AHRI, 400, 1000), Matchup(DARIUS, 40, 100), Matchup(THRESH, 100, 300)]


def test_assign_roles_moves_yasuo_top_when_mid_is_taken():
    ugg = FakeUGG("16.19")
    assert ugg.assign_roles([YASUO, LEE, THRESH, JINX, AHRI]) == {
        "top": YASUO, "jungle": LEE, "mid": AHRI, "adc": JINX, "support": THRESH,
    }


def test_counters_inverts_win_rate_and_filters():
    ugg = FakeUGG("16.19")
    picks = ugg.counters(YASUO, "mid", min_matches=100)
    # Darius has the best raw win rate (60%) but rarely plays mid; Thresh never does.
    assert [m.opponent_id for m in picks] == [AHRI]
    assert picks[0].win_rate == 0.6
    assert ugg.counters(YASUO, "mid", pool={THRESH}, min_role_share=0) == [Matchup(THRESH, 200, 300)]


def test_shrunk_win_rate_prefers_bigger_samples():
    assert shrunk_win_rate(570, 1000) > shrunk_win_rate(60, 100)


def test_rank_value_orders_tiers_and_divisions():
    assert advisor.rank_value("IRON", "IV") == 0
    assert advisor.rank_value("GOLD", "II", 50) == 14.5
    assert advisor.rank_value("MASTER", "I", 0) == 28
    assert advisor.rank_value("NONE", "NA") is None
    assert advisor.rank_label(14.5) == "Gold II"
    assert advisor.rank_label(29) == "Master+"


def test_player_rank_falls_back_to_other_queue_then_last_season():
    solo = {"queueType": "RANKED_SOLO_5x5", "tier": "PLATINUM", "division": "III", "leaguePoints": 20}
    flex = {"queueType": "RANKED_FLEX_SR", "tier": "GOLD", "division": "I", "leaguePoints": 0}
    stats = {"queues": [solo, flex]}
    assert advisor.player_rank(stats, advisor.SOLO)["label"] == "Platinum III"
    assert advisor.player_rank(stats, advisor.FLEX)["label"] == "Gold I"

    only_flex = {"queueMap": {"RANKED_SOLO_5x5": {"tier": "NONE"}, "RANKED_FLEX_SR": flex}}
    r = advisor.player_rank(only_flex, advisor.SOLO)
    assert (r["label"], r["source"], r["queue"]) == ("Gold I", "other queue", "flex")

    last = {"queueMap": {"RANKED_SOLO_5x5": {"tier": "", "previousSeasonEndTier": "SILVER", "previousSeasonEndDivision": "II"}}}
    assert advisor.player_rank(last, advisor.SOLO)["source"] == "last season"
    assert advisor.player_rank({"queueMap": {}}, advisor.SOLO) is None
    assert advisor.player_rank(None) is None


def test_average_rank_ignores_unranked():
    gold = advisor.player_rank({"queueMap": {"RANKED_SOLO_5x5": {"tier": "GOLD", "division": "IV"}}})
    plat = advisor.player_rank({"queueMap": {"RANKED_SOLO_5x5": {"tier": "PLATINUM", "division": "IV"}}})
    avg = advisor.average_rank([gold, plat, None])
    assert (avg["label"], avg["ranked"], avg["players"]) == ("Gold II", 2, 3)
    assert advisor.average_rank([None]) is None


def test_game_mode():
    assert advisor.game_mode()["kind"] == "sr"
    assert advisor.game_mode(live={"gameData": {"gameMode": "ARAM"}})["kind"] == "aram"
    aram = advisor.game_mode({"id": 450, "gameMode": "ARAM"})
    assert (aram["kind"], aram["label"]) == ("aram", "ARAM")
    arena = advisor.game_mode({"id": 9999, "gameMode": "CHERRY"})
    assert (arena["kind"], arena["label"]) == ("other", "Arena")
    assert advisor.game_mode(session={"benchEnabled": True})["kind"] == "aram"
    assert advisor.game_mode({"id": 420, "gameMode": "CLASSIC"})["label"] == "Ranked Solo/Duo"


def test_parse_champ_select_bench():
    s = lcu.parse_champ_select({"benchEnabled": True, "benchChampions": [{"championId": JINX}, {"championId": 0}]})
    assert s.bench == [JINX] and s.bench_enabled


def test_mayhem_is_aram_with_augments():
    m = advisor.game_mode({"id": 2400, "gameMode": "ARAM"})
    assert (m["kind"], m["label"], m["mayhem"]) == ("aram", "ARAM: Mayhem", True)
    assert advisor.game_mode({"id": 9999, "gameMode": "ARAM", "description": "ARAM: Mayhem"})["mayhem"]
    assert not advisor.game_mode({"id": 450, "gameMode": "ARAM"})["mayhem"]


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body
        self.content = b"x" if body is not None else b""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    def __init__(self, pages, post_fails=False):
        self.pages, self.post_fails, self.calls = pages, post_fails, []

    def get(self, url, timeout=5):
        return FakeResponse(200, self.pages)

    def request(self, method, url, json=None, timeout=5):
        self.calls.append((method, url.split("/lol-perks/v1/")[1], json and json["name"]))
        if method == "POST" and self.post_fails and len([c for c in self.calls if c[0] == "POST"]) == 1:
            return FakeResponse(400, {"message": "Max pages reached"})
        return FakeResponse(200, {})


def rune_client(pages, post_fails=False):
    c = lcu.LCU(lcu.Credentials(1, "x"))
    c.session = FakeSession(pages, post_fails)
    return c


def test_rune_page_reuses_ours_creates_or_replaces_the_current():
    mine = {"id": 7, "name": "LA: Ahri Mid", "isEditable": True, "isDeletable": True}
    theirs = {"id": 3, "name": "My page", "isEditable": True, "isDeletable": True, "current": True}
    c = rune_client([theirs, mine])
    assert c.set_rune_page("Jinx Bot", 8000, 8100, list(range(9))) == "updated"
    assert c.session.calls == [("DELETE", "pages/7", None), ("POST", "pages", "LA: Jinx Bot")]

    c = rune_client([theirs])
    assert c.set_rune_page("Jinx Bot", 8000, 8100, list(range(9))) == "created"

    c = rune_client([theirs], post_fails=True)  # every slot taken
    assert c.set_rune_page("Jinx Bot", 8000, 8100, list(range(9))) == 'replaced your page "My page"'
    assert ("DELETE", "pages/3", None) in c.session.calls


def test_rune_page_from_build():
    build = {"champion": {"name": "Ahri"}, "role": "mid", "runes": {
        "primary": {"id": 8100, "runes": [{"id": i} for i in (8112, 8126, 8138, 8135)]},
        "secondary": {"id": 8200, "runes": [{"id": 8226}, {"id": 8210}]}, "shardIds": [5008, 5008, 5001]}}
    assert advisor.rune_page(build) == {"name": "Ahri Mid", "primary": 8100, "sub": 8200,
                                        "perks": [8112, 8126, 8138, 8135, 8226, 8210, 5008, 5008, 5001]}
    assert advisor.rune_page({**build, "runes": {**build["runes"], "shardIds": []}}) is None


def test_agent_imports_each_rune_page_once_and_retries_failures(monkeypatch):
    from leagueassistant import agent as agent_mod

    calls = []

    class Client:
        fail = True

        def set_rune_page(self, name, primary, sub, perks):
            calls.append(name)
            if Client.fail:
                import requests
                raise requests.HTTPError("500")
            return "created"

    a = agent_mod.Agent("http://x", runes=True)
    a.client = Client()
    page = {"name": "Ahri Mid", "primary": 8100, "sub": 8200, "perks": list(range(9))}
    a._apply_runes(page)
    assert a.runes_status["ok"] is False
    a._apply_runes(page)                      # no retry inside 10 seconds
    assert calls == ["Ahri Mid"]
    a.runes_retry_at = 0
    Client.fail = False
    a._apply_runes(page)
    a._apply_runes(page)                      # same page: not written again
    assert calls == ["Ahri Mid", "Ahri Mid"] and a.runes_status == {"ok": True, "name": "Ahri Mid", "note": "created"}
    a._apply_runes({**page, "name": "Jinx Bot"})  # champion changed: written again
    assert calls[-1] == "Jinx Bot"
