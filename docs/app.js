// AFL Fantasy 2026 — League Projector
// Static SPA: loads docs/data/players.json + (optional) docs/data/rosters.json,
// projects per-player and per-team scores, supports browsing, league ladder,
// and per-team drilldowns. Phosphor/CRT theme.

const STORAGE_KEY = "aflFantasy.v1";
const POS_ORDER = ["DEF", "MID", "RUC", "FWD"];

const state = {
  players: [],
  playersById: new Map(),
  playersByName: new Map(),
  rosters: null,
  model: "blend",
  excluded: new Set(),
  tab: "ladder",
  filter: { search: "", pos: "", team: "", only2025: false },
  sort: { key: "proj", dir: "desc" },
};

// ---------- Persistence ----------
function loadPrefs() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const obj = JSON.parse(raw);
    if (obj.model) state.model = obj.model;
    if (Array.isArray(obj.excluded)) state.excluded = new Set(obj.excluded);
    if (obj.tab) state.tab = obj.tab;
  } catch (e) { /* ignore */ }
}
function savePrefs() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    model: state.model,
    excluded: [...state.excluded],
    tab: state.tab,
  }));
}

// ---------- Data loading ----------
async function loadData() {
  const statusEl = document.getElementById("data-status");
  try {
    const pr = await fetch("data/players.json", { cache: "no-store" });
    if (!pr.ok) throw new Error(`players.json ${pr.status}`);
    const pdata = await pr.json();
    state.players = pdata.players || [];
    for (const p of state.players) {
      state.playersById.set(p.id, p);
      state.playersByName.set(nameKey(p.name, p.team), p);
    }
    statusEl.classList.add("ok");
  } catch (e) {
    statusEl.textContent = "players.json missing — run scripts/parse_stats.py";
    statusEl.classList.add("bad");
    return;
  }

  try {
    const rr = await fetch("data/rosters.json", { cache: "no-store" });
    if (rr.ok) {
      state.rosters = await rr.json();
      resolveRosterPlayers();
    }
  } catch (e) { /* rosters optional */ }

  updateStatusLine();
  updateTagline();
}

function updateStatusLine() {
  const statusEl = document.getElementById("data-status");
  const parts = [`${state.players.length} players`];
  if (state.rosters?.teams) parts.push(`${state.rosters.teams.length} teams`);
  if (state.rosters?.round) parts.push(`R${state.rosters.round}`);
  parts.push(state.model.toUpperCase());
  statusEl.textContent = parts.join(" · ");
}

function updateTagline() {
  const el = document.getElementById("league-tagline");
  if (!el) return;
  if (state.rosters?.league_name) {
    el.textContent = state.rosters.league_name;
  }
}

function nameKey(name, team) {
  return `${(name || "").trim().toLowerCase()}|${(team || "").toUpperCase()}`;
}

function resolveRosterPlayers() {
  if (!state.rosters?.teams) return;
  for (const team of state.rosters.teams) {
    for (const entry of team.roster || []) {
      let player = null;
      if (entry.cd_id) player = state.playersById.get(entry.cd_id);
      if (!player) player = state.playersByName.get(nameKey(entry.name, entry.team_afl));
      entry.player = player || null;
      entry.unmatched = !player;
    }
  }
}

// ---------- Projection ----------
// All inputs use Cookie Cup scoring (standard AFL Fantasy Classic), so the
// weekly CSV's L5/L3/avgPts and keeperfantasy's projAvg are directly comparable.
function project(p, mode = state.model) {
  if (!p) return null;
  if (mode === "kf") return firstNum(p.proj_avg, p.l5, p.avg_pts);
  if (mode === "l5") return firstNum(p.l5, p.l3, p.avg_pts, p.proj_avg);
  if (mode === "season") return firstNum(p.avg_pts, p.l5, p.proj_avg);
  // blend (default): recent form + season base rate + KF's own projection
  const signals = [];
  if (p.l5 != null) signals.push([p.l5, 0.35]);
  if (p.l3 != null) signals.push([p.l3, 0.15]);
  if (p.avg_pts != null) signals.push([p.avg_pts, 0.30]);
  if (p.proj_avg != null) signals.push([p.proj_avg, 0.20]);
  if (signals.length === 0) return null;
  const totalW = signals.reduce((s, [, w]) => s + w, 0);
  return signals.reduce((s, [v, w]) => s + v * w, 0) / totalW;
}
function firstNum(...vals) {
  for (const v of vals) if (v != null) return v;
  return null;
}

