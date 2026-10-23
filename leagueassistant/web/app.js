"use strict";

const app = document.getElementById("app");
const statusEl = document.getElementById("status");
let state = null;
let currentView = null;
let liveClock = { gameTime: 0, at: 0 };
let timerMax = {};

// ---------- Helpers ----------

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (x == null ? "–" : (x * 100).toFixed(1) + "%");
const wrClass = (x) => (x == null ? "" : x >= 0.53 ? "good" : x < 0.48 ? "bad" : "ok");
const num = (n) => Number(n || 0).toLocaleString();
const clock = (secs) => `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, "0")}`;
const ROLE_LABEL = { top: "Top", jungle: "Jungle", mid: "Mid", adc: "Bot", support: "Support", aram: "ARAM" };
const roleLabel = (r) => ROLE_LABEL[r] || "";

function icon(ref, cls = "") {
  if (!ref || !ref.icon) return `<span class="icon empty ${cls}"></span>`;
  return `<img class="icon ${cls}" src="${esc(ref.icon)}" alt="${esc(ref.name)}" title="${esc(ref.name)}" loading="lazy">`;
}
function splash(ref) {
  if (!ref || !ref.icon) return "";
  // Champion icon URL -> splash art URL (…/img/champion/Ahri.png -> …/img/champion/splash/Ahri_0.jpg)
  const m = ref.icon.match(/\/img\/champion\/([^/.]+)\.png$/);
  return m ? `https://ddragon.leagueoflegends.com/cdn/img/champion/splash/${m[1]}_0.jpg` : "";
}
function timeAgo(ms) {
  if (!ms) return "";
  const m = Math.round((Date.now() - ms) / 60000);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

// ---------- Status bar ----------

function renderStatus(s) {
  const pill = (on, label) => `<span class="pill"><span class="dot ${on ? "on" : "off"}"></span>${label}</span>`;
  const phase = { None: "Idle", ChampSelect: "Champ select", InProgress: "In game", EndOfGame: "Post game" }[s.phase] || s.phase;
  const who = s.summoner?.gameName ? `<span class="pill wide">${esc(s.summoner.gameName)}<span class="muted">#${esc(s.summoner.tagLine)}</span></span>` : "";
  statusEl.innerHTML =
    who +
    pill(s.agentOnline, "PC agent") +
    pill(s.clientConnected, "League client") +
    `<span class="pill">${esc(phase)}${s.mode ? ` · ${esc(s.mode.label)}` : ""}</span>` +
    `<span class="pill wide">Patch ${esc(s.stats.patch || "…")} · ${esc(s.stats.rank.replace("_plus", "+").replace("_", " "))} · ${esc(s.stats.region)}</span>`;
}

// ---------- Build card ----------

function renderBuild(b, opts = {}) {
  if (!b) return "";
  const p = b.runes.primary, s = b.runes.secondary;
  const tree = (t, keystone) => `
    <div class="tree">${icon(t, "sm round")}
      ${t.runes.map((r, i) => icon(r, keystone && i === 0 ? "keystone round" : "round")).join("")}
    </div>`;
  const later = b.later.map((slot, i) => `
    <div class="item-row" style="margin-top:6px"><span class="slot-label">${i + 4}</span>
      ${slot.map((o) => `<div class="opt">${icon(o, "sm")}<span class="p">${pct(o.winRate)}</span></div>`).join("")}
    </div>`).join("");
  const order = b.skills.order;
  const skills = ["Q", "W", "E", "R"].map((k) =>
    `<div class="k">${k}</div>` + Array.from({ length: 18 }, (_, i) => `<div class="c ${order[i] === k ? "on " + k : ""}">${order[i] === k ? i + 1 : ""}</div>`).join("")
  ).join("");
  return `
    <div class="panel gold fade-in">
      ${opts.hideHead ? "" : `<div class="build-head">${icon(b.champion, "lg")}
        <div><div class="title">${esc(b.champion.name)} <span class="muted">${esc(roleLabel(b.role))}</span></div>
        <div class="muted"><span class="wr ${wrClass(b.winRate)}">${pct(b.winRate)}</span> win rate · ${num(b.games)} games</div></div></div>`}
      <div class="build">
        <div>
          <div class="section"><h3>Runes</h3>${tree(p, true)}${tree(s, false)}
            <div class="shards">${b.runes.shards.map((x) => `<span class="shard">${esc(x)}</span>`).join("")}</div></div>
          <div class="section"><h3>Summoner spells</h3><div class="item-row">${b.spells.map((x) => icon(x)).join("")}</div></div>
        </div>
        <div>
          <div class="section"><h3>Starting items</h3><div class="item-row">${b.starting.map((x) => icon(x)).join("")}</div></div>
          <div class="section"><h3>Core build</h3><div class="item-row">${b.core.map((x) => icon(x)).join('<span class="arrow">›</span>')}</div>
            ${b.bootsNote ? `<div class="muted" style="font-size:12px;margin-top:6px">${esc(b.bootsNote)}</div>` : ""}
            ${b.bootsOptions?.length > 1 ? `<div class="item-row" style="margin-top:6px">${b.bootsOptions.map((o) => `<div class="opt">${icon(o, "sm")}<span class="p">${Math.round(o.share * 100)}%</span></div>`).join("")}</div>` : ""}</div>
          <div class="section"><h3>Later options</h3>${later}</div>
        </div>
        <div>
          <div class="section"><h3>Skill order</h3>
            <div class="prio">${b.skills.priority.map((k) => `<span class="key">${k}</span>`).join('<span class="arrow">›</span>')}</div>
            <div class="skills">${skills}</div></div>
        </div>
      </div>
    </div>`;
}

function renderCounters(list, enemy) {
  if (!list || !list.length) return `<div class="muted">No counter data yet.</div>`;
  return `<div class="counters">${list.map((c, i) => `
    <div class="counter ${i === 0 ? "top" : ""}">${icon(c, i === 0 ? "lg" : "")}
      <div><div class="cname">${esc(c.name)}</div>
      <div><span class="wr ${wrClass(c.winRate)}">${pct(c.winRate)}</span> <span class="games">${num(c.games)} games</span></div></div>
      ${i === 0 ? `<span class="tag">Play this${enemy ? " into " + esc(enemy.name) : ""}</span>` : ""}
    </div>`).join("")}</div>`;
}

// ---------- Idle ----------

function renderHistory(history) {
  if (!history.length) return `<div class="muted">No recent games yet.</div>`;
  return history.map((m) => `
    <div class="match ${m.win ? "win" : ""}">
      <span class="res">${m.win ? "Win" : "Loss"}</span>${icon(m.champion)}
      <div><div class="kda">${m.kills} / ${m.deaths} / ${m.assists}</div><div class="muted" style="font-size:12px">${m.cs} CS</div></div>
      <div class="item-row">${m.items.map((i) => icon(i, "sm")).join("")}</div>
      <div class="meta">${esc(m.queue)}<br>${clock(m.duration)} · ${timeAgo(m.playedAt)}</div>
    </div>`).join("");
}

// ---------- Post game ranks ----------

const RANK_SOURCE = { "other queue": (r) => `${r.queue === "flex" ? "Flex" : "Solo"} rank`, "last season": () => "Last season" };

function rankBadge(r) {
  if (!r) return `<span class="rank unranked">Unranked</span>`;
  const note = RANK_SOURCE[r.source];
  const record = r.wins || r.losses ? `${r.wins}W ${r.losses}L` : "";
  return `<span class="rank tier-${esc(r.tier)}" title="${esc(record)}">${esc(r.label)}</span>${note ? `<span class="rank-note">${esc(note(r))}</span>` : ""}`;
}

function avgLabel(a) {
  return a ? `≈ <b>${esc(a.label)}</b> <span class="muted">(${a.ranked}/${a.players} ranked)</span>` : `<span class="muted">No ranked players</span>`;
}

function renderPostGame(pg) {
  if (!pg || !pg.teams.length) return "";
  const team = (t) => `
    <div class="pg-team ${t.ally ? "ally" : "enemy"}">
      <div class="pg-head"><h2 style="color:var(--${t.ally ? "blue" : "red"})">${t.ally ? "Your team" : "Enemy team"} · ${t.win ? "Win" : "Loss"}</h2>
        <div class="pg-avg">${avgLabel(t.average)}</div></div>
      ${t.players.map((p) => `
        <div class="prow ${p.me ? "me" : ""}">
          <div class="champ">${icon(p.id ? p : null)}</div>
          <div style="min-width:0"><div class="pname">${esc(p.player || p.name)}${p.me ? ' <span class="muted">(you)</span>' : ""}</div>
            <div class="pstats">${esc(p.name || "")} · <span class="pkda">${p.kills}/${p.deaths}/${p.assists}</span> · ${p.cs} CS</div></div>
          <div class="pg-rank">${rankBadge(p.rank)}</div>
        </div>`).join("")}
    </div>`;
  const basis = pg.ranked
    ? `${pg.queue === "flex" ? "Flex" : "Solo/Duo"} ranks.`
    : "Unranked game, so this compares Solo/Duo ranks. Normal matchmaking uses a hidden rating that can differ.";
  const est = pg.mmr, one = pg.lobbyEstimate;
  const mmr = est || one ? `
      <div class="mmr">
        <div><div class="muted mmr-k">ESTIMATED MMR${est?.mode ? ` · ${esc(est.mode)}` : ""}</div>
          <div class="mmr-v">≈ ${esc((est || one).label)}</div></div>
        <div class="muted mmr-basis">${est ? `Average rank of your lobbies over the last ${est.games} game${est.games === 1 ? "" : "s"} in this queue (${est.players} ranked players).` : `From this game's lobby (${one.ranked} ranked players).`}
          ${est && one ? ` This game: ≈ ${esc(one.label)}.` : ""} Riot keeps MMR hidden; matchmaking puts you with players near yours, so this is the usual way to estimate it.</div>
      </div>` : "";
  return `
    <div class="panel fade-in pg">
      ${mmr}
      <div class="pg-title"><h2>Lobby rank</h2><div class="pg-avg big">${avgLabel(pg.average)}</div></div>
      <div class="pg-grid">${pg.teams.map(team).join("")}</div>
      <div class="muted pg-foot">${basis} Players without a rank this season fall back to their other queue or last season.</div>
    </div>`;
}

function idleHero(s) {
  if (!s.agentOnline) return `<div><div class="big">PC agent offline</div><div class="sub">Run <code>leagueassistant agent --server ${esc(location.origin)}</code> on your gaming PC.</div></div>`;
  if (!s.clientConnected) return `<div><div class="big">Waiting for League</div><div class="sub">The agent is running. Open the League client.</div></div>`;
  if (s.phase === "EndOfGame") return `<div><div class="big">GG</div><div class="sub">Queue up again and the next champ select shows up here.</div></div>`;
  const msg = { Lobby: "In lobby", Matchmaking: "Finding a match", ReadyCheck: "Match found!" }[s.phase] || "Ready";
  return `<div><div class="big">${msg}</div><div class="sub">Picks, counters and your build show up here as soon as champ select starts.</div></div>`;
}

function renderIdle(s, banner) {
  if (currentView !== "idle") {
    currentView = "idle";
    app.innerHTML = `
      <div id="banner"></div>
      <div id="postgame"></div>
      <div class="hero" id="hero"></div>
      <div class="grid idle-grid">
        <div class="panel"><h2>Recent games</h2><div id="mmr-summary"></div><div id="history"></div></div>
        <div class="panel"><h2>Look up a champion</h2>
          <form class="lookup-form" id="lookup">
            <input id="lk-champ" list="champ-list" placeholder="Champion, e.g. Ahri" autocomplete="off" required>
            <select id="lk-role"><option value="">Main role</option><option value="top">Top</option><option value="jungle">Jungle</option><option value="mid">Mid</option><option value="adc">Bot</option><option value="support">Support</option><option value="aram">ARAM</option></select>
            <button>Search</button>
          </form>
          <datalist id="champ-list"></datalist>
          <div id="lookup-out" class="grid"></div>
        </div>
      </div>`;
    setupLookup();
  }
  document.getElementById("banner").innerHTML = banner || "";
  // Only re-render when the game changes so the fade-in doesn't replay on every update.
  const pgEl = document.getElementById("postgame");
  const pgKey = s.postGame ? JSON.stringify(s.postGame) : "";
  if (pgEl.dataset.key !== pgKey) {
    pgEl.dataset.key = pgKey;
    pgEl.innerHTML = renderPostGame(s.postGame);
  }
  document.getElementById("hero").innerHTML = idleHero(s);
  document.getElementById("history").innerHTML = renderHistory(s.history || []);
  document.getElementById("mmr-summary").innerHTML = s.mmr?.length
    ? `<div class="chips" title="Estimated from the average rank of your recent lobbies">${s.mmr.map((m) => `<span class="chip warn">${esc(m.mode || "Unknown queue")} MMR ≈ ${esc(m.label)} · ${m.games} game${m.games === 1 ? "" : "s"}</span>`).join("")}</div>`
    : "";
}

let champList = null;
async function setupLookup() {
  if (!champList) {
    try { champList = await (await fetch("/api/champions")).json(); } catch { champList = []; }
  }
  document.getElementById("champ-list").innerHTML = champList.map((c) => `<option value="${esc(c.name)}">`).join("");
  document.getElementById("lookup").addEventListener("submit", async (e) => {
    e.preventDefault();
    const champ = document.getElementById("lk-champ").value.trim();
    const role = document.getElementById("lk-role").value;
    const out = document.getElementById("lookup-out");
    out.innerHTML = `<div class="center-msg" style="padding:30px 0"><div class="spinner"></div></div>`;
    const q = role ? `?role=${role}` : "";
    try {
      const [b, c] = await Promise.all([
        fetch(`/api/build/${encodeURIComponent(champ)}${q}`).then((r) => r.ok ? r.json() : Promise.reject(r)),
        fetch(`/api/counters/${encodeURIComponent(champ)}${q}`).then((r) => r.ok ? r.json() : Promise.reject(r)),
      ]);
      out.innerHTML = renderBuild(b) + (c.role === "aram" ? "" : `<div class="panel"><h2>Best picks into ${esc(c.champion.name)} ${esc(roleLabel(c.role))}</h2>${renderCounters(c.counters)}</div>`);
    } catch (err) {
      out.innerHTML = `<div class="muted">Couldn't find stats for "${esc(champ)}".</div>`;
    }
  });
}

