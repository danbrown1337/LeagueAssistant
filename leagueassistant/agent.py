"""Runs on the gaming PC and forwards League client data to the server.

The League client and in-game APIs only listen on 127.0.0.1, so a server
elsewhere on the network can't read them directly. This agent polls them and
POSTs snapshots to the server, which does the U.gg lookups and serves the UI.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

import requests

from . import lcu

log = logging.getLogger("leagueassistant.agent")

HEARTBEAT_SECONDS = 10
HISTORY_PHASES = {"None", "Lobby", "EndOfGame"}
POST_GAME_PHASES = {"PreEndOfGame", "EndOfGame"}


class Agent:
    def __init__(self, server: str, token: str | None = None, lockfile: str | None = None,
                 interval: float = 2.0):
        self.url = server.rstrip("/") + "/api/agent"
        self.lockfile = lockfile
        self.interval = interval
        self.http = requests.Session()
        if token:
            self.http.headers["Authorization"] = f"Bearer {token}"
        self.client: lcu.LCU | None = None
        self.last_phase = None
        self.pickable: list[int] = []
        self.history: list[dict] = []
        self.history_due = 0.0
        self.post_game: dict | None = None
        self.last_hash = None
        self.last_sent = 0.0

    def _connect(self) -> bool:
        creds = lcu.find_credentials(self.lockfile)
        if not creds:
            return False
        client = lcu.LCU(creds)
        try:
            client.phase()
        except Exception:
            return False
        self.client = client
        self.history_due = time.time()
        log.info("Connected to the League client")
        return True

    def snapshot(self) -> dict:
        if self.client is None and not self._connect():
            # The client may be closed while a game is still running.
            return {"clientConnected": False, "phase": "None", "live": lcu.live_game()}
        try:
            return self._client_snapshot()
        except requests.RequestException:
            log.info("Lost the League client")
            self.client = None
            return {"clientConnected": False, "phase": "None"}

    def _client_snapshot(self) -> dict:
        phase = self.client.phase()
        snap = {"clientConnected": True, "phase": phase}
        if phase != self.last_phase:
            if phase == "ChampSelect":
                self.pickable = sorted(self.client.pickable_champions())
            if phase in HISTORY_PHASES:
                # Match history takes a few seconds to include the game that just ended.
                self.history_due = time.time() + (5 if phase == "EndOfGame" else 0)
            self.last_phase = phase

        if self.history_due and time.time() >= self.history_due:
            self.history = self.client.match_history(10)
            self.history_due = 0.0
            if not self.history:
                self.history_due = time.time() + 30
        if self.history:
            snap["history"] = self.history

        if phase == "ChampSelect":
            snap["champSelect"] = self.client.champ_select()
            snap["pickable"] = self.pickable
        elif phase in ("InProgress", "Reconnect"):
            snap["live"] = lcu.live_game()
        elif phase in POST_GAME_PHASES:
            self._load_post_game()
            if self.post_game:
                snap["postGame"] = self.post_game

        summoner = self.client.current_summoner() or {}
        snap["summoner"] = {k: summoner.get(k) for k in ("gameName", "tagLine", "profileIconId", "summonerLevel")}
        return snap

    def _load_post_game(self):
        # The scoreboard can take a moment to appear; ranks are looked up once per game.
        game = self.client.end_of_game()
        if not game or not game.get("teams"):
            return
        if self.post_game and self.post_game["game"].get("gameId") == game.get("gameId"):
            return
        ranks = {}
        for team in game["teams"]:
            for p in team.get("players") or []:
                puuid = p.get("puuid")
                if not puuid or p.get("botPlayer"):
                    continue
                try:
                    ranks[puuid] = self.client.ranked_stats(puuid)
                except requests.RequestException as e:
                    log.info("No ranked stats for a player: %s", e)
        self.post_game = {"game": game, "ranks": ranks}

    def send(self, snap: dict):
        body = json.dumps(snap, sort_keys=True)
        digest = hashlib.sha1(body.encode()).hexdigest()
        if digest == self.last_hash and time.time() - self.last_sent < HEARTBEAT_SECONDS:
            return
        r = self.http.post(self.url, data=body, headers={"Content-Type": "application/json"}, timeout=10)
        if r.status_code == 401:
            raise SystemExit("The server rejected the agent token. Check --token / AGENT_TOKEN.")
        r.raise_for_status()
        self.last_hash, self.last_sent = digest, time.time()

    def run(self):
        log.info("Sending League client data to %s", self.url)
        while True:
            try:
                self.send(self.snapshot())
            except requests.RequestException as e:
                log.warning("Server unreachable: %s", e)
                time.sleep(5)
            except SystemExit:
                raise
            except Exception:
                log.exception("Agent error")
                time.sleep(5)
            time.sleep(self.interval)
