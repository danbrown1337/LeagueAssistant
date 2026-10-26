# LeagueAssistant

A live dashboard for your League games. Run it on your gaming PC and open it
on a phone, tablet or second monitor. It follows your game:

- **Champ select:** guesses each enemy's role, finds your lane opponent and
  shows the best counters you own, your matchup win rate and the full U.gg
  build (runes, spells, items, skill order). Updates as picks come in.
- **In game:** live scoreboard with kills, towers, drakes, barons and item gold
  lead; every player's items, KDA, CS and respawn timers; your lane opponent's
  items; and a build that adjusts to the game. Every update it reads the enemy
  team (how much of their damage is magic or physical, healing, armor and
  magic resist stacking, crowd control, who is fed) and tells you what to buy
  next and why, including the part you can afford right now.
  On Summoner's Rift it also times Dragon, Elder, Baron and inhibitor
  respawns from the game's kill events, shows each team's drakes and soul,
  and flags when enemies are dead and how long the window is.
  Summoner spell timers for all ten players: tap a spell when you see it
  used and it counts down (cooldowns from Data Dragon, less Summoner Spell
  Haste from items like Ionian Boots; tap again to undo). Taps are shared,
  so marking Flash on your phone shows on the PC too. There are no ultimate
  timers: Riot's third-party app policy bans enemy ult tracking.
- **Post game:** the rank of every player in the game you just played, the
  average rank of your team, the enemy team and the whole lobby, and an
  estimated MMR for that queue (normals and ARAM included).
- **Between games:** recent match history and a champion lookup.
- **ARAM:** uses U.gg's ARAM builds instead of Summoner's Rift ones, drops
  lanes and counters, and ranks your champion, your teammates' and the bench
  by ARAM win rate so you know what to grab or trade for. For modes U.gg has
  no data for (Arena, URF and others) it says so and shows only the
  scoreboard.
- **ARAM: Mayhem:** the ARAM build (U.gg uses the same one for Mayhem) plus
  U.gg's augment ranking for your champion, Prismatic, Gold and Silver, best
  first, with a search box to check where an offered augment ranks.

## Run it on the gaming PC (Windows)

