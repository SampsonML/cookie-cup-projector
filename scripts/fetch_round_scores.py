"""Scrape per-round, per-player actual fantasy scores for completed rounds.

For every completed round, keeperfantasy's per-matchup detail page
    /afl/<league>/matchup?round=<R>&m=<M>      (M = 1..7)
embeds a Symfony UX React props blob whose `staticData` contains:
  - team1 / team2 .lineup.players  -> full lineup with `type` (starter/emg)
  - pointsByPlayerId               -> the ACTUAL points each player scored
  - teamSeason.{leagueTeamId,seasonName} -> which fantasy team fielded them

We reconstruct each team's scoring 12 (type == "starter") and their real
round points, then write docs/data/round_scores.json. This is what the
"Win Contributors" tab (Win Probability Added) is computed from.

Completed rounds = 1 .. min(played) from docs/data/rosters.json standings.

Run after fetch_rosters.py:
    python3 scripts/fetch_round_scores.py
"""
from __future__ import annotations
import html as html_lib
import json
import pathlib
import re
import sys
import time

import requests

REPO = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO / "scripts" / "config.json"
COOKIE_PATH = REPO / "scripts" / "cookie.txt"
ROSTERS_PATH = REPO / "docs" / "data" / "rosters.json"
OUT_PATH = REPO / "docs" / "data" / "round_scores.json"

MATCHUPS_PER_ROUND = 7
REQUEST_GAP_SECONDS = 0.4
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

PROPS_RE = re.compile(
    r'data-symfony--ux-react--react-props-value="([^"]+)"')


def load_session_and_config():
    if not CONFIG_PATH.exists():
        sys.exit(f"Missing {CONFIG_PATH.relative_to(REPO)}. See config.example.json.")
    cfg = json.loads(CONFIG_PATH.read_text())
    if not COOKIE_PATH.exists():
        sys.exit(f"Missing {COOKIE_PATH.relative_to(REPO)}. Paste cookie there.")
    cookie = COOKIE_PATH.read_text().strip()
    if not cookie:
        sys.exit(f"{COOKIE_PATH.relative_to(REPO)} is empty.")
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": UA,
        "Cookie": cookie,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return sess, cfg


def static_data(html_text: str):
    m = PROPS_RE.search(html_text)
    if not m:
        return None
    try:
        return json.loads(html_lib.unescape(m.group(1))).get("staticData")
    except json.JSONDecodeError:
        return None


def completed_through() -> int:
    """Fully completed rounds = the fewest games any team has played."""
    if not ROSTERS_PATH.exists():
        sys.exit("docs/data/rosters.json missing — run fetch_rosters.py first.")
    standings = json.loads(ROSTERS_PATH.read_text()).get("standings") or []
    if not standings:
        sys.exit("No standings in rosters.json — run fetch_rosters.py first.")
    return min(int(s.get("played") or 0) for s in standings)


def parse_side(side: dict, points_by_id: dict) -> dict:
    ts = side.get("teamSeason") or {}
    starters = []
    for p in (side.get("lineup") or {}).get("players") or []:
        if p.get("type") != "starter":
            continue
        pid = p.get("id")
        starters.append({
            "id": pid,
            "name": p.get("name"),
            "pos": p.get("selectedPos"),
            "points": int(points_by_id.get(str(pid), points_by_id.get(pid, 0)) or 0),
        })
    return {
        "league_team_id": ts.get("leagueTeamId"),
        "name": ts.get("seasonName"),
        "score": sum(s["points"] for s in starters),
        "starters": starters,
    }


def main():
    sess, cfg = load_session_and_config()
    league = str(cfg.get("league_id") or "").strip()
    if not league:
        sys.exit("config.json missing league_id.")
    base = f"https://keeperfantasy.com/afl/{league}"

    last = completed_through()
    if last < 1:
        sys.exit("No completed rounds yet.")
    print(f"Scraping actual player scores for rounds 1..{last} "
          f"({MATCHUPS_PER_ROUND} matchups each)…")

    rounds = []
    for r in range(1, last + 1):
        matchups = []
        for m in range(1, MATCHUPS_PER_ROUND + 1):
            url = f"{base}/matchup?round={r}&m={m}"
            resp = sess.get(url, timeout=30)
            time.sleep(REQUEST_GAP_SECONDS)
            if resp.status_code != 200:
                print(f"  round {r:>2} m{m}: HTTP {resp.status_code} — skipped")
                continue
            sd = static_data(resp.text)
            if not sd or "pointsByPlayerId" not in sd:
                print(f"  round {r:>2} m{m}: no score data — skipped")
                continue
            pts = sd.get("pointsByPlayerId") or {}
            t1 = parse_side(sd.get("team1") or {}, pts)
            t2 = parse_side(sd.get("team2") or {}, pts)
            matchups.append({"m": m, "team1": t1, "team2": t2})
        print(f"  round {r:>2}: {len(matchups)} matchups, "
              f"{sum(len(x['team1']['starters']) + len(x['team2']['starters']) for x in matchups)} starter scores")
        rounds.append({"round": r, "matchups": matchups})

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "league_id": league,
        "completed_through": last,
        "rounds": rounds,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2))
    total = sum(len(x["team1"]["starters"]) + len(x["team2"]["starters"])
                for rd in rounds for x in rd["matchups"])
    print(f"\nWrote {OUT_PATH.relative_to(REPO)} — {len(rounds)} rounds, "
          f"{total} starter-round scores.")


if __name__ == "__main__":
    main()