function isAvailable(entry) {
  if (!entry.player) return false;
  if (state.excluded.has(entry.player.id)) return false;
  if (entry.injured) return false;
  if (entry.playing_status != null && entry.playing_status !== 1) return false;
  return true;
}

function teamProjection(team) {
  if (!team) return { total: 0, captainId: null, captainProj: -1, items: [], hasLineup: false };
  const allRoster = team.roster || [];
  const hasLineup = allRoster.some(e => e.starting === true);
  const eligible = allRoster.filter(isAvailable);
  const scoring = hasLineup ? eligible.filter(e => e.starting) : eligible;
  let total = 0;
  let capPid = null, capProj = -1;
  const items = [];
  for (const e of scoring) {
    const proj = project(e.player);
    if (proj == null) continue;
    total += proj;
    if (proj > capProj) { capProj = proj; capPid = e.player.id; }
    items.push({ entry: e, proj });
  }
  if (capPid && state.rosters?.has_captains) total += capProj;
  return { total, captainId: capPid, captainProj: capProj, items, hasLineup };
}

function expectedStarters(team) {
  // From formation: sum of starters[*].limit. Falls back to count on roster.
  const formation = team?.formation?.starters;
  if (formation?.length) return formation.reduce((s, x) => s + (x.limit || 0), 0);
  return (team?.roster || []).filter(e => e.starting).length;
}

// ---------- Tabs ----------
function setTab(tab) {
  state.tab = tab;
  savePrefs();
  for (const btn of document.querySelectorAll(".tab")) {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.classList.toggle("hidden", panel.id !== `tab-${tab}`);
  }
  if (tab === "ladder") renderLadder();
  if (tab === "matchups") renderMatchups();
  if (tab === "browse") renderBrowse();
}

// ---------- Ladder ----------
function renderLadder() {
  const empty = document.getElementById("ladder-empty");
  const content = document.getElementById("ladder-content");
  const metaEl = document.getElementById("ladder-meta");

  if (!state.rosters?.teams?.length) {
    empty.classList.remove("hidden");
    content.innerHTML = "";
    metaEl.textContent = "—";
    return;
  }
  empty.classList.add("hidden");

  const rows = state.rosters.teams.map(team => ({ team, proj: teamProjection(team) }));
  rows.sort((a, b) => b.proj.total - a.proj.total);

  const roundLabel = state.rosters.round_name
    || (state.rosters.round != null ? `Round ${state.rosters.round}` : "Next round");
  metaEl.textContent = `${roundLabel} · ${state.model.toUpperCase()} model`;

  let html = `<div class="table-wrap"><table class="leaderboard">
    <thead><tr>
      <th class="rank">#</th>
      <th>Team</th>
      <th>Starters</th>
      ${state.rosters.has_captains ? '<th>Captain</th>' : ''}
      <th class="num">Projection</th>
    </tr></thead><tbody>`;

  rows.forEach(({ team, proj }, i) => {
    const expected = expectedStarters(team);
    const available = proj.items.length;
    const captain = proj.captainId ? state.playersById.get(proj.captainId) : null;
    let chipCls = "avail-chip";
    if (available < expected) chipCls += " partial";
    if (available <= expected - 3) chipCls += " bad";
    const captainCell = state.rosters.has_captains
      ? `<td><span class="player-name" style="font-size:0.95rem">${captain ? escape(captain.name) : "—"}</span></td>`
      : "";
    const rankCls = i === 0 ? "rank rank-1" : (i < 3 ? "rank rank-2" : "rank");
    const rowCls = i === 0 ? "rank-1-row" : "";
    const meta = proj.hasLineup ? "lineup locked" : "no lineup";
    html += `<tr class="${rowCls}" data-team-id="${escape(team.id)}">
      <td class="${rankCls}">${i + 1}</td>
      <td><span class="team-name-cell">${escape(team.name || team.id)}<span class="team-meta">${meta}</span></span></td>
      <td><span class="${chipCls}">${available}<span class="muted"> / ${expected}</span></span></td>
      ${captainCell}
      <td class="num proj-cell">${fmtNum(proj.total, 0)}</td>
    </tr>`;
  });
  html += `</tbody></table></div>`;
  content.innerHTML = html;

  for (const tr of content.querySelectorAll("tr[data-team-id]")) {
    tr.addEventListener("click", () => openTeamDrawer(tr.dataset.teamId));
  }
}

