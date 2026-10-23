from leagueassistant import itemizer


def item(name, total, tags, stats=None, desc="", frm=None, maps=("11", "12")):
    return {"name": name, "gold": {"total": total, "purchasable": True}, "tags": tags, "stats": stats or {},
            "description": desc, "from": [str(f) for f in frm or []], "maps": {m: True for m in maps}}


ITEMS = {
    1: item("Ludens", 2750, ["SpellDamage"], {"FlatMagicDamageMod": 90}),
    2: item("Sorcs", 1100, ["Boots"], {"FlatMovementSpeedMod": 45}),
    3: item("Morello", 2850, ["SpellDamage", "Health"], {"FlatMagicDamageMod": 75}, "applies 40% Wounds", frm=[30]),
    30: item("Oblivion Orb", 800, ["SpellDamage"], {"FlatMagicDamageMod": 25}),
    4: item("Mortal Reminder", 3000, ["Damage", "CriticalStrike"], {"FlatPhysicalDamageMod": 35, "FlatCritChanceMod": 0.25}, "Wounds"),
    5: item("Lord Doms", 3300, ["Damage", "ArmorPenetration"], {"FlatPhysicalDamageMod": 35, "FlatCritChanceMod": 0.25}),
    6: item("Infinity Edge", 3400, ["Damage", "CriticalStrike"], {"FlatPhysicalDamageMod": 70, "FlatCritChanceMod": 0.25}),
    7: item("Thornmail", 2450, ["Armor", "Health"], {"FlatArmorMod": 75, "FlatHPPoolMod": 150}, "Wounds"),
    8: item("Banshee", 3000, ["SpellBlock", "SpellDamage"], {"FlatMagicDamageMod": 105, "FlatSpellBlockMod": 40}),
    9: item("Zhonya", 3250, ["Armor", "SpellDamage"], {"FlatMagicDamageMod": 105, "FlatArmorMod": 50}),
    10: item("Mercs", 1250, ["Boots", "SpellBlock", "Tenacity"], {"FlatSpellBlockMod": 20}),
    11: item("Steelcaps", 1200, ["Boots", "Armor"], {"FlatArmorMod": 25}),
    12: item("Bandlepipes", 2300, ["Health", "SpellBlock", "AttackSpeed"], {"FlatSpellBlockMod": 20, "FlatHPPoolMod": 200}),
    13: item("Wits End", 2800, ["SpellBlock", "AttackSpeed"], {"FlatSpellBlockMod": 45, "PercentAttackSpeedMod": 0.5}),
    14: item("Randuin", 2700, ["Armor", "Health"], {"FlatArmorMod": 75, "FlatHPPoolMod": 350}),
    15: item("Ludens Copy", 2750, ["SpellDamage"], {"FlatMagicDamageMod": 90}),
}
CHAMPS = {
    1: {"id": "Ahri", "tags": ["Mage", "Assassin"], "info": {"attack": 3, "magic": 8}},
    2: {"id": "Soraka", "tags": ["Support", "Mage"], "info": {"attack": 2, "magic": 7}},
    3: {"id": "Vladimir", "tags": ["Mage", "Fighter"], "info": {"attack": 2, "magic": 8}},
    4: {"id": "Malphite", "tags": ["Tank", "Fighter"], "info": {"attack": 5, "magic": 7}},
    5: {"id": "Ornn", "tags": ["Tank", "Fighter"], "info": {"attack": 5, "magic": 3}},
    6: {"id": "Zed", "tags": ["Assassin"], "info": {"attack": 9, "magic": 1}},
    7: {"id": "Jinx", "tags": ["Marksman"], "info": {"attack": 9, "magic": 2}},
    8: {"id": "Syndra", "tags": ["Mage"], "info": {"attack": 2, "magic": 9}},
}


class FakeDD:
    def item(self, iid):
        return ITEMS.get(iid, {})

    def all_items(self):
        return ITEMS

    def champion(self, cid):
        return CHAMPS.get(cid, {})

    def item_name(self, iid):
        return ITEMS.get(iid, {}).get("name", f"#{iid}")

    def item_icon(self, iid):
        return None

    def item_cost(self, iid):
        return ITEMS.get(iid, {}).get("gold", {}).get("total", 0)


DD = FakeDD()
AP_BUILD = {"core": [{"id": 1}, {"id": 2}, {"id": 15}], "later": []}
ADC_BUILD = {"core": [{"id": 6}, {"id": 2}], "later": []}


def enemy(cid, items=(), kills=2, deaths=2):
    return {"id": cid, "name": CHAMPS[cid]["id"], "items": [{"id": i} for i in items], "kills": kills, "deaths": deaths,
            "itemGold": sum(DD.item_cost(i) for i in items)}


def test_core_comes_first_in_a_neutral_game():
    a = itemizer.advise({"id": 1, "items": []}, [enemy(7), enemy(6)], AP_BUILD, DD, game_time=300)
    assert a["style"] == "ap"
    assert a["next"]["name"] == "Ludens" and a["next"]["purpose"] == "core"


