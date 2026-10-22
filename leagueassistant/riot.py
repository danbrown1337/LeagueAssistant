"""Official Riot API, for match history outside the League client.

Needs a key from https://developer.riotgames.com in RIOT_API_KEY. Development
keys expire every 24 hours.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import requests

# Regional routing for account-v1 and match-v5.
ROUTING = {"americas", "europe", "asia", "sea"}


class RiotAPI:
    def __init__(self, api_key: str | None = None, routing: str = "americas"):
        self.api_key = api_key or os.environ.get("RIOT_API_KEY")
        if not self.api_key:
            raise RuntimeError("Set RIOT_API_KEY to use the Riot API")
        if routing not in ROUTING:
            raise ValueError(f"routing must be one of {sorted(ROUTING)}")
        self.base = f"https://{routing}.api.riotgames.com"
        self.session = requests.Session()
        self.session.headers["X-Riot-Token"] = self.api_key

    def _get(self, path: str, **params):
        r = self.session.get(self.base + path, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def puuid(self, riot_id: str) -> str:
        """riot_id looks like 'Name#TAG'."""
        name, _, tag = riot_id.partition("#")
        if not tag:
            raise ValueError("Riot ID must look like Name#TAG")
        return self._get(f"/riot/account/v1/accounts/by-riot-id/{quote(name)}/{quote(tag)}")["puuid"]

    def match_ids(self, puuid: str, count: int = 10, queue: int | None = None) -> list[str]:
        params = {"count": count}
        if queue is not None:
            params["queue"] = queue
        return self._get(f"/lol/match/v5/matches/by-puuid/{puuid}/ids", **params)

    def match(self, match_id: str) -> dict:
        return self._get(f"/lol/match/v5/matches/{match_id}")
