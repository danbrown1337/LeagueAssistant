"""Web server: receives agent snapshots, adds U.gg advice, streams it to browsers.

Run with `leagueassistant serve` or the Docker image. Settings come from
environment variables:

    AGENT_TOKEN   shared secret the agent must send (recommended)
    UGG_REGION    U.gg stats region (default world)
    UGG_RANK      U.gg rank filter (default emerald_plus)
    MIN_GAMES     ignore matchups with fewer games (default 100)
    LEAGUEASSISTANT_LOG   set to 0 to stop saving finished games to games.jsonl
    LEAGUEASSISTANT_DATA  folder for games.jsonl (default: your local app data folder)
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
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import advisor, lcu
from .gamelog import GameLog
from .updater import current_build
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
    def __init__(self, stats: Stats, token: str | None, min_games: int, gamelog: GameLog | None = None):
        self.stats = stats
        self.gamelog = gamelog
        self.prev_phase = "None"
        self.post_mode: dict | None = None  # mode of the game on the post game screen
        self.spell_uses: dict[str, float] = {}  # "player|slot" -> game time it was marked used
        self.my_boots: int | None = None  # your boots this game, kept when the quest slot hides them
        self.boots_source: str | None = None  # seen, gold (worked out from your gold) or you (you set them)
        self.prev_items: list[int] | None = None
        self.prev_gold: float | None = None
        self.debug_saved = 0.0
        self.last_game_time: float | None = None
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
            "mode": None,
            "mmr": [],
            "history": [],
            "stats": {"region": stats.region, "rank": stats.rank, "patch": None},
            "build": current_build(),
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
        live = snap.get("live")
        mode = advisor.game_mode(snap.get("queue"), live, session)
        s["mode"] = mode if s["phase"] in ("ChampSelect", "GameStart", "InProgress", "Reconnect") or live else None
        if session:
            state = lcu.parse_champ_select(session)
            key = (state.my_champion, state.my_role, state.my_locked, tuple(state.enemies),
                   tuple(sorted(state.allies.items())), tuple(state.bans),
                   tuple((p["champion"], p["locked"]) for p in state.my_team), tuple(state.bench), mode["gameMode"])
            if key != self.select_key:
                pool = set(snap.get("pickable") or []) or None
                s["champSelect"] = advisor.champ_select_view(state, ugg, dd, pool=pool, min_matches=self.min_games,
                                                             mode=mode)
                self.select_key = key
                self.my_build = s["champSelect"].get("build")
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
            if snap.get("queue"):
                self.post_mode = advisor.game_mode(snap["queue"])
        elif s["phase"] == "ChampSelect":
            s["postGame"] = None

        if live:
            t = (live.get("gameData") or {}).get("gameTime", 0)
            if self.last_game_time is None or t + 30 < self.last_game_time:
                self.spell_uses = {}  # a new game
                self.my_boots = self.boots_source = self.prev_items = self.prev_gold = None
            self._save_debug(live)  # as the game sent it
            live = self._keep_boots(live, dd)
            self.last_game_time = t
            s["live"] = advisor.live_view(live, dd, ugg, self._build_for_live(live, dd, ugg, mode), mode=mode,
                                          spell_uses=self.spell_uses)
            s["live"]["boots"] = {"id": self.my_boots, "source": self.boots_source,
                                  "choices": self._boots_choices(dd)}
        elif s["phase"] != "InProgress":
            s["live"] = None
            self.last_game_time = None
        if self.gamelog:
            self._log(snap, mode, dd)
            s["mmr"] = self.gamelog.mmr_summary()
        if s["postGame"]:
            gl = self.gamelog
            s["postGame"]["mmr"] = gl.mmr_estimate(self.post_mode or gl.last_mode) if gl else None
        self.prev_phase = s["phase"]
        s["updatedAt"] = int(time.time() * 1000)

    def _log(self, snap: dict, mode: dict, dd: DDragon):
        s, gl = self.state, self.gamelog
        queue = snap.get("queue") or {}
        mode = mode if queue or snap.get("live") else None
        try:
            if snap.get("live") and s["live"]:
                gl.live(s["live"], queue.get("gameId"), dd)
            post = snap.get("postGame")
            if post and post.get("game") and s["postGame"]:
                gl.post_game(post["game"], s["postGame"], mode, dd)
            ended = ("PreEndOfGame", "EndOfGame")
            if self.prev_phase in ended and s["phase"] not in ended and gl.current and snap.get("history"):
                gl.history(snap["history"][0], s["history"][0] if s["history"] else None, mode, dd)
        except Exception:
            log.exception("Couldn't record the game")  # never break the dashboard over logging

    def _build_for_live(self, game: dict, dd: DDragon, ugg: UGG, mode: dict) -> dict | None:
        # Reuse the champ select build; look it up fresh if the agent started mid-game.
        if mode["kind"] == "other":
            return None
        me = advisor.live_view(game, dd, mode=mode).get("me")
        if not me or not me.get("id"):
            return self.my_build
        aram = mode["kind"] == "aram"
        b = self.my_build
        if b and b["champion"]["id"] == me["id"] and (b["role"] == "aram") == aram:
            return b
        try:
            self.my_build = advisor.full_build_view(me["id"], None if aram else me.get("role"), ugg, dd, aram=aram)
        except LookupError:
            self.my_build = None
        return self.my_build

    def _keep_boots(self, live: dict, dd: DDragon) -> dict:
        """Your boots, including ones in the bot lane quest's boots slot, which the game doesn't list.

        Patch 26.1's bot lane quest opens a seventh slot for boots, and the live data leaves it out:
        boots moved there vanish, and boots bought into it never appear. So: boots once seen are kept;
        gold spent on nothing visible that matches a boots price means boots (or an upgrade) went into
        that slot; and you can set them yourself on the dashboard.
        """
        names = advisor._player_names(live.get("activePlayer") or {})
        me = next((p for p in live.get("allPlayers") or [] if advisor._player_names(p) & names), None)
        if not me:
            return live
        items = [dd.base_item(i.get("itemID") or 0) for i in me.get("items") or []
                 if i.get("itemID") and "Trinket" not in dd.item(dd.base_item(i["itemID"])).get("tags", [])]
        gold = (live.get("activePlayer") or {}).get("currentGold")
        seen = [i for i in items if advisor.is_boots(i, dd)]
        if seen:
            self.my_boots, self.boots_source = seen[0], "seen"
        elif self.prev_items is not None and gold is not None and self.prev_gold is not None:
            unexplained = (self.prev_gold - gold) - self._visible_spend(self.prev_items, items, dd)
            guess = self._boots_for_gold(unexplained, dd) if unexplained > 200 else None
            if guess:
                self.my_boots, self.boots_source = guess, "gold"
        self.prev_items, self.prev_gold = items, gold
        if seen or not self.my_boots:
            return live
        kept = {**me, "items": [*(me.get("items") or []), {"itemID": self.my_boots, "slot": "boots", "count": 1}]}
        return {**live, "allPlayers": [kept if p is me else p for p in live["allPlayers"]]}

    @staticmethod
    def _visible_spend(before: list[int], after: list[int], dd: DDragon) -> int:
        """Gold the items that appeared since the last update cost, given what you held before."""
        from collections import Counter

        from .itemizer import finish_cost

        new = Counter(after) - Counter(before)
        held = list(before)
        total = 0
        for iid in new.elements():
            cost, used = finish_cost(iid, held, dd)
            total += cost
            for u in used:
                if u in held:
                    held.remove(u)
        return total

    def _boots_for_gold(self, spent: float, dd: DDragon) -> int | None:
        """The boots (or boots upgrade) that cost about `spent`, preferring the ones in your build."""
        from .itemizer import finish_cost

        boots = [i for i, it in dd.all_items().items() if i < 10000 and advisor.is_boots(i, dd)
                 and it.get("gold", {}).get("purchasable") and (it.get("maps", {}).get("11") or it.get("maps", {}).get("12"))]
        held = [self.my_boots] if self.my_boots else []
        options = []
        for b in boots:
            if b == self.my_boots:
                continue
            cost, used = finish_cost(b, held, dd)
            if held and not used:
                continue  # not an upgrade of the boots you have
            if abs(cost - spent) <= 40:
                options.append((b, abs(cost - spent)))
        if not options:
            return None
        build = self.my_build or {}
        preferred = [i["id"] for i in build.get("core", [])] + [o["id"] for o in build.get("bootsOptions", [])]
        options.sort(key=lambda o: (o[0] not in preferred, preferred.index(o[0]) if o[0] in preferred else 0, o[1]))
        return options[0][0]

    def _boots_choices(self, dd: DDragon) -> list[dict]:
        """Boots to pick from on the dashboard: your build's first, then the other common pairs."""
        build = self.my_build or {}
        ids = [i["id"] for i in build.get("core", []) if advisor.is_boots(i["id"], dd)]
        ids += [o["id"] for o in build.get("bootsOptions", [])]
        ids += [1001, 3006, 3009, 3020, 3047, 3111, 3158, 3008]
        out, seen = [], set()
        for i in ids:
            if i not in seen and dd.item(i):
                seen.add(i)
                out.append(advisor.item_ref(i, dd))
        return out

    async def set_boots(self, iid: int | None):
        async with self.lock:
            self.my_boots, self.boots_source = iid, ("you" if iid else None)
        await self.broadcast()

    def _save_debug(self, live: dict):
        """The latest raw game data, every 30 seconds, for checking what the game reports."""
        if not self.gamelog or time.time() - self.debug_saved < 30:
            return
        self.debug_saved = time.time()
        try:
            path = self.gamelog.path.parent / "last-game-data.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(live, indent=1), encoding="utf-8")
        except OSError:
            pass

    async def mark_spell(self, player: str, slot: str, at: float | None):
        """Start (at = game time) or clear (at = None) a summoner spell timer, then update everyone."""
        async with self.lock:
            key = f"{player}|{slot}"
            if at is None:
                self.spell_uses.pop(key, None)
            else:
                self.spell_uses[key] = float(at)
            live = self.state.get("live")
            for p in (live or {}).get("spells", []):
                if p["key"] == player:
                    for sp in p["spells"]:
                        if sp["slot"] == slot:
                            sp["usedAt"] = self.spell_uses.get(key)
        await self.broadcast()

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
    gamelog = GameLog(build=current_build()) if os.environ.get("LEAGUEASSISTANT_LOG", "1") != "0" else None
    hub = Hub(stats, os.environ.get("AGENT_TOKEN") or None, int(os.environ.get("MIN_GAMES", "100")), gamelog)

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

    @app.get("/api/agent", include_in_schema=False)
    async def agent_in_browser():
        # Only the agent POSTs here; send people who open it in a browser to the dashboard.
        return RedirectResponse("/")

    @app.post("/api/boots")
    async def boots(request: Request):
        # From the dashboard: the boots in your quest slot, which the game doesn't report.
        iid = (await request.json()).get("id")
        dd, _ = stats.get()
        if iid is not None and not (isinstance(iid, int) and advisor.is_boots(iid, dd)):
            raise HTTPException(400, "Not boots")
        await hub.set_boots(iid)
        return {"ok": True}

    @app.post("/api/spells")
    async def spells(request: Request):
        # From the dashboard (phone or PC), so no agent token: it only starts or clears a timer.
        body = await request.json()
        player, slot, at = body.get("player"), body.get("slot"), body.get("at")
        known = any(p["key"] == player and any(sp["slot"] == slot for sp in p["spells"])
                    for p in (hub.state.get("live") or {}).get("spells", []))
        if not known or (at is not None and not isinstance(at, (int, float))):
            raise HTTPException(400, "Unknown player or spell")
        await hub.mark_spell(player, slot, at)
        return {"ok": True}

    @app.api_route("/api/state", methods=["GET", "HEAD"])
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
            if role == "aram":
                return dd, ugg, cid, role
            role = normalize_role(role) if role else ugg.main_role(cid)
        except (KeyError, ValueError) as e:
            raise HTTPException(404, str(e))
        return dd, ugg, cid, role

    @app.get("/api/build/{champion}")
    def build(champion: str, role: str | None = None):
        dd, ugg, cid, role = lookup(champion, role)
        try:
            return advisor.full_build_view(cid, None if role == "aram" else role, ugg, dd, aram=role == "aram")
        except LookupError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/counters/{champion}")
    def counters(champion: str, role: str | None = None, limit: int = 8):
        dd, ugg, cid, role = lookup(champion, role)
        if role == "aram":
            return {"champion": advisor.champ_ref(cid, dd), "role": role, "counters": []}
        picks = ugg.counters(cid, role, min_matches=hub.min_games, limit=limit)
        return {"champion": advisor.champ_ref(cid, dd), "role": role, "counters": advisor.counter_views(picks, dd)}

    @app.get("/api/champions")
    def champions():
        dd, _ = stats.get()
        return sorted((advisor.champ_ref(cid, dd) for cid in dd.champions), key=lambda c: c["name"])

    # HEAD too: uptime monitors and link previews check pages that way.
    @app.api_route("/healthz", methods=["GET", "HEAD"])
    async def healthz():
        return {"ok": True, "build": current_build()}

    @app.api_route("/", methods=["GET", "HEAD"])
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
