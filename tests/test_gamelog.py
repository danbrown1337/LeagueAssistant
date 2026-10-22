import json

from leagueassistant.gamelog import GameLog
from tests.test_itemizer import FakeDD

DD = FakeDD()
MODE = {"kind": "sr", "label": "Ranked Solo/Duo", "queueId": 420, "gameMode": "CLASSIC"}


def live(t, items, nxt, situational=()):
    return {
        "gameTime": t, "gold": 500, "mode": MODE,
        "me": {"id": 1, "name": "Ahri", "role": "mid", "items": [{"id": i} for i in items]},
        "build": {"core": [{"id": 1}, {"id": 2}]},
        "advice": {"next": {"id": nxt, "name": DD.item_name(nxt), "purpose": "core", "reason": "because"},
                   "situational": [{"id": i, "name": DD.item_name(i), "reason": "also"} for i in situational],
                   "threats": [{"label": "Healing"}]},
    }


def play(gl):
    gl.live(live(60, [], 1), 777, DD)
    gl.live(live(400, [1], 1), 777, DD)          # bought the advised Ludens
    gl.live(live(410, [1], 3), 777, DD)          # advice switches to Morello
    gl.live(live(600, [1, 30], 3), 777, DD)      # Oblivion Orb, a part of Morello
    gl.live(live(900, [1, 30, 9], 3), 777, DD)   # Zhonya, not advised


POST_RAW = {"gameId": 777, "gameLength": 1500, "localPlayer": {"puuid": "me"},
            "teams": [{"players": [{"puuid": "me", "items": [1, 30, 9]}]}]}
POST_VIEW = {"ranked": True, "average": {"label": "Gold I", "value": 15.0, "ranked": 2, "players": 2},
             "teams": [{"ally": True, "win": True, "average": {"label": "Gold I"},
                        "players": [{"id": 1, "name": "Ahri", "player": "Me", "me": True, "kills": 9, "deaths": 2,
                                     "assists": 7, "cs": 200, "rank": {"tier": "gold", "label": "Gold I", "value": 15.0,
                                                                       "queue": "solo", "source": "current"}}]}]}


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_records_advice_and_whether_purchases_followed_it(tmp_path):
    gl = GameLog(tmp_path / "games.jsonl", build="abc")
    play(gl)
    gl.post_game(POST_RAW, POST_VIEW, MODE, DD)
    [rec] = lines(tmp_path / "games.jsonl")
    assert (rec["gameId"], rec["source"], rec["win"], rec["kills"], rec["duration"]) == (777, "post game", True, 9, 1500)
    assert rec["champion"]["name"] == "Ahri" and rec["role"] == "mid" and rec["app"] == "abc"
    assert [a["next"]["name"] for a in rec["advice"]] == ["Ludens", "Morello"]
    assert [(p["name"], p["followedAdvice"]) for p in rec["purchases"]] == [
        ("Ludens", True), ("Oblivion Orb", True), ("Zhonya", False)]
    assert rec["teams"][0]["players"][0]["rank"]["label"] == "Gold I"
    assert [i["name"] for i in rec["items"]] == ["Ludens", "Oblivion Orb", "Zhonya"]


def test_each_game_is_saved_once_even_across_restarts(tmp_path):
    path = tmp_path / "games.jsonl"
    gl = GameLog(path)
    gl.post_game(POST_RAW, POST_VIEW, MODE, DD)
    gl.post_game(POST_RAW, POST_VIEW, MODE, DD)
    GameLog(path).post_game(POST_RAW, POST_VIEW, MODE, DD)  # restarted on the post game screen
    assert len(lines(path)) == 1


def test_match_history_is_the_fallback(tmp_path):
    gl = GameLog(tmp_path / "games.jsonl")
    play(gl)
    game = {"gameId": 777, "gameCreation": 2_000_000_000_000}
    view = {"champion": {"id": 1, "name": "Ahri"}, "win": False, "kills": 1, "deaths": 5, "assists": 2, "cs": 90,
            "duration": 1200, "items": [{"id": 1}]}
    gl.history(game, view, MODE, DD)
    [rec] = lines(tmp_path / "games.jsonl")
    assert rec["source"] == "match history" and rec["win"] is False and len(rec["purchases"]) == 3


def test_joining_mid_game_doesnt_count_existing_items_as_purchases(tmp_path):
    gl = GameLog(tmp_path / "games.jsonl")
    gl.live(live(900, [1, 30], 3), 777, DD)
    gl.live(live(950, [1, 30, 9], 3), 777, DD)
    assert [p["name"] for p in gl.current.purchases] == ["Zhonya"]


def test_unwritable_log_doesnt_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    gl = GameLog(blocker / "games.jsonl")  # parent is a file, so it can't be created
    gl.post_game(POST_RAW, POST_VIEW, MODE, DD)
    assert gl.failed