// ---------- Champ select ----------

function renderTimer(cs) {
  return `<div class="timer"><span class="label">${esc({ BAN_PICK: "Pick & ban", PLANNING: "Declare intent", FINALIZATION: "Finalize" }[cs.timer.phase] || "Champ select")}</span>
      <div class="bar"><div class="fill" id="timer-fill"></div></div><span class="secs" id="timer-secs"></span></div>`;
}

const SOURCE_LABEL = { you: "You", teammate: "Teammate", bench: "Bench" };

function renderNoLanesChampSelect(s) {
  // ARAM and the other modes without lanes.
  currentView = "champselect";
  const cs = s.champSelect;
  const me = cs.me;
  const opts = cs.aramOptions || [];
  const mine = opts.find((o) => o.source === "you");
  const best = opts.find((o) => o.source === "bench");
  const team = cs.team.map((p) => `
    <div class="slot ${p.me ? "me" : ""}">${icon(p.id ? p : null)}
      <div><div class="name">${p.id ? esc(p.name) : '<span class="muted">Picking…</span>'}${p.me ? ' <span class="muted">(you)</span>' : ""}</div></div>
    </div>`).join("");
  const options = opts.map((o) => `
    <div class="aram-opt ${o.source}">${icon(o)}
      <div style="min-width:0"><div class="cname">${esc(o.name)} <span class="src">${SOURCE_LABEL[o.source]}</span></div>
        <div class="track"><div class="fillbar" style="width:${Math.max(0, Math.min(100, ((o.winRate - 0.4) / 0.2) * 100))}%"></div></div></div>
      <div class="wrcol"><span class="wr ${wrClass(o.winRate)}">${pct(o.winRate)}</span><span class="games">${num(o.games)} games</span></div>
    </div>`).join("");
  const swap = best && mine && best.source === "bench" && best.winRate - mine.winRate >= 0.01
    ? `<div class="swap-tip">${icon(best, "lg")}<div><div class="muted" style="font-size:12px">BEST ON THE BENCH</div>
        <div class="n">${esc(best.name)} <span class="wr ${wrClass(best.winRate)}">${pct(best.winRate)}</span></div>
        <div class="muted">${pct(best.winRate - mine.winRate)} better than ${esc(mine.name)} in ARAM. Grab it before a teammate does.</div></div></div>` : "";
  const hero = `
    <div class="versus aram">
      <div class="vs-side" style="${me ? `background-image:url('${splash(me)}')` : ""}">
        <div class="role">${esc(cs.mode.label)}</div>
        <div class="who">${me ? esc(me.name) : "Waiting for your champion"}</div>
        ${mine ? `<div><span class="wr ${wrClass(mine.winRate)}">${pct(mine.winRate)}</span> ARAM win rate · ${num(mine.games)} games</div>` : ""}
      </div>
    </div>`;
  app.innerHTML = `
    ${renderTimer(cs)}
    <div class="grid cs-grid">
      <div class="panel"><h2>Your team</h2>${team}</div>
      <div class="grid cs-center">
        ${hero}
        ${swap}
        ${opts.length ? `<div class="panel"><h2>ARAM win rates · your team and bench</h2>${options}</div>` : ""}
        ${cs.errors.map((e) => `<div class="panel muted">${esc(e)}</div>`).join("")}
      </div>
      <div class="panel"><h2>Bench</h2>${cs.bench?.length ? `<div class="bench">${cs.bench.map((b) => icon(b)).join("")}</div>` : '<div class="muted">Empty. Rerolled champions land here.</div>'}</div>
    </div>
    <div style="margin-top:16px">${cs.build ? renderBuild(cs.build) : ""}</div>`;
  tickTimer();
}

