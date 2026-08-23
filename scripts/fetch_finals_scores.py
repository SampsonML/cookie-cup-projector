"""Scrape per-player scores for the finals rounds (22-24).

The regular pipeline (fetch_round_scores.py) stops at min(played) from the
ladder, which only counts home-and-away rounds. Run this after the season
ends; it writes scripts/.cache/finals_scores.json for build_season_review.py.
"""
import json, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_round_scores as frs

sess, cfg = frs.load_session_and_config()
league = str(cfg["league_id"]).strip()
base = f"https://keeperfantasy.com/afl/{league}"

rounds = []
for r in (22, 23, 24):
    matchups = []
    for m in range(1, 8):
        url = f"{base}/matchup?round={r}&m={m}"
        resp = sess.get(url, timeout=30)
        time.sleep(0.4)
        if resp.status_code != 200:
            print(f"  round {r} m{m}: HTTP {resp.status_code} — skipped")
            continue
        sd = frs.static_data(resp.text)
        if not sd or "pointsByPlayerId" not in sd:
            print(f"  round {r} m{m}: no score data — skipped")
            continue
        pts = sd.get("pointsByPlayerId") or {}
        t1 = frs.parse_side(sd.get("team1") or {}, pts)
        t2 = frs.parse_side(sd.get("team2") or {}, pts)
        if not t1.get("league_team_id") and not t2.get("league_team_id"):
            print(f"  round {r} m{m}: empty sides — skipped")
            continue
        matchups.append({"m": m, "team1": t1, "team2": t2})
    print(f"round {r}: {len(matchups)} matchups")
    rounds.append({"round": r, "matchups": matchups})

out = pathlib.Path(__file__).resolve().parent / ".cache" / "finals_scores.json"
out.write_text(json.dumps({"rounds": rounds}, indent=1))
print("wrote", out)
