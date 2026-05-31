"""Scrape all 14 keeperfantasy team lineups into docs/data/rosters.json.

Pipeline:
    1. Fetch /afl/<league>/matchup to get the AFL team-id -> 3-letter map and
       the round metadata + scoring formula.
    2. Fetch /afl/<league>/<team_id> for team_id in 1..14 and pull the
       `data-lineup-lineup-value` JSON inlined by the Stimulus controller.
    3. Resolve each player against docs/data/players.json by name + AFL team,
       attaching the Champion Data ID where matched.

Run after `parse_stats.py` (which generates players.json).
"""
from __future__ import annotations
import html as html_lib
import json
import pathlib
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

REPO = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO / "scripts" / "config.json"
COOKIE_PATH = REPO / "scripts" / "cookie.txt"
PLAYERS_PATH = REPO / "docs" / "data" / "players.json"
OUT_PATH = REPO / "docs" / "data" / "rosters.json"

NUM_TEAMS = 14
REQUEST_GAP_SECONDS = 0.4
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# data-lineup-lineup-value="{HTML-escaped JSON}"
ATTR_RE_TEMPLATE = r'{name}="([^"]+)"'
TITLE_RE = re.compile(r"<title>\s*Lineup - (.+?) - Keeper\s*</title>", re.DOTALL)
# The landing /matchup page lazy-loads the current round's league matchups via a
# turbo-frame whose src embeds the round number.
CURRENT_ROUND_RE = re.compile(r"_matchups\?round=(\d+)")


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


def extract_attr_json(html_text: str, attr_name: str):
    pattern = ATTR_RE_TEMPLATE.format(name=re.escape(attr_name))
    m = re.search(pattern, html_text)
    if not m:
        return None
    try:
        return json.loads(html_lib.unescape(m.group(1)))
    except json.JSONDecodeError:
        return None


def parse_matchup_page(html_text):
    data = extract_attr_json(html_text, "data-symfony--ux-react--react-props-value")
    if not data:
        return None
    return data.get("staticData", {})


def fetch_static_data(sess, base):
    """Locate the React `staticData` blob (AFL team-id->abbr map, scoring
    formula, league meta, AFL sport fixtures).

    Keeperfantasy used to inline this on /matchup, but it moved to the per-
    matchup detail pages /matchup?round=N&m=1. That detail React only renders
    for rounds that already have data (results or in-progress), so an upcoming
    round that hasn't started carries no blob. We read the current round from
    the landing page's lazy `_matchups` turbo-frame, then walk detail rounds
    down from there to the most recent one that still carries staticData.

    Returns (current_round, data_round, static). data_round is the round the
    returned staticData actually describes — it can lag current_round in the gap
    between a finished round and the next one starting. Either may be None.
    """
    land = sess.get(f"{base}/matchup", timeout=30)
    if land.status_code != 200:
        sys.exit(f"matchup page returned {land.status_code} — cookie likely invalid.")
    m = CURRENT_ROUND_RE.search(land.text)
    current_round = int(m.group(1)) if m else None
    for rnd in range(current_round or 30, 0, -1):
        resp = sess.get(f"{base}/matchup?round={rnd}&m=1", timeout=30)
        if resp.status_code != 200:
            continue
        static = parse_matchup_page(resp.text)
        if static and static.get("sportFixtures"):
            return current_round, rnd, static
        time.sleep(REQUEST_GAP_SECONDS)
    return current_round, None, None


def parse_team_page(html_text):
    title_m = TITLE_RE.search(html_text)
    name = html_lib.unescape(title_m.group(1).strip()) if title_m else None
    lineup = extract_attr_json(html_text, "data-lineup-lineup-value")
    formation = extract_attr_json(html_text, "data-lineup-formation-value")
    return name, lineup, formation


# Common first-name shortenings seen in keeperfantasy vs DFS Australia data.
NICKNAMES = {
    "matty": "matt", "matt": "matty",
    "ollie": "oliver", "oliver": "ollie",
    "nick": "nicholas", "nicholas": "nick",
    "ed": "edward", "edward": "ed",
    "sam": "samuel", "samuel": "sam",
    "mitch": "mitchell", "mitchell": "mitch",
    "alex": "alexander", "alexander": "alex",
    "ben": "benjamin", "benjamin": "ben",
    "tom": "thomas", "thomas": "tom",
    "will": "william", "william": "will",
    "harry": "harrison", "harrison": "harry",
    "jack": "jackson", "jackson": "jack",
    "jamie": "james", "james": "jamie",
    "dan": "daniel", "daniel": "dan",
    "andy": "andrew", "andrew": "andy",
    "chris": "christopher", "christopher": "chris",
    "brad": "bradley", "bradley": "brad",
    "lachie": "lachlan", "lachlan": "lachie",
    "cam": "cameron", "cameron": "cam",
    "pat": "patrick", "patrick": "pat",
    "nat": "nathan", "nathan": "nat",
    "josh": "joshua", "joshua": "josh",
}


