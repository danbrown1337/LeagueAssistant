"""The League client's local API (LCU) and the in-game Live Client Data API.

Both only exist on the PC running League. The LCU port and password change
every time the client starts, so we read them from the running
LeagueClientUx process or from the client's lockfile.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import requests
import urllib3

# Both local APIs use Riot's self-signed certificate.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LOCKFILE_PATHS = [
    Path(r"C:\Riot Games\League of Legends\lockfile"),
    Path("/Applications/League of Legends.app/Contents/LoL/lockfile"),
]
LIVE_CLIENT_URL = "https://127.0.0.1:2999/liveclientdata"


@dataclass
class Credentials:
    port: int
    password: str


def parse_lockfile(text: str) -> Credentials:
    # Format: LeagueClient:<pid>:<port>:<password>:https
    _, _, port, password, _ = text.strip().split(":")
    return Credentials(int(port), password)


def credentials_from_process() -> Credentials | None:
    import psutil

    for proc in psutil.process_iter(["name", "cmdline"]):
        name = (proc.info.get("name") or "").lower()
        if not name.startswith("leagueclientux"):
            continue
        args = {}
        for arg in proc.info.get("cmdline") or []:
            if arg.startswith("--") and "=" in arg:
                k, v = arg[2:].split("=", 1)
                args[k] = v
        if "app-port" in args and "remoting-auth-token" in args:
            return Credentials(int(args["app-port"]), args["remoting-auth-token"])
    return None


def find_credentials(lockfile: str | None = None) -> Credentials | None:
    lockfile = lockfile or os.environ.get("LEAGUE_LOCKFILE")
    if lockfile:
        return parse_lockfile(Path(lockfile).read_text())
    try:
        creds = credentials_from_process()
    except Exception:
        creds = None
    if creds:
        return creds
    for path in LOCKFILE_PATHS:
        if path.exists():
            return parse_lockfile(path.read_text())
    return None


class LCU:
    def __init__(self, creds: Credentials):
        self.base = f"https://127.0.0.1:{creds.port}"
        self.session = requests.Session()
        self.session.auth = ("riot", creds.password)
        self.session.verify = False

    def get(self, path: str):
        r = self.session.get(self.base + path, timeout=5)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def phase(self) -> str:
        """None, Lobby, Matchmaking, ReadyCheck, ChampSelect, InProgress, EndOfGame, ..."""
        return self.get("/lol-gameflow/v1/gameflow-phase") or "None"

    def queue(self) -> dict | None:
        """The current lobby or game's queue: id, gameMode (CLASSIC, ARAM, CHERRY, ...) and mapId."""
        q = ((self.get("/lol-gameflow/v1/session") or {}).get("gameData") or {}).get("queue") or {}
        return {k: q.get(k) for k in ("id", "gameMode", "mapId", "description")} if q else None

    def champ_select(self) -> dict | None:
        return self.get("/lol-champ-select/v1/session")

    def pickable_champions(self) -> set[int]:
        return set(self.get("/lol-champ-select/v1/pickable-champion-ids") or [])

    def current_summoner(self) -> dict | None:
        return self.get("/lol-summoner/v1/current-summoner")

    def match_history(self, count: int = 10) -> list[dict]:
        data = self.get(f"/lol-match-history/v1/products/lol/current-summoner/matches?begIndex=0&endIndex={count}")
        return (data or {}).get("games", {}).get("games", [])

    def end_of_game(self) -> dict | None:
        """The post-game scoreboard: both teams with each player's puuid, champion and stats."""
        return self.get("/lol-end-of-game/v1/eog-stats-block")

    def ranked_stats(self, puuid: str) -> dict | None:
        """Any player's ranked tiers per queue, plus last season's."""
        return self.get(f"/lol-ranked/v1/ranked-stats/{puuid}")


def live_game() -> dict | None:
    """All in-game data, or None when no game is running."""
    try:
        r = requests.get(f"{LIVE_CLIENT_URL}/allgamedata", verify=False, timeout=2)
    except requests.RequestException:
        return None
    return r.json() if r.status_code == 200 else None


@dataclass
class ChampSelectState:
    my_champion: int  # locked or hovered, 0 if none
    my_role: str | None  # U.gg role name
    allies: dict[str, int]  # role -> champion id, teammates with a known role
    enemies: list[int]  # enemy champion ids picked so far
    bans: list[int]
    my_team: list[dict] = field(default_factory=list)  # {champion, role, locked, me} per teammate
    my_locked: bool = False
    timer_phase: str = ""  # BAN_PICK, FINALIZATION, ...
    time_left_ms: int = 0
    bench: list[int] = field(default_factory=list)  # ARAM champions anyone on the team can swap for
    bench_enabled: bool = False


def parse_champ_select(session: dict) -> ChampSelectState:
    from .ugg import LCU_POSITIONS

    me = session.get("localPlayerCellId")
    # A pick action that is completed means that player has locked in.
    locked_cells = {
        a.get("actorCellId")
        for group in session.get("actions") or []
        for a in group
        if a.get("type") == "pick" and a.get("completed")
    }
    my_champion, my_role, allies, team = 0, None, {}, []
    for p in session.get("myTeam", []):
        champ = p.get("championId") or p.get("championPickIntent") or 0
        role = LCU_POSITIONS.get(p.get("assignedPosition") or "")
        is_me = p.get("cellId") == me
        team.append({"champion": champ, "role": role, "locked": p.get("cellId") in locked_cells, "me": is_me})
        if is_me:
            my_champion, my_role = champ, role
        elif role and champ:
            allies[role] = champ
    enemies = [p["championId"] for p in session.get("theirTeam", []) if p.get("championId")]
    bans_info = session.get("bans") or {}
    bans = [b for b in bans_info.get("myTeamBans", []) + bans_info.get("theirTeamBans", []) if b]
    timer = session.get("timer") or {}
    bench = [b.get("championId") if isinstance(b, dict) else b
             for b in session.get("benchChampions") or session.get("benchChampionIds") or []]
    return ChampSelectState(
        my_champion, my_role, allies, enemies, bans,
        my_team=team,
        my_locked=me in locked_cells,
        timer_phase=timer.get("phase", ""),
        time_left_ms=int(timer.get("adjustedTimeLeftInPhase", 0)),
        bench=[b for b in bench if b],
        bench_enabled=bool(session.get("benchEnabled")),
    )