def test_heavy_healing_gets_anti_heal_for_your_style_and_jumps_the_core():
    enemies = [enemy(2), enemy(3, [1]), enemy(4)]
    enemies[0]["name"] = "Soraka"
    a = itemizer.advise({"id": 1, "items": [{"id": 1}, {"id": 2}]}, enemies + [enemy(8)], AP_BUILD, DD, gold=900,
                        game_time=900)
    assert a["next"]["name"] == "Morello"
    assert "Soraka" in a["next"]["reason"] and "Vladimir" in a["next"]["reason"]
    # Can't afford it yet: buy the part you can.
    assert a["affordable"]["name"] == "Oblivion Orb"


def test_stacked_armor_gets_armor_pen_for_an_adc():
    tanks = [enemy(4, [7, 14]), enemy(5, [7, 14]), enemy(8)]
    a = itemizer.advise({"id": 7, "items": [{"id": 6}, {"id": 11}]}, tanks, ADC_BUILD, DD, game_time=1500)
    assert a["style"] == "adc"
    assert any(p["name"] == "Lord Doms" for p in [a["next"], *a["situational"]])


def test_fed_physical_assassin_gets_armor_for_a_mage():
    a = itemizer.advise({"id": 1, "items": [{"id": 1}, {"id": 2}]}, [enemy(6, [6], kills=9, deaths=1), enemy(7, [6]), enemy(5)],
                        AP_BUILD, DD, game_time=1200)
    assert a["next"]["name"] == "Zhonya"
    assert "Zed is fed" in a["next"]["reason"]


def test_magic_heavy_team_swaps_boots_and_picks_magic_resist_by_stats():
    mages = [enemy(8, [1], kills=7, deaths=1), enemy(3, [1]), enemy(1, [1]), enemy(2)]
    a = itemizer.advise({"id": 7, "items": [{"id": 6}]}, mages, ADC_BUILD, DD, game_time=900)
    assert a["boots"]["name"] == "Mercs" and a["next"]["name"] == "Mercs"
    picks = [p["name"] for p in a["situational"]]
    assert "Wits End" in picks and "Bandlepipes" not in picks  # Bandlepipes gives no damage


def test_owning_an_item_for_a_threat_stops_suggesting_more():
    enemies = [enemy(2), enemy(3, [1]), enemy(4)]
    a = itemizer.advise({"id": 1, "items": [{"id": 1}, {"id": 3}]}, enemies, AP_BUILD, DD, game_time=900)
    assert "Morello" not in [p["name"] for p in [a["next"], *a["situational"]]]


def test_urgent_picks_wait_until_you_have_boots():
    enemies = [enemy(2), enemy(3, [1]), enemy(4), enemy(8)]
    a = itemizer.advise({"id": 1, "items": [{"id": 1}]}, enemies, AP_BUILD, DD, game_time=900)
    # Boots first (swapped to Mercury's here, since the enemy damage is mostly magic), anti-heal after.
    assert "Boots" in ITEMS[a["next"]["id"]]["tags"]
    assert "Morello" in [p["name"] for p in a["situational"]]


def test_aram_build_gets_boots_from_the_rift_build_when_missing():
    from leagueassistant import advisor
    from leagueassistant.ugg import Build

    def build(core):
        return Build(1, "aram", 50, 100, (8100, 8200), [], [], [], [], core, [], [], "QWE")

    class UGG:
        def aram_build(self, cid):
            return build([1, 15, 3])

        def build(self, cid, role=None):
            return build([1, 2, 15])

    class DDWithRunes(FakeDD):
        runes, spells = {}, {}

        def rune_icon(self, r):
            return None

        def runes_in_tree(self, runes, tree):
            return []

        def spell_icon(self, s):
            return None

        def champion_name(self, cid):
            return "Ahri"

        def champion_icon(self, cid):
            return None

    v = advisor.aram_build_view(1, UGG(), DDWithRunes())
    assert [i["name"] for i in v["core"]] == ["Ludens", "Sorcs", "Ludens Copy", "Morello"]
    assert v["core"][1]["added"] and "Summoner's Rift" in v["bootsNote"]


def test_starting_items_at_the_start_of_the_game():
    build = {**AP_BUILD, "starting": [{"id": 30}, {"id": 2}]}
    a = itemizer.advise({"id": 1, "items": []}, [enemy(7)], build, DD, game_time=5)
    assert [i["name"] for i in a["start"]["items"]] == ["Oblivion Orb", "Sorcs"] and a["start"]["cost"] == 1900
    assert itemizer.advise({"id": 1, "items": [{"id": 30}]}, [enemy(7)], build, DD, game_time=60)["start"] is None
    assert itemizer.advise({"id": 1, "items": []}, [enemy(7)], build, DD, game_time=400)["start"] is None
