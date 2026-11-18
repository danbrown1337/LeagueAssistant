"""Champion stats behind every recommendation, whatever site or database they come from.

A source supplies the raw numbers (games per role, builds, boots, matchups,
augments). Main roles, counters and enemy role guesses are worked out from
those here, the same way for every source.

Sources (STATS_SOURCE):
    ugg   U.gg's stats files (leagueassistant.ugg). Personal use only: U.gg's
          terms don't allow reusing their data in a product.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

ROLE_ORDER = ["top", "jungle", "mid", "adc", "support"]
# League client assignedPosition -> role name
LCU_POSITIONS = {"top": "top", "jungle": "jungle", "middle": "mid", "bottom": "adc", "utility": "support"}
SOURCES = ("ugg",)


def normalize_role(role: str) -> str:
    role = role.lower()
    aliases = {"middle": "mid", "bot": "adc", "bottom": "adc", "utility": "support", "supp": "support", "jg": "jungle", "jng": "jungle"}
    role = aliases.get(role, role)
    if role not in ROLE_ORDER:
        raise ValueError(f"Unknown role: {role}")
    return role


@dataclass
class Build:
    champion_id: int
    role: str
    wins: int
    matches: int
    rune_trees: tuple[int, int]
    runes: list[int]
    shards: list[int]
    spells: list[int]
    starting_items: list[int]
    core_items: list[int]
    # Options for the 4th, 5th and 6th item slots: (item_id, wins, matches)
    later_items: list[list[tuple[int, int, int]]]
    skill_order: list[str]
    skill_priority: str

    @property
    def win_rate(self) -> float:
        return self.wins / self.matches if self.matches else 0.0


@dataclass
class Matchup:
    opponent_id: int
    wins: int
    matches: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.matches if self.matches else 0.0


def shrunk_win_rate(wins: int, matches: int, prior_games: int = 200) -> float:
    """Win rate with `prior_games` imaginary 50% games added, so 60% over 100
    games ranks below 57% over 2,000."""
    return (wins + prior_games / 2) / (matches + prior_games)


class StatsSource:
    """Base for stats sources. Subclasses implement the methods that raise NotImplementedError;
    lookups with no data raise LookupError."""

    def role_matches(self, champion_id: int) -> dict[str, int]:
        """Ranked games played per role (every role in ROLE_ORDER)."""
        raise NotImplementedError

    def build(self, champion_id: int, role: str | None = None) -> Build:
        """The most played build in `role` (default: the champion's main role)."""
        raise NotImplementedError

    def aram_build(self, champion_id: int) -> Build:
        raise NotImplementedError

    def boots(self, champion_id: int, role: str | None = None, aram: bool = False):
        """Returns (choices, cores): choices are (item, wins, games) per boots bought, item 0
        meaning no boots; cores are ([3 item ids], wins, games) combinations, which show when
        boots are bought."""
        raise NotImplementedError

    def mayhem_augments(self, champion_id: int) -> dict[str, list[str]]:
        """Augment names ranked best first for ARAM: Mayhem, per rarity (prismatic, gold, silver)."""
        raise NotImplementedError

    def matchups(self, champion_id: int, role: str | None = None) -> list[Matchup]:
        """How this champion does against each lane opponent in the given role."""
        raise NotImplementedError

    def main_role(self, champion_id: int) -> str:
        counts = self.role_matches(champion_id)
        return max(counts, key=counts.get)

    def role_share(self, champion_id: int, role: str) -> float:
        counts = self.role_matches(champion_id)
        total = sum(counts.values())
        return counts[role] / total if total else 0.0

    def counters(self, enemy_id: int, role: str, min_matches: int = 100,
                 pool: set[int] | None = None, limit: int = 5,
                 min_role_share: float = 0.1) -> list[Matchup]:
        """Best picks against `enemy_id` in `role`, from the picker's point of view.

        Returned Matchups have opponent_id = the suggested champion and
        win_rate = the suggested champion's win rate against the enemy.
        Picks are ranked by a win rate pulled toward 50% for small samples,
        and champions that rarely play `role` are skipped.
        """
        picks = []
        for m in self.matchups(enemy_id, role):
            if m.matches < min_matches or (pool is not None and m.opponent_id not in pool):
                continue
            picks.append(Matchup(m.opponent_id, m.matches - m.wins, m.matches))
        picks.sort(key=lambda m: shrunk_win_rate(m.wins, m.matches), reverse=True)
        out = []
        for m in picks:
            if min_role_share and self.role_share(m.opponent_id, role) < min_role_share:
                continue
            out.append(m)
            if len(out) == limit:
                break
        return out

    def matchup(self, my_id: int, enemy_id: int, role: str) -> Matchup | None:
        for m in self.matchups(my_id, role):
            if m.opponent_id == enemy_id:
                return m
        return None

    def assign_roles(self, champion_ids: list[int], taken: dict[str, int] | None = None) -> dict[str, int]:
        """Guess which role each enemy champion is playing.

        Picks the assignment that maximizes the product of each champion's
        share of games in its role. `taken` holds roles already known.
        """
        taken = dict(taken or {})
        free_roles = [r for r in ROLE_ORDER if r not in taken]
        champs = [c for c in champion_ids if c not in taken.values()][: len(free_roles)]
        shares = {}
        for c in champs:
            counts = self.role_matches(c)
            total = sum(counts.values()) or 1
            # Small floor so off-role picks are unlikely but still possible.
            shares[c] = {r: counts[r] / total + 0.001 for r in ROLE_ORDER}
        best, best_score = {}, -1.0
        for roles in itertools.permutations(free_roles, len(champs)):
            score = 1.0
            for c, r in zip(champs, roles):
                score *= shares[c][r]
            if score > best_score:
                best, best_score = dict(zip(roles, champs)), score
        return {**taken, **best}


def make_source(name: str, patch: str, region: str = "world", rank: str = "emerald_plus") -> StatsSource:
    """The stats source called `name` (see SOURCES) for this patch."""
    if name == "ugg":
        # Imported here so a build can leave the U.gg client out.
        from .ugg import UGG
        return UGG(patch, region=region, rank=rank)
    raise ValueError(f"Unknown stats source {name!r}: use one of {', '.join(SOURCES)}")
