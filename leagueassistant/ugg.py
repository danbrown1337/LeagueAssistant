"""Client for U.gg's stats files (stats2.u.gg).

U.gg has no public API. These are the JSON files its own site loads, so the
layout is undocumented and may change. Each file is keyed
region -> rank -> role -> data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import requests

from .stats import Build, Matchup, StatsSource, normalize_role

BASE = "https://stats2.u.gg/lol/1.5"
API_VERSION = "1.5.0"
# Cloudflare rejects requests without a browser-like User-Agent.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

REGIONS = {
    "na": "1", "euw": "2", "kr": "3", "eune": "4", "br": "5", "lan": "6", "las": "7",
    "oce": "8", "ru": "9", "tr": "10", "jp": "11", "world": "12",
}
RANKS = {
    "challenger": "1", "master": "2", "diamond": "3", "platinum": "4", "gold": "5",
    "silver": "6", "bronze": "7", "overall": "8", "platinum_plus": "10",
    "diamond_plus": "11", "iron": "12", "grandmaster": "13", "master_plus": "14",
    "diamond_2_plus": "15", "emerald": "16", "emerald_plus": "17",
}
ROLES = {"jungle": "1", "support": "2", "adc": "3", "top": "4", "mid": "5"}
# ARAM: Mayhem augment rankings live on U.gg's static host, not stats2.
MAYHEM_BASE = "https://static.bigbrain.gg/custom-aram-mayhem"
MAYHEM_RARITIES = {"kPrismatic": "prismatic", "kGold": "gold", "kSilver": "silver"}
# ARAM stats have one role and one rank bucket.
ARAM_QUEUE, ARAM_RANK, ARAM_ROLE = "normal_aram", "8", "6"


@dataclass
class UGG(StatsSource):
    patch: str  # "16.19"
    region: str = "world"
    rank: str = "emerald_plus"
    _cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def _fetch(self, kind: str, champion_id: int, queue: str = "ranked_solo_5x5") -> dict:
        """Fetch a stats file, falling back to the previous patch early in a new one."""
        key = (kind, champion_id, queue)
        if key in self._cache:
            return self._cache[key]
        major, minor = (int(x) for x in self.patch.split("."))
        candidates = [f"{major}_{minor}"]
        if minor > 1:
            candidates.append(f"{major}_{minor - 1}")
        last_error = None
        for patch in candidates:
            url = f"{BASE}/{kind}/{patch}/{queue}/{champion_id}/{API_VERSION}.json"
            r = self.session.get(url, timeout=30)
            if r.status_code == 200:
                self._cache[key] = r.json()
                return self._cache[key]
            last_error = f"{r.status_code} for {url}"
        raise LookupError(f"U.gg has no {kind} data: {last_error}")

    def _role_data(self, data: dict, role: str):
        try:
            return data[REGIONS[self.region]][RANKS[self.rank]][ROLES[role]]
        except KeyError:
            return None

    def role_matches(self, champion_id: int) -> dict[str, int]:
        """Ranked games played per role, used to guess where a champion is going."""
        data = self._fetch("rankings", champion_id)
        out = {}
        for role in ROLES:
            entry = self._role_data(data, role)
            out[role] = entry[1] if entry else 0
        return out

    def build(self, champion_id: int, role: str | None = None) -> Build:
        role = normalize_role(role) if role else self.main_role(champion_id)
        entry = self._role_data(self._fetch("overview", champion_id), role)
        if not entry:
            raise LookupError(f"No build data for champion {champion_id} in {role}")
        return parse_build(champion_id, role, entry[0])

    def aram_build(self, champion_id: int) -> Build:
        data = self._fetch("overview", champion_id, ARAM_QUEUE)
        try:
            entry = data[REGIONS[self.region]][ARAM_RANK][ARAM_ROLE]
        except KeyError:
            entry = None
        if not entry:
            raise LookupError(f"No ARAM build data for champion {champion_id}")
        return parse_build(champion_id, "aram", entry[0])

    def boots(self, champion_id: int, role: str | None = None, aram: bool = False):
        """U.gg's boots stats, which its overview file leaves out."""
        try:
            if aram:
                entry = self._fetch("builds", champion_id, ARAM_QUEUE)[REGIONS[self.region]][ARAM_RANK][ARAM_ROLE]
            else:
                role = normalize_role(role) if role else self.main_role(champion_id)
                entry = self._fetch("builds", champion_id)[REGIONS[self.region]][RANKS[self.rank]][ROLES[role]]
            choices = [(int(r[0]), int(r[1]), int(r[2])) for r in entry[5]]
            cores = [(list(r[0]), int(r[1]), int(r[2])) for r in entry[3]]
        except (KeyError, IndexError, TypeError, ValueError):
            raise LookupError(f"No boots data for champion {champion_id}")
        return choices, cores

    def _mayhem(self, name: str) -> dict:
        """A Mayhem file for this patch, or the previous one early in a new patch."""
        if name not in self._cache:
            self._cache[name] = None  # remembered as missing too, so it isn't refetched every update
            major, minor = (int(x) for x in self.patch.split("."))
            for patch in [f"{major}_{minor}"] + ([f"{major}_{minor - 1}"] if minor > 1 else []):
                r = self.session.get(f"{MAYHEM_BASE}/{patch}/" + name.format(patch=patch), timeout=30)
                if r.status_code == 200:
                    self._cache[name] = r.json()
                    break
        if self._cache[name] is None:
            raise LookupError(f"U.gg has no ARAM: Mayhem data ({name})")
        return self._cache[name]

    def mayhem_augments(self, champion_id: int) -> dict[str, list[str]]:
        """Augments ranked best first for this champion in ARAM: Mayhem, per rarity."""
        names = self._mayhem("aram-mayhem-augment-manifest-{patch}.json")
        data = self._mayhem(f"tierlist-per-champion-augments-rarity-{{patch}}/tierlist-augments-{champion_id}-{{patch}}.json")
        out = {}
        for key, rarity in MAYHEM_RARITIES.items():
            out[rarity] = [names.get(str(i), f"Augment {i}") for i in (data.get("rarities") or {}).get(key, [])]
        if not any(out.values()):
            raise LookupError(f"No ARAM: Mayhem augments for champion {champion_id}")
        return out

    def matchups(self, champion_id: int, role: str | None = None) -> list[Matchup]:
        role = normalize_role(role) if role else self.main_role(champion_id)
        entry = self._role_data(self._fetch("matchups", champion_id), role)
        if not entry:
            return []
        return [Matchup(e[0], e[1], e[2]) for e in entry[0]]


def parse_build(champion_id: int, role: str, d: list) -> Build:
    # d[0] runes: [matches, wins, primary tree, secondary tree, [perk ids]]
    # d[1] spells, d[2] starting items, d[3] core items: [matches, wins, [ids]]
    # d[4] skills: [matches, wins, [level order], "QWE" priority]
    # d[5] later item slots: [[[item, wins, matches], ...], ...]
    # d[6] [wins, matches] for the champion in this role
    # d[8] shards: [matches, wins, ["5005", ...]]
    return Build(
        champion_id=champion_id,
        role=role,
        wins=d[6][0],
        matches=d[6][1],
        rune_trees=(d[0][2], d[0][3]),
        runes=list(d[0][4]),
        shards=[int(s) for s in d[8][2]],
        spells=list(d[1][2]),
        starting_items=list(d[2][2]),
        core_items=list(d[3][2]),
        later_items=[[tuple(o) for o in slot] for slot in d[5] if slot],
        skill_order=list(d[4][2]),
        skill_priority=d[4][3],
    )
