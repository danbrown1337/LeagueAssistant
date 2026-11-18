"""Official Riot API, for match history outside the League client and for collecting matches.

Needs a key from https://developer.riotgames.com in RIOT_API_KEY. Development
keys expire every 24 hours.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from urllib.parse import quote

import requests

log = logging.getLogger("leagueassistant.riot")

# Regional routing for account-v1 and match-v5.
ROUTING = {"americas", "europe", "asia", "sea"}
# Platform (league-v4) -> regional routing (match-v5).
PLATFORMS = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe", "me1": "europe",
    "kr": "asia", "jp1": "asia",
    "oc1": "sea", "sg2": "sea", "tw2": "sea", "vn2": "sea",
}
# A development key's limits, until the first response says what the key really allows.
DEV_LIMITS = [(20, 1), (100, 120)]


class KeyRejected(RuntimeError):
    """Riot refused the key: it's wrong, or a development key that has expired."""


class RateLimiter:
    """Keeps requests under every (requests, seconds) limit at once."""

    def __init__(self, limits: list[tuple[int, int]] | None = None, sleep=time.sleep, clock=time.monotonic):
        self.limits = list(limits or DEV_LIMITS)
        self.calls: deque[float] = deque()
        self.sleep, self.clock = sleep, clock

    def wait(self):
        while True:
            now = self.clock()
            longest = max(w for _, w in self.limits)
            while self.calls and now - self.calls[0] >= longest:
                self.calls.popleft()
            delay = 0.0
            for n, window in self.limits:
                recent = [t for t in self.calls if now - t < window]
                if len(recent) >= n:
                    delay = max(delay, window - (now - recent[-n]))
            if delay <= 0:
                break
            self.sleep(delay)
        self.calls.append(now)

    def update(self, header: str | None):
        """Use the limits from an X-App-Rate-Limit header, like "20:1,100:120"."""
        try:
            limits = [(int(n), int(w)) for n, w in (part.split(":") for part in header.split(","))]
        except (AttributeError, ValueError):
            return
        if limits:
            self.limits = limits


class RiotAPI:
    def __init__(self, api_key: str | None = None, routing: str | None = None, platform: str = "na1",
                 sleep=time.sleep):
        self.api_key = api_key or os.environ.get("RIOT_API_KEY")
        if not self.api_key:
            raise RuntimeError("Set RIOT_API_KEY to use the Riot API")
        if platform not in PLATFORMS:
            raise ValueError(f"platform must be one of {sorted(PLATFORMS)}")
        routing = routing or PLATFORMS[platform]
        if routing not in ROUTING:
            raise ValueError(f"routing must be one of {sorted(ROUTING)}")
        self.base = f"https://{routing}.api.riotgames.com"
        self.platform_base = f"https://{platform}.api.riotgames.com"
        self.session = requests.Session()
        self.session.headers["X-Riot-Token"] = self.api_key
        # One limiter for the whole key: Riot counts app limits per region, and we use one of each.
        self.limiter = RateLimiter(sleep=sleep)
        self.sleep = sleep

    def _get(self, path: str, platform: bool = False, **params):
        """GET a Riot API path. Returns None for 404; waits out rate limits and brief outages."""
        url = (self.platform_base if platform else self.base) + path
        for attempt in range(5):
            self.limiter.wait()
            r = self.session.get(url, params=params, timeout=20)
            self.limiter.update(r.headers.get("X-App-Rate-Limit"))
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            if r.status_code in (401, 403):
                raise KeyRejected(f"Riot rejected the API key ({r.status_code}). Development keys expire after 24 hours.")
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After") or 10)
                log.info("Rate limited (%s), waiting %ss", r.headers.get("X-Rate-Limit-Type", "?"), wait)
                self.sleep(wait)
            elif r.status_code >= 500:
                self.sleep(2 ** attempt)
            else:
                r.raise_for_status()
        r.raise_for_status()
        raise RuntimeError(f"Riot API kept failing for {path}")

    def puuid(self, riot_id: str) -> str:
        """riot_id looks like 'Name#TAG'."""
        name, _, tag = riot_id.partition("#")
        if not tag:
            raise ValueError("Riot ID must look like Name#TAG")
        found = self._get(f"/riot/account/v1/accounts/by-riot-id/{quote(name)}/{quote(tag)}")
        if not found:
            raise LookupError(f"No player called {riot_id}")
        return found["puuid"]

    def match_ids(self, puuid: str, count: int = 10, queue: int | None = None, start_time: int | None = None) -> list[str]:
        params = {"count": count}
        if queue is not None:
            params["queue"] = queue
        if start_time is not None:
            params["startTime"] = start_time
        return self._get(f"/lol/match/v5/matches/by-puuid/{puuid}/ids", **params) or []

    def match(self, match_id: str) -> dict | None:
        return self._get(f"/lol/match/v5/matches/{match_id}")

    def timeline(self, match_id: str) -> dict | None:
        return self._get(f"/lol/match/v5/matches/{match_id}/timeline")

    def apex_league(self, tier: str, queue: str = "RANKED_SOLO_5x5") -> list[dict]:
        """Every player in Challenger, Grandmaster or Master, with the tier filled in on each entry."""
        league = self._get(f"/lol/league/v4/{tier.lower()}leagues/by-queue/{queue}", platform=True) or {}
        return [{**e, "tier": league.get("tier", tier.upper())} for e in league.get("entries", [])]

    def league_entries(self, tier: str, division: str, page: int = 1, queue: str = "RANKED_SOLO_5x5") -> list[dict]:
        """One page of players in a tier and division (Diamond and below), empty past the last page."""
        return self._get(f"/lol/league-exp/v4/entries/{queue}/{tier.upper()}/{division}", platform=True, page=page) or []
