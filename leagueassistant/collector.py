"""Collects ranked, ARAM and Mayhem matches from the Riot API into a SQLite database.

This is the start of our own stats, to replace U.gg in a version of the app
that can be sold. Nothing reads the database yet; the dashboard still uses
U.gg. Run it with `leagueassistant collect` (the homelab runs it as the
`collector` container).

It keeps a list of players in the chosen tiers (refreshed daily), visits
whoever was visited longest ago, and stores each new match they played with
its timeline, so builds can be worked out later: runes, spells, final items,
the order items were bought, skill order and Mayhem augments per player.
Player names and IDs aren't stored with the matches.

Settings (environment variables, or the matching `collect` options):
    RIOT_API_KEY      required
    RIOT_PLATFORM     server to collect from (default na1)
    COLLECT_TIERS     tiers to collect players from (default challenger,grandmaster,master,diamond,emerald)
    COLLECT_QUEUES    queue ids (default 420,450,2400: ranked solo, ARAM, ARAM: Mayhem)
    LEAGUEASSISTANT_DATA  folder for matches.db
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from .gamelog import data_dir
from .riot import KeyRejected, RiotAPI

log = logging.getLogger("leagueassistant.collector")

APEX_TIERS = ("challenger", "grandmaster", "master")
DIVISIONS = ("I", "II", "III", "IV")
DEFAULT_TIERS = "challenger,grandmaster,master,diamond,emerald"
DEFAULT_QUEUES = "420,450,2400"
POSITIONS = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "adc", "UTILITY": "support"}
SKILLS = {1: "Q", 2: "W", 3: "E", 4: "R"}
PLAYERS_REFRESH = 24 * 3600
FIRST_LOOKBACK = 3 * 24 * 3600  # how far back to look the first time a player is visited
KEY_RETRY = 600  # seconds to wait after Riot rejects the key

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    puuid TEXT PRIMARY KEY,
    tier TEXT, division TEXT, lp INTEGER,
    seen_at INTEGER,     -- last time a league list included them
    visited_at INTEGER   -- last time their matches were collected
);
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    queue INTEGER, patch TEXT, game_version TEXT,
    started_at INTEGER, duration INTEGER,
    remake INTEGER,      -- ended in an early surrender: leave out of stats
    tier TEXT,           -- tier of the player it was found through
    collected_at INTEGER
);
CREATE TABLE IF NOT EXISTS participants (
    match_id TEXT, participant_id INTEGER,
    team_id INTEGER, champion_id INTEGER, position TEXT, win INTEGER,
    spells TEXT,         -- [spell, spell]
    rune_trees TEXT,     -- [primary, secondary]
    runes TEXT,          -- 4 primary then 2 secondary
    shards TEXT,         -- [offense, flex, defense]
    items TEXT,          -- final item0..item6
    purchases TEXT,      -- [[ms, item], ...] in order, undos removed
    skills TEXT,         -- "QWEQ..." in level order
    augments TEXT,       -- Mayhem/Arena augment ids
    PRIMARY KEY (match_id, participant_id)
);
CREATE INDEX IF NOT EXISTS participants_champion ON participants (champion_id, position);
CREATE INDEX IF NOT EXISTS matches_patch ON matches (patch, queue);
"""


def default_db() -> Path:
    return data_dir() / "matches.db"


def connect(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")  # so a reader can query while the collector writes
    db.executescript(SCHEMA)
    return db


def patch_of(game_version: str) -> str:
    """'16.22.712.3456' -> '16.22'"""
    return ".".join(game_version.split(".")[:2])


def parse_timeline(timeline: dict | None) -> dict[int, dict]:
    """Purchases (with undos taken back) and skill order per participant id."""
    out: dict[int, dict] = {}
    for frame in ((timeline or {}).get("info") or {}).get("frames") or []:
        for e in frame.get("events") or []:
            pid = e.get("participantId")
            if not pid:
                continue
            p = out.setdefault(pid, {"purchases": [], "skills": ""})
            kind = e.get("type")
            if kind == "ITEM_PURCHASED":
                p["purchases"].append([e.get("timestamp", 0), e.get("itemId")])
            elif kind == "ITEM_UNDO":
                undone = e.get("beforeId")
                for i in range(len(p["purchases"]) - 1, -1, -1):
                    if p["purchases"][i][1] == undone:
                        del p["purchases"][i]
                        break
            elif kind == "SKILL_LEVEL_UP" and e.get("levelUpType", "NORMAL") == "NORMAL":
                p["skills"] += SKILLS.get(e.get("skillSlot"), "")
    return out


def parse_participant(p: dict, timeline: dict[int, dict]) -> dict:
    perks = p.get("perks") or {}
    styles = perks.get("styles") or []
    stat = perks.get("statPerks") or {}
    runes = [s.get("perk") for st in styles for s in st.get("selections") or []]
    t = timeline.get(p.get("participantId"), {})
    return {
        "participant_id": p.get("participantId"),
        "team_id": p.get("teamId"),
        "champion_id": p.get("championId"),
        "position": POSITIONS.get(p.get("teamPosition") or ""),
        "win": int(bool(p.get("win"))),
        "spells": [p.get("summoner1Id"), p.get("summoner2Id")],
        "rune_trees": [st.get("style") for st in styles[:2]],
        "runes": runes,
        "shards": [stat.get("offense"), stat.get("flex"), stat.get("defense")],
        "items": [p.get(f"item{i}", 0) for i in range(7)],
        "purchases": t.get("purchases", []),
        "skills": t.get("skills", ""),
        "augments": [a for a in (p.get(f"playerAugment{i}") for i in range(1, 7)) if a],
    }


def save_match(db: sqlite3.Connection, match: dict, timeline: dict | None, tier: str | None):
    info = match.get("info") or {}
    match_id = (match.get("metadata") or {}).get("matchId")
    parts = info.get("participants") or []
    t = parse_timeline(timeline)
    remake = any(p.get("gameEndedInEarlySurrender") for p in parts)
    version = info.get("gameVersion") or ""
    with db:
        db.execute("INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?,?)",
                   (match_id, info.get("queueId"), patch_of(version), version,
                    (info.get("gameStartTimestamp") or 0) // 1000, info.get("gameDuration"),
                    int(remake), tier, int(time.time())))
        for p in parts:
            row = parse_participant(p, t)
            db.execute("INSERT OR REPLACE INTO participants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (match_id, row["participant_id"], row["team_id"], row["champion_id"], row["position"],
                        row["win"], *(json.dumps(row[k]) for k in ("spells", "rune_trees", "runes", "shards", "items",
                                                                   "purchases")),
                        row["skills"], json.dumps(row["augments"])))


