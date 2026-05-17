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
  roundScores: null,
  model: "blend",
  excluded: new Set(),
  tab: "matchups",
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

  try {
    const sr = await fetch("data/round_scores.json", { cache: "no-store" });
    if (sr.ok) state.roundScores = await sr.json();
  } catch (e) { /* round scores optional */ }

  updateStatusLine();
}

function updateStatusLine() {
  const statusEl = document.getElementById("data-status");
  const parts = [`${state.players.length} players`];
  if (state.rosters?.teams) parts.push(`${state.rosters.teams.length} teams`);
  if (state.rosters?.round) parts.push(`R${state.rosters.round}`);
  parts.push(state.model.toUpperCase());
  statusEl.textContent = parts.join(" · ");
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
  // keeperfantasy playing_status: 9 = not in AFL team this round (won't score).
  // Values 1/2/4/5 all mean the player IS in the squad — don't auto-exclude.
  // The `injured` flag alone is "has injury history" and doesn't gate playing
  // (e.g. Sam Berry, John Noble: status 2, injured false, playing this week).
  if (entry.playing_status === 9) return false;
  return true;
}

// A starter who won't score this round and must be subbed out of projections:
// user-excluded, not in their AFL team (status 9), carrying an injury, or
// with no projectable stats. Distinct from isAvailable (display-only).
function isOut(entry) {
  if (!entry || !entry.player) return true;
  if (state.excluded.has(entry.player.id)) return true;
  if (entry.playing_status === 9) return true;
  if (entry.injured) return true;
  return project(entry.player) == null;
}