def name_variants(name: str):
    """Yield plausible variants for fuzzy matching: original, swapped first-name nickname,
    and (last name only) for ambiguous-but-distinctive surnames."""
    name = name.strip()
    yield name.lower()
    parts = name.split(maxsplit=1)
    if len(parts) == 2:
        first, rest = parts
        alt = NICKNAMES.get(first.lower())
        if alt:
            yield f"{alt} {rest}".lower()


def afl_team_map(static):
    """KF internal AFL team id -> 3-letter abbr (e.g. 2 -> 'BRL')."""
    out = {}
    for fix in static.get("sportFixtures", []) or []:
        for side in fix.get("teams", []) or []:
            for key in ("team", "opponent"):
                t = side.get(key) or {}
                if "id" in t and "abbr" in t:
                    out[t["id"]] = t["abbr"]
    return out


def parse_ladder(html_text):
    """Pull the current standings from /afl/<league>/ladder.

    Returns a list of {rank, name_cell, played, w, l, d, pf, pa, avg, streak, pts}.
    name_cell is the raw 'Team Owner' string from the cell; team_id resolution
    happens later via matching against scraped team names.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cells) < 11:
            continue
        # Cells: rank, team(+owner), rank, P, W, L, D, PF, PA, Avg, Strk, PTS
        if not re.match(r"^\d+(?:st|nd|rd|th)$", cells[0]):
            continue
        try:
            rows.append({
                "rank": int(re.match(r"(\d+)", cells[0]).group(1)),
                "name_cell": cells[1],
                "played": int(cells[3]),
                "w": int(cells[4]),
                "l": int(cells[5]),
                "d": int(cells[6]),
                "pf": int(cells[7]),
                "pa": int(cells[8]),
                "avg": float(cells[9]),  # site now reports one decimal (e.g. 1066.5)
                "streak": cells[10],
                "pts": int(cells[11]),
            })
        except (ValueError, IndexError, AttributeError):
            continue
    return rows


def parse_matchups(html_text, round_no):
    """Pull all 7 matchups for a round from /_matchups?round=N.

    Returns a list of {round, home_name, home_record, home_score, away_name,
    away_record, away_score}. Scores are 0/0 for unplayed rounds.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    text_blob = html_lib.unescape(soup.get_text(" ", strip=True))
    # Strip the "Matchups Round N" header so it doesn't get vacuumed into the
    # first team name.
    text_blob = re.sub(r"^.*?Round\s+\d+\s+", "", text_blob, count=1)

    # Split by W-L-D record pattern; team names live between records and
    # contain arbitrary chars (hyphens in "Bont-ju", digits in "8-balls").
    # parts ends up alternating: [team1, rec1, " s vs s team2 ", rec2,
    #                             " team3 ", rec3, " s vs s team4 ", rec4, ...]
    parts = re.split(r"(\d+-\d+-\d+)", text_blob)
    matchups = []
    score_pat = re.compile(r"^\s*(\d+)\s+vs\s+(\d+)\s+(.+?)\s*$")
    i = 0
    while i + 3 < len(parts):
        home_name = parts[i].strip()
        home_rec = parts[i + 1]
        m = score_pat.match(parts[i + 2])
        if not m or not home_name:
            i += 2
            continue
        home_score, away_score, away_name = m.group(1), m.group(2), m.group(3).strip()
        away_rec = parts[i + 3]
        matchups.append({
            "round": round_no,
            "home_name": home_name,
            "home_record": home_rec,
            "home_score": int(home_score),
            "away_name": away_name,
            "away_record": away_rec,
            "away_score": int(away_score),
        })
        i += 4
    return matchups