function renderChampSelect(s) {
  if (s.champSelect.mode && s.champSelect.mode.kind !== "sr") return renderNoLanesChampSelect(s);
  currentView = "champselect";
  const cs = s.champSelect;
  const lane = cs.laneOpponent;
  const team = cs.team.map((p) => `
    <div class="slot ${p.me ? "me" : ""} ${p.id && !p.locked ? "hover" : ""}">
      ${icon(p.id ? p : null)}
      <div><div class="name">${p.id ? esc(p.name) : '<span class="muted">Picking…</span>'}${p.me ? ' <span class="muted">(you)</span>' : ""}</div>
      <div class="role">${roleLabel(p.role)}</div></div>
    </div>`).join("");
  const enemies = cs.enemies.map((p) => `
    <div class="slot ${lane && p.id === lane.id ? "lane" : ""}">${icon(p)}
      <div><div class="name">${esc(p.name)}</div><div class="role">${p.role ? roleLabel(p.role) + "?" : ""}</div></div>
    </div>`).join("") || `<div class="muted">No enemy picks yet.</div>`;

  const me = cs.me;
  const versus = `
    <div class="versus">
      <div class="vs-side" style="${me ? `background-image:url('${splash(me)}')` : ""}">
        <div class="role">${roleLabel(cs.myRole) || "You"}</div>
        <div class="who">${me ? esc(me.name) : "Pick a champion"}</div>
        ${me && !cs.locked ? '<div class="muted">Hovering</div>' : ""}
      </div>
      <div class="vs-mid">VS</div>
      <div class="vs-side right" style="${lane ? `background-image:url('${splash(lane)}')` : ""}">
        <div class="role">Lane opponent</div>
        <div class="who">${lane ? esc(lane.name) : "Unknown"}</div>
      </div>
    </div>
    ${cs.matchup ? `<div class="wrbar"><div class="track"><div class="fillbar" style="width:${cs.matchup.winRate * 100}%"></div></div>
      <div class="legend"><span><span class="wr ${wrClass(cs.matchup.winRate)}">${pct(cs.matchup.winRate)}</span> for ${esc(me.name)}</span><span>${num(cs.matchup.games)} games</span></div></div>` : ""}`;

  app.innerHTML = `
    ${renderTimer(cs)}
    <div class="grid cs-grid">
      <div class="panel"><h2>Your team</h2>${team}
        ${cs.bans.length ? `<h3 style="margin-top:14px">Bans</h3><div class="bans">${cs.bans.map((b) => icon(b, "sm")).join("")}</div>` : ""}</div>
      <div class="grid cs-center">
        ${versus}
        ${lane ? `<div class="panel"><h2>Counter picks you own</h2>${renderCounters(cs.counters, lane)}</div>` : ""}
        ${cs.errors.map((e) => `<div class="muted">${esc(e)}</div>`).join("")}
      </div>
      <div class="panel"><h2>Enemy team</h2>${enemies}</div>
    </div>
    <div style="margin-top:16px">${cs.build ? renderBuild(cs.build) : ""}</div>`;
  tickTimer();
}

