"""Summoner spell timers you start by tapping.

The game doesn't report anyone's cooldowns, so a timer starts when you tap a
spell you saw used. Cooldowns come from Data Dragon, reduced by Summoner Spell
Haste from items (Ionian Boots). Rune haste (Cosmic Insight) isn't in the live
data, so it isn't counted.

Ultimate timers are deliberately left out: Riot's third-party app policy has
banned enemy ultimate tracking, manual or automatic, since March 2025.
"""

from __future__ import annotations

import re

from .ddragon import DDragon

SLOTS = ("summonerSpellOne", "summonerSpellTwo")
SKIP = {"SummonerSmite"}  # charges every 15 seconds; not worth a timer
HASTE = re.compile(r"(\d+)\s*Summoner Spell Haste")


def _spell(raw: dict, dd: DDragon) -> dict | None:
    # rawDisplayName looks like GeneratedTip_SummonerSpell_SummonerFlash_DisplayName.
    ids = set(re.findall(r"Summoner[A-Za-z]+", raw.get("rawDisplayName") or ""))  # includes "SummonerSpell"
    for key, s in dd.spell_list().items():
        if s.get("id") in ids:
            return {"key": key, "id": s.get("id"), "name": s.get("name"), "cooldown": (s.get("cooldown") or [0])[0]}
    for key, s in dd.spell_list().items():
        if s.get("name") == raw.get("displayName"):
            return {"key": key, "id": s.get("id"), "name": s.get("name"), "cooldown": (s.get("cooldown") or [0])[0]}
    return None


def summoner_haste(item_ids: list[int], dd: DDragon) -> int:
    total = 0
    for iid in item_ids:
        found = HASTE.findall(re.sub(r"<[^>]+>", " ", dd.item(iid).get("description", "")))
        total += sum(int(n) for n in found)
    return total


def player_key(p: dict) -> str:
    return p.get("riotId") or p.get("summonerName") or p.get("championName") or ""


def spells_view(players: list[dict], views: list[dict], uses: dict, dd: DDragon) -> list[dict]:
    """Each player's two summoner spells with cooldown and when you marked them used (game seconds)."""
    out = []
    for p, v in zip(players, views):
        key = player_key(p)
        haste = summoner_haste([i["id"] for i in v.get("items", [])], dd)
        spells = []
        for slot in SLOTS:
            s = _spell((p.get("summonerSpells") or {}).get(slot) or {}, dd)
            if not s or s["id"] in SKIP or not s["cooldown"]:
                continue
            cooldown = round(s["cooldown"] * 100 / (100 + haste))
            spells.append({"slot": slot, "name": s["name"], "icon": dd.spell_icon(s["key"]), "cooldown": cooldown,
                           "usedAt": uses.get(f"{key}|{slot}")})
        out.append({"key": key, "id": v.get("id"), "name": v.get("name"), "icon": v.get("icon"),
                    "ally": v.get("ally"), "me": v.get("me"), "haste": haste, "spells": spells})
    return out
