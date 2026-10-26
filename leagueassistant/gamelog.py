"""Saves a record of each finished game, with the build advice given during it.

One JSON object per line in games.jsonl. Each record has the result, your
stats, every player's rank and the team averages, what the build advice said
over the game, and what you bought, so the advice can be checked against what
you actually built.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

from .ddragon import DDragon

log = logging.getLogger("leagueassistant.gamelog")

RECORD_VERSION = 1
MAX_TIMELINE = 400  # advice changes kept per game
ESTIMATE_GAMES = 10  # games per queue in the rolling MMR estimate


def mode_key(mode: dict | None):
    """Games in the same queue share an MMR, so the estimate is per queue."""
    mode = mode or {}
    return mode.get("queueId") or mode.get("label") or "unknown"


def data_dir() -> Path:
    if os.environ.get("LEAGUEASSISTANT_DATA"):
        return Path(os.environ["LEAGUEASSISTANT_DATA"])
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LeagueAssistant"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "leagueassistant"


def _item(iid: int, dd: DDragon) -> dict:
    return {"id": iid, "name": dd.item_name(iid)}


def _is_real_item(iid: int, dd: DDragon) -> bool:
    """Skip potions, wards and things you're given rather than buy (rune biscuits, Poro-Snax)."""
    it = dd.item(iid)
    return (bool(it) and "Consumable" not in it.get("tags", []) and "Trinket" not in it.get("tags", [])
            and it.get("gold", {}).get("purchasable", True) and not it.get("consumed"))


class Recording:
    """What happened during one game, built up from live updates."""

    def __init__(self, game_id: int | None, started: float):
        self.game_id = game_id
        self.started = started
        self.timeline: list[dict] = []
        self.purchases: list[dict] = []
        self.items: set[int] = set()
        self.seeded = False  # items seen once, so joining mid-game doesn't count them as purchases
        self.last_advice = None
        self.core: list[int] = []
        self.starting: set[int] = set()
        self.me: dict | None = None
        self.mode: dict | None = None
        self.game_time = 0.0

    def update(self, live: dict, dd: DDragon):
        me = live.get("me")
        if not me:
            return
        t = round(live.get("gameTime", 0))
        self.game_time, self.me, self.mode = t, me, live.get("mode")
        if live.get("build"):
            self.core = [i["id"] for i in live["build"].get("core", [])]
            self.starting = {i["id"] for i in live["build"].get("starting", [])}
        advice = live.get("advice") or {}
        nxt = advice.get("next")
        situational = [p["id"] for p in advice.get("situational", [])]

        items = {i["id"] for i in me.get("items", [])}
        if self.seeded:
            for iid in sorted(items - self.items):
                if not _is_real_item(iid, dd):
                    continue
                # Did this match the advice the moment before it was bought? Parts of it count.
                last = self.last_advice or {}
                advised = [last.get("next"), *last.get("situational", [])]
                parts = {int(p) for a in advised if a for p in dd.item(a).get("from", [])}
                # The starting items count too: at the start, buying them is following the build.
                followed = iid in advised or iid in parts or (t < 5 * 60 and iid in self.starting)
                self.purchases.append({**_item(iid, dd), "t": t, "followedAdvice": followed,
                                       "advised": _item(last["next"], dd) if last.get("next") else None})
        self.items, self.seeded = items, True

        key = (nxt and nxt.get("id"), tuple(situational))
        if nxt and key != (self.last_advice or {}).get("key") and len(self.timeline) < MAX_TIMELINE:
            self.timeline.append({
                "t": t,
                "next": {"id": nxt["id"], "name": nxt.get("name"), "purpose": nxt.get("purpose"),
                         "reason": nxt.get("reason")},
                "situational": [{"id": p["id"], "name": p.get("name"), "reason": p.get("reason")}
                                for p in advice.get("situational", [])],
                "threats": [c["label"] for c in advice.get("threats", [])],
                "gold": live.get("gold", 0),
                "items": sorted(items),
            })
        if nxt:
            self.last_advice = {"key": key, "next": nxt["id"], "situational": situational}


def _player(p: dict) -> dict:
    r = p.get("rank")
    return {
        "champion": {"id": p.get("id"), "name": p.get("name")}, "player": p.get("player"), "me": p.get("me", False),
        "kills": p.get("kills", 0), "deaths": p.get("deaths", 0), "assists": p.get("assists", 0), "cs": p.get("cs", 0),
        "rank": r and {k: r.get(k) for k in ("tier", "label", "value", "queue", "source")},
    }


