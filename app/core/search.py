"""
Shared web search helper — wraps Serper.dev (Google Search API).

All search callers in the codebase use this module instead of calling
Brave/Serper directly, so switching providers is a single-file change.

Serper.dev docs: https://serper.dev/
Cost: $0.30 per 1,000 queries (vs Brave $50/mo cap).
"""

import os
import re
import requests
from datetime import datetime, timezone


SERPER_URL = 'https://google.serper.dev/search'
TIMEOUT = 6


def serper_search(query: str, num: int = 10) -> list[dict]:
    """
    Execute a Google search via Serper.dev.

    Returns list of dicts with normalized field names:
        {"title", "url", "snippet", "date"}

    Field mapping from Serper raw response:
        link   → url
        snippet → snippet  (unchanged)
        date   → date      (unchanged — may be "2 days ago" or "Jan 15, 2024")
    """
    api_key = os.getenv('SERPER_API_KEY', '')
    if not api_key:
        return []
    try:
        resp = requests.post(
            SERPER_URL,
            json={'q': query, 'num': num},
            headers={
                'X-API-KEY': api_key,
                'Content-Type': 'application/json',
            },
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        raw_results = resp.json().get('organic', [])
        # Normalize field names to match what callers expect
        return [
            {
                'title':   r.get('title', ''),
                'url':     r.get('link', ''),
                'snippet': r.get('snippet', ''),
                'date':    r.get('date', ''),
            }
            for r in raw_results
        ]
    except Exception:
        return []


def parse_result_age(date_str: str) -> int:
    """
    Parse a Serper date string into approximate days ago.

    Handles two formats:
      - Relative: "2 days ago", "3 hours ago", "1 week ago"
      - Absolute: "Jan 15, 2024", "Mar 3, 2025"

    Returns 999 if unparseable.
    """
    if not date_str:
        return 999
    lower = date_str.lower().strip()

    # --- Relative format: "X hours/days/weeks/months/years ago" ---
    num_match = re.search(r'(\d+)', lower)
    num = int(num_match.group(1)) if num_match else 1

    if 'hour' in lower:
        return 0
    elif 'day' in lower and 'ago' in lower:
        return num
    elif 'week' in lower:
        return num * 7
    elif 'month' in lower and 'ago' in lower:
        return num * 30
    elif 'year' in lower and 'ago' in lower:
        return num * 365

    # --- Absolute format: "Jan 15, 2024" or "March 3, 2025" ---
    for fmt in ('%b %d, %Y', '%B %d, %Y', '%b %d %Y', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            delta = datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)
            return max(0, delta.days)
        except ValueError:
            continue

    return 999


def parse_age_to_strength(date_str: str):
    """
    Map a Serper date string → (strength_label, weight, age_label).

    Used by attribution_engine for temporal weighting of partnership signals.
    Returns strings for strength to avoid importing models here — callers
    map to their own SignalStrength enum.

    Returns: (strength: str, weight: float, age_label: str)
        strength is one of 'strong', 'medium', 'weak'
    """
    if not date_str:
        return 'medium', 0.6, 'unknown date'

    days = parse_result_age(date_str)
    if days <= 180:       # ~6 months: hours, days, weeks, months
        return 'strong', 1.0, date_str
    elif days <= 548:     # ~1.5 years
        return 'medium', 0.6, date_str
    else:
        return 'weak', 0.3, date_str