class Collector:
    def __init__(self, api: RiotAPI, db: sqlite3.Connection, tiers: list[str], queues: list[int],
                 batch: int = 20, clock=time.time):
        self.api, self.db, self.tiers, self.queues = api, db, [t.lower() for t in tiers], queues
        self.batch, self.clock = batch, clock
        self.players_at = 0.0

    def refresh_players(self):
        """Add everyone currently in the chosen tiers to the player list."""
        now = int(self.clock())
        found = 0
        for tier in self.tiers:
            if tier in APEX_TIERS:
                pages = [self.api.apex_league(tier)]
            else:
                pages = self._division_pages(tier)
            for entries in pages:
                with self.db:
                    for e in entries:
                        if not e.get("puuid"):
                            continue
                        found += 1
                        self.db.execute(
                            "INSERT INTO players (puuid, tier, division, lp, seen_at) VALUES (?,?,?,?,?) "
                            "ON CONFLICT(puuid) DO UPDATE SET tier=excluded.tier, division=excluded.division, "
                            "lp=excluded.lp, seen_at=excluded.seen_at",
                            (e["puuid"], (e.get("tier") or tier).lower(), e.get("rank"), e.get("leaguePoints"), now))
        self.players_at = self.clock()
        log.info("Player list refreshed: %d players in %s", found, ", ".join(self.tiers))

    def _division_pages(self, tier: str):
        for division in DIVISIONS:
            page = 1
            while True:
                entries = self.api.league_entries(tier, division, page)
                if not entries:
                    break
                yield entries
                page += 1

    def next_players(self) -> list[tuple[str, str, int | None]]:
        """Players in the chosen tiers, least recently visited first."""
        marks = ",".join("?" * len(self.tiers))
        return self.db.execute(
            f"SELECT puuid, tier, visited_at FROM players WHERE tier IN ({marks}) "
            "ORDER BY visited_at IS NOT NULL, visited_at LIMIT ?", (*self.tiers, self.batch)).fetchall()

    def known(self, match_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM matches WHERE match_id = ?", (match_id,)).fetchone() is not None

    def visit(self, puuid: str, tier: str, visited_at: int | None) -> int:
        """Collect this player's new matches. Returns how many were saved."""
        now = int(self.clock())
        since = visited_at or now - FIRST_LOOKBACK
        saved = 0
        for queue in self.queues:
            for match_id in self.api.match_ids(puuid, count=100, queue=queue, start_time=since):
                if self.known(match_id):
                    continue
                match = self.api.match(match_id)
                if not match:
                    continue
                save_match(self.db, match, self.api.timeline(match_id), tier)
                saved += 1
        with self.db:
            self.db.execute("UPDATE players SET visited_at = ? WHERE puuid = ?", (now, puuid))
        return saved

    def step(self) -> int:
        """One batch of players. Returns matches saved."""
        if self.clock() - self.players_at > PLAYERS_REFRESH:
            self.refresh_players()
        saved = sum(self.visit(*p) for p in self.next_players())
        total = self.db.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        log.info("Saved %d new matches (%d in the database)", saved, total)
        return saved

    def run(self, sleep=time.sleep):
        while True:
            try:
                if not self.step():
                    sleep(60)  # everyone was visited recently; let them play some games
            except KeyRejected as e:
                log.error("%s Put a new key in RIOT_API_KEY and restart the collector. Trying again in %d minutes.",
                          e, KEY_RETRY // 60)
                sleep(KEY_RETRY)
            except Exception:
                log.exception("Collecting failed; trying again in a minute")
                sleep(60)