function tickTimer() {
  const cs = state?.champSelect;
  const fill = document.getElementById("timer-fill");
  const secsEl = document.getElementById("timer-secs");
  if (!cs || !fill) return;
  const left = Math.max(0, cs.timer.endsAt - Date.now());
  timerMax[cs.timer.phase] = Math.max(timerMax[cs.timer.phase] || 0, left);
  const max = timerMax[cs.timer.phase] || 1;
  fill.style.width = `${(left / max) * 100}%`;
  secsEl.textContent = Math.ceil(left / 1000);
}

// ---------- In game ----------

function playerRow(p, lane) {
  return `
    <div class="prow ${p.me ? "me" : ""} ${lane && p === lane ? "lane" : ""} ${p.dead ? "dead" : ""}">
      <div class="champ">${icon(p)}<span class="lvl">${p.level}</span>${p.dead && p.respawn ? `<span class="respawn">${p.respawn}</span>` : ""}</div>
      <div style="min-width:0"><div class="pname">${esc(p.name)} <span class="role">${roleLabel(p.role)}</span></div>
        <div class="pstats"><span class="pkda">${p.kills}/${p.deaths}/${p.assists}</span> · ${p.cs} CS · ${esc(p.player || "")}</div></div>
      <div class="items">${p.items.map((i) => icon(i)).join("")}</div>
    </div>`;
}

