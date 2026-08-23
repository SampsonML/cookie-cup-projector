#!/usr/bin/env python3
"""Cookie Cup 2026 season-review analytics.

Reads the generated data (rosters/round_scores/players), the finals scores
(scripts/fetch_finals_scores.py), the draft CSV and the April roster snapshot,
and writes docs/data/season_review.json for docs/season_review.html.

Run order: parse_stats -> fetch_rosters -> fetch_round_scores ->
fetch_finals_scores -> build_season_review.
"""
import csv
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "docs" / "data"

rosters = json.loads((DATA / "rosters.json").read_text())
round_scores = json.loads((DATA / "round_scores.json").read_text())
finals_scores = json.loads((REPO / "scripts" / ".cache" / "finals_scores.json").read_text())
players = json.loads((DATA / "players.json").read_text())["players"]

SHORT = {
    "Jags to riches": "Jags",
    "The Forrie Crystals": "Forrie",
    "Scissorlift Victims Unit": "Scissorlift",
    "Heartbreak Jake & da Subs": "Heartbreak Jake",
    "Prince McAndrew": "Prince McAndrew",
    "Kids can call you Bont-ju": "Bont-ju",
    "iWalshMyslfwthATonInMyMid": "iWalsh",
    "Houston we have a problem": "Houston",
    "Lachie’s Flesh Clarrynet": "Clarrynet",
    "GuldenEye": "GuldenEye",
    "JD Vyvance": "JD Vyvance",
    "Xing’s Brother": "Xing’s",
    "uFlorent": "uFlorent",
    "Naughty Dog Entertainment": "Naughty Dog",
}

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).replace("’", "'").strip()
    return re.sub(r"\s+", " ", s).lower()

NICKS = [("brad", "bradley"), ("ollie", "oliver"), ("matty", "matt"),
         ("matt", "matthew"), ("lachie", "lachlan"), ("josh", "joshua"),
         ("sam", "samuel"), ("tom", "thomas"), ("nick", "nicholas"),
         ("zach", "zachary"), ("mitch", "mitchell"), ("cam", "cameron"),
         ("tim", "timothy"), ("will", "william"), ("harry", "harrison"),
         ("nat", "nathan"), ("jez", "jeremy"), ("charlie", "charles")]

def name_variants(n: str):
    n = norm(n)
    out = {n}
    parts = n.split(" ", 1)
    if len(parts) == 2:
        first, rest = parts
        for a, b in NICKS:
            if first == a:
                out.add(f"{b} {rest}")
            if first == b:
                out.add(f"{a} {rest}")
    return out

# ── teams ────────────────────────────────────────────────────────────────────
teams = {int(t["id"]): t["name"] for t in rosters["teams"]}
ids = sorted(teams)
idx = {tid: i for i, tid in enumerate(ids)}
names = [teams[tid] for tid in ids]
n_teams = len(ids)

managers = {}
for s in rosters["standings"]:
    tid = int(s["team_id"])
    cell = unicodedata.normalize("NFKC", s["name_cell"]).strip()
    tname = unicodedata.normalize("NFKC", teams[tid]).strip()
    managers[tid] = cell[len(tname):].strip() if cell.startswith(tname) else ""

# ── official weekly scores (rounds 1..21) ────────────────────────────────────
n_rounds = min(int(s["played"]) for s in rosters["standings"])
S = np.zeros((n_teams, n_rounds))
opp = np.zeros((n_teams, n_rounds), int)
for f in rosters["fixtures"]:
    r = f["round"] - 1
    if r >= n_rounds:
        continue
    i, j = idx[int(f["home_team_id"])], idx[int(f["away_team_id"])]
    S[i, r], S[j, r] = f["home_score"], f["away_score"]
    opp[i, r], opp[j, r] = j, i

standings = {int(s["team_id"]): s for s in rosters["standings"]}
cols = np.arange(n_rounds)
for t, tid in enumerate(ids):
    w = int((S[t] > S[opp[t], cols]).sum())
    d = int((S[t] == S[opp[t], cols]).sum())
    a = standings[tid]
    assert (w, d, int(S[t].sum())) == (a["w"], a["d"], int(a["pf"])), \
        f"fixtures do not reconcile with ladder for {names[t]}"