// ---------- Matchups (kept minimal until league fixtures are scraped) ----------
function renderMatchups() {
  const empty = document.getElementById("matchups-empty");
  const content = document.getElementById("matchups-content");
  const metaEl = document.getElementById("matchups-meta");
  const fixtures = state.rosters?.fixtures || [];
  if (!state.rosters?.teams?.length || !fixtures.length) {
    empty.classList.remove("hidden");
    content.innerHTML = "";
    metaEl.textContent = "—";
    return;
  }
  empty.classList.add("hidden");
  metaEl.textContent = `Round ${state.rosters.round ?? "?"}`;
  // (kept for later — fixtures aren't scraped yet)
  content.innerHTML = "";
}

// ---------- Browse ----------
function renderBrowse() {
  const tbody = document.getElementById("players-rows");
  const teamSel = document.getElementById("team-filter");
  const metaEl = document.getElementById("browse-meta");
  if (teamSel.options.length <= 1) {
    const teams = [...new Set(state.players.map(p => p.team).filter(Boolean))].sort();
    for (const t of teams) {
      const opt = document.createElement("option");
      opt.value = t; opt.textContent = t;
      teamSel.appendChild(opt);
    }
  }

  const f = state.filter;
  const q = f.search.trim().toLowerCase();
  let rows = state.players.filter(p => {
    if (q && !p.name.toLowerCase().includes(q) && !(p.team || "").toLowerCase().includes(q)) return false;
    if (f.pos && !p.positions.includes(f.pos)) return false;
    if (f.team && p.team !== f.team) return false;
    if (f.only2025 && p.afl_2025?.fp == null) return false;
    return true;
  });

  const { key, dir } = state.sort;
  const mult = dir === "asc" ? 1 : -1;
  rows.sort((a, b) => mult * cmp(sortVal(a, key), sortVal(b, key)));

  document.getElementById("result-count").textContent = `${rows.length} / ${state.players.length}`;
  metaEl.textContent = `${state.players.length} catalogued · ${state.model.toUpperCase()} model`;

  for (const th of document.querySelectorAll(".players-table th[data-sort]")) {
    th.classList.remove("sorted-asc", "sorted-desc");
    if (th.dataset.sort === key) th.classList.add(dir === "asc" ? "sorted-asc" : "sorted-desc");
  }

  const frag = document.createDocumentFragment();
  for (const p of rows.slice(0, 500)) {
    const proj = project(p);
    const tr = document.createElement("tr");
    if (state.excluded.has(p.id)) tr.classList.add("excluded");
    tr.innerHTML = `
      <td><span class="player-name">${escape(p.name)}</span></td>
      <td><span class="team-badge">${escape(p.team || "")}</span></td>
      <td>${p.positions.map(pos => `<span class="pos-tag">${pos}</span>`).join("")}</td>
      <td class="muted">${escape(p.owner || "")}</td>
      <td class="num">${fmtNum(proj)}</td>
      <td class="num">${fmtNum(p.l5)}</td>
      <td class="num">${fmtNum(p.l3)}</td>
      <td class="num">${fmtNum(p.avg_pts)}</td>
      <td class="num">${fmtNum(p.proj_avg)}</td>
      <td class="num">${fmtNum(p.tog_pct, 0)}%</td>
    `;
    frag.appendChild(tr);
  }
  tbody.replaceChildren(frag);
}