function renderObjectives(g) {
  const o = g.objectives;
  // Enemies who are dead, and how long until the first is back: the window to take something.
  const down = g.enemies.filter((e) => e.dead && e.respawn > 0);
  const strip = down.length ? `
    <div class="down"><span class="down-n">${down.length} enem${down.length === 1 ? "y" : "ies"} dead</span>
      ${down.sort((a, b) => a.respawn - b.respawn).map((e) => `<span class="down-p">${icon(e, "sm")}<span data-at="${g.gameTime + e.respawn}">${e.respawn}s</span></span>`).join("")}
      <span class="muted">window <b data-at="${g.gameTime + Math.min(...down.map((e) => e.respawn))}">${Math.min(...down.map((e) => e.respawn))}s</b></span></div>` : "";
  if (!o) return strip ? `<div class="objectives">${strip}</div>` : "";
  const drakes = (list) => list.length ? list.map((d) => `<span class="drake ${d.toLowerCase()}">${esc(d)}</span>`).join("") : '<span class="muted">none</span>';
  const soul = o.soul ? `<span class="chip ${o.soul === "ally" ? "warn" : "bad"}">${o.soul === "ally" ? "Your" : "Their"} soul</span>`
    : o.soulPoint ? `<span class="chip ${o.soulPoint === "ally" ? "warn" : "bad"}">${o.soulPoint === "ally" ? "You're" : "They're"} on soul point</span>` : "";
  const timers = o.timers.map((t) => `<span class="timer-chip ${t.side || ""}">${esc(t.label)} <b data-at="${t.at}" data-up="up">${t.at > g.gameTime ? clock(t.at - g.gameTime) : "up"}</b></span>`).join("");
  return `
    <div class="objectives">
      <div class="timers">${timers}</div>
      <div class="drakes"><span class="muted">Your drakes</span> ${drakes(o.dragons.ally)} <span class="muted" style="margin-left:10px">Theirs</span> ${drakes(o.dragons.enemy)} ${soul}</div>
      ${strip}
    </div>`;
}

