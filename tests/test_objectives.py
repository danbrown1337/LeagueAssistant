from leagueassistant.objectives import objectives_view

TEAM = {"Ally": "ORDER", "Foe": "CHAOS"}


def drake(t, who, kind):
    return {"EventName": "DragonKill", "EventTime": t, "KillerName": who, "DragonType": kind}


def timer(view, key):
    return next((t for t in view["timers"] if t["key"] == key), None)


def test_first_dragon_spawns_at_five_minutes():
    v = objectives_view([], TEAM, "ORDER", 60)
    assert timer(v, "dragon")["at"] == 300 and timer(v, "baron") is None


def test_dragon_respawns_five_minutes_after_a_kill_and_soul_point():
    events = [drake(400, "Ally", "Fire"), drake(800, "Ally", "Water"), drake(1200, "Foe", "Air"), drake(1600, "Ally", "Fire")]
    v = objectives_view(events, TEAM, "ORDER", 1650)
    assert v["dragons"] == {"ally": ["Infernal", "Ocean", "Infernal"], "enemy": ["Cloud"]}
    assert timer(v, "dragon") == {"key": "dragon", "label": "Dragon", "at": 1900, "side": None}
    assert v["soulPoint"] == "ally" and v["soul"] is None


def test_soul_brings_elder_six_minutes_later():
    events = [drake(t, "Foe", "Earth") for t in (400, 800, 1200, 1600)]
    v = objectives_view(events, TEAM, "ORDER", 1700)
    assert v["soul"] == "enemy" and v["soulPoint"] is None
    assert timer(v, "dragon")["label"] == "Elder Dragon" and timer(v, "dragon")["at"] == 1960
    v = objectives_view(events + [drake(2000, "Ally", "Elder")], TEAM, "ORDER", 2010)
    assert timer(v, "dragon")["at"] == 2360 and len(v["dragons"]["ally"]) == 0  # Elder isn't an elemental drake


def test_baron_and_inhibitors():
    events = [
        {"EventName": "BaronKill", "EventTime": 1500, "KillerName": "Foe"},
        {"EventName": "InhibKilled", "EventTime": 1600, "InhibKilled": "Barracks_T1_C1"},
        {"EventName": "InhibKilled", "EventTime": 1610, "InhibKilled": "Barracks_T2_R1"},
        {"EventName": "InhibKilled", "EventTime": 1000, "InhibKilled": "Barracks_T2_L1"},
        {"EventName": "InhibRespawned", "EventTime": 1300, "InhibRespawned": "Barracks_T2_L1"},
    ]
    v = objectives_view(events, TEAM, "ORDER", 1700)
    assert timer(v, "baron")["at"] == 1860
    assert timer(v, "inhib-ORDER-mid") == {"key": "inhib-ORDER-mid", "at": 1900, "side": "ally", "label": "Your mid inhibitor"}
    assert timer(v, "inhib-CHAOS-bot")["label"] == "Their bot inhibitor"
    assert timer(v, "inhib-CHAOS-top") is None  # respawned
