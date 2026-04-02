#!/usr/bin/env python3
"""
Test the OpenAI job scoring mechanism.

Usage:
  1. Set OPENAI_API_KEY in the environment
  2. Edit CANDIDATE_PROFILE below as needed
  3. Run: python test_scoring.py

You can also fetch a live job page by editing live_url in main().
"""

from __future__ import annotations

import json
import os
import sys

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

from job_scout import SYSTEM_PROMPT

MODEL = "gpt-4o-mini"

CANDIDATE_PROFILE = """
- 2 years of full-time post-graduation experience in backend/infra engineering
- 2 years of prior contract experience during college (counts as profile depth, not toward YOE filters)
- Primary language: Golang (production systems, internal tools, ABDM healthcare integration)
- Secondary: TypeScript, Node.js, Python
- Strong experience with: PostgreSQL, Redis, MongoDB, Docker, Kubernetes, AWS
- Domain experience: Healthcare tech (ABDM/NHA certified), logistics, crypto/web3 infra
- Built IAM systems, search infrastructure (Typesense), data pipelines (ClickHouse + PeerDB)
- Comfortable with system design, DevOps, CI/CD, performance optimization
- Education: B.Tech CSE, SRM (9.1 CGPA, 2024)
- Location: India (open to Hyderabad, Bangalore, Gurugram, Delhi NCR, Pune, Chennai, Remote)
- Target: Junior to mid-level SDE/Backend roles
- YOE fit: 2 yrs required = perfect | 3 yrs = comfortable applying | 4 yrs = stretch | 5+ = skip
"""


def fetch_job_page(url: str, timeout: int = 15) -> str:
    """Fetch and extract text from a job posting URL."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)[:4000]


def score_job(client: OpenAI, job_text: str) -> dict:
    """Score a job posting against the candidate profile (raw JSON from the model)."""
    user_message = f"CANDIDATE PROFILE:\n{CANDIDATE_PROFILE}\n\nJOB POSTING:\n{job_text}"

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def print_result(job: dict, result: dict) -> None:
    """Pretty print the scoring result."""
    print("\n" + "=" * 70)
    print(f"JOB: {job['title']}")
    print(f"URL: {job['url']}")
    print("-" * 70)
    print(f"SCORE: {result.get('match_score', 0)}/100")
    print()
    bd = result.get("breakdown") or {}
    if isinstance(bd, dict) and bd:
        print("BREAKDOWN:")
        for key in ("experience", "tech_stack", "role_fit", "location"):
            block = bd.get(key)
            if isinstance(block, dict):
                print(f"  • {key}: {block.get('score', '?')}/25 — {block.get('reason', '')}")
        print()
    print("REASONS (if any flat list):")
    for r in result.get("reasons", []) or []:
        print(f"  • {r}")
    if result.get("disqualifiers"):
        print()
        print("DISQUALIFIERS:")
        for d in result["disqualifiers"]:
            print(f"  ✗ {d}")
    print("=" * 70)


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("Set OPENAI_API_KEY in the environment.", file=sys.stderr)
        return 1

    client = OpenAI(api_key=api_key)

    live_url = "https://jobs.ashbyhq.com/bjakcareer/14c43b0c-574a-4d9f-a8be-b17739c8df04"
    print(f"\nFetching {live_url}...")
    try:
        text = fetch_job_page(live_url)
        result = score_job(client, text)
        print_result({"title": "Live Job", "url": live_url}, result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