function tickCountdowns() {
  if (!liveClock.at) return;
  const now = liveClock.gameTime + (Date.now() - liveClock.at) / 1000;
  document.querySelectorAll?.("[data-at]").forEach((el) => {
    const left = Number(el.dataset.at) - now;
    if (el.dataset.up) el.textContent = left > 0 ? clock(left) : "up";
    else el.textContent = `${Math.max(0, Math.ceil(left))}s`;
  });
}

function renderGame(s) {
  currentView = "game";
  const g = s.live;
  liveClock = { gameTime: g.gameTime, at: Date.now() };
  const a = g.score.ally, e = g.score.enemy;
  const diff = a.itemGold - e.itemGold;
  const total = Math.max(a.itemGold + e.itemGold, 1);
  const leadPct = Math.min(50, (Math.abs(diff) / total) * 100 * 2.5);
  const objs = (t) => `<div class="objs"><span><b>${t.towers}</b> towers</span><span><b>${t.dragons}</b> drakes</span><span><b>${t.barons}</b> barons</span></div>`;

  let side = "";
  if (g.buildProgress && g.build) {
    const adv = g.advice;
    const next = adv?.next || g.buildProgress.find((i) => !i.owned) ||
      (g.build.later.flat().find((o) => !g.me.items.some((i) => i.id === o.id)));
    const urgent = adv?.next && adv.next.priority >= 2;
    const nextCard = next ? `
      <div class="next-item ${urgent ? "urgent" : ""}">${icon(next, "lg")}
        <div style="min-width:0"><div class="muted" style="font-size:12px">BUY NEXT${next.cost ? ` · ${num(next.cost)} gold` : ""}</div>
          <div class="n">${esc(next.name)}</div>
          ${next.reason ? `<div class="why">${esc(next.reason)}</div>` : ""}
          ${adv?.affordable ? `<div class="afford">${icon(adv.affordable, "sm")}<span>You have ${num(g.gold)} gold: buy <b>${esc(adv.affordable.name)}</b> (${num(adv.affordable.cost)}) now.</span></div>` : ""}
        </div></div>` : "";
    const chips = adv?.threats?.length ? `<div class="chips">${adv.threats.map((c) => `<span class="chip ${c.level}">${esc(c.label)}</span>`).join("")}</div>` : "";
    const extra = [...(adv?.situational || []), ...(adv?.boots && adv.boots.id !== next?.id ? [adv.boots] : [])];
    const situational = extra.length ? `<h3>For this game</h3>${extra.map((p) => `
      <div class="sit">${icon(p, "sm")}<div style="min-width:0"><div class="sname">${esc(p.name)}</div><div class="why">${esc(p.reason)}</div></div></div>`).join("")}` : "";
    const startCard = adv?.start ? `
      <div class="next-item start"><div class="item-row">${adv.start.items.map((i) => icon(i, "lg")).join("")}</div>
        <div style="min-width:0"><div class="muted" style="font-size:12px">START WITH · ${num(adv.start.cost)} gold</div>
          <div class="n">${adv.start.items.map((i) => esc(i.name)).join(" + ")}</div>
          <div class="why">${esc(adv.start.reason)}</div></div></div>` : "";
    side = `
      <div class="panel gold"><h2>Your build · ${esc(g.build.champion.name)}${g.build.role === "aram" ? " · ARAM" : ""}</h2>
        ${chips}
        ${startCard}
        ${startCard ? nextCard.replace("BUY NEXT", "THEN") : nextCard}
        ${situational}
        ${g.build.starting?.length ? `<h3>Starting items</h3><div class="item-row" style="margin:6px 0 12px">${g.build.starting.map((i) => icon(i)).join("")}</div>` : ""}
        <h3>Core</h3><div class="item-row" style="margin:6px 0 12px">${g.buildProgress.map((i) => `<span class="owned ${i.owned ? "have" : ""}">${icon(i)}</span>`).join('<span class="arrow">›</span>')}</div>
        <h3>Later options</h3>${g.build.later.map((slot, i) => `<div class="item-row" style="margin-top:6px"><span class="slot-label">${i + 4}</span>${slot.map((o) => `<div class="opt">${icon(o, "sm")}<span class="p">${pct(o.winRate)}</span></div>`).join("")}</div>`).join("")}
        <h3 style="margin-top:12px">Skills</h3><div class="prio" style="margin-top:6px">${g.build.skills.priority.map((k) => `<span class="key">${k}</span>`).join('<span class="arrow">›</span>')}</div>
      </div>`;
  }
  if (!g.build && g.mode?.kind === "other") {
    side += `<div class="panel"><h2>${esc(g.mode.label)}</h2><div class="muted">U.gg has no builds for this mode, so there's no build to follow. The scoreboard still updates.</div></div>`;
  }
  if (g.laneOpponent) {
    const l = g.laneOpponent;
    side += `<div class="panel"><h2>Lane opponent</h2>
      <div class="build-head" style="margin-bottom:8px">${icon(l, "lg")}<div><div class="title">${esc(l.name)}</div>
      <div class="muted">Level ${l.level} · ${l.kills}/${l.deaths}/${l.assists} · ${l.cs} CS</div></div></div>
      <div class="item-row">${l.items.map((i) => icon(i)).join("") || '<span class="muted">No items yet</span>'}</div></div>`;
  }

  app.innerHTML = `
    <div class="panel gold">
      <div class="scoreboard">
        <div class="score-side ally"><span class="kills">${a.kills}</span>${objs(a)}</div>
        <div class="clock"><div class="t" id="game-clock">${clock(g.gameTime)}</div><div class="muted">${num(s.live.gold)} gold in pocket</div></div>
        <div class="score-side enemy">${objs(e)}<span class="kills">${e.kills}</span></div>
      </div>
      <div class="goldbar"><div class="track">
        <div class="lead" style="${diff >= 0 ? `right:50%;width:${leadPct}%;background:var(--blue)` : `left:50%;width:${leadPct}%;background:var(--red)`}"></div><div class="mid"></div></div>
        <div class="legend">${diff === 0 ? "Even on item gold" : `${diff > 0 ? "Your team" : "Enemy team"} +${num(Math.abs(diff))} item gold`}</div></div>
      ${renderObjectives(g)}
    </div>
    <div class="grid game-grid">
      <div class="panel"><h2 style="color:var(--blue)">Your team</h2>${g.allies.map((p) => playerRow(p, null)).join("")}</div>
      <div class="panel"><h2 style="color:var(--red)">Enemy team</h2>${g.enemies.map((p) => playerRow(p, g.laneOpponent && g.enemies.find((x) => x.id === g.laneOpponent.id))).join("")}</div>
      <div class="grid side">${side}</div>
    </div>`;
}

