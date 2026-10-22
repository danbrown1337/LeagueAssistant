"""Command line entry point.

Everything on the gaming PC (what the Windows .exe runs when double-clicked):
    leagueassistant local

Or split, with the server on the homelab (or in Docker):
    leagueassistant serve

On the gaming PC:
    leagueassistant agent --server http://homelab:8080 --token SECRET

Quick lookups from any machine:
    leagueassistant build ahri mid
    leagueassistant counters yasuo mid
    leagueassistant history
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import advisor, lcu
from .ddragon import DDragon
from .riot import RiotAPI
from .ugg import RANKS, REGIONS, UGG, normalize_role


def make_clients(args) -> tuple[DDragon, UGG]:
    dd = DDragon()
    return dd, UGG(dd.patch, region=args.region, rank=args.rank)


def cmd_build(args):
    dd, ugg = make_clients(args)
    cid = dd.champion_id(args.champion)
    role = normalize_role(args.role) if args.role else None
    print(advisor.format_build(ugg.build(cid, role), dd))


def cmd_counters(args):
    dd, ugg = make_clients(args)
    cid = dd.champion_id(args.champion)
    role = normalize_role(args.role) if args.role else ugg.main_role(cid)
    picks = ugg.counters(cid, role, min_matches=args.min_games, limit=args.limit)
    print(advisor.format_counters(cid, role, picks, dd))


def cmd_history(args):
    dd = DDragon()
    if args.riot_id:
        api = RiotAPI(routing=args.routing)
        puuid = api.puuid(args.riot_id)
        for mid in api.match_ids(puuid, count=args.count):
            print(advisor.format_riot_match(api.match(mid), puuid))
        return
    creds = lcu.find_credentials(args.lockfile)
    if not creds:
        sys.exit("League client not running. Start it, or pass --riot-id Name#TAG with RIOT_API_KEY set.")
    for game in lcu.LCU(creds).match_history(args.count):
        print(advisor.format_match(advisor.lcu_match_view(game, dd)))


def cmd_agent(args):
    from .agent import Agent

    Agent(args.server, token=args.token, lockfile=args.lockfile, interval=args.interval).run()


def cmd_serve(args):
    import uvicorn

    os.environ.setdefault("UGG_REGION", args.region)
    os.environ.setdefault("UGG_RANK", args.rank)
    os.environ.setdefault("MIN_GAMES", str(args.min_games))
    uvicorn.run("leagueassistant.server:create_app", factory=True, host=args.host, port=args.port)


def cmd_local(args):
    """Server and agent in one process, for running everything on the gaming PC."""
    import secrets
    import threading
    import time
    import webbrowser

    import uvicorn

    from . import updater
    from .agent import Agent

    just_updated = bool(os.environ.pop(updater.JUST_UPDATED, None))
    auto_update = args.update and updater.enabled()
    if auto_update:
        updater.cleanup()
        if not just_updated:
            updater.update(sys.argv[1:])  # restarts into the new version if there is one

    # Only this process talks to its own server, so a fresh token each run is enough.
    token = secrets.token_hex(16)
    os.environ["AGENT_TOKEN"] = token
    os.environ.setdefault("UGG_REGION", args.region)
    os.environ.setdefault("UGG_RANK", args.rank)
    os.environ.setdefault("MIN_GAMES", str(args.min_games))
    from .server import create_app

    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning"))
    url = f"http://127.0.0.1:{args.port}"

    def keep_updated():
        # Only between games, so an update never interrupts champ select or a game.
        while True:
            time.sleep(updater.CHECK_EVERY)
            state = app.state.hub.state
            if state["phase"] in ("None", "Lobby", "EndOfGame") and not state["live"]:
                updater.update(sys.argv[1:])

    def run_agent():
        while not server.started:
            if server.should_exit:
                return
            time.sleep(0.2)
        build = updater.current_build()
        print(f"\nLeagueAssistant{f' (build {build[:7]})' if build else ''} is running. Dashboard: {url}")
        if args.host in ("127.0.0.1", "localhost"):
            print(f"To open it on your phone over Tailscale, run this once:  tailscale serve --bg {args.port}")
        print("Leave this window open. Close it or press Ctrl+C to stop.\n")
        if args.open and not just_updated:
            webbrowser.open(url)
        Agent(url, token=token, lockfile=args.lockfile, interval=args.interval).run()

    threading.Thread(target=run_agent, daemon=True).start()
    if auto_update:
        threading.Thread(target=keep_updated, daemon=True).start()
    try:
        server.run()
    except SystemExit:
        pass  # uvicorn exits this way when it can't listen
    if not server.started:
        raise RuntimeError(f"Couldn't listen on {args.host}:{args.port}. Something else may be using that port; "
                           f"try --port {args.port + 1}.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="leagueassistant", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", default="world", choices=REGIONS, help="U.gg stats region (default: world)")
    p.add_argument("--rank", default="emerald_plus", choices=RANKS, help="U.gg rank filter (default: emerald_plus)")
    p.add_argument("--lockfile", help="Path to the League client lockfile if it can't be found automatically")
    p.add_argument("--min-games", type=int, default=100, help="Ignore matchups with fewer games than this")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="Run the web dashboard server")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(func=cmd_serve)

    a = sub.add_parser("agent", help="Run on the gaming PC: send League client data to the server")
    a.add_argument("--server", default=os.environ.get("LEAGUEASSISTANT_SERVER"), required=not os.environ.get("LEAGUEASSISTANT_SERVER"),
                   help="Server URL, e.g. http://homelab:8080")
    a.add_argument("--token", default=os.environ.get("AGENT_TOKEN"), help="Must match AGENT_TOKEN on the server")
    a.add_argument("--interval", type=float, default=2.0, help="Seconds between updates")
    a.set_defaults(func=cmd_agent)

    lo = sub.add_parser("local", help="Run the server and agent together on the gaming PC")
    lo.add_argument("--host", default="127.0.0.1",
                    help="Address to listen on (default 127.0.0.1; use tailscale serve to reach it from other devices)")
    lo.add_argument("--port", type=int, default=8080)
    lo.add_argument("--interval", type=float, default=2.0, help="Seconds between updates")
    lo.add_argument("--no-browser", dest="open", action="store_false", help="Don't open the dashboard on start")
    lo.add_argument("--no-update", dest="update", action="store_false",
                    help="Don't install new versions from GitHub automatically (the .exe only)")
    lo.set_defaults(func=cmd_local)

    b = sub.add_parser("build", help="Show the U.gg build for a champion")
    b.add_argument("champion")
    b.add_argument("role", nargs="?")
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("counters", help="Show the best picks against a champion")
    c.add_argument("champion")
    c.add_argument("role", nargs="?")
    c.add_argument("--limit", type=int, default=8)
    c.set_defaults(func=cmd_counters)

    h = sub.add_parser("history", help="Show recent matches")
    h.add_argument("--count", type=int, default=10)
    h.add_argument("--riot-id", help="Use the Riot API for this Name#TAG instead of the League client")
    h.add_argument("--routing", default="americas", help="Riot API region: americas, europe, asia or sea")
    h.set_defaults(func=cmd_history)

    frozen = getattr(sys, "frozen", False)  # the Windows .exe
    if argv is None:
        argv = sys.argv[1:]
    if frozen and not any(a in sub.choices for a in argv):
        # Double-clicked or run with only options: run everything. Shared options go before
        # the command and the rest after it.
        shared = {"--region", "--rank", "--lockfile", "--min-games"}
        before, after, it = [], [], iter(argv)
        for a in it:
            if a.split("=", 1)[0] in shared:
                before += [a] if "=" in a else [a, next(it, "")]
            else:
                after.append(a)
        argv = [*before, "local", *after]
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        args.func(args)
    except KeyboardInterrupt:
        pass
    except (KeyError, LookupError, ValueError, RuntimeError, OSError) as e:
        if frozen:
            # Keep the console window open long enough to read the error.
            print(f"\nError: {e}")
            try:
                input("Press Enter to close.")
            except EOFError:
                pass
        sys.exit(str(e))


if __name__ == "__main__":
    main()