function sortVal(p, key) {
  switch (key) {
    case "name": return p.name?.toLowerCase() || "";
    case "team": return p.team || "";
    case "positions": return (p.positions[0] || "");
    case "owner": return (p.owner || "").toLowerCase();
    case "proj": return project(p) ?? -1;
    case "l5": return p.l5 ?? -1;
    case "l3": return p.l3 ?? -1;
    case "avg_pts": return p.avg_pts ?? -1;
    case "proj_avg": return p.proj_avg ?? -1;
    case "tog_pct": return p.tog_pct ?? -1;
    default: return 0;
  }
}
function cmp(a, b) {
  if (a === b) return 0;
  if (typeof a === "string" || typeof b === "string") {
    return String(a).localeCompare(String(b));
  }
  return a < b ? -1 : 1;
}

// ---------- Drawer: team detail ----------
function openTeamDrawer(teamId) {
  const team = state.rosters?.teams.find(t => t.id === teamId);
  if (!team) return;
  const drawer = document.getElementById("team-drawer");
  const title = document.getElementById("drawer-title");
  const eyebrow = document.getElementById("drawer-eyebrow");
  const content = document.getElementById("drawer-content");
  const proj = teamProjection(team);
  title.textContent = team.name || team.id;
  eyebrow.textContent = `${state.rosters.league_name || "League"} · ${state.rosters.round_name || ("Round " + (state.rosters.round ?? "?"))}`;

  // Bucket by lineup role
  const buckets = { starter: [], emg: [], bench: [] };
  for (const e of team.roster || []) {
    (buckets[e.type] || buckets.bench).push(e);
  }

  const startersExpected = expectedStarters(team);
  const startersAvailable = proj.items.length;
  const captain = proj.captainId ? state.playersById.get(proj.captainId) : null;

  let html = `<section class="drawer-summary">
    <div class="stat">
      <div class="stat-label">Projection</div>
      <div class="stat-value accent">${fmtNum(proj.total, 0)}</div>
      <div class="stat-sub">${state.model.toUpperCase()} blend</div>
    </div>
    <div class="stat">
      <div class="stat-label">Starters available</div>
      <div class="stat-value">${startersAvailable}<span style="font-family:var(--mono);font-size:1.1rem;color:var(--muted);"> / ${startersExpected}</span></div>
      <div class="stat-sub">${Math.max(0, startersExpected - startersAvailable)} out / excluded</div>
    </div>`;
  if (state.rosters?.has_captains && captain) {
    html += `<div class="stat">
      <div class="stat-label">Captain</div>
      <div class="stat-value" style="font-size:1.15rem;">${escape(captain.name)}</div>
      <div class="stat-sub">+${fmtNum(proj.captainProj)} bonus</div>
    </div>`;
  }
  html += `</section>`;

  html += renderLineupSection("Starters", buckets.starter, "starter");
  if (buckets.emg.length) html += renderLineupSection("Emergencies", buckets.emg, "emg");
  if (buckets.bench.length) html += renderLineupSection("Bench", buckets.bench, "bench");

  content.innerHTML = html;
  drawer.classList.remove("hidden");

  for (const btn of content.querySelectorAll(".exclude-btn[data-id]")) {
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      if (state.excluded.has(id)) state.excluded.delete(id);
      else state.excluded.add(id);
      savePrefs();
      openTeamDrawer(teamId);
      if (state.tab === "ladder") renderLadder();
      if (state.tab === "browse") renderBrowse();
    });
  }
}

function renderLineupSection(label, entries, role) {
  // Group by lineup position. Starters/emgs use selected_pos; bench uses
  // the player's first eligible position (since selected_pos === "BN").
  const groups = {};
  for (const e of entries) {
    const pos = (role === "bench" || e.selected_pos === "BN")
      ? (e.positions?.[0] || "OTHER")
      : e.selected_pos;
    (groups[pos] = groups[pos] || []).push(e);
  }

  let subtotal = 0;
  for (const e of entries) {
    if (role === "starter" && isAvailable(e)) {
      const v = project(e.player);
      if (v != null) subtotal += v;
    }
  }

  const POS_PLUS = [...POS_ORDER, "OTHER"];
  let html = `<section class="lineup-section">
    <header class="lineup-section-head">
      <span class="label">${escape(label)}</span>
      <span class="count">${entries.length} player${entries.length === 1 ? "" : "s"}</span>
      ${role === "starter" ? `<span class="subtotal">subtotal ${fmtNum(subtotal, 0)}</span>` : ""}
    </header>`;

  for (const pos of POS_PLUS) {
    const players = groups[pos];
    if (!players?.length) continue;
    html += `<div class="pos-row">
      <span class="pos-label">${pos}</span>
      <span class="pos-bar"></span>
      <span class="muted">${players.length}</span>
    </div>`;
    html += `<table class="roster-table"><tbody>`;
    for (const e of players) html += renderPlayerRow(e);
    html += `</tbody></table>`;
  }
  html += `</section>`;
  return html;
}