print("fixture scores reconcile with the official ladder ✓")

# H&A ladder order (PTS desc, PF desc)
hna_order = sorted(ids, key=lambda tid: (-standings[tid]["pts"], -standings[tid]["pf"]))
hna_rank = {tid: r + 1 for r, tid in enumerate(hna_order)}

# ── final placements from finals fixtures ────────────────────────────────────
placement_games = [("GF", 1), ("3RD", 3), ("5TH", 5), ("CGF", 7),
                   ("9TH", 9), ("11TH", 11), ("13TH", 13)]

def classify(label: str):
    cons = "Consolation" in label
    if "Grand Final" in label:
        return "CGF" if cons else "GF"
    for pat, code in [("3rd Place", "3RD"), ("5th Place", "5TH"), ("9th Place", "9TH"),
                      ("11th Place", "11TH"), ("13th Place", "13TH")]:
        if pat in label:
            return code
    return None

placement = {}
finals_results = []
for f in rosters["fixtures"]:
    if f["round"] <= n_rounds:
        continue
    code = classify(f["home_name"])
    hi, ai = int(f["home_team_id"]), int(f["away_team_id"])
    hs, as_ = f["home_score"], f["away_score"]
    wtid, ltid = (hi, ai) if hs > as_ else (ai, hi)
    finals_results.append({
        "round": f["round"], "label": f["home_name"], "code": code,
        "home": teams[hi], "away": teams[ai], "hs": hs, "as": as_,
    })
    for c, place in placement_games:
        if code == c:
            placement[wtid] = place
            placement[ltid] = place + 1
assert len(placement) == 14, f"only {len(placement)} teams placed"

