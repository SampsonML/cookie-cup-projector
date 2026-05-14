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

    print(f"Fetching matchup page for round + AFL team map: {base}/matchup")
    r = sess.get(f"{base}/matchup", timeout=30)
    if r.status_code != 200:
        sys.exit(f"matchup page returned {r.status_code} — cookie likely invalid.")
    static = parse_matchup_page(r.text)
    if not static:
        sys.exit("Could not extract matchup data; site markup may have changed.")
    team_map = afl_team_map(static)
    if not team_map:
        print("WARN: AFL team map is empty; positions/teams will be unresolved.")

    round_info = (static.get("sportFixtures") or [{}])[0].get("round", {}) or {}
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

    payload = {
        "league_id": league,
        "league_name": league_meta.get("name"),
        "scoring_type": league_meta.get("scoringType"),
        "round": round_info.get("number"),
        "round_name": round_info.get("name"),
        "has_captains": (league_round.get("numCaptains") or 0) > 0,
        "scoring_formula": static.get("formula"),
        "afl_team_map": {str(k): v for k, v in team_map.items()},
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams": teams_out,
        "sport_fixtures": static.get("sportFixtures") or [],
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
