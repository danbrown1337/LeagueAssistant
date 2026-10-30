"""Adjusts the U.gg build to the game in progress.

U.gg's build is the most common one for a champion, not the one for this
game. Every update we look at the enemy team (damage type, healing, armor and
magic resist, crowd control, who is fed), pick situational items that suit
your champion from Data Dragon's item data, and decide what to buy next, with
the reason.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .ddragon import DDragon

# Champions whose kits heal or drain a lot (Data Dragon has no stat for this).
HEALERS = {
    "Aatrox", "Briar", "DrMundo", "Fiddlesticks", "Gwen", "Illaoi", "Kayn", "Maokai", "Milio", "Nami",
    "Nasus", "Olaf", "Renekton", "Seraphine", "Sona", "Soraka", "Swain", "Sylas", "Trundle", "Vladimir",
    "Volibear", "Warwick", "Yuumi", "Zac", "Yorick", "Belveth",
}
# Champions with reliable hard crowd control (stuns, roots, knock-ups, suppression).
HARD_CC = {
    "Alistar", "Amumu", "Annie", "Ashe", "Blitzcrank", "Braum", "Galio", "Leona", "Lissandra", "Lux",
    "Malphite", "Malzahar", "Maokai", "Morgana", "Nautilus", "Neeko", "Ornn", "Poppy", "Rammus", "Rell",
    "Sejuani", "Skarner", "Taric", "Thresh", "TwistedFate", "Veigar", "Vi", "Warwick", "Zyra", "Leblanc",
    "Rakan", "JarvanIV", "Sion", "Zac", "Gragas", "Cassiopeia", "Xerath", "Pyke", "Nami",
}

MAP_SR, MAP_ARAM = "11", "12"


@dataclass
class Enemy:
    id: int
    name: str
    weight: float  # how much this enemy matters, from item gold and KDA
    magic: float  # share of their damage that is magic, 0..1
    tags: list[str]
    items: list[int]
    armor: int
    mr: int
    kills: int
    deaths: int
    slug: str

    @property
    def fed(self) -> bool:
        lead = self.kills - self.deaths
        return lead >= 3 or (lead >= 2 and self.weight >= 1.6)


def _stat(item: dict, key: str) -> float:
    return item.get("stats", {}).get(key, 0)


def _enemies(views: list[dict], dd: DDragon) -> list[Enemy]:
    gold = [v.get("itemGold", 0) for v in views]
    avg = sum(gold) / len(gold) if gold else 0
    out = []
    for v in views:
        champ = dd.champion(v.get("id") or 0)
        info, tags = champ.get("info", {}), champ.get("tags", [])
        base = info.get("magic", 5) / max(info.get("magic", 5) + info.get("attack", 5), 1)
        ids = [i["id"] for i in v.get("items", [])]
        ap_gold = ad_gold = 0
        for iid in ids:
            it = dd.item(iid)
            cost = it.get("gold", {}).get("total", 0)
            t = it.get("tags", [])
            if "SpellDamage" in t:
                ap_gold += cost
            elif {"Damage", "CriticalStrike", "AttackSpeed"} & set(t):
                ad_gold += cost
        magic = base
        if ap_gold + ad_gold >= 1500:
            magic = 0.4 * base + 0.6 * ap_gold / (ap_gold + ad_gold)
        # The constant keeps early-game gold differences from looking like a big lead.
        weight = (v.get("itemGold", 0) + 1500) / (avg + 1500)
        weight *= 1 + 0.08 * max(0, v.get("kills", 0) - v.get("deaths", 0))
        if tags[:1] == ["Tank"] and not {"Assassin", "Mage", "Marksman"} & set(tags):
            weight *= 0.6  # tanks deal less of the team's damage
        out.append(Enemy(
            id=v.get("id") or 0, name=v.get("name") or "", weight=round(min(max(weight, 0.4), 3.0), 2),
            magic=magic, tags=tags, items=ids,
            armor=sum(_stat(dd.item(i), "FlatArmorMod") for i in ids),
            mr=sum(_stat(dd.item(i), "FlatSpellBlockMod") for i in ids),
            kills=v.get("kills", 0), deaths=v.get("deaths", 0), slug=champ.get("id", ""),
        ))
    return out


def threats(enemy_views: list[dict], dd: DDragon) -> dict:
    enemies = _enemies(enemy_views, dd)
    total = sum(e.weight for e in enemies) or 1
    magic = sum(e.weight * e.magic for e in enemies) / total
    # Finished items only: starter items like Doran's Blade have a little lifesteal too.
    lifesteal = lambda e: any({"LifeSteal", "SpellVamp"} & set(dd.item(i).get("tags", []))
                              and dd.item(i).get("gold", {}).get("total", 0) >= 2000 for i in e.items)
    healing = sum(e.weight for e in enemies if e.slug in HEALERS) + 0.5 * sum(e.weight for e in enemies if lifesteal(e))
    tanky_armor = [e for e in enemies if e.armor >= 80 or ("Tank" in e.tags and e.armor >= 40)]
    tanky_mr = [e for e in enemies if e.mr >= 60 or ("Tank" in e.tags and e.mr >= 40)]
    cc = [e for e in enemies if e.slug in HARD_CC]
    fed_assassins = [e for e in enemies if e.fed and "Assassin" in e.tags]
    marksmen = [e for e in enemies if "Marksman" in e.tags]
    return {
        "enemies": enemies,
        "magicShare": round(magic, 2),
        "healing": round(healing, 1),
        "healers": [e.name for e in enemies if e.slug in HEALERS or lifesteal(e)],
        "tankyArmor": [e.name for e in tanky_armor],
        "tankyMr": [e.name for e in tanky_mr],
        "cc": [e.name for e in cc],
        "fedAssassins": [e for e in fed_assassins],
        "fed": [e for e in enemies if e.fed],
        "marksmen": [e.name for e in marksmen],
    }


# --- Your champion -------------------------------------------------------------

def play_style(build: dict, dd: DDragon, champion_tags: list[str]) -> str:
    """ap, ad, adc (crit/attack speed) or tank, from the items U.gg says you build."""
    tags = [set(dd.item(i["id"]).get("tags", [])) for i in build.get("core", [])]
    ap = sum("SpellDamage" in t for t in tags)
    ad = sum(bool({"Damage", "CriticalStrike"} & t) and "SpellDamage" not in t for t in tags)
    if ap >= 2 or (ap and ap >= ad):
        return "ap"
    if ad == 0:
        return "tank"
    if "Marksman" in champion_tags or sum(bool({"CriticalStrike", "AttackSpeed"} & t) for t in tags) >= 2:
        return "adc"
    return "ad"


def fit(item: dict, style: str) -> float:
    """How well an item suits a play style, from the stats it gives; 0 means not at all.

    Uses stats rather than tags: some items are tagged for stats they don't give.
    """
    st = lambda k: _stat(item, k)
    ap, ad = st("FlatMagicDamageMod"), st("FlatPhysicalDamageMod")
    attack = ad or st("PercentAttackSpeedMod") >= 0.3 or st("FlatCritChanceMod")
    if style == "ap":
        return 1.0 if ap else 0
    if style == "tank":
        if ap or attack:
            return 0
        return 1.0 if st("FlatHPPoolMod") or st("FlatArmorMod") or st("FlatSpellBlockMod") else 0
    if ap or not attack:
        return 0
    if style == "adc":
        return 1.0 + 0.5 * bool(st("FlatCritChanceMod") or st("PercentAttackSpeedMod"))
    return 1.0 + 0.5 * bool(st("FlatHPPoolMod"))


def _legendaries(dd: DDragon, game_map: str) -> dict[int, dict]:
    out = {}
    for iid, it in dd.all_items().items():
        g = it.get("gold", {})
        if (g.get("purchasable") and it.get("maps", {}).get(game_map) and not it.get("into")
                and g.get("total", 0) >= 2000 and it.get("inStore", True) and not it.get("requiredChampion")
                and "Boots" not in it.get("tags", []) and "Consumable" not in it.get("tags", [])):
            out[iid] = it
    return out


def owned_with_upgrades(item_ids, dd: DDragon) -> set[int]:
    """What you own, counting items that upgraded in game as the item they came from too.

    Manamune becomes Muramana once stacked and the item id changes; without this the build
    would tell you to buy Manamune again.
    """
    out = set(item_ids)
    for iid in list(out):
        out.update(dd.upgraded_from(iid))
        # A map's copy of an item (ARAM Hubris 126697) also counts as the standard one (6697), and the other
        # way round, so a build listing either id matches.
        out.add(dd.base_item(iid))
    return out


def is_boots(iid: int, dd: DDragon) -> bool:
    return "Boots" in dd.item(iid).get("tags", [])


def is_finished_boots(iid: int, dd: DDragon) -> bool:
    """Any boots past plain Boots: tier 2, tier 3, and ones you can't buy (Symbiotic Soles).

    Plain Boots and the rune's Slightly Magical Footwear cost 300 and are only a start.
    """
    it = dd.item(iid)
    return "Boots" in it.get("tags", []) and it.get("gold", {}).get("total", 0) > 300


def _parts_owned(iid: int, owned: set[int], dd: DDragon) -> list[int]:
    """Owned items that build into `iid`, from its recipe or from theirs (Magical Footwear -> boots)."""
    recipe = {int(p) for p in dd.item(iid).get("from", [])}
    return sorted(p for p in owned if p in recipe or str(iid) in dd.item(p).get("into", []))


# Items that stand in for another in recipes: the rune's Slightly Magical Footwear works as Boots.
STANDS_IN_FOR = {2422: 1001}


def finish_cost(iid: int, inventory: list[int], dd: DDragon) -> tuple[int, list[int]]:
    """Gold left to finish `iid` with what you hold, the way the shop prices it, and the parts used.

    Walks the whole recipe, so a Long Sword toward the Pickaxe inside Infinity Edge counts, and
    each item you hold is used once (Berserker's Greaves needs both of your Daggers).
    """
    inv = Counter(STANDS_IN_FOR.get(i, i) for i in inventory)
    used: list[int] = []

    def cost(item: int, top: bool) -> int:
        if not top and inv[item] > 0:
            inv[item] -= 1
            used.append(item)
            return 0
        it = dd.item(item)
        recipe = [int(p) for p in it.get("from", [])]
        if not recipe:
            return dd.item_cost(item)
        return it.get("gold", {}).get("base", 0) + sum(cost(p, False) for p in recipe)

    total = cost(iid, True)
    originals = {v: k for k, v in STANDS_IN_FOR.items()}
    shown = [originals[u] if u in originals and u not in inventory else u for u in used]
    return total, shown


def _boots(dd: DDragon, game_map: str) -> dict[int, dict]:
    return {iid: it for iid, it in dd.all_items().items()
            if "Boots" in it.get("tags", []) and it.get("gold", {}).get("purchasable")
            and it.get("maps", {}).get(game_map) and 900 <= it.get("gold", {}).get("total", 0) <= 1500}


# --- Advice -------------------------------------------------------------------

def _ref(iid: int, dd: DDragon) -> dict:
    return {"id": iid, "name": dd.item_name(iid), "icon": dd.item_icon(iid), "cost": dd.item_cost(iid)}


def _names(names: list[str], n: int = 3) -> str:
    names = list(dict.fromkeys(names))
    return ", ".join(names[:n]) + (f" and {len(names) - n} more" if len(names) > n else "")


def advise(me: dict, enemy_views: list[dict], build: dict, dd: DDragon, gold: int = 0,
           game_time: float = 0, aram: bool = False) -> dict:
    """What to buy next for this game, situational picks and the threats behind them."""
    game_map = MAP_ARAM if aram else MAP_SR
    owned = owned_with_upgrades((i["id"] for i in me.get("items", [])), dd)
    has_boots = any(is_finished_boots(i, dd) for i in owned)  # you can only own one pair
    owned_parts = owned | {int(p) for i in owned for p in dd.item(i).get("from", [])}
    style = play_style(build, dd, dd.champion(me.get("id") or 0).get("tags", []))
    t = threats(enemy_views, dd)
    legend = _legendaries(dd, game_map)
    ugg_wr = {o["id"]: o.get("winRate") or 0 for slot in build.get("later", []) for o in slot}

    def best(pred, why: str, priority: int, purpose: str, stat: str | None = None):
        # Items U.gg players build on this champion always fit, whatever their stats.
        options = [(iid, 1.5 if iid in ugg_wr else fit(it, style)) for iid, it in legend.items() if pred(it)]
        options = [(iid, f) for iid, f in options if f > 0]
        if not options:
            return None
        if any(iid in owned for iid, _ in options):
            return None  # already handled
        # Best fit, then what U.gg players pick, then the most of the stat that's needed.
        iid = max(options, key=lambda o: (o[1], ugg_wr.get(o[0], 0), _stat(legend[o[0]], stat) if stat else 0,
                                         o[0] in owned_parts))[0]
        return {**_ref(iid, dd), "reason": why, "priority": priority, "purpose": purpose}

    tags = lambda it: set(it.get("tags", []))
    picks = []

    # Healing
    if t["healing"] >= 1.5:
        why = f"Anti-heal: {_names(t['healers'])} heal a lot."
        picks.append(best(lambda it: "Wounds" in it.get("description", ""), why, 3 if t["healing"] >= 2.5 else 2, "antiheal"))
    # Tanks
    if style in ("ad", "adc") and len(t["tankyArmor"]) >= 2:
        why = f"Armor penetration: {_names(t['tankyArmor'])} are stacking armor."
        picks.append(best(lambda it: "ArmorPenetration" in tags(it), why, 3 if len(t["tankyArmor"]) >= 3 else 2, "pen"))
    if style == "ap" and len(t["tankyMr"]) >= 2:
        why = f"Magic penetration: {_names(t['tankyMr'])} are stacking magic resist."
        picks.append(best(lambda it: "MagicPenetration" in tags(it), why, 3 if len(t["tankyMr"]) >= 3 else 2, "pen"))
    # Damage you take
    magic = t["magicShare"]
    fed_ap = [e.name for e in t["fed"] if e.magic >= 0.6]
    fed_ad = [e.name for e in t["fed"] if e.magic < 0.4]
    burst_ap = [e.name for e in t["fedAssassins"] if e.magic >= 0.6]
    burst_ad = [e.name for e in t["fedAssassins"] if e.magic < 0.4]
    if magic >= 0.6 and (fed_ap or magic >= 0.7):
        why = f"Magic resist: {round(magic * 100)}% of the enemy damage is magic" + (f", and {_names(fed_ap)} {'is' if len(set(fed_ap)) == 1 else 'are'} fed." if fed_ap else ".")
        picks.append(best(lambda it: _stat(it, "FlatSpellBlockMod") > 0, why, 3 if burst_ap else 2, "defense",
                          "FlatSpellBlockMod"))
    if magic <= 0.4 and (fed_ad or magic <= 0.3):
        why = f"Armor: {round((1 - magic) * 100)}% of the enemy damage is physical" + (f", and {_names(fed_ad)} {'is' if len(set(fed_ad)) == 1 else 'are'} fed." if fed_ad else ".")
        picks.append(best(lambda it: _stat(it, "FlatArmorMod") > 0, why, 3 if burst_ad else 2, "defense",
                          "FlatArmorMod"))
    picks = [p for p in picks if p]
    # One pick per item, highest priority first.
    seen, situational = set(), []
    for p in sorted(picks, key=lambda p: -p["priority"]):
        if p["id"] not in seen:
            seen.add(p["id"])
            situational.append(p)

    # Boots
    boots_note = None
    boots = _boots(dd, game_map)
    if not aram and not has_boots:
        merc = next((i for i, it in boots.items() if {"SpellBlock", "Tenacity"} <= tags(it)), None)
        steel = next((i for i, it in boots.items() if "Armor" in tags(it)), None)
        if merc and (magic >= 0.65 or len(t["cc"]) >= 3):
            reason = (f"{round(magic * 100)}% of the enemy damage is magic" if magic >= 0.65 else
                      f"{_names(t['cc'])} have hard crowd control")
            boots_note = {**_ref(merc, dd), "reason": f"Mercury's Treads: {reason}.", "priority": 2, "purpose": "boots"}
        elif steel and magic <= 0.35 and t["marksmen"]:
            boots_note = {**_ref(steel, dd), "reason": f"Plated Steelcaps: {round((1 - magic) * 100)}% of the enemy damage is physical, mostly from basic attacks.",
                          "priority": 2, "purpose": "boots"}
        if boots_note and style in ("ap", "adc") and boots_note["priority"] < 3:
            # Damage boots matter a lot for carries; only swap when the threat is clear.
            if not (magic >= 0.75 or magic <= 0.25 or len(t["cc"]) >= 4):
                boots_note = None

    # Buy next
    core = [i for i in build.get("core", []) if i["id"] not in owned]
    core_boots = [i for i in core if is_boots(i["id"], dd)]
    if has_boots:
        core = [i for i in core if i not in core_boots]  # any finished boots count
    elif boots_note and core_boots:
        core = [boots_note if i in core_boots else i for i in core]
    have_core = len(build.get("core", [])) - len([i for i in build.get("core", []) if i["id"] not in owned])
    urgent = [p for p in situational if p["priority"] >= 3]

    # Urgent picks can jump core legendaries, but never boots.
    boots_next = bool(core) and is_boots(core[0]["id"], dd)
    if urgent and (have_core >= 1 or game_time >= 15 * 60) and not boots_next:
        nxt = urgent[0]
    elif core:
        c = core[0]
        nxt = c if c.get("reason") else {**_ref(c["id"], dd), "reason": "Next item in the U.gg core build.", "priority": 1, "purpose": "core"}
    elif situational:
        nxt = situational[0]
    else:
        # No second pair of boots, but an upgrade of the pair you have (tier 3) is fine.
        later = [o for slot in build.get("later", []) for o in slot if o["id"] not in owned
                 and not (has_boots and is_boots(o["id"], dd) and not _parts_owned(o["id"], owned, dd))]
        top = max(later, key=lambda o: o.get("winRate") or 0, default=None)
        nxt = top and {**_ref(top["id"], dd), "priority": 1, "purpose": "later",
                       "reason": f"Highest win rate late item on U.gg ({round((top.get('winRate') or 0) * 100, 1)}%)."}

    # At the start of the game: U.gg's starting items, until you've bought one of them.
    starting = [i for i in build.get("starting", []) if i.get("id")]
    start = None
    # Gone as soon as you own any real item, even one that isn't on U.gg's list (Boots of Swiftness
    # instead of Boots + potions + Serrated Dirk): you've already started.
    bought = {i for i in owned if dd.item(i) and not {"Consumable", "Trinket"} & set(dd.item(i).get("tags", []))}
    if starting and game_time < 5 * 60 and not bought:
        start = {"items": [_ref(i["id"], dd) for i in starting],
                 "cost": sum(dd.item_cost(i["id"]) for i in starting),
                 "reason": "U.gg's most common start for this champion."}

    # If you already own parts of it, say so and how much more it costs to finish.
    if nxt:
        remaining, have = finish_cost(nxt["id"], [i["id"] for i in me.get("items", [])], dd)
        if have:
            nxt = {**nxt, "upgrade": {"have": [_ref(p, dd) for p in have], "remaining": remaining}}

    # Something you can buy with the gold you have now.
    affordable = None
    if nxt and gold and nxt["cost"] > gold:
        parts = [int(p) for p in dd.item(nxt["id"]).get("from", [])]
        # Parts you already cover with something else (Magical Footwear stands in for Boots).
        covered = {p for p in parts for h in (nxt.get("upgrade") or {}).get("have", [])
                   if is_boots(p, dd) and is_boots(h["id"], dd)}
        parts = [p for p in parts if p not in owned and p not in covered and 0 < dd.item_cost(p) <= gold]
        if parts:
            affordable = _ref(max(parts, key=dd.item_cost), dd)

    chips = [{"label": f"{round(magic * 100)}% magic · {round((1 - magic) * 100)}% physical", "level": "info"}]
    if t["healing"] >= 1.5:
        chips.append({"label": "Heavy healing" if t["healing"] >= 2.5 else "Healing", "level": "warn"})
    if len(t["tankyArmor"]) >= 2 or len(t["tankyMr"]) >= 2:
        chips.append({"label": f"{max(len(t['tankyArmor']), len(t['tankyMr']))} tanky enemies", "level": "warn"})
    if len(t["cc"]) >= 3:
        chips.append({"label": f"{len(t['cc'])} with hard CC", "level": "warn"})
    for e in t["fed"]:
        chips.append({"label": f"{e.name} fed {e.kills}/{e.deaths}", "level": "bad"})

    return {
        "style": style,
        "start": start,
        "next": nxt,
        "affordable": affordable,
        "situational": [p for p in situational if not nxt or p["id"] != nxt["id"]],
        "boots": boots_note,
        "threats": chips,
    }
