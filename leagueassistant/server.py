"""Web server: receives agent snapshots, adds U.gg advice, streams it to browsers.

Run with `leagueassistant serve` or the Docker image. Settings come from
environment variables:

    AGENT_TOKEN   shared secret the agent must send (recommended)
    UGG_REGION    U.gg stats region (default world)
    UGG_RANK      U.gg rank filter (default emerald_plus)
    MIN_GAMES     ignore matchups with fewer games (default 100)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import advisor, lcu
from .ddragon import DDragon
from .ugg import UGG, normalize_role

log = logging.getLogger("leagueassistant.server")

WEB_DIR = Path(__file__).parent / "web"
AGENT_TIMEOUT = 30  # seconds without a snapshot before the agent counts as offline
STATS_REFRESH = 6 * 3600  # re-check the patch this often


class Stats:
    """Data Dragon and U.gg clients, rebuilt when a new patch comes out."""

    def __init__(self, region: str, rank: str):
        self.region, self.rank = region, rank
        self.loaded_at = 0.0
        self.dd: DDragon | None = None
        self.ugg: UGG | None = None

    def get(self) -> tuple[DDragon, UGG]:
        if self.dd is None or time.time() - self.loaded_at > STATS_REFRESH:
            dd = DDragon()
            if self.dd is None or dd.version != self.dd.version:
                self.dd, self.ugg = dd, UGG(dd.patch, region=self.region, rank=self.rank)
                log.info("Using patch %s", dd.patch)
            self.loaded_at = time.time()
        return self.dd, self.ugg


class Hub:
    def __init__(self, stats: Stats, token: str | None, min_games: int):
        self.stats = stats
        self.token = token
        self.min_games = min_games
        self.lock = asyncio.Lock()
        self.subscribers: set[asyncio.Queue] = set()
        self.last_seen = 0.0
        self.select_key = None
        self.my_build: dict | None = None
        self.state = {
            "agentOnline": False,
            "clientConnected": False,
            "phase": "None",
            "summoner": None,
            "champSelect": None,
            "live": None,
            "postGame": None,
            "history": [],
            "stats": {"region": stats.region, "rank": stats.rank, "patch": None},
            "updatedAt": 0,
        }

    # --- Updates from the agent ---

    def _process(self, snap: dict):
        dd, ugg = self.stats.get()
        s = self.state
        s["stats"]["patch"] = dd.patch
        s["agentOnline"] = True
        s["clientConnected"] = bool(snap.get("clientConnected"))
        s["phase"] = snap.get("phase") or "None"
        s["summoner"] = snap.get("summoner") or s["summoner"]

        if snap.get("history") is not None:
            s["history"] = [advisor.lcu_match_view(g, dd) for g in snap["history"]]

        session = snap.get("champSelect")
        if session:
            state = lcu.parse_champ_select(session)
            key = (state.my_champion, state.my_role, state.my_locked, tuple(state.enemies),
                   tuple(sorted(state.allies.items())), tuple(state.bans),
                   tuple((p["champion"], p["locked"]) for p in state.my_team))
            if key != self.select_key:
                pool = set(snap.get("pickable") or []) or None
                s["champSelect"] = advisor.champ_select_view(state, ugg, dd, pool=pool, min_matches=self.min_games)
                self.select_key = key
                build = s["champSelect"].get("build")
                if build:
                    self.my_build = build
            elif s["champSelect"]:
                # Only the timer changed.
                s["champSelect"]["timer"] = {"phase": state.timer_phase,
                                             "endsAt": int(time.time() * 1000) + state.time_left_ms}
        elif s["phase"] not in ("ChampSelect", "GameStart", "InProgress"):
            self.select_key = None

        # Kept after the post game screen closes, until the next champ select.
        post = snap.get("postGame")
        if post and post.get("game"):
            s["postGame"] = advisor.post_game_view(post["game"], post.get("ranks") or {}, dd)
        elif s["phase"] == "ChampSelect":
            s["postGame"] = None

        live = snap.get("live")
        if live:
            s["live"] = advisor.live_view(live, dd, ugg, self._build_for_live(live, dd, ugg))
        elif s["phase"] != "InProgress":
            s["live"] = None
        s["updatedAt"] = int(time.time() * 1000)

    def _build_for_live(self, game: dict, dd: DDragon, ugg: UGG) -> dict | None:
        # Reuse the champ select build; look it up fresh if the agent started mid-game.
        me = advisor.live_view(game, dd).get("me")
        if not me or not me.get("id"):
            return self.my_build
        if self.my_build and self.my_build["champion"]["id"] == me["id"]:
            return self.my_build
        try:
            self.my_build = advisor.build_view(ugg.build(me["id"], me.get("role")), dd)
        except LookupError:
            self.my_build = None
        return self.my_build

    async def receive(self, snap: dict):
        async with self.lock:
            self.last_seen = time.time()
            await asyncio.to_thread(self._process, snap)
        await self.broadcast()

    async def watchdog(self):
        while True:
            await asyncio.sleep(5)
            if self.state["agentOnline"] and time.time() - self.last_seen > AGENT_TIMEOUT:
                self.state.update(agentOnline=False, clientConnected=False)
                await self.broadcast()

    # --- Browsers ---

    async def broadcast(self):
        data = json.dumps(self.state)
        for q in list(self.subscribers):
            if q.full():
                q.get_nowait()  # drop the stale update for slow clients
            q.put_nowait(data)

    async def stream(self, request: Request):
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.subscribers.add(q)
        try:
            yield f"data: {json.dumps(self.state)}\n\n"
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self.subscribers.discard(q)


def create_app() -> FastAPI:
    stats = Stats(os.environ.get("UGG_REGION", "world"), os.environ.get("UGG_RANK", "emerald_plus"))
    hub = Hub(stats, os.environ.get("AGENT_TOKEN") or None, int(os.environ.get("MIN_GAMES", "100")))

    @asynccontextmanager
    async def lifespan(app):
        if not hub.token:
            log.warning("AGENT_TOKEN is not set: anyone on the network can send data to this server")
        task = asyncio.create_task(hub.watchdog())
        yield
        task.cancel()

    app = FastAPI(title="LeagueAssistant", lifespan=lifespan)
    app.state.hub = hub

    @app.post("/api/agent")
    async def agent(request: Request):
        if hub.token and request.headers.get("authorization") != f"Bearer {hub.token}":
            raise HTTPException(401, "Bad agent token")
        await hub.receive(await request.json())
        return {"ok": True}

    @app.get("/api/state")
    async def state():
        return hub.state

    @app.get("/api/events")
    async def events(request: Request):
        return StreamingResponse(hub.stream(request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def lookup(champion: str, role: str | None):
        dd, ugg = stats.get()
        try:
            cid = dd.champion_id(champion)
            role = normalize_role(role) if role else ugg.main_role(cid)
        except (KeyError, ValueError) as e:
            raise HTTPException(404, str(e))
        return dd, ugg, cid, role

    @app.get("/api/build/{champion}")
    def build(champion: str, role: str | None = None):
        dd, ugg, cid, role = lookup(champion, role)
        try:
            return advisor.build_view(ugg.build(cid, role), dd)
        except LookupError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/counters/{champion}")
    def counters(champion: str, role: str | None = None, limit: int = 8):
        dd, ugg, cid, role = lookup(champion, role)
        picks = ugg.counters(cid, role, min_matches=hub.min_games, limit=limit)
        return {"champion": advisor.champ_ref(cid, dd), "role": role, "counters": advisor.counter_views(picks, dd)}

    @app.get("/api/champions")
    def champions():
        dd, _ = stats.get()
        return sorted((advisor.champ_ref(cid, dd) for cid in dd.champions), key=lambda c: c["name"])

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