class GameLog:
    def __init__(self, path: Path | None = None, build: str | None = None):
        self.path = path or data_dir() / "games.jsonl"
        self.build = build
        self.current: Recording | None = None
        self.last_mode: dict | None = None  # mode of the last saved game
        self.logged: set = set()
        self.estimates: list[dict] = []  # per game: queue, lobby rank value, ranked players
        self.failed = False
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    self.logged.add(rec.get("gameId"))
                    self._add_estimate(rec)
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("Can't read the game log at %s: %s", self.path, e)

    # --- Updates ---

    def live(self, live: dict, game_id: int | None, dd: DDragon):
        if self.current is None or (game_id and self.current.game_id and game_id != self.current.game_id):
            self.current = Recording(game_id, time.time())
        elif game_id and not self.current.game_id:
            self.current.game_id = game_id
        self.current.update(live, dd)

    def post_game(self, raw: dict, view: dict, mode: dict | None, dd: DDragon):
        """The post game screen: the richest record, with ranks."""
        game_id = raw.get("gameId")
        if game_id in self.logged:
            return
        rec = self.current if self.current and self.current.game_id in (None, game_id) else None
        teams = view.get("teams") or []
        mine = next((t for t in teams if t.get("ally")), {})
        me = next((p for p in mine.get("players", []) if p.get("me")), {})
        raw_me = next((p for t in raw.get("teams") or [] for p in t.get("players") or []
                       if p.get("puuid") and p.get("puuid") == (raw.get("localPlayer") or {}).get("puuid")), {})
        items = [i for i in raw_me.get("items") or [] if i]
        # The post game list has six slots plus the trinket and leaves boots out; add any we saw.
        items += [i for i in sorted(rec.items if rec else []) if i not in items]
        self._write({
            "gameId": game_id,
            "source": "post game",
            "mode": mode or (rec.mode if rec else None),
            "duration": raw.get("gameLength") or (rec.game_time if rec else None),
            "champion": {"id": me.get("id"), "name": me.get("name")},
            "role": ((rec.me or {}).get("role") if rec else None),
            "win": bool(mine.get("win")),
            "kills": me.get("kills", 0), "deaths": me.get("deaths", 0), "assists": me.get("assists", 0),
            "cs": me.get("cs", 0),
            "items": [_item(i, dd) for i in items],
            "ranked": view.get("ranked"),
            "lobbyAverage": view.get("average"),
            "mmrEstimate": view.get("lobbyEstimate"),
            "teams": [{"ally": t.get("ally"), "win": t.get("win"), "average": t.get("average"),
                       "players": [_player(p) for p in t.get("players", [])]} for t in teams],
        }, rec)

    def history(self, game: dict | None, view: dict | None, mode: dict | None, dd: DDragon):
        """Fallback when the post game screen never arrived: the match history entry."""
        rec = self.current
        if not game or not view or not rec:
            return
        game_id = game.get("gameId")
        if game_id in self.logged or (rec.game_id and game_id != rec.game_id):
            return
        if (game.get("gameCreation") or 0) / 1000 < rec.started - 4 * 3600:
            return  # an older game, not the one we recorded
        self._write({
            "gameId": game_id,
            "source": "match history",
            "mode": mode or rec.mode,
            "duration": view.get("duration"),
            "champion": {"id": (view.get("champion") or {}).get("id"), "name": (view.get("champion") or {}).get("name")},
            "role": (rec.me or {}).get("role"),
            "win": view.get("win"),
            "kills": view.get("kills"), "deaths": view.get("deaths"), "assists": view.get("assists"),
            "cs": view.get("cs"),
            "items": [_item(i["id"], dd) for i in view.get("items", [])],
            "ranked": None, "lobbyAverage": None, "teams": [],
        }, rec)

    # --- MMR estimate ---

    def _add_estimate(self, rec: dict):
        est = rec.get("mmrEstimate") or rec.get("lobbyAverage")
        if est and est.get("value") is not None and est.get("ranked"):
            self.estimates.append({"key": mode_key(rec.get("mode")), "label": (rec.get("mode") or {}).get("label"),
                                   "value": est["value"], "ranked": est["ranked"]})

    def mmr_estimate(self, mode: dict | None) -> dict | None:
        """Lobby rank over your last games in this queue, weighted by how many ranked players each had."""
        from .advisor import rank_label

        key = mode_key(mode)
        recent = [e for e in self.estimates if e["key"] == key][-ESTIMATE_GAMES:]
        players = sum(e["ranked"] for e in recent)
        if not players:
            return None
        value = sum(e["value"] * e["ranked"] for e in recent) / players
        return {"label": rank_label(value), "value": round(value, 2), "games": len(recent), "players": players,
                "mode": (mode or {}).get("label") or recent[-1]["label"]}

    def mmr_summary(self) -> list[dict]:
        """The estimate for every queue you've played, most played first."""
        keys = {}
        for e in self.estimates:
            keys[e["key"]] = keys.get(e["key"], 0) + 1
        out = []
        for key in sorted(keys, key=lambda k: -keys[k]):
            label = next(e["label"] for e in reversed(self.estimates) if e["key"] == key)
            est = self.mmr_estimate({"queueId": key if isinstance(key, int) else None,
                                     "label": key if not isinstance(key, int) else label})
            if est:
                out.append(est)
        return out

    # --- Saving ---

    def _write(self, record: dict, rec: Recording | None):
        record = {
            "v": RECORD_VERSION,
            "endedAt": int(time.time() * 1000),
            **record,
            "coreBuild": rec.core if rec else [],
            "advice": rec.timeline if rec else [],
            "purchases": rec.purchases if rec else [],
            "app": self.build,
        }
        self.logged.add(record["gameId"])
        self.last_mode = record.get("mode")
        self._add_estimate(record)
        self.current = None
        if self.failed:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            log.info("Saved the game to %s", self.path)
        except OSError as e:
            self.failed = True  # don't retry every game; the dashboard keeps working
            log.warning("Can't write the game log at %s: %s", self.path, e)
