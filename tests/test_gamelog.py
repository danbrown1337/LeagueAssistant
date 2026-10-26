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


def post_view(value, ranked, me_value=99.0):
    others = {"label": "x", "value": value, "ranked": ranked, "players": 9}
    return {**POST_VIEW, "lobbyEstimate": others, "average": {**others, "value": me_value}}


ARAM = {"kind": "aram", "label": "ARAM", "queueId": 450, "gameMode": "ARAM"}
DRAFT = {"kind": "sr", "label": "Normal Draft", "queueId": 400, "gameMode": "CLASSIC"}


def test_mmr_estimate_is_per_queue_and_weighted_by_ranked_players(tmp_path):
    path = tmp_path / "games.jsonl"
    gl = GameLog(path)
    gl.post_game({**POST_RAW, "gameId": 1}, post_view(12.0, 9), ARAM, DD)   # Gold IV, 9 ranked
    gl.post_game({**POST_RAW, "gameId": 2}, post_view(16.0, 3), ARAM, DD)   # Platinum IV, 3 ranked
    gl.post_game({**POST_RAW, "gameId": 3}, post_view(4.0, 9), DRAFT, DD)   # Bronze IV, another queue
    est = gl.mmr_estimate(ARAM)
    assert (est["value"], est["label"], est["games"], est["players"], est["mode"]) == (13.0, "Gold III", 2, 12, "ARAM")
    assert gl.mmr_estimate(DRAFT)["label"] == "Bronze IV"
    # Survives a restart, and the summary lists every queue.
    again = GameLog(path)
    assert again.mmr_estimate(ARAM)["value"] == 13.0
    assert [m["mode"] for m in again.mmr_summary()] == ["ARAM", "Normal Draft"]


def test_mmr_estimate_uses_the_last_ten_games(tmp_path):
    gl = GameLog(tmp_path / "games.jsonl")
    for n in range(12):
        gl.post_game({**POST_RAW, "gameId": n}, post_view(0.0 if n < 2 else 20.0, 5), ARAM, DD)
    est = gl.mmr_estimate(ARAM)
    assert est["games"] == 10 and est["value"] == 20.0


def test_given_items_arent_purchases_and_the_start_counts_as_following(tmp_path):
    from tests import test_itemizer

    test_itemizer.ITEMS[40] = {"name": "Biscuit", "tags": [], "gold": {"total": 50, "purchasable": False}, "consumed": True}
    try:
        gl = GameLog(tmp_path / "games.jsonl")
        start = live(5, [], 1)
        start["build"]["starting"] = [{"id": 30}]
        gl.live(start, 9, DD)
        bought = live(20, [30], 1)
        bought["build"]["starting"] = [{"id": 30}]
        gl.live(bought, 9, DD)
        gl.live({**live(130, [30, 40], 1), "build": bought["build"]}, 9, DD)
        assert [(p["name"], p["followedAdvice"]) for p in gl.current.purchases] == [("Oblivion Orb", True)]
    finally:
        del test_itemizer.ITEMS[40]
