"""Probe keeperfantasy.com to discover the page data shape.

Fetches a few league pages using your session cookie, extracts the Next.js
__NEXT_DATA__ JSON blob, and writes everything to scripts/.cache/probe/. Once
you've run this, share what's there and I'll build the real scraper.

Usage:
    1. Copy scripts/config.example.json -> scripts/config.json (just for
       league_id / optional team_url_hint — no cookie goes here).
    2. Paste your raw cookie value into scripts/cookie.txt (gitignored).
    3. python3 scripts/probe_keeperfantasy.py

Getting your cookie (easiest method):
    Log into keeperfantasy.com in your browser. Open DevTools, go to the
    Network tab, click any keeperfantasy.com request, find the "Cookie"
    request header, right-click -> Copy value. Paste that whole string
    (it'll look like "name1=value1; name2=value2; ...") into cookie.txt.
    No quoting, no JSON escaping required.
"""
from __future__ import annotations
import json
import pathlib
import re
import sys
from urllib.parse import urljoin

import requests

REPO = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO / "scripts" / "config.json"
COOKIE_PATH = REPO / "scripts" / "cookie.txt"
CACHE_DIR = REPO / "scripts" / ".cache" / "probe"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit(
            f"Missing {CONFIG_PATH.relative_to(REPO)}. Copy "
            "scripts/config.example.json to scripts/config.json."
        )
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as exc:
        sys.exit(
            f"{CONFIG_PATH.relative_to(REPO)} is not valid JSON: {exc}\n"
            f"Check it with: python3 -m json.tool {CONFIG_PATH.relative_to(REPO)}"
        )

    if not COOKIE_PATH.exists():
        sys.exit(
            f"Missing {COOKIE_PATH.relative_to(REPO)}. Paste your raw "
            "keeperfantasy cookie header value into that file (one line, "
            "no quotes). See the script docstring for how to grab it."
        )
    cookie = COOKIE_PATH.read_text().strip()
    if not cookie:
        sys.exit(f"{COOKIE_PATH.relative_to(REPO)} is empty.")
    cfg["cookie"] = cookie
    return cfg


def make_session(cookie: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Cookie": cookie,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return s


def fetch(session: requests.Session, url: str, label: str):
    print(f"  fetching {url}")
    r = session.get(url, timeout=30, allow_redirects=True)
    out = CACHE_DIR / f"{label}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(r.text)
    print(f"    status={r.status_code} -> wrote {out.relative_to(REPO)} ({len(r.text):,} bytes)")
    if r.status_code != 200:
        print(f"    (non-200; likely login redirect or wrong cookie)")
    return r


def extract_next_data(html: str):
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        print(f"    __NEXT_DATA__ JSON parse failed: {exc}")
        return None


def write_next_data(html: str, label: str):
    data = extract_next_data(html)
    if data is None:
        print(f"    no __NEXT_DATA__ in {label}.html")
        return None
    out = CACHE_DIR / f"{label}.next.json"
    out.write_text(json.dumps(data, indent=2))
    print(f"    extracted __NEXT_DATA__ -> {out.relative_to(REPO)}")
    return data


def summarize(data, label: str):
    """Print top-level keys + a hint of where rosters/teams might live."""
    if not data:
        return
    print(f"  {label} top-level keys: {list(data.keys())}")
    props = data.get("props", {}).get("pageProps", {})
    if props:
        print(f"  {label} pageProps keys: {list(props.keys())[:20]}")


def find_team_urls(data) -> list[str]:
    """Best-effort: walk the JSON looking for things that look like team links."""
    found = set()
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and re.search(r"/afl/\d+/(team|roster|squad)", v):
                    found.add(v)
                stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return sorted(found)


def main():
    cfg = load_config()
    league_id = cfg.get("league_id", "292426")
    base = f"https://keeperfantasy.com/afl/{league_id}"

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sess = make_session(cfg["cookie"])

    # Probe the league pages we know are visible to the logged-in user.
    targets = [
        ("league_home", base + "/"),
        ("ladder", base + "/ladder"),
        ("matchup", base + "/matchup"),
        ("teams", base + "/teams"),
    ]

    if hint := cfg.get("team_url_hint"):
        if hint.startswith("/"):
            hint = urljoin("https://keeperfantasy.com", hint)
        targets.append(("team_sample", hint))

    print(f"Probing {base} (league {league_id})\n")
    candidates = set()
    for label, url in targets:
        r = fetch(sess, url, label)
        nd = write_next_data(r.text, label)
        summarize(nd, label)
        for u in find_team_urls(nd or {}):
            candidates.add(u)
        print()

    if candidates:
        print(f"Possible team/roster URLs discovered: {len(candidates)}")
        for u in sorted(candidates)[:20]:
            print(f"  {u}")
    else:
        print("No team URLs auto-discovered. Check the .next.json files manually — "
              "if you find one team URL, add it to config.json as 'team_url_hint' and re-run.")

    print(f"\nDone. Outputs in {CACHE_DIR.relative_to(REPO)}/")


if __name__ == "__main__":
    main()