1. Download `leagueassistant.exe` from the
   [latest release](https://github.com/danbrown1337/LeagueAssistant/releases/latest).
2. Double-click it. The dashboard opens in your browser at
   `http://127.0.0.1:8080`. Leave the window open while you play. It waits for
   the League client and picks up champ select and games on its own.

The first time you run it, Windows may say "Windows protected your PC" because
the .exe isn't code-signed. Click **More info**, then **Run anyway**.

### Updates

The .exe updates itself. When it starts, and every 6 hours between games, it
checks the latest release. If there's a newer build, it downloads it, checks
it against the release's checksum, replaces itself and restarts in a new
window. That takes a few seconds and never happens during champ select or a
game. Keep the .exe in a folder you can write to (Downloads or Documents is
fine; Program Files isn't). Add `--no-update` to turn this off.

### Boots and the bot lane quest

Since patch 26.1, completing the bot lane role quest opens a seventh item slot
for boots, and the game's live data doesn't include it: boots moved there
vanish, and boots bought into it never show up. The app keeps boots it has
seen, works out boots bought into that slot from gold you spent on nothing
visible (300 for Boots, 700 more for Swiftness, and so on), and shows a row
of boots in the build panel so you can tap yours if it gets them wrong. The
latest raw game data is saved every 30 seconds to `last-game-data.json` next
to `games.jsonl`, for checking what the game reports.

### Game log

Every finished game is saved as one line of JSON in
`%LOCALAPPDATA%\LeagueAssistant\games.jsonl` (the console shows the exact
path when it starts). Each record has the mode, champion, result, K/D/A, CS,
duration, final items, every player's rank with the team and lobby averages,
each change in the build advice (time, item, reason, threats), and every item
you bought with whether it matched the advice at the time. Nothing leaves the
PC. Add `--no-log` to turn it off.

### Open it on your phone over Tailscale

With Tailscale installed on the PC, run this once in a terminal:

```
tailscale serve --bg 8080
```

The dashboard is then at `https://<pc-name>.<tailnet>.ts.net` on every device
in your tailnet, and nowhere else. Tailscale remembers this setting across
restarts. If it asks you to enable HTTPS for your tailnet, follow the link it
prints.

### Start it when you log in

Press Win+R, run `shell:startup`, and put a shortcut to `leagueassistant.exe`
in that folder. To stop it opening a browser each time, edit the shortcut's
target to add `--no-browser` at the end.

### Options

Add these to the shortcut's target or the command line:

| Option | Default | Example |
| --- | --- | --- |
| `--region` | `world` | `--region na` (also `euw`, `kr`, ...) |
| `--rank` | `emerald_plus` | `--rank diamond_plus` (also `platinum_plus`, `overall`, ...) |
| `--port` | `8080` | `--port 8081` if 8080 is taken |
| `--lockfile` | found automatically | `--lockfile "D:\Riot Games\League of Legends\lockfile"` |
| `--no-browser` | opens the browser | |
| `--no-update` | updates itself | |
| `--no-log` | saves finished games | |

The app only reads data from the League client and the in-game API. It never
sends input to the game.

## Running the server on a home lab instead

```
 Gaming PC                                Home lab (Docker)
┌──────────────────────────┐   HTTP     ┌──────────────────────────┐
│ League client ─┐         │  snapshots │ leagueassistant serve    │──▶ U.gg, Data Dragon
│ In-game API  ──┴▶ agent ─┼───────────▶│ advice + web dashboard   │
└──────────────────────────┘            └────────────┬─────────────┘
                                                     │ live updates
                                         phone / tablet / browser
```

The League client and in-game APIs only listen on `127.0.0.1` of the gaming
PC, so in this setup a small agent there forwards their data to the server.

### 1. Run the server on the home lab

```
git clone git@github.com:danbrown1337/LeagueAssistant.git
cd LeagueAssistant
echo "AGENT_TOKEN=$(openssl rand -hex 16)" > .env
docker compose up -d --build
```

The dashboard is at `http://<homelab>:8080`. Keep the token from `.env` for
the next step.

Optional settings in `.env`: `UGG_RANK` (default `emerald_plus`; e.g.
`diamond_plus`, `platinum_plus`, `overall`) and `UGG_REGION` (default `world`;
e.g. `na`, `euw`, `kr`).

### 2. Run the agent on the gaming PC

With the .exe:

```
leagueassistant.exe agent --server http://<homelab>:8080 --token <AGENT_TOKEN>
```

Or from source, with Python 3.10+ (`winget install Python.Python.3.12`):

```
git clone https://github.com/danbrown1337/LeagueAssistant.git
cd LeagueAssistant
python -m venv .venv
.venv\Scripts\activate
pip install -e .
leagueassistant agent --server http://<homelab>:8080 --token <AGENT_TOKEN>
```

Leave it running; it waits for the League client and reconnects on its own.
You can also set `LEAGUEASSISTANT_SERVER` and `AGENT_TOKEN` as environment
variables and just run `leagueassistant agent`.

## Try it without League

With the server running, this plays a sped-up fake game through every screen:

```
python scripts/demo_agent.py --server http://<homelab>:8080 --token <AGENT_TOKEN>
```

## Command line

The same stats work from a terminal:

```
leagueassistant build ahri mid
leagueassistant counters yasuo mid
leagueassistant history                       # needs the League client running
leagueassistant history --riot-id "Name#TAG"  # needs RIOT_API_KEY
```

## Data sources

| Source | Used for | Notes |
| --- | --- | --- |
| League client API | Game phase, champ select, match history, post-game ranks | Local to the gaming PC, no key |
| Live Client Data API (`:2999`) | In-game scoreboard and items | Only while a game is running |
| U.gg (`stats2.u.gg`) | Builds, matchups, role play rates | Unofficial; the format can change without notice |
| Data Dragon | Names and icons for champions, items, runes, spells | Official static data |
| Riot API | Match history by Riot ID | Optional, needs a key from developer.riotgames.com |

The in-game build starts from U.gg's core build. A situational item jumps
ahead of it only for an urgent threat (heavy healing, three or more enemies
stacking armor or magic resist, or a fed assassin of the damage type it
counters), and only once you have your first core item or the game is past 15
minutes. Situational items are chosen from Data Dragon's item stats to suit
how your champion builds (magic damage, attack damage, crit or tank), and U.gg
picks for your champion win ties. Who counts as a healer or as heavy crowd
control is a hand-written list in `leagueassistant/itemizer.py`.

Counter picks are ranked by matchup win rate pulled toward 50% for small
samples, and champions that play the role in under 10% of their games are
left out. Item gold in the scoreboard is the value of items bought, the
closest thing to team gold the in-game API exposes.

Riot keeps MMR hidden, for normals and ranked alike, and no API exposes it.
The estimate uses the usual method: matchmaking puts you with players near
your MMR, so the average rank of the other nine players is a good guess. A
single lobby is noisy, so the estimate averages your last 10 games in the
same queue from the game log, weighted by how many ranked players each lobby
had. It's per queue (Normal Draft, Quickplay, ARAM, ...) because each has
its own MMR, and it gets steadier the more you play.

Post-game ranks use each player's rank in the queue you played. Normal games
use Solo/Duo. If a player has no rank there this season, their other ranked
queue is used, and then their rank from last season. Team averages put every
division on one scale, so Gold II and Platinum II average to Platinum IV.
Master and above count as one tier. Normal games match players on a hidden
rating, so for those games the averages are only a rough guide.

## Development

```
pip install -e ".[server,dev]"
pytest
leagueassistant local            # server and agent together
```

Every push to `main` runs the tests, builds `leagueassistant.exe` on Windows,
checks that it starts, and publishes it as the `latest` release
(`.github/workflows/windows-exe.yml`). To build it yourself on Windows:

```
pip install ".[server]" pyinstaller
pyinstaller packaging/leagueassistant.spec
```
