import json

import pytest

from leagueassistant import collector
from leagueassistant.riot import KeyRejected, RateLimiter, RiotAPI


def participant(pid, champ, pos, win, **extra):
    return {
        "participantId": pid, "teamId": 100 if pid <= 5 else 200, "championId": champ, "teamPosition": pos,
        "win": win, "summoner1Id": 4, "summoner2Id": 14,
        "perks": {"statPerks": {"offense": 5008, "flex": 5008, "defense": 5011},
                  "styles": [{"style": 8100, "selections": [{"perk": p} for p in (8112, 8139, 8143, 8106)]},
                             {"style": 8200, "selections": [{"perk": 8226}, {"perk": 8210}]}]},
        **{f"item{i}": 3000 + i for i in range(7)}, **extra,
    }


MATCH = {"metadata": {"matchId": "NA1_1"},
         "info": {"queueId": 420, "gameVersion": "16.22.712.3456", "gameStartTimestamp": 1_700_000_000_000,
                  "gameDuration": 1800,
                  "participants": [participant(1, 103, "MIDDLE", True, playerAugment1=7, playerAugment2=0),
                                   participant(6, 157, "MIDDLE", False)]}}
TIMELINE = {"info": {"frames": [{"events": [
    {"type": "ITEM_PURCHASED", "participantId": 1, "itemId": 1056, "timestamp": 1000},
    {"type": "ITEM_PURCHASED", "participantId": 1, "itemId": 2003, "timestamp": 1100},
    {"type": "ITEM_UNDO", "participantId": 1, "beforeId": 2003, "afterId": 0, "timestamp": 1200},
    {"type": "SKILL_LEVEL_UP", "participantId": 1, "skillSlot": 1, "levelUpType": "NORMAL"},
    {"type": "SKILL_LEVEL_UP", "participantId": 1, "skillSlot": 3, "levelUpType": "NORMAL"},
    {"type": "SKILL_LEVEL_UP", "participantId": 1, "skillSlot": 1, "levelUpType": "EVOLVE"},
]}]}}


def test_save_match():
    db = collector.connect(":memory:")
    collector.save_match(db, MATCH, TIMELINE, "emerald")
    m = db.execute("SELECT queue, patch, started_at, remake, tier FROM matches").fetchone()
    assert m == (420, "16.22", 1_700_000_000, 0, "emerald")
    row = db.execute("SELECT champion_id, position, win, rune_trees, runes, shards, items, purchases, skills, augments "
                     "FROM participants WHERE participant_id = 1").fetchone()
    champ, pos, win, trees, runes, shards, items, purchases, skills, augments = row
    assert (champ, pos, win) == (103, "mid", 1)
    assert json.loads(trees) == [8100, 8200]
    assert json.loads(runes) == [8112, 8139, 8143, 8106, 8226, 8210]
    assert json.loads(shards) == [5008, 5008, 5011]
    assert json.loads(items) == [3000, 3001, 3002, 3003, 3004, 3005, 3006]
    assert json.loads(purchases) == [[1000, 1056]]  # the potion was undone
    assert skills == "QE"
    assert json.loads(augments) == [7]


class FakeAPI:
    def __init__(self):
        self.calls = []

    def apex_league(self, tier):
        return [{"puuid": f"{tier}-1", "tier": tier.upper(), "rank": "I", "leaguePoints": 900}]

    def league_entries(self, tier, division, page):
        return [{"puuid": f"{tier}-{division}", "tier": tier.upper(), "rank": division}] if page == 1 else []

    def match_ids(self, puuid, count, queue, start_time):
        self.calls.append((puuid, queue, start_time))
        return ["NA1_1"] if queue == 420 else []

    def match(self, mid):
        return MATCH

    def timeline(self, mid):
        return TIMELINE


def test_collector_visits_players_and_skips_known_matches():
    api, db = FakeAPI(), collector.connect(":memory:")
    c = collector.Collector(api, db, ["master", "diamond"], [420, 450], batch=10, clock=lambda: 2_000_000)
    assert c.step() == 1  # five players found NA1_1, only saved once
    assert db.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 5
    assert db.execute("SELECT COUNT(*) FROM players WHERE visited_at = 2000000").fetchone()[0] == 5
    assert api.calls[0][2] == 2_000_000 - collector.FIRST_LOOKBACK
    api.calls.clear()
    c.clock = lambda: 2_000_100
    c.step()
    assert {start for _, _, start in api.calls} == {2_000_000}  # only matches since the last visit


def test_rate_limiter_waits_for_the_window():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    rl = RateLimiter([(2, 1), (3, 10)], sleep=sleep, clock=lambda: now[0])
    for _ in range(4):
        rl.wait()
    assert slept == [1.0, 9.0]
    rl.update("20:1,100:120")
    assert rl.limits == [(20, 1), (100, 120)]


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        raise RuntimeError(self.status_code)


def test_riot_api_retries_rate_limits_and_rejects_bad_keys():
    slept = []
    api = RiotAPI("key", platform="na1", sleep=slept.append)
    replies = [Resp(429, headers={"Retry-After": "3"}), Resp(200, {"ok": 1}), Resp(404), Resp(403)]
    api.session.get = lambda *a, **k: replies.pop(0)
    assert api.match("NA1_1") == {"ok": 1} and slept == [3.0]
    assert api.match("NA1_2") is None
    with pytest.raises(KeyRejected):
        api.match("NA1_3")
    assert api.base == "https://americas.api.riotgames.com"
