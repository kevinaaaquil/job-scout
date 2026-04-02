#!/usr/bin/env python3
"""
Brave Search API test script. Fill BRAVE_API_KEY, edit QUERIES, then:

  cd job-scout && python test_cse_queries.py

Get your API key at: https://brave.com/search/api/
"""

from __future__ import annotations

import sys

import requests

# --- paste your Brave API key here (do not commit) ---
BRAVE_API_KEY = "BSAJrPrm1t7ChaN-H9N7skaJ3zXafki"
# ------------------------------------------------------

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

# Freshness options: "pd" (past day), "pw" (past week), "pm" (past month), "py" (past year), or None
FRESHNESS = "pw"  # past week

QUERIES = [
    'site:boards.greenhouse.io ("Software Engineer" OR "Backend Engineer") India',
    'site:myworkdayjobs.com ("Software Engineer" OR "Backend Engineer") India',
    'site:lever.co ("Software Engineer" OR "Backend Developer") India',
    'site:jobs.ashbyhq.com ("Software Engineer" OR "Backend Engineer") India',
    'site:jobs.smartrecruiters.com ("Software Engineer" OR "Backend Developer") India',
]


def run_one(q: str, max_results: int = 20) -> int:
    headers = {
        "X-Subscription-Token": BRAVE_API_KEY,
        "Accept": "application/json",
    }
    params = {
        "q": q,
        "count": min(20, max_results),  # Brave max is 20 per request
    }
    if FRESHNESS:
        params["freshness"] = FRESHNESS

    print(f"\n--- query ({len(q)} chars) ---\n{q[:200]}{'...' if len(q) > 200 else ''}")

    r = requests.get(BRAVE_ENDPOINT, headers=headers, params=params, timeout=25)
    print(f"HTTP {r.status_code}")

    if not r.ok:
        try:
            err = r.json()
            print(f"Error: {err}")
        except Exception:
            print(f"Error: {r.text[:500]}")
        return 0

    data = r.json()
    web = data.get("web") or {}
    results = web.get("results") or []

    print(f"results: {len(results)}")
    for i, item in enumerate(results[:5], 1):
        title = item.get("title", "")[:80]
        url = item.get("url", "")
        age = item.get("age", "")
        print(f"  {i}. {title}")
        print(f"     {url}")
        if age:
            print(f"     ({age})")

    if len(results) > 5:
        print(f"  ... +{len(results) - 5} more")

    return len(results)


def main() -> int:
    if not BRAVE_API_KEY.strip():
        print("Set BRAVE_API_KEY at the top of this file.", file=sys.stderr)
        print("Get one at: https://brave.com/search/api/", file=sys.stderr)
        return 1

    total = 0
    for q in QUERIES:
        total += run_one(q.strip())

    print(f"\n=== Total results (first page each): {total} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