def discover_fixtures(sess, base, current_round, max_round=30):
    """Iterate /_matchups?round=N for each round from 1 to max_round until
    we hit a round with no matchups (i.e., past season end)."""
    fixtures = []
    last_with_data = None
    for r in range(1, max_round + 1):
        url = f"{base}/_matchups?round={r}"
        resp = sess.get(url, timeout=20)
        if resp.status_code != 200:
            continue
        round_matches = parse_matchups(resp.text, r)
        if round_matches:
            fixtures.extend(round_matches)
            last_with_data = r
            print(f"  round {r:>2}: {len(round_matches)} matchups")
        else:
            # No matchups found — likely past end of season
            if last_with_data and r > last_with_data + 1:
                break
        time.sleep(0.2)
    return fixtures, last_with_data


def load_players():
    if not PLAYERS_PATH.exists():
        sys.exit(f"Missing {PLAYERS_PATH.relative_to(REPO)}. Run parse_stats.py first.")
    payload = json.loads(PLAYERS_PATH.read_text())
    players = payload.get("players", [])
    by_name_team = {}
    by_name = {}
    for p in players:
        nk = (p["name"].strip().lower(), (p.get("team") or "").upper())
        by_name_team[nk] = p
        by_name.setdefault(p["name"].strip().lower(), []).append(p)
    return players, by_name_team, by_name


def resolve_player(entry, by_name_team, by_name):
    team = (entry.get("team_afl") or "").upper()
    # 1) exact name + team
    for variant in name_variants(entry["name"]):
        p = by_name_team.get((variant, team))
        if p:
            return p["id"], "ok"
    # 2) name-only match (single candidate) — accept even if team differs
    #    (covers cases where a player changed clubs after the xlsx snapshot)
    for variant in name_variants(entry["name"]):
        candidates = by_name.get(variant, [])
        if len(candidates) == 1:
            return candidates[0]["id"], "fuzzy"
        if len(candidates) > 1:
            # multiple candidates with same name — pick the one matching team if any
            for c in candidates:
                if (c.get("team") or "").upper() == team:
                    return c["id"], "fuzzy"
    return None, "unmatched"


