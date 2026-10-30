"""Riot Data Dragon: static data for champions, items, runes and summoner spells."""

from __future__ import annotations

import functools

import requests

BASE = "https://ddragon.leagueoflegends.com"

# Stat shards are not in runesReforged.json.
SHARDS = {
    5001: "Health Scaling",
    5005: "Attack Speed",
    5007: "Ability Haste",
    5008: "Adaptive Force",
    5010: "Move Speed",
    5011: "Health",
    5013: "Tenacity and Slow Resist",
}


def _get(path: str):
    r = requests.get(f"{BASE}{path}", timeout=20)
    r.raise_for_status()
    return r.json()


class DDragon:
    def __init__(self, version: str | None = None, lang: str = "en_US"):
        self.version = version or _get("/api/versions.json")[0]
        self.lang = lang

    def _data(self, name: str):
        return _get(f"/cdn/{self.version}/data/{self.lang}/{name}.json")

    @property
    def patch(self) -> str:
        """Major.minor patch, e.g. '16.19'."""
        return ".".join(self.version.split(".")[:2])

    @functools.cached_property
    def _champion_data(self) -> dict[int, dict]:
        return {int(c["key"]): c for c in self._data("champion")["data"].values()}

    @property
    def champions(self) -> dict[int, str]:
        return {cid: c["name"] for cid, c in self._champion_data.items()}

    @functools.cached_property
    def _item_data(self) -> dict[int, dict]:
        return {int(k): v for k, v in self._data("item")["data"].items()}

    @property
    def items(self) -> dict[int, str]:
        return {iid: v["name"] for iid, v in self._item_data.items()}

    @functools.cached_property
    def _base_ids(self) -> dict[str, int]:
        # Standard item ids are below 10000; some maps and modes add copies, like 223047 (Arena Plated Steelcaps).
        out = {}
        for iid, v in self._item_data.items():
            if iid < 10000 and v.get("name") and (v["name"] not in out or iid < out[v["name"]]):
                out[v["name"]] = iid
        return out

    def base_item(self, iid: int) -> int:
        """The standard id for a map- or mode-specific copy of an item, so it matches U.gg's builds."""
        if iid < 10000:
            return iid
        return self._base_ids.get(self._item_data.get(iid, {}).get("name"), iid)

    def upgraded_from(self, iid: int) -> list[int]:
        """Items this one turned from in game (Muramana <- Manamune, Bounty of Worlds <- Runic Compass <- World Atlas)."""
        out, seen = [], {iid}
        base = self._item_data.get(iid, {}).get("specialRecipe")
        while base and base not in seen:
            out.append(base)
            seen.add(base)
            base = self._item_data.get(base, {}).get("specialRecipe")
        return out

    def item(self, iid: int) -> dict:
        return self._item_data.get(iid, {})

    def all_items(self) -> dict[int, dict]:
        return self._item_data

    def champion(self, cid: int) -> dict:
        return self._champion_data.get(cid, {})

    def item_cost(self, iid: int) -> int:
        return self._item_data.get(iid, {}).get("gold", {}).get("total", 0)

    @functools.cached_property
    def _spell_data(self) -> dict[int, dict]:
        return {int(s["key"]): s for s in self._data("summoner")["data"].values()}

    def spell_list(self) -> dict[int, dict]:
        """Summoner spells by key, with id (SummonerFlash), name and cooldown."""
        return self._spell_data

    @property
    def spells(self) -> dict[int, str]:
        return {sid: s["name"] for sid, s in self._spell_data.items()}

    @functools.cached_property
    def _rune_data(self) -> tuple[dict[int, str], dict[int, tuple[int, int]], dict[int, str]]:
        names = dict(SHARDS)
        positions = {}  # rune id -> (tree id, slot index)
        icons = {}
        for tree in self._data("runesReforged"):
            names[tree["id"]] = tree["name"]
            icons[tree["id"]] = tree["icon"]
            for slot_index, slot in enumerate(tree["slots"]):
                for rune in slot["runes"]:
                    names[rune["id"]] = rune["name"]
                    positions[rune["id"]] = (tree["id"], slot_index)
                    icons[rune["id"]] = rune["icon"]
        return names, positions, icons

    # Image URLs for the web UI

    def champion_icon(self, cid: int) -> str | None:
        c = self._champion_data.get(cid)
        return f"{BASE}/cdn/{self.version}/img/champion/{c['image']['full']}" if c else None

    def champion_splash(self, cid: int) -> str | None:
        c = self._champion_data.get(cid)
        return f"{BASE}/cdn/img/champion/splash/{c['id']}_0.jpg" if c else None

    def item_icon(self, iid: int) -> str:
        return f"{BASE}/cdn/{self.version}/img/item/{iid}.png"

    def spell_icon(self, sid: int) -> str | None:
        s = self._spell_data.get(sid)
        return f"{BASE}/cdn/{self.version}/img/spell/{s['image']['full']}" if s else None

    def rune_icon(self, rid: int) -> str | None:
        icon = self._rune_data[2].get(rid)
        return f"{BASE}/cdn/img/{icon}" if icon else None

    @property
    def runes(self) -> dict[int, str]:
        return self._rune_data[0]

    def runes_in_tree(self, rune_ids: list[int], tree_id: int) -> list[int]:
        """The runes from `rune_ids` that belong to `tree_id`, in slot order."""
        positions = self._rune_data[1]
        picked = [r for r in rune_ids if positions.get(r, (None,))[0] == tree_id]
        return sorted(picked, key=lambda r: positions[r][1])

    def champion_id(self, name: str) -> int:
        """Look up a champion by name, ignoring case, spaces and punctuation."""
        norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())
        target = norm(name)
        for cid, cname in self.champions.items():
            if norm(cname) == target:
                return cid
        matches = [cid for cid, cname in self.champions.items() if norm(cname).startswith(target)]
        if len(matches) == 1:
            return matches[0]
        raise KeyError(f"Unknown champion: {name}")

    def champion_name(self, cid: int) -> str:
        return self.champions.get(cid, f"#{cid}")

    def item_name(self, iid: int) -> str:
        return self.items.get(iid, f"#{iid}")
