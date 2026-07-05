#!/usr/bin/env python3
"""Cookie Cup 2026 — ladder luck analysis via random-schedule simulation.

Null model: every team's weekly scores are exactly what they were; only the
schedule (who plays whom each round) is re-drawn uniformly at random. Any
difference between a team's actual ladder position and its simulated rank
distribution is pure fixture luck, not performance.

Ladder rules matched to keeperfantasy: 4 pts win / 2 draw / 0 loss,
ranked by PTS then raw PF (confirmed against the live ladder tiebreaks).
"""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({
    "font.family": "serif",
    "xtick.minor.visible": True,
    "ytick.minor.visible": True,
    "xtick.top": True,
    "ytick.right": True,
    "xtick.direction": "in",
    "ytick.direction": "in",
})

ROOT = Path(__file__).parent
DATA = ROOT / "docs" / "data"
PLOTS = ROOT / "plots"
PLOTS.mkdir(exist_ok=True)

N_SIMS = 20_000
RNG = np.random.default_rng(2026)

SPOTLIGHT = "Lachie’s Flesh Clarrynet"


def short(name: str, n: int = 24) -> str:
    name = str(name)
    return name if len(name) <= n else name[: n - 1] + "…"


def load():
    rounds = json.loads((DATA / "round_scores.json").read_text())["rounds"]
    rosters = json.loads((DATA / "rosters.json").read_text())

    id_to_name = {int(t["id"]): t["name"] for t in rosters["teams"]}
    ids = sorted(id_to_name)
    idx = {tid: i for i, tid in enumerate(ids)}
    names = [id_to_name[tid] for tid in ids]
    n_teams, n_rounds = len(ids), len(rounds)

    S = np.zeros((n_teams, n_rounds))          # actual weekly scores
    opp = np.zeros((n_teams, n_rounds), int)   # actual opponent index
    for r, rnd in enumerate(rounds):
        for m in rnd["matchups"]:
            i = idx[int(m["team1"]["league_team_id"])]
            j = idx[int(m["team2"]["league_team_id"])]
            S[i, r] = m["team1"]["score"]
            S[j, r] = m["team2"]["score"]
            opp[i, r], opp[j, r] = j, i

    standings = {int(s["team_id"]): s for s in rosters["standings"]}
    actual = [standings[tid] for tid in ids]
    return names, S, opp, actual