// ---------- Router ----------

function render(s) {
  state = s;
  renderStatus(s);
  if (s.phase === "ChampSelect" && s.champSelect) return renderChampSelect(s);
  if (s.live) return renderGame(s);
  if (["InProgress", "GameStart", "Reconnect"].includes(s.phase)) {
    currentView = "loading";
    app.innerHTML = `<div class="center-msg"><div class="spinner"></div>Loading into the game…</div>`;
    return;
  }
  let banner = "";
  if (s.phase === "EndOfGame" && s.history?.length) {
    const m = s.history[0];
    banner = `<div class="result-banner ${m.win ? "win" : "loss"} fade-in"><div class="r">${m.win ? "Victory" : "Defeat"}</div>
      <div class="muted" style="margin-top:8px">${esc(m.champion.name)} · ${m.kills}/${m.deaths}/${m.assists} · ${m.cs} CS · ${clock(m.duration)}</div></div>`;
  }
  renderIdle(s, banner);
}

setInterval(() => {
  tickTimer();
  const el = document.getElementById("game-clock");
  if (el && liveClock.at) el.textContent = clock(liveClock.gameTime + (Date.now() - liveClock.at) / 1000);
  tickCountdowns();
}, 250);

function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = (e) => render(JSON.parse(e.data));
  es.onerror = () => {
    statusEl.innerHTML = `<span class="pill"><span class="dot off"></span>Server disconnected, retrying…</span>`;
  };
}
connect();
