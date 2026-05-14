"""Build docs/data/players.json from the latest weekly Cookie Cup CSV export.

Source: data/The_Cookie_Cup_players_<YYYYMMDD>_<HHMM>.csv — uses the keeper
league's actual scoring formula (standard AFL Fantasy Classic), so its `L5`,
`avgPts`, etc. transfer directly to projections.
"""
from __future__ import annotations
import csv
import glob
import json
import pathlib
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
OUT_PATH = REPO / "docs" / "data" / "players.json"
CSV_GLOB = "The_Cookie_Cup_players_*.csv"


def _num(v):
    if v is None or v == "":
        return None
    try:
        n = float(v)
        return n
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def _str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def find_latest_csv() -> pathlib.Path:
    candidates = sorted(DATA_DIR.glob(CSV_GLOB))
    if not candidates:
        raise SystemExit(
            f"No {CSV_GLOB} files found in {DATA_DIR.relative_to(REPO)}. "
            "Export the weekly player stats from keeperfantasy and drop it in data/."
        )
    # Filename-date sort already orders chronologically; mtime is the tiebreaker.
    return max(candidates, key=lambda p: (p.name, p.stat().st_mtime))


def parse_row(row: dict) -> dict | None:
    # Strip BOM-prefixed name key produced by some CSV exports.
    name = _str(row.get("name") or row.get("﻿name"))
    if not name:
        return None
    team = _str(row.get("team"))
    pos_raw = _str(row.get("position")) or ""
    positions = [p.strip() for p in pos_raw.split("/") if p.strip()]
    return {
        "id": f"{name}|{team or ''}",
        "name": name,
        "team": team,
        "positions": positions,
        "owner": _str(row.get("owner")),
        "age": _int(row.get("age")),
        "career_games": _int(row.get("careerGames")),
        "seasons": _int(row.get("seasons")),
        "adp": _num(row.get("adp")),
        "owned_pct": _num(row.get("ownedPct")),
        # Form windows and season aggregates (already in league scoring).
        "l5": _num(row.get("L5")),
        "l3": _num(row.get("L3")),
        "l1": _num(row.get("L1")),
        "avg_pts": _num(row.get("avgPts")),
        "total_pts": _num(row.get("totalPts")),
        "proj_avg": _num(row.get("projAvg")),  # keeperfantasy's own per-game projection
        "games": _int(row.get("games")),
        "tog_pct": _num(row.get("TOG%")),
        # Per-game raw stats — kept for future model variants.
        "per_game": {
            "kicks": _num(row.get("kicks")),
            "handballs": _num(row.get("handballs")),
            "marks": _num(row.get("marks")),
            "hitouts": _num(row.get("hitouts")),
            "tackles": _num(row.get("tackles")),
            "frees_for": _num(row.get("freesFor")),
            "frees_against": _num(row.get("freesAgainst")),
            "goals": _num(row.get("goals")),
            "behinds": _num(row.get("behinds")),
        },
    }


def main():
    src = find_latest_csv()
    players = []
    with src.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rec = parse_row(row)
            if rec:
                players.append(rec)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(src.relative_to(REPO)),
        "scoring": "AFL Fantasy Classic (Cookie Cup formula)",
        "count": len(players),
        "players": players,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"Wrote {len(players)} players to {OUT_PATH.relative_to(REPO)}")
    print(f"Source: {src.relative_to(REPO)}")


if __name__ == "__main__":
    main()