def simulate(S):
    """Random perfect matching each round, N_SIMS times.

    Returns (pts, wins): each (N_SIMS, n_teams) — ladder points and win counts.
    """
    n_teams, n_rounds = S.shape
    pts = np.zeros((N_SIMS, n_teams))
    wins = np.zeros((N_SIMS, n_teams))
    rows = np.arange(N_SIMS)
    for r in range(n_rounds):
        # shuffle-and-pair-adjacent = uniform random perfect matching
        perm = np.argsort(RNG.random((N_SIMS, n_teams)), axis=1)
        for k in range(n_teams // 2):
            a, b = perm[:, 2 * k], perm[:, 2 * k + 1]
            sa, sb = S[a, r], S[b, r]
            pts[rows, a] += 4 * (sa > sb) + 2 * (sa == sb)
            pts[rows, b] += 4 * (sb > sa) + 2 * (sa == sb)
            wins[rows, a] += (sa > sb)
            wins[rows, b] += (sb > sa)
    return pts, wins


def rank_teams(pts, pf):
    """Rank (1 = top) by PTS desc, then PF desc — PF is schedule-invariant."""
    key = pts * 1e6 + pf[None, :]
    order = np.argsort(-key, axis=1, kind="stable")
    ranks = np.empty_like(order)
    n_sims, n_teams = pts.shape
    ranks[np.arange(n_sims)[:, None], order] = np.arange(1, n_teams + 1)[None, :]
    return ranks


def allplay_win_probs(S, i):
    """Per-round P(win) for team i vs a uniformly random opponent."""
    n_teams, n_rounds = S.shape
    return np.array([(S[:, r] < S[i, r]).sum() / (n_teams - 1)
                     for r in range(n_rounds)])


def poisson_binomial_pmf(p):
    """Exact PMF of a sum of independent Bernoulli(p_r) via DP."""
    pmf = np.array([1.0])
    for pr in p:
        pmf = np.append(pmf * (1 - pr), 0) + np.append(0, pmf * pr)
    return pmf


def plot_rank_heatmap(names, rank_prob, actual, n_rounds):
    order = np.argsort([a["rank"] for a in actual])
    n = len(names)
    mat = rank_prob[order]

    fig, ax = plt.subplots(figsize=(12, 9))
    im = ax.imshow(mat, cmap=plt.cm.viridis, vmin=0, vmax=mat.max(), aspect="auto")
    ax.set_xticks(range(n))
    ax.set_xticklabels(range(1, n + 1))
    ax.set_yticks(range(n))
    ax.set_yticklabels([f"{actual[t]['rank']}. {short(names[t])}" for t in order])
    thresh = mat.max() * 0.55
    for row, t in enumerate(order):
        for col in range(n):
            v = mat[row, col]
            if v >= 0.005:
                ax.text(col, row, f"{v * 100:.0f}", ha="center", va="center",
                        fontsize=8, color="white" if v < thresh else "black")
        ar = actual[t]["rank"] - 1
        ax.add_patch(plt.Rectangle((ar - 0.5, row - 0.5), 1, 1, fill=False,
                                   edgecolor="red", lw=2))
    cbar = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cbar.set_label("P(ladder position)")
    ax.set_xlabel("Ladder position after 17 rounds")
    ax.set_title(f"Ladder-position probability under {N_SIMS:,} random schedules\n"
                 f"Actual weekly scores fixed, opponents re-drawn each round;  "
                 f"cell = % of sims;  red box = actual position",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(PLOTS / "ladder_01_rank_heatmap.png", dpi=140)
    plt.close(fig)


def plot_prob_current(names, rank_prob, actual):
    n = len(names)
    p_actual = np.array([rank_prob[t, actual[t]["rank"] - 1] for t in range(n)])
    mode = rank_prob.argmax(axis=1) + 1
    order = np.argsort(p_actual)

    cmap = plt.cm.RdYlGn
    vals = p_actual[order]
    norm = (vals - vals.min()) / (vals.max() - vals.min()) if vals.max() > vals.min() else np.full_like(vals, 0.5)

    fig, ax = plt.subplots(figsize=(13, 7))
    labels = [f"{actual[t]['rank']}. {short(names[t])}" for t in order]
    bars = ax.barh(labels, vals * 100, color=cmap(norm),
                   edgecolor="black", linewidth=0.5)
    ax.bar_label(bars, labels=[
        f"{v * 100:.0f}%  (most likely: {mode[t]}{'‡' if mode[t] == actual[t]['rank'] else ''})"
        for v, t in zip(vals, order)], padding=4, fontsize=9)
    ax.margins(x=0.18)
    ax.set_xlabel("P(sitting at actual ladder position) under random schedules (%)")
    ax.set_title("How probable is each team's actual ladder position?\n"
                 "Label = P(actual position) and the simulation-mode position;  ‡ = reality matches the mode",
                 fontsize=12)
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(PLOTS / "ladder_02_prob_current.png", dpi=140)
    plt.close(fig)


def plot_expected_points(names, pts, actual):
    n = len(names)
    order = np.argsort([-a["rank"] for a in actual])  # rank 1 at top
    mean_pts = pts.mean(axis=0)
    lo = np.percentile(pts, 5, axis=0)
    hi = np.percentile(pts, 95, axis=0)

    fig, ax = plt.subplots(figsize=(13, 8))
    ys = np.arange(n)
    for y, t in zip(ys, order):
        a = actual[t]
        lucky = a["pts"] > mean_pts[t]
        unlucky = a["pts"] < mean_pts[t]
        color = "#2ca02c" if lucky else ("#d62728" if unlucky else "#555555")
        ax.plot([lo[t], hi[t]], [y, y], color="#999", lw=2, zorder=2)
        ax.scatter([mean_pts[t]], [y], marker="o", s=70, facecolors="white",
                   edgecolors="black", zorder=3,
                   label="expected (mean ± 5–95%)" if y == 0 else None)
        ax.scatter([a["pts"]], [y], marker="D", s=60, color=color, zorder=4,
                   label="actual" if y == 0 else None)
        ax.annotate(f"{a['w']}-{a['l']}-{a['d']}", (a["pts"], y),
                    xytext=(0, 9), textcoords="offset points",
                    ha="center", fontsize=8, color=color)
    ax.set_yticks(ys)
    ax.set_yticklabels([f"{actual[t]['rank']}. {short(names[t])}" for t in order])
    ax.set_xlabel("Ladder points after 17 rounds")
    ax.set_title("Actual ladder points vs random-schedule expectation\n"
                 "Diamond = actual (green = above expectation → lucky, red = below → unlucky);  "
                 "whisker = 5–95% of sims",
                 fontsize=12)
    ax.grid(True, axis="x", alpha=0.3)
    ax.legend(loc="upper left", framealpha=0.9)
    fig.tight_layout()
    fig.savefig(PLOTS / "ladder_03_expected_points.png", dpi=140)
    plt.close(fig)


def plot_spotlight_tail(names, S, wins, actual):
    i = names.index(SPOTLIGHT)
    a = actual[i]
    actual_wins = a["w"]

    p = allplay_win_probs(S, i)
    pmf = poisson_binomial_pmf(p)
    exact_tail = pmf[: actual_wins + 1].sum()
    sim_tail = (wins[:, i] <= actual_wins).mean()

    n_rounds = S.shape[1]
    ks = np.arange(n_rounds + 1)
    sim_pmf = np.array([(wins[:, i] == k).mean() for k in ks])

    fig, ax = plt.subplots(figsize=(12, 7))
    colors = ["#d62728" if k <= actual_wins else "#4878a8" for k in ks]
    ax.bar(ks, sim_pmf, color=colors, edgecolor="black", linewidth=0.5,
           alpha=0.85, label=f"simulated ({N_SIMS:,} random schedules)")
    ax.plot(ks, pmf, "ko-", ms=4, lw=1.2,
            label="exact (Poisson–binomial, all-play win rates)")
    ax.text(0.98, 0.88,
            f"actual: {actual_wins} wins\nP(≤{actual_wins} wins) = "
            f"{exact_tail * 100:.2f}% exact, {sim_tail * 100:.2f}% simulated",
            transform=ax.transAxes, ha="right", va="top",
            fontsize=11, color="#d62728",
            bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="#d62728", alpha=0.9))
    ax.set_xticks(ks)
    ax.set_xlabel("Wins in 17 rounds vs a random opponent each round")
    ax.set_ylabel("Probability")
    mean_wins = float(p.sum())
    ax.set_title(f"{SPOTLIGHT} — how unlucky is {actual_wins}-{a['l']}-{a['d']}?\n"
                 f"League-best PF; expected wins vs random draw = {mean_wins:.1f};  "
                 f"red bars = outcomes at least this bad",
                 fontsize=12)
    ax.legend(loc="upper left", framealpha=0.9)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(PLOTS / "ladder_04_clarrynet_tail.png", dpi=140)
    plt.close(fig)
    return mean_wins, exact_tail, sim_tail


def main():
    names, S, opp, actual = load()
    n_teams, n_rounds = S.shape
    pf = S.sum(axis=1)

    print(f"{n_teams} teams, {n_rounds} completed rounds, {N_SIMS:,} sims")
    pts, wins = simulate(S)
    ranks = rank_teams(pts, pf)

    rank_prob = np.zeros((n_teams, n_teams))
    for t in range(n_teams):
        rank_prob[t] = np.bincount(ranks[:, t], minlength=n_teams + 1)[1:] / N_SIMS

    plot_rank_heatmap(names, rank_prob, actual, n_rounds)
    plot_prob_current(names, rank_prob, actual)
    plot_expected_points(names, pts, actual)
    mean_wins, exact_tail, sim_tail = plot_spotlight_tail(names, S, wins, actual)

    print(f"\n{'team':26s} {'act':>3} {'mode':>4} {'med':>3} {'P(act)':>7} "
          f"{'E[wins]':>7} {'actW':>4} {'P(≤actW)':>8} {'P(≥actW)':>8}")
    for t in np.argsort([a["rank"] for a in actual]):
        a = actual[t]
        med = int(np.median(ranks[:, t]))
        mode = int(rank_prob[t].argmax()) + 1
        p = allplay_win_probs(S, t)
        pmf = poisson_binomial_pmf(p)
        lo_tail = pmf[: a["w"] + 1].sum()
        hi_tail = pmf[a["w"]:].sum()
        print(f"{short(names[t], 26):26s} {a['rank']:>3} {mode:>4} {med:>3} "
              f"{rank_prob[t, a['rank'] - 1] * 100:>6.1f}% {p.sum():>7.2f} "
              f"{a['w']:>4} {lo_tail * 100:>7.2f}% {hi_tail * 100:>7.2f}%")

    print(f"\n{SPOTLIGHT}: expected wins {mean_wins:.2f}, "
          f"P(wins ≤ actual) exact {exact_tail * 100:.2f}% / sim {sim_tail * 100:.2f}%")
    print(f"Wrote 4 plots to {PLOTS}/")


if __name__ == "__main__":
    main()