// Each team projects a full 12-man lineup every week. Out/injured/excluded
// starters are replaced by the best available player on the same roster —
// preferring someone eligible for the vacated position, falling back to the
// best remaining sub of any position so the count is always 12.
function teamProjection(team) {
  if (!team) return { total: 0, captainId: null, captainProj: -1, items: [], hasLineup: false };
  const allRoster = team.roster || [];
  const hasLineup = allRoster.some(e => e.starting === true);

  // Substitution pool: every non-starter who can actually score, best first.
  const pool = allRoster
    .filter(e => !e.starting && !isOut(e))
    .map(e => ({ entry: e, proj: project(e.player) }))
    .sort((a, b) => b.proj - a.proj);

  let slots;
  if (hasLineup) {
    slots = allRoster.filter(e => e.starting);
  } else {
    // No explicit lineup — take the 12 best available players outright.
    slots = allRoster
      .filter(e => !isOut(e))
      .sort((a, b) => project(b.player) - project(a.player))
      .slice(0, 12);
  }

  // Fill scarce positions first so a versatile sub isn't burnt on a MID slot.
  const posRank = { RUC: 0, DEF: 1, FWD: 2, MID: 3 };
  const ordered = slots.slice().sort(
    (a, b) => (posRank[a.selected_pos] ?? 4) - (posRank[b.selected_pos] ?? 4));

  const used = new Set();
  const items = [];
  let total = 0, capPid = null, capProj = -1;

  for (const slot of ordered) {
    let pick = null, pickProj = null, substituted = false;
    if (!isOut(slot)) {
      pick = slot; pickProj = project(slot.player);
    } else {
      const pos = slot.selected_pos;
      // 1) best unused sub eligible for this exact position
      let cand = pool.find(c => !used.has(c) && (c.entry.positions || []).includes(pos));
      // 2) otherwise best unused sub of any position (guarantees 12)
      if (!cand) cand = pool.find(c => !used.has(c));
      if (cand) { used.add(cand); pick = cand.entry; pickProj = cand.proj; substituted = true; }
    }
    if (pick == null || pickProj == null) continue; // truly nobody left
    total += pickProj;
    if (pickProj > capProj) { capProj = pickProj; capPid = pick.player.id; }
    items.push({ entry: pick, proj: pickProj, slot, substituted });
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
  if (tab === "final") renderFinalLadder();
  if (tab === "premiership") renderPremiership();
  if (tab === "rankdist") renderRankDist();
  if (tab === "matchups") renderMatchups();
  if (tab === "mvp") renderMVP();
  if (tab === "browse") renderBrowse();
}

// Gaussian noise — used by runMCMC.
function gaussianNoise(sigma) {
  let u1 = 0, u2 = 0;
  while (u1 === 0) u1 = Math.random();
  while (u2 === 0) u2 = Math.random();
  return sigma * Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

// Colour the n-th segment of a rank-distribution bar. Rank 1 = amber (the
// championship), ranks 2–6 = teal gradient (finals zone), ranks 7–14 = red
// gradient (missed finals).
function rankSegmentColor(rank) {
  if (rank === 1) return "rgba(255, 176, 0, 0.95)";
  if (rank <= 6) {
    const t = (rank - 2) / 4;            // 0 at rank 2, 1 at rank 6
    const alpha = 0.92 - 0.45 * t;
    return `rgba(94, 200, 232, ${alpha})`;
  }
  const t = (rank - 7) / 7;              // 0 at rank 7, 1 at rank 14
  const alpha = 0.28 + 0.45 * t;
  return `rgba(232, 90, 90, ${alpha})`;
}

function renderRankDist() {
  const empty = document.getElementById("rankdist-empty");
  const content = document.getElementById("rankdist-content");
  const metaEl = document.getElementById("rankdist-meta");

  const sim = runMCMC();
  if (!sim) {
    empty.classList.remove("hidden");
    content.innerHTML = "";
    metaEl.textContent = "—";
    return;
  }
  empty.classList.add("hidden");
  metaEl.textContent = `${MCMC_SIMS.toLocaleString()} sims · σ=${MCMC_SIGMA} · ${state.model.toUpperCase()} model`;

  const teamById = new Map(state.rosters.teams.map(t => [String(t.id), t]));
  const meanRank = dist => dist.reduce((s, p, i) => s + p * (i + 1), 0);
  // Sort by mean (average) finishing position — best average on top.
  const rows = [...sim.teams]
    .map(r => ({ ...r, mean_rank: meanRank(r.rank_dist || []) }))
    .sort((a, b) => a.mean_rank - b.mean_rank);
  const numRanks = rows.length;

  // Build legend (rank index → color chip).
  let legend = `<div class="rank-legend">`;
  for (let r = 1; r <= numRanks; r++) {
    const labelCls = r === 1 ? " champ" : (r <= 6 ? " finals" : " missed");
    legend += `<span class="rank-legend-chip${labelCls}" style="background:${rankSegmentColor(r)}">${r}</span>`;
  }
  legend += `</div>`;

  let html = `<p class="ladder-meta">Each row is one team's full posterior over final ladder position from ${MCMC_SIMS.toLocaleString()} Monte Carlo sims. Bar segments are widths-as-probabilities: <span style="color:var(--accent)">amber</span> = P(1st), <span style="color:var(--teal)">teal</span> = ranks 2–6 (finals), red = ranks 7–14 (missed). Hover any segment for the exact percent.</p>`;
  html += `<div class="rank-legend-wrap"><span class="rank-legend-label">FINAL POSITION:</span>${legend}</div>`;

  html += `<div class="rank-dist-list">`;
  rows.forEach((row) => {
    const team = teamById.get(row.team_id);
    const dist = row.rank_dist || [];
    // Most likely rank = argmax.
    let mode = 0, modeP = 0;
    dist.forEach((p, i) => { if (p > modeP) { modeP = p; mode = i + 1; } });

    let bar = "";
    dist.forEach((p, i) => {
      const rank = i + 1;
      if (p <= 0) return;
      const pct = p * 100;
      const pctStr = pct < 0.1 ? "<0.1%" : `${pct.toFixed(1)}%`;
      bar += `<span class="rank-segment" style="width:${pct.toFixed(2)}%;background:${rankSegmentColor(rank)}" title="${rank}${ordSuffix(rank)}: ${pctStr}"></span>`;
    });

    html += `<div class="rank-row" data-team-id="${escape(row.team_id)}">
      <div class="rank-row-name">
        <span class="team-name-cell">${escape(team?.name || row.team_id)}</span>
        <span class="team-meta">avg ${row.mean_rank.toFixed(2)} · mode ${mode}${ordSuffix(mode)} · ${(row.p_finals * 100).toFixed(0)}% finals</span>
      </div>
      <div class="rank-bar">${bar}</div>
    </div>`;
  });
  html += `</div>`;

  content.innerHTML = html;

  for (const row of content.querySelectorAll(".rank-row[data-team-id]")) {
    row.addEventListener("click", () => openTeamDrawer(row.dataset.teamId));
  }
}

function renderPremiership() {
  const empty = document.getElementById("premiership-empty");
  const content = document.getElementById("premiership-content");
  const metaEl = document.getElementById("premiership-meta");

  const sim = runMCMC();
  if (!sim) {
    empty.classList.remove("hidden");
    content.innerHTML = "";
    metaEl.textContent = "—";
    return;
  }
  empty.classList.add("hidden");
  metaEl.textContent = `${MCMC_SIMS.toLocaleString()} sims · σ=${MCMC_SIGMA} · ${state.model.toUpperCase()} model`;

  const teamById = new Map(state.rosters.teams.map(t => [String(t.id), t]));
  const result = [...sim.teams].sort((a, b) =>
    b.p_premier - a.p_premier || b.p_finals - a.p_finals);
  const maxP = Math.max(...result.map(r => r.p_premier), 0.01);

  let html = `<p class="ladder-meta">Monte Carlo: each simulation re-rolls every remaining regular-season game with σ=${MCMC_SIGMA} per team, builds the final ladder, and runs the finals bracket (3v6, 4v5 → 1 vs winner(4v5), 2 vs winner(3v6) → GF). Probability shown is the share of simulations that team wins the Cookie Cup.</p>`;
  html += `<div class="table-wrap"><table class="leaderboard">
    <thead><tr>
      <th class="rank">#</th>
      <th>Team</th>
      <th class="num">P(finals)</th>
      <th class="num">P(premiership)</th>
      <th>Distribution</th>
    </tr></thead><tbody>`;
  result.forEach((row, i) => {
    const team = teamById.get(row.team_id);
    const rankCls = i === 0 ? "rank rank-1" : (i < 3 ? "rank rank-2" : "rank");
    const rowCls = i === 0 ? "rank-1-row" : "";
    const barPct = (row.p_premier / maxP) * 100;
    const pPct = row.p_premier * 100;
    const pPctText = pPct < 0.1 ? "<0.1%" : `${pPct.toFixed(1)}%`;
    html += `<tr class="${rowCls}" data-team-id="${escape(row.team_id)}">
      <td class="${rankCls}">${i + 1}</td>
      <td><span class="team-name-cell">${escape(team?.name || row.team_id)}</span></td>
      <td class="num">${(row.p_finals * 100).toFixed(0)}%</td>
      <td class="num proj-cell">${pPctText}</td>
      <td><div class="prob-bar-wrap"><div class="prob-bar" style="width:${barPct.toFixed(1)}%"></div></div></td>
    </tr>`;
  });
  html += `</tbody></table></div>`;
  content.innerHTML = html;

  for (const tr of content.querySelectorAll("tr[data-team-id]")) {
    tr.addEventListener("click", () => openTeamDrawer(tr.dataset.teamId));
  }
}

// ---------- Monte Carlo: shared engine for Predicted Final + Premiership Hope ----------
// Runs one Monte Carlo pass that re-rolls every unplayed regular-season fixture
// with Gaussian noise around each team's projection, then plays out the finals
// bracket (3v6, 4v5 → 1 vs winner(4v5), 2 vs winner(3v6) → GF). The result holds
// expected W/L/D/PF/PA/PTS per team, P(make finals), and P(win premiership).
// Cached by (model, excluded set) so tab-switching doesn't re-simulate.

const MCMC_SIMS = 10000;
const MCMC_SIGMA = 120;

let _mcmcCache = null;
let _mcmcCacheKey = null;

function mcmcCacheKey() {
  return `${state.model}|${[...state.excluded].sort().join(",")}|${MCMC_SIMS}|${MCMC_SIGMA}`;
}

function runMCMC() {
  const key = mcmcCacheKey();
  if (_mcmcCacheKey === key && _mcmcCache) return _mcmcCache;

  const teams = state.rosters?.teams || [];
  const standings = state.rosters?.standings || [];
  const fixtures = state.rosters?.fixtures || [];
  if (!teams.length || !standings.length || !fixtures.length) return null;

  const proj = new Map();
  for (const t of teams) proj.set(String(t.id), teamProjection(t).total);

  const baseState = new Map();
  for (const s of standings) {
    if (!s.team_id) continue;
    baseState.set(String(s.team_id), {
      w: s.w, l: s.l, d: s.d, pf: s.pf, pa: s.pa, rank: s.rank, pts: s.pts,
      played: s.played,
    });
  }
  const teamIds = [...baseState.keys()];

  // Bake in completed PRIOR rounds the ladder hasn't picked up yet. The ladder
  // typically only refreshes after a round fully ends, so a finished earlier
  // round can be missing from standings while still present in /_matchups.
  // The *current* round (rosters.round) may be in progress — its /_matchups
  // scores are partial/live, so we never bake it: current points must match
  // the official ladder, and the live round is projected instead (below).
  const currentRound = Number(state.rosters?.round) || Infinity;
  for (const f of fixtures) {
    if (f.round >= currentRound) continue; // live/future round — simulate it
    const hs = f.home_score || 0;
    const as_ = f.away_score || 0;
    if (hs + as_ === 0) continue; // prior round with no score yet — simulate it
    const ht = String(f.home_team_id);
    const at = String(f.away_team_id);
    const bh = baseState.get(ht);
    const ba = baseState.get(at);
    if (!bh || !ba) continue;
    // If both teams' standings already include this round, skip.
    if (bh.played >= f.round && ba.played >= f.round) continue;
    bh.pf += hs; bh.pa += as_;
    ba.pf += as_; ba.pa += hs;
    if (hs > as_) { bh.w++; ba.l++; }
    else if (hs < as_) { bh.l++; ba.w++; }
    else { bh.d++; ba.d++; }
    bh.played++; ba.played++;
    bh.pts = bh.w * 4 + bh.d * 2;
    ba.pts = ba.w * 4 + ba.d * 2;
  }

  // Anything not yet reflected in the (possibly baked) standings gets
  // simulated — including the live current round, which we project rather
  // than freeze at its in-progress partial score.
  const remaining = fixtures.filter(f => {
    if (f.home_team_id == null || f.away_team_id == null) return false;
    const bh = baseState.get(String(f.home_team_id));
    return bh && f.round > bh.played;
  });

  const sumStats = new Map();
  for (const tid of teamIds) sumStats.set(tid, { w: 0, l: 0, d: 0, pf: 0, pa: 0, pts: 0 });
  const finalsCount = new Map(teamIds.map(t => [t, 0]));
  const premierCount = new Map(teamIds.map(t => [t, 0]));
  // rankCounts[tid][i] = number of sims where team finished at rank (i+1).
  const rankCounts = new Map(teamIds.map(t => [t, new Array(teamIds.length).fill(0)]));

  const samp = tid => (proj.get(tid) || 0) + gaussianNoise(MCMC_SIGMA);

  for (let s = 0; s < MCMC_SIMS; s++) {
    const sim = new Map();
    for (const [tid, st] of baseState) {
      sim.set(tid, { w: st.w, l: st.l, d: st.d, pf: st.pf, pa: st.pa });
    }

    // Resimulate the rest of the regular season.
    for (const f of remaining) {
      const ht = String(f.home_team_id);
      const at = String(f.away_team_id);
      const hScore = samp(ht);
      const aScore = samp(at);
      const h = sim.get(ht);
      const a = sim.get(at);
      if (!h || !a) continue;
      h.pf += hScore; h.pa += aScore;
      a.pf += aScore; a.pa += hScore;
      if (Math.abs(hScore - aScore) < 0.5) { h.d++; a.d++; }
      else if (hScore > aScore) { h.w++; a.l++; }
      else { h.l++; a.w++; }
    }

    // Accumulate per-team stats for the expected-ladder view.
    for (const [tid, st] of sim) {
      const acc = sumStats.get(tid);
      acc.w += st.w; acc.l += st.l; acc.d += st.d;
      acc.pf += st.pf; acc.pa += st.pa;
      acc.pts += st.w * 4 + st.d * 2;
    }

    // Build this sim's final ladder; mark top-6 as finalists; run the bracket.
    const ladder = [...sim.entries()].map(([tid, st]) => ({
      tid, pts: st.w * 4 + st.d * 2, pf: st.pf,
    }));
    ladder.sort((a, b) => b.pts - a.pts || b.pf - a.pf);
    ladder.forEach((entry, i) => { rankCounts.get(entry.tid)[i]++; });
    const top6 = ladder.slice(0, 6).map(x => x.tid);
    for (const tid of top6) finalsCount.set(tid, finalsCount.get(tid) + 1);

    const wk1a = samp(top6[2]) > samp(top6[5]) ? top6[2] : top6[5]; // 3 v 6
    const wk1b = samp(top6[3]) > samp(top6[4]) ? top6[3] : top6[4]; // 4 v 5
    const wk2a = samp(top6[0]) > samp(wk1b) ? top6[0] : wk1b;        // 1 v winner(4v5)
    const wk2b = samp(top6[1]) > samp(wk1a) ? top6[1] : wk1a;        // 2 v winner(3v6)
    const champ = samp(wk2a) > samp(wk2b) ? wk2a : wk2b;
    premierCount.set(champ, premierCount.get(champ) + 1);
  }

  // Build per-team rows of expected stats + probabilities.
  const rows = teamIds.map(tid => {
    const base = baseState.get(tid);
    const sum = sumStats.get(tid);
    return {
      team_id: tid,
      current_rank: base.rank,
      current_w: base.w, current_l: base.l, current_d: base.d,
      current_pts: base.pts, current_pf: base.pf, current_pa: base.pa,
      expected_w: sum.w / MCMC_SIMS,
      expected_l: sum.l / MCMC_SIMS,
      expected_d: sum.d / MCMC_SIMS,
      expected_pf: sum.pf / MCMC_SIMS,
      expected_pa: sum.pa / MCMC_SIMS,
      expected_pts: sum.pts / MCMC_SIMS,
      // Increments are means too, for the "+W / +L / +D" column.
      pred_w: sum.w / MCMC_SIMS - base.w,
      pred_l: sum.l / MCMC_SIMS - base.l,
      pred_d: sum.d / MCMC_SIMS - base.d,
      p_finals: finalsCount.get(tid) / MCMC_SIMS,
      p_premier: premierCount.get(tid) / MCMC_SIMS,
      rank_dist: rankCounts.get(tid).map(c => c / MCMC_SIMS),
    };
  });

  // Expected final rank = sort by expected_pts (tiebreak expected_pf).
  const sorted = [...rows].sort((a, b) =>
    b.expected_pts - a.expected_pts || b.expected_pf - a.expected_pf);
  sorted.forEach((r, i) => { r.expected_rank = i + 1; r.delta = r.current_rank - r.expected_rank; });

  const result = { numSims: MCMC_SIMS, sigma: MCMC_SIGMA, teams: rows };
  _mcmcCache = result;
  _mcmcCacheKey = key;
  return result;
}

function renderFinalLadder() {
  const empty = document.getElementById("final-empty");
  const content = document.getElementById("final-content");
  const metaEl = document.getElementById("final-meta");

  const sim = runMCMC();
  if (!sim) {
    empty.classList.remove("hidden");
    content.innerHTML = "";
    metaEl.textContent = "—";
    return;
  }
  empty.classList.add("hidden");

  const currentRound = state.rosters.round;
  const totalRounds = state.rosters.total_rounds;
  const remaining = totalRounds && currentRound ? totalRounds - currentRound : "?";
  metaEl.textContent = `${MCMC_SIMS.toLocaleString()} sims · σ=${MCMC_SIGMA} · ${state.model.toUpperCase()} model · ${remaining} rounds remaining`;

  const teamById = new Map(state.rosters.teams.map(t => [String(t.id), t]));
  const rows = [...sim.teams].sort((a, b) => a.expected_rank - b.expected_rank);

  let html = `<p class="ladder-meta">Expected final standings from ${MCMC_SIMS.toLocaleString()} Monte Carlo simulations (σ=${MCMC_SIGMA} per team per game). Close projections become near-50/50 in any single round; blowouts stay decisive. Points: 4·W + 2·D, tiebreak points-for. <strong>Top 6 make finals.</strong></p>`;
  html += `<div class="table-wrap"><table class="leaderboard">
    <thead><tr>
      <th class="rank">#</th>
      <th>Team</th>
      <th class="num">Δ</th>
      <th class="num">Now</th>
      <th class="num">+W / +L / +D <span class="muted">(mean)</span></th>
      <th>Expected W-L-D</th>
      <th class="num">PF / PA</th>
      <th class="num">Expected PTS</th>
    </tr></thead><tbody>`;
  rows.forEach((row, i) => {
    const team = teamById.get(row.team_id);
    const rankCls = i === 0 ? "rank rank-1" : (i < 3 ? "rank rank-2" : "rank");
    const rowClsList = [];
    if (i === 0) rowClsList.push("rank-1-row");
    if (i < 6) rowClsList.push("finals-zone");
    const delta = row.delta;
    const deltaHtml = delta > 0
      ? `<span class="rank-up">▲ ${delta}</span>`
      : delta < 0
        ? `<span class="rank-down">▼ ${Math.abs(delta)}</span>`
        : `<span class="muted">—</span>`;
    html += `<tr class="${rowClsList.join(" ")}" data-team-id="${escape(row.team_id)}">
      <td class="${rankCls}">${row.expected_rank}</td>
      <td><span class="team-name-cell">${escape(team?.name || row.team_id)}<span class="team-meta">was ${row.current_rank}${ordSuffix(row.current_rank)}</span></span></td>
      <td class="num">${deltaHtml}</td>
      <td class="num">${row.current_pts}</td>
      <td class="num">+${row.pred_w.toFixed(1)} / +${row.pred_l.toFixed(1)} / +${row.pred_d.toFixed(1)}</td>
      <td>${row.expected_w.toFixed(1)}–${row.expected_l.toFixed(1)}–${row.expected_d.toFixed(1)}</td>
      <td class="num"><span class="muted">${fmtNum(row.expected_pf, 0)} / ${fmtNum(row.expected_pa, 0)}</span></td>
      <td class="num proj-cell">${row.expected_pts.toFixed(1)}</td>
    </tr>`;
    if (i === 5) {
      html += `<tr class="cutoff-row"><td colspan="8">✧ Finals cutoff ✧</td></tr>`;
    }
  });
  html += `</tbody></table></div>`;
  content.innerHTML = html;

  for (const tr of content.querySelectorAll("tr[data-team-id]")) {
    tr.addEventListener("click", () => openTeamDrawer(tr.dataset.teamId));
  }
}

function ordSuffix(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return s[(v - 20) % 10] || s[v] || s[0];
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

// ---------- Matchups ----------
// keeperfantasy's /matchup `round` lags — it stays on a round until the next
// one opens, so a finished round keeps reporting itself as current. The
// standings are the reliable signal: the next round to play is one past the
// fewest games any team has played. Falls back to rosters.round if standings
// are unavailable, and never runs past the final round.
function displayRound() {
  const st = state.rosters?.standings || [];
  const fallback = state.rosters?.round;
  if (!st.length) return fallback;
  const minPlayed = Math.min(...st.map(s => s.played ?? 0));
  const maxRound = state.rosters?.total_rounds
    || Math.max(0, ...(state.rosters?.fixtures || []).map(f => f.round));
  return Math.min(minPlayed + 1, maxRound) || fallback;
}

function renderMatchups() {
  const empty = document.getElementById("matchups-empty");
  const content = document.getElementById("matchups-content");
  const metaEl = document.getElementById("matchups-meta");
  const allFixtures = state.rosters?.fixtures || [];
  const currentRound = displayRound();
  if (!state.rosters?.teams?.length || !allFixtures.length || !currentRound) {
    empty.classList.remove("hidden");
    content.innerHTML = "";
    metaEl.textContent = "—";
    return;
  }
  const roundFixtures = allFixtures.filter(f => f.round === currentRound);
  if (!roundFixtures.length) {
    empty.classList.remove("hidden");
    content.innerHTML = "";
    return;
  }
  empty.classList.add("hidden");
  metaEl.textContent = `Round ${currentRound} · ${roundFixtures.length} matchups`;

  const teamById = new Map(state.rosters.teams.map(t => [String(t.id), t]));

  let html = `<p class="ladder-meta">Round ${currentRound} matchups. Live scores show where a game has begun; otherwise projected scores from the ${state.model.toUpperCase()} model. Click either team to inspect its lineup.</p>`;
  html += `<div class="matchup-list">`;
  for (const f of roundFixtures) {
    const home = teamById.get(String(f.home_team_id));
    const away = teamById.get(String(f.away_team_id));
    const hasActual = (f.home_score + f.away_score) > 0;
    const hScore = hasActual ? f.home_score : teamProjection(home).total;
    const aScore = hasActual ? f.away_score : teamProjection(away).total;
    const scoreLabel = hasActual ? "live" : "projected";
    const homeWin = hScore > aScore + 0.5;
    const awayWin = aScore > hScore + 0.5;
    html += `<div class="matchup-row-h">
      <div class="team-side home${homeWin ? " winning" : ""}" data-team-id="${escape(f.home_team_id)}">
        <div class="team-name-cell">${escape(home?.name || f.home_name)}</div>
        <div class="team-record">${escape(f.home_record)}</div>
      </div>
      <div class="score-block">
        <span class="score${homeWin ? " winning" : ""}">${fmtNum(hScore, 0)}</span>
        <span class="vs">vs</span>
        <span class="score${awayWin ? " winning" : ""}">${fmtNum(aScore, 0)}</span>
        <div class="score-type${hasActual ? " live" : ""}">${scoreLabel}</div>
      </div>
      <div class="team-side away${awayWin ? " winning" : ""}" data-team-id="${escape(f.away_team_id)}">
        <div class="team-name-cell">${escape(away?.name || f.away_name)}</div>
        <div class="team-record">${escape(f.away_record)}</div>
      </div>
    </div>`;
  }
  html += `</div>`;
  content.innerHTML = html;

  for (const side of content.querySelectorAll(".team-side[data-team-id]")) {
    side.addEventListener("click", () => openTeamDrawer(side.dataset.teamId));
  }
}

// ---------- MVP / Win shares ----------
// Win Probability Added: per round, how much a starter's actual score moved
// their team's win probability versus a position-replacement-level score.
// Win probability uses a Gaussian single-game model: margin ~ Normal(meanA -
// meanB, SIGMA_GAME·√2). Replacement level = the league-wide mean starter
// score at that position in that round. A player's season "win shares" is the
// sum of these per-round deltas ≈ expected extra wins generated vs replacement.
const MVP_SIGMA_GAME = 100;

function normCdf(x) {
  // Abramowitz & Stegun 7.1.26 erf approximation.
  const z = x / Math.SQRT2;
  const t = 1 / (1 + 0.3275911 * Math.abs(z));
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
    - 0.284496736) * t + 0.254829592) * t * Math.exp(-z * z);
  const erf = z >= 0 ? y : -y;
  return 0.5 * (1 + erf);
}

let _winSharesCache = null;
function computeWinShares() {
  if (_winSharesCache) return _winSharesCache;
  const rs = state.roundScores;
  if (!rs?.rounds?.length) return null;
  const sd = MVP_SIGMA_GAME * Math.SQRT2;

  // kf_id -> current roster context (owner / eligible positions).
  const ctx = new Map();
  for (const t of state.rosters?.teams || []) {
    for (const e of t.roster || []) {
      ctx.set(String(e.kf_id), { owner: t.name, positions: e.positions });
    }
  }

  const agg = new Map(); // id -> { id, name, wpa, gp, pts, posCount, lastTeam }
  for (const rd of rs.rounds) {
    // Replacement baseline = league-wide mean starter score per position.
    const byPos = new Map(); // pos -> [sum, n]
    let allSum = 0, allN = 0;
    for (const mu of rd.matchups) {
      for (const side of [mu.team1, mu.team2]) {
        for (const p of side.starters || []) {
          const b = byPos.get(p.pos) || [0, 0];
          b[0] += p.points; b[1]++; byPos.set(p.pos, b);
          allSum += p.points; allN++;
        }
      }
    }
    const repl = pos => {
      const b = byPos.get(pos);
      return b && b[1] ? b[0] / b[1] : (allN ? allSum / allN : 0);
    };

    for (const mu of rd.matchups) {
      const sides = [[mu.team1, mu.team2], [mu.team2, mu.team1]];
      for (const [side, opp] of sides) {
        const S = side.score, O = opp.score;
        const pWith = normCdf((S - O) / sd);
        for (const p of side.starters || []) {
          const without = normCdf((S - p.points + repl(p.pos) - O) / sd);
          const wpa = pWith - without;
          const key = String(p.id);
          let a = agg.get(key);
          if (!a) { a = { id: key, name: p.name, wpa: 0, gp: 0, pts: 0, posCount: {}, lastTeam: side.name }; agg.set(key, a); }
          a.wpa += wpa; a.gp += 1; a.pts += p.points;
          a.posCount[p.pos] = (a.posCount[p.pos] || 0) + 1;
          a.lastTeam = side.name;
        }
      }
    }
  }

  const rows = [...agg.values()].map(a => {
    const c = ctx.get(a.id);
    const playedPos = Object.entries(a.posCount).sort((x, y) => y[1] - x[1])[0]?.[0];
    return {
      ...a,
      owner: c?.owner || a.lastTeam || "—",
      pos: playedPos || (c?.positions || [])[0] || "",
      avg: a.gp ? a.pts / a.gp : 0,
    };
  }).sort((x, y) => y.wpa - x.wpa);

  _winSharesCache = { rows, completed_through: rs.completed_through };
  return _winSharesCache;
}

function renderMVP() {
  const empty = document.getElementById("mvp-empty");
  const content = document.getElementById("mvp-content");
  const metaEl = document.getElementById("mvp-meta");
  const data = computeWinShares();
  if (!data || !data.rows.length) {
    empty.classList.remove("hidden");
    content.innerHTML = "";
    metaEl.textContent = "—";
    return;
  }
  empty.classList.add("hidden");
  const top = data.rows.slice(0, 30);
  metaEl.textContent = `Rounds 1–${data.completed_through} · ${data.rows.length} players ranked`;

  let html = `<p class="ladder-meta">Win shares = sum over completed rounds of how much each player's actual score shifted their team's win probability versus a position-replacement-level performance (Gaussian game model, σ=${MVP_SIGMA_GAME}). It approximates the expected extra wins a player has generated for their fantasy team. Top 30 shown.</p>`;
  html += `<div class="table-wrap"><table class="players-table"><thead><tr>
    <th class="num">#</th><th>Player</th><th>Pos</th><th>Owner</th>
    <th class="num">GP</th><th class="num">Pts</th><th class="num">Avg</th>
    <th class="num">Win shares</th></tr></thead><tbody>`;
  top.forEach((r, i) => {
    html += `<tr>
      <td class="num muted">${i + 1}</td>
      <td><span class="player-name">${escape(r.name)}</span></td>
      <td><span class="pos-tag">${escape(r.pos)}</span></td>
      <td class="muted">${escape(r.owner)}</td>
      <td class="num">${r.gp}</td>
      <td class="num">${fmtNum(r.pts, 0)}</td>
      <td class="num">${fmtNum(r.avg, 1)}</td>
      <td class="num proj-cell">+${r.wpa.toFixed(2)}</td>
    </tr>`;
  });
  html += `</tbody></table></div>`;
  content.innerHTML = html;
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
  for (const p of rows) {
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
      if (state.tab === "final") renderFinalLadder();
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
  if (e.playing_status === 9) {
    tags.push('<span class="injury-tag out">OUT</span>');
  } else if (e.injured) {
    tags.push(`<span class="injury-tag" title="${escape(e.injury_desc || "Injured")}">${escape((e.injury_desc || "INJ").toUpperCase())}</span>`);
  }
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
    if (state.tab === "final") renderFinalLadder();
    if (state.tab === "premiership") renderPremiership();
    if (state.tab === "rankdist") renderRankDist();
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