def main():
    sess, cfg = load_session_and_config()
    league = str(cfg.get("league_id") or "").strip()
    if not league:
        sys.exit("config.json missing 'league_id'.")
    base = f"https://keeperfantasy.com/afl/{league}"

    print(f"Locating matchup staticData (round + AFL team map): {base}/matchup")
    current_round, data_round, static = fetch_static_data(sess, base)
    if not static:
        sys.exit("Could not extract matchup staticData; site markup may have changed.")
    # The live/current round (12) can be ahead of the round whose staticData we
    # found (11) in the gap between a finished round and the next one starting.
    live_has_data = data_round == current_round
    print(f"  current round = {current_round}, staticData from round {data_round}"
          f" ({'live' if live_has_data else 'lagging — upcoming round not yet started'})")
    team_map = afl_team_map(static)
    if not team_map:
        print("WARN: AFL team map is empty; positions/teams will be unresolved.")

    static_round = (static.get("sportFixtures") or [{}])[0].get("round", {}) or {}
    round_number = current_round or static_round.get("number")
    round_name = (static_round.get("name") if live_has_data
                  else (f"Round {round_number}" if round_number else None))
    league_meta = static.get("leagueSeason", {}) or {}
    league_round = static.get("leagueRound", {}) or {}

    _, by_name_team, by_name = load_players()

    teams_out = []
    summary = {"ok": 0, "fuzzy": 0, "unmatched": 0, "total": 0}
    print(f"\nFetching {NUM_TEAMS} team lineups…")
    for tid in range(1, NUM_TEAMS + 1):
        url = f"{base}/{tid}"
        r = sess.get(url, timeout=30)
        if r.status_code != 200:
            print(f"  team {tid:>2}: HTTP {r.status_code} — skipping")
            time.sleep(REQUEST_GAP_SECONDS)
            continue

        name, lineup, formation = parse_team_page(r.text)
        if not lineup:
            print(f"  team {tid:>2}: no lineup JSON found — skipping")
            time.sleep(REQUEST_GAP_SECONDS)
            continue

        roster = []
        for p in lineup.get("players", []) or []:
            afl_abbr = team_map.get(p.get("teamId"))
            entry = {
                "kf_id": p.get("id"),
                "name": p.get("name"),
                "abbr_name": p.get("abbrName"),
                "team_afl": afl_abbr,
                "kf_team_id": p.get("teamId"),
                "positions": p.get("playerPos") or [],
                "selected_pos": p.get("selectedPos"),
                "type": p.get("type"),
                "starting": p.get("type") == "starter",
                "playing_status": p.get("playingStatus"),
                "injured": (p.get("injuryStatus") or "0") != "0",
                "injury_desc": p.get("injuryDesc"),
            }
            cd_id, quality = resolve_player(entry, by_name_team, by_name)
            entry["cd_id"] = cd_id
            entry["match_quality"] = quality
            summary["total"] += 1
            summary[quality] = summary.get(quality, 0) + 1
            roster.append(entry)

        teams_out.append({
            "id": str(tid),
            "name": name or f"Team {tid}",
            "formation": formation,
            "roster": roster,
        })
        starters = sum(1 for e in roster if e["starting"])
        print(f"  team {tid:>2}: {name!r:40s} {starters} starters / {len(roster)} total")
        time.sleep(REQUEST_GAP_SECONDS)

    # ---- Scrape current ladder ----
    print(f"\nFetching ladder: {base}/ladder")
    r = sess.get(f"{base}/ladder", timeout=20)
    standings_raw = parse_ladder(r.text) if r.status_code == 200 else []
    print(f"  parsed {len(standings_raw)} ladder rows")

    # Map team name → keeperfantasy team_id using the scraped team names.
    name_to_id = {t["name"]: t["id"] for t in teams_out if t.get("name")}
    # Build a normalised-name index for fuzzy lookups (lowercase, no punct)
    def norm(s):
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())
    norm_to_id = {norm(k): v for k, v in name_to_id.items()}

    def resolve_team(label: str):
        # Ladder cell is "TeamName Owner..." — try matching longest known team
        # name as a prefix.
        nlabel = norm(label)
        # exact full-string match first
        if nlabel in norm_to_id:
            return norm_to_id[nlabel]
        # prefix match: find the longest known name that nlabel starts with
        candidates = [(n, tid) for n, tid in norm_to_id.items() if nlabel.startswith(n)]
        if candidates:
            return max(candidates, key=lambda x: len(x[0]))[1]
        # substring fallback (matchup parser leaves trailing whitespace)
        candidates = [(n, tid) for n, tid in norm_to_id.items() if n in nlabel or nlabel in n]
        if candidates:
            return max(candidates, key=lambda x: len(x[0]))[1]
        return None

    standings = []
    for row in standings_raw:
        tid = resolve_team(row["name_cell"])
        standings.append({**row, "team_id": tid})

    # ---- Scrape full season fixtures ----
    print(f"\nDiscovering full-season fixtures via /_matchups…")
    fixtures_raw, last_round = discover_fixtures(sess, base, round_number or 1)
    fixtures = []
    for f in fixtures_raw:
        f_resolved = {
            **f,
            "home_team_id": resolve_team(f["home_name"]),
            "away_team_id": resolve_team(f["away_name"]),
        }
        fixtures.append(f_resolved)
    unresolved = [f for f in fixtures if not f["home_team_id"] or not f["away_team_id"]]
    if unresolved:
        print(f"  WARN: {len(unresolved)} fixtures with unresolved team names "
              "(check parse_matchups regex):")
        for f in unresolved[:5]:
            print(f"    r{f['round']}  {f['home_name']!r} vs {f['away_name']!r}")
    print(f"  total fixtures captured: {len(fixtures)} (rounds 1..{last_round})")

    payload = {
        "league_id": league,
        "league_name": league_meta.get("name"),
        "scoring_type": league_meta.get("scoringType"),
        "round": round_number,
        "round_name": round_name,
        "total_rounds": last_round,
        "has_captains": (league_round.get("numCaptains") or 0) > 0,
        "scoring_formula": static.get("formula"),
        "afl_team_map": {str(k): v for k, v in team_map.items()},
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams": teams_out,
        "standings": standings,
        "fixtures": fixtures,
        # Only the live round's AFL games drive in-progress projections. Between
        # rounds the staticData lags, so its (already-finished) fixtures would
        # wrongly mark the upcoming round's teams as "done" — store none then.
        "sport_fixtures": (static.get("sportFixtures") or []) if live_has_data else [],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, separators=(",", ":")))

    print(f"\nWrote {len(teams_out)} teams to {OUT_PATH.relative_to(REPO)}")
    print(f"Player match summary: {summary['ok']} exact, "
          f"{summary['fuzzy']} fuzzy, {summary['unmatched']} unmatched "
          f"(of {summary['total']}).")
    if summary["unmatched"]:
        print("\nUnmatched players (name differences between keeperfantasy and the xlsx):")
        for team in teams_out:
            for e in team["roster"]:
                if e["match_quality"] == "unmatched":
                    print(f"  {team['name']:40s} {e['name']:25s} ({e.get('team_afl') or '?'})")


if __name__ == "__main__":
    main()