# ── schedule-luck simulation ─────────────────────────────────────────────────
N_SIMS = 20000
RNG = np.random.default_rng(2026)
pts_sim = np.zeros((N_SIMS, n_teams))
wins_sim = np.zeros((N_SIMS, n_teams))
rows = np.arange(N_SIMS)
for r in range(n_rounds):
    perm = np.argsort(RNG.random((N_SIMS, n_teams)), axis=1)
    for k in range(n_teams // 2):
        a, b = perm[:, 2 * k], perm[:, 2 * k + 1]
        sa, sb = S[a, r], S[b, r]
        pts_sim[rows, a] += 4 * (sa > sb) + 2 * (sa == sb)
        pts_sim[rows, b] += 4 * (sb > sa) + 2 * (sa == sb)
        wins_sim[rows, a] += sa > sb
        wins_sim[rows, b] += sb > sa

pf = S.sum(axis=1)
key = pts_sim * 1e6 + pf[None, :]
order_sim = np.argsort(-key, axis=1, kind="stable")
ranks_sim = np.empty_like(order_sim)
ranks_sim[np.arange(N_SIMS)[:, None], order_sim] = np.arange(1, n_teams + 1)[None, :]

allplay = np.array([[(S[:, r] < S[t, r]).sum() / (n_teams - 1) for r in range(n_rounds)]
                    for t in range(n_teams)])
luck = []
for t, tid in enumerate(ids):
    a = standings[tid]
    luck.append({
        "team_id": tid,
        "actual_wins": a["w"] + 0.5 * a["d"],
        "exp_wins_sim": round(float(wins_sim[:, t].mean()), 2),
        "exp_wins_allplay": round(float(allplay[t].sum()), 2),
        "mean_rank": round(float(ranks_sim[:, t].mean()), 2),
        "actual_hna_rank": hna_rank[tid],
        "p_top6": round(float((ranks_sim[:, t] <= 6).mean()), 3),
        "opp_avg_faced": round(float(S[opp[t], cols].mean()), 1),
    })
league_weekly_mean = float(S.mean())

# ── race data: cumulative rank per round ─────────────────────────────────────
race = {tid: [] for tid in ids}
cum_pts = np.zeros(n_teams)
cum_pf = np.zeros(n_teams)
for r in range(n_rounds):
    res = S[:, r] > S[opp[:, r], r]
    draw = S[:, r] == S[opp[:, r], r]
    cum_pts += 4 * res + 2 * draw
    cum_pf += S[:, r]
    order_r = sorted(range(n_teams), key=lambda t: (-cum_pts[t], -cum_pf[t]))
    for pos, t in enumerate(order_r, 1):
        race[ids[t]].append(pos)

# ── records & awards (team level) ────────────────────────────────────────────
all_games = []
for f in rosters["fixtures"]:
    if f["home_score"] == 0 and f["away_score"] == 0:
        continue
    all_games.append({
        "round": f["round"], "finals": f["round"] > n_rounds,
        "home": teams[int(f["home_team_id"])], "away": teams[int(f["away_team_id"])],
        "hs": f["home_score"], "as": f["away_score"],
        "margin": abs(f["home_score"] - f["away_score"]),
        "total": f["home_score"] + f["away_score"],
    })

team_scores_flat = []
for g in all_games:
    team_scores_flat.append((g["hs"], g["home"], g["round"], g["away"], g["finals"]))
    team_scores_flat.append((g["as"], g["away"], g["round"], g["home"], g["finals"]))

records = {
    "highest_team": sorted(team_scores_flat, reverse=True)[:5],
    "lowest_team": sorted(team_scores_flat)[:5],
    "biggest_blowout": sorted(all_games, key=lambda g: -g["margin"])[:5],
    "closest": sorted([g for g in all_games if g["margin"] > 0], key=lambda g: g["margin"])[:5],
    "draws": [g for g in all_games if g["margin"] == 0],
    "shootout": sorted(all_games, key=lambda g: -g["total"])[:3],
    "stinker": sorted(all_games, key=lambda g: g["total"])[:3],
}

# streaks (H&A only)
streaks = []
for t, tid in enumerate(ids):
    res = np.where(S[t] > S[opp[t], cols], "W", np.where(S[t] < S[opp[t], cols], "L", "D"))
    best_w = best_l = cur = 0
    cur_ch = ""
    for ch in res:
        if ch == cur_ch:
            cur += 1
        else:
            cur, cur_ch = 1, ch
        if ch == "W":
            best_w = max(best_w, cur)
        elif ch == "L":
            best_l = max(best_l, cur)
    streaks.append({"team_id": tid, "longest_win": int(best_w), "longest_loss": int(best_l)})

# weekly top team score counts (H&A)
weekly_top_counts = defaultdict(int)
for r in range(n_rounds):
    weekly_top_counts[names[int(S[:, r].argmax())]] += 1

# ── player-level: banked points per (team, player) across all rounds ─────────
all_rounds = round_scores["rounds"] + finals_scores["rounds"]
banked = defaultdict(lambda: {"pts": 0, "games": 0})          # (tid, pid)
player_names = {}
best_rounds = []
weekly_top_player = []
player_total = defaultdict(lambda: {"pts": 0, "games": 0})
for rd in all_rounds:
    rnum = rd["round"]
    round_best = None
    for mu in rd["matchups"]:
        for side in ("team1", "team2"):
            tside = mu[side]
            tid = int(tside["league_team_id"])
            for st in tside["starters"]:
                pid = st["id"]
                player_names[pid] = st["name"]
                b = banked[(tid, pid)]
                b["pts"] += st["points"]
                b["games"] += 1
                pt = player_total[pid]
                pt["pts"] += st["points"]
                pt["games"] += 1
                rec = (st["points"], st["name"], teams[tid], rnum, rnum > n_rounds)
                best_rounds.append(rec)
                if round_best is None or st["points"] > round_best[0]:
                    round_best = rec
    weekly_top_player.append(round_best)
best_rounds.sort(reverse=True)

# ── draft CSV ────────────────────────────────────────────────────────────────
draft_rows = []
with open(REPO / "data" / "The_Cookie_Cup_draft_20260412_1335.csv", encoding="utf-8-sig") as fh:
    for row in csv.DictReader(fh):
        row["overall_pick"] = int(row["overall_pick"])
        row["round"] = int(row["round"])
        row["team_id"] = int(str(row["team_id"])[-2:])
        draft_rows.append(row)
print(f"draft picks: {len(draft_rows)}; teams: {len(set(r['team_id'] for r in draft_rows))}")

# players.json lookup by normalized name
pj_by_name = defaultdict(list)
for p in players:
    for v in name_variants(p["name"]):
        pj_by_name[v].append(p)

def find_player(nm):
    for v in name_variants(nm):
        if v in pj_by_name:
            return pj_by_name[v][0]
    return None

# kf_id by name from rosters + round scores
kfid_by_name = defaultdict(set)
for t in rosters["teams"]:
    for e in t["roster"]:
        for v in name_variants(e["name"]):
            kfid_by_name[v].add(e["kf_id"])
for pid, nm in player_names.items():
    for v in name_variants(nm):
        kfid_by_name[v].add(pid)

def find_kfid(nm):
    for v in name_variants(nm):
        if kfid_by_name[v]:
            return sorted(kfid_by_name[v])[0]
    return None

unmatched = []
for row in draft_rows:
    p = find_player(row["player_name"])
    row["pj"] = p
    row["kf_id"] = find_kfid(row["player_name"])
    if p is None:
        unmatched.append(row["player_name"])
print("unmatched draft names vs players.json:", unmatched)

# expected season-average by overall pick: rolling median over the draft board
board = sorted(draft_rows, key=lambda r: r["overall_pick"])
avgs = np.array([(r["pj"]["avg_pts"] if r["pj"] and r["pj"].get("avg_pts") and (r["pj"].get("games") or 0) >= 3
                  else 0.0) for r in board])
picks_arr = np.array([r["overall_pick"] for r in board])
W = 15
exp_curve = np.array([np.median(avgs[max(0, i - W // 2): i + W // 2 + 1]) for i in range(len(board))])
# enforce monotone non-increasing so late picks never "expect" more than early ones
exp_curve = np.minimum.accumulate(np.maximum.accumulate(exp_curve[::-1])[::-1])

for r_, exp in zip(board, exp_curve):
    p = r_["pj"]
    avg = (p or {}).get("avg_pts") or 0.0
    games = (p or {}).get("games") or 0
    r_["season_avg"] = round(avg, 1)
    r_["games"] = games
    r_["exp_avg"] = round(float(exp), 1)
    r_["resid"] = round(avg - float(exp), 1)
    b = banked.get((r_["team_id"], r_["kf_id"]), {"pts": 0, "games": 0})
    r_["banked_for_drafter"] = b["pts"]
    r_["banked_games"] = b["games"]

# per-team draft summary
draft_teams = {}
for tid in ids:
    rows_t = [r for r in board if r["team_id"] == tid]
    resids = [r["resid"] for r in rows_t]
    hits = [r for r in rows_t if r["resid"] > 8]
    busts = [r for r in rows_t if r["resid"] < -8]
    best = max(rows_t, key=lambda r: r["resid"])
    worst = min(rows_t, key=lambda r: r["resid"])
    draft_teams[tid] = {
        "picks": len(rows_t),
        "mean_resid": round(float(np.mean(resids)), 2),
        "total_banked": sum(r["banked_for_drafter"] for r in rows_t),
        "hits": len(hits), "busts": len(busts),
        "best": {"name": best["player_name"], "pick": best["overall_pick"],
                 "avg": best["season_avg"], "exp": best["exp_avg"], "resid": best["resid"]},
        "worst": {"name": worst["player_name"], "pick": worst["overall_pick"],
                  "avg": worst["season_avg"], "exp": worst["exp_avg"],
                  "resid": worst["resid"], "games": worst["games"]},
    }

steals = sorted(board, key=lambda r: -r["resid"])[:12]
busts_all = sorted([r for r in board if r["overall_pick"] <= 140], key=lambda r: r["resid"])[:12]

# ── opening rosters (April CSV) → keepers vs in-season acquisitions ──────────
drafted_by = {}
for r_ in board:
    if r_["kf_id"] is not None:
        drafted_by[r_["kf_id"]] = r_["team_id"]

draft_name_to_tid = {norm(r["team_name"]): r["team_id"] for r in draft_rows}
# April roster snapshot (~round 4). Never-drafted players owned then are either
# keepers (4 per team: 20 roster slots - 16 draft rounds) or early FA pickups.
# Keepers are the stars, so take the 4 lowest-ADP candidates per team.
keeper_cand = defaultdict(list)
april_names = {}
with open(REPO / "data" / "The_Cookie_Cup_players_20260412_1337.csv", encoding="utf-8-sig") as fh:
    for row in csv.DictReader(fh):
        tid = draft_name_to_tid.get(norm(row["owner"]))
        if tid is None:
            continue
        pid = find_kfid(row["name"])
        if pid is None or pid in drafted_by:
            continue
        april_names[pid] = row["name"]
        p = find_player(row["name"]) or {}
        keeper_cand[tid].append((p.get("adp") or 999, pid))
keepers_by_team = {tid: [pid for _, pid in sorted(cands)[:4]]
                   for tid, cands in keeper_cand.items()}
kept_by = {pid: tid for tid, pids in keepers_by_team.items() for pid in pids}
print("keepers per team:", {SHORT[teams[t]]: len(v) for t, v in sorted(keepers_by_team.items())})

keepers = []
for tid, pids in keepers_by_team.items():
    for pid in pids:
        b = banked.get((tid, pid), {"pts": 0, "games": 0})
        pname = player_names.get(pid) or april_names.get(pid) or "?"
        p = find_player(pname) or {}
        keepers.append({
            "team_id": tid, "player": pname, "kf_id": pid,
            "banked": b["pts"], "games": b["games"],
            "avg": p.get("avg_pts") or 0,
        })
keepers.sort(key=lambda k: -k["banked"])

acq = []
acq_team_totals = defaultdict(int)
for (tid, pid), b in banked.items():
    if drafted_by.get(pid) == tid or kept_by.get(pid) == tid:
        continue
    origin = kept_by.get(pid, drafted_by.get(pid))
    acq.append({
        "team_id": tid, "player": player_names[pid], "kf_id": pid,
        "pts": b["pts"], "games": b["games"],
        "avg": round(b["pts"] / b["games"], 1),
        "from": teams.get(origin),
    })
    acq_team_totals[tid] += b["pts"]
acq.sort(key=lambda a: -a["pts"])

# ── league-wide over/under performers vs ADP ─────────────────────────────────
adp_pool = [p for p in players if p.get("adp") and p.get("avg_pts")]
adp_pool.sort(key=lambda p: p["adp"])
adp_arr = np.array([p["adp"] for p in adp_pool])
avg_arr = np.array([p["avg_pts"] if (p.get("games") or 0) >= 3 else 0.0 for p in adp_pool])
exp_adp = np.array([np.median(avg_arr[max(0, i - 10): i + 11]) for i in range(len(adp_pool))])
exp_adp = np.minimum.accumulate(np.maximum.accumulate(exp_adp[::-1])[::-1])
perf = []
for p, e in zip(adp_pool, exp_adp):
    if p["adp"] > 220:
        continue
    delivered = p["avg_pts"] if (p.get("games") or 0) >= 3 else 0.0
    perf.append({"name": p["name"], "team": p["team"], "adp": p["adp"],
                 "avg": p["avg_pts"], "delivered": round(delivered, 1),
                 "exp": round(float(e), 1),
                 "resid": round(delivered - float(e), 1),
                 "games": p.get("games") or 0, "owner": p.get("owner")})
over = sorted([x for x in perf if x["games"] >= 8], key=lambda x: -x["resid"])[:12]
under = sorted(perf, key=lambda x: x["resid"])[:12]

# ── best & worst players / Team of the Year ──────────────────────────────────
elig = [p for p in players if (p.get("games") or 0) >= 10 and p.get("avg_pts")]
top_avg = sorted(elig, key=lambda p: -p["avg_pts"])[:15]
top_banked = sorted(
    [{"name": player_names[pid], "kf_id": pid, **v,
      "avg": round(v["pts"] / v["games"], 1)} for pid, v in player_total.items()],
    key=lambda x: -x["pts"])[:15]

FORMATION = {"DEF": 3, "MID": 5, "RUC": 1, "FWD": 3}
def pick_team(pool, key):
    slots = dict(FORMATION)
    chosen = []
    for p in sorted(pool, key=key):
        poss = [x for x in p["positions"] if slots.get(x, 0) > 0]
        if not poss:
            continue
        # prefer scarcer slot
        pos = min(poss, key=lambda x: slots[x])
        slots[pos] -= 1
        chosen.append({**{k: p[k] for k in ("name", "team", "positions", "owner", "games")},
                       "avg_pts": p.get("avg_pts") or 0.0, "slot": pos})
        if sum(slots.values()) == 0:
            break
    return chosen

toty = pick_team(elig, key=lambda p: -p["avg_pts"])

# flop XII: drafted in first 10 rounds, worst resid
flop_pool = []
for r_ in board:
    if r_["overall_pick"] > 140 or not r_["pj"]:
        continue
    flop_pool.append({**r_["pj"], "resid": r_["resid"], "pick": r_["overall_pick"]})
flops = pick_team(flop_pool, key=lambda p: p["resid"])
for fl in flops:
    src = next(x for x in flop_pool if x["name"] == fl["name"])
    fl["resid"] = src["resid"]
    fl["pick"] = src["pick"]

# ── assemble ─────────────────────────────────────────────────────────────────
out = {
    "meta": {
        "n_rounds": n_rounds,
        "csv_date": "2026-08-05",
        "league_weekly_mean": round(league_weekly_mean, 1),
        "n_sims": N_SIMS,
    },
    "teams": [
        {"id": tid, "name": teams[tid], "short": SHORT[teams[tid]],
         "manager": managers.get(tid, ""), "hna_rank": hna_rank[tid],
         "placement": placement[tid],
         "w": standings[tid]["w"], "l": standings[tid]["l"], "d": standings[tid]["d"],
         "pf": standings[tid]["pf"], "pa": standings[tid]["pa"],
         "avg": standings[tid]["avg"], "pts": standings[tid]["pts"],
         "race": race[tid],
         "weekly_scores": [int(x) for x in S[idx[tid]]],
         "draft": draft_teams[tid],
         "acq_pts": acq_team_totals.get(tid, 0),
         "streak": next(s for s in streaks if s["team_id"] == tid),
         }
        for tid in ids
    ],
    "finals_results": finals_results,
    "luck": luck,
    "records": records,
    "weekly_top_counts": dict(weekly_top_counts),
    "best_rounds": best_rounds[:12],
    "weekly_top_player": weekly_top_player,
    "draft_board": [
        {k: r[k] for k in ("overall_pick", "round", "team_id", "player_name",
                            "position", "season_avg", "games", "exp_avg", "resid",
                            "banked_for_drafter", "banked_games")}
        for r in board
    ],
    "steals": [{k: r[k] for k in ("overall_pick", "team_id", "player_name",
                                   "season_avg", "exp_avg", "resid", "games")} for r in steals],
    "busts": [{k: r[k] for k in ("overall_pick", "team_id", "player_name",
                                  "season_avg", "exp_avg", "resid", "games")} for r in busts_all],
    "acquisitions": acq[:20],
    "keepers": keepers,
    "over": over,
    "under": under,
    "top_avg": [{k: p[k] for k in ("name", "team", "positions", "owner", "avg_pts",
                                    "games", "total_pts")} for p in top_avg],
    "top_banked": top_banked,
    "toty": toty,
    "flops": flops,
}
out_path = REPO / "docs" / "data" / "season_review.json"
out_path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(f"wrote {out_path.relative_to(REPO)}")
print("\nfinal placements:")
for tid, place in sorted(placement.items(), key=lambda kv: kv[1]):
    print(f"  {place:>2}. {teams[tid]}  (H&A {hna_rank[tid]})")