function renderPlayerRow(e) {
  const p = e.player;
  const isExcl = p && state.excluded.has(p.id);
  const isAuto = p && !isExcl && !isAvailable(e);
  const projV = p ? project(p) : null;
  const tags = [];
  if (e.injured) tags.push(`<span class="injury-tag out" title="${escape(e.injury_desc || "Injured")}">${escape((e.injury_desc || "INJ").toUpperCase())}</span>`);
  else if (e.playing_status != null && e.playing_status !== 1) tags.push('<span class="injury-tag out">OUT</span>');
  const rowCls = [];
  if (isExcl) rowCls.push("excluded");
  if (isAuto) rowCls.push("auto-excluded");
  const label = p
    ? `<span class="player-name">${escape(p.name)}</span>${tags.length ? " " + tags.join(" ") : ""}`
    : `<span class="muted">${escape(e.name || "?")} (unmatched)</span>`;
  return `<tr class="${rowCls.join(" ")}">
    <td>${label}</td>
    <td>${p ? `<span class="team-badge">${escape(p.team || "")}</span>` : ""}</td>
    <td class="num">${fmtNum(p?.l5)}</td>
    <td class="num">${fmtNum(p?.l3)}</td>
    <td class="num">${fmtNum(p?.avg_pts)}</td>
    <td class="num">${fmtNum(p?.proj_avg)}</td>
    <td class="num">${fmtNum(projV)}</td>
    <td class="num">${p ? `<button class="exclude-btn ${isExcl ? "on" : ""}" data-id="${escape(p.id)}">${isExcl ? "On" : "Exclude"}</button>` : ""}</td>
  </tr>`;
}

// ---------- Formatting ----------
function fmtNum(n, digits = 1) {
  if (n == null || Number.isNaN(n)) return "—";
  return Number(n).toFixed(digits);
}
function fmtSalary(s) {
  if (s == null) return "—";
  return `$${(s / 1000).toFixed(0)}k`;
}
function fmtPct(p) {
  if (p == null) return "—";
  return `${(p * 100).toFixed(0)}%`;
}
function escape(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// ---------- Event wiring ----------
function wire() {
  document.getElementById("model-select").value = state.model;
  document.getElementById("model-select").addEventListener("change", (e) => {
    state.model = e.target.value;
    savePrefs();
    updateStatusLine();
    if (state.tab === "ladder") renderLadder();
    if (state.tab === "browse") renderBrowse();
  });

  for (const btn of document.querySelectorAll(".tab")) {
    btn.addEventListener("click", () => setTab(btn.dataset.tab));
  }

  document.getElementById("search").addEventListener("input", (e) => {
    state.filter.search = e.target.value;
    renderBrowse();
  });
  document.getElementById("pos-filter").addEventListener("change", (e) => {
    state.filter.pos = e.target.value;
    renderBrowse();
  });
  document.getElementById("team-filter").addEventListener("change", (e) => {
    state.filter.team = e.target.value;
    renderBrowse();
  });
  document.getElementById("has-2025-only").addEventListener("change", (e) => {
    state.filter.only2025 = e.target.checked;
    renderBrowse();
  });

  for (const th of document.querySelectorAll(".players-table th[data-sort]")) {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sort.key === key) {
        state.sort.dir = state.sort.dir === "asc" ? "desc" : "asc";
      } else {
        state.sort.key = key;
        state.sort.dir = ["name", "team", "positions"].includes(key) ? "asc" : "desc";
      }
      renderBrowse();
    });
  }

  document.getElementById("drawer-close").addEventListener("click", () => {
    document.getElementById("team-drawer").classList.add("hidden");
  });
}

// ---------- Init ----------
async function init() {
  loadPrefs();
  wire();
  await loadData();
  setTab(state.tab);
}

init();
