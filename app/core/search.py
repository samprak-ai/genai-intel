"""
Shared web search helper — wraps Serper.dev (Google Search API).

All search callers in the codebase use this module instead of calling
Brave/Serper directly, so switching providers is a single-file change.

Serper.dev docs: https://serper.dev/
Cost: $0.30 per 1,000 queries (vs Brave $50/mo cap).
"""

import os
import re
import threading
import requests
from datetime import datetime, date, timezone


SERPER_URL = 'https://google.serper.dev/search'
TIMEOUT = 6


# ---------------------------------------------------------------------------
# Usage tracking — thread-safe counters flushed at end of pipeline run
# ---------------------------------------------------------------------------

_usage_lock = threading.Lock()
_usage: dict[str, int] = {}  # key = "source" label, value = query count


def _track(source: str) -> None:
    """Increment the query counter for a given source."""
    with _usage_lock:
        _usage[source] = _usage.get(source, 0) + 1


def get_usage_stats() -> dict[str, int]:
    """Return a snapshot of current usage counters (does not reset)."""
    with _usage_lock:
        return dict(_usage)


def flush_usage_to_db() -> dict:
    """
    Write accumulated query counts to the `search_api_usage` table in Supabase,
    then reset counters. Returns the flushed stats.

    Table schema:
        usage_date  DATE
        source      TEXT      (e.g. 'attribution', 'trigger_detection')
        query_count INTEGER
        created_at  TIMESTAMPTZ DEFAULT now()
    """
    with _usage_lock:
        snapshot = dict(_usage)
        _usage.clear()

    if not snapshot:
        return {}

    try:
        from supabase import create_client
        sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])
        today = date.today().isoformat()
        rows = [
            {'usage_date': today, 'source': src, 'query_count': cnt}
            for src, cnt in snapshot.items()
        ]
        sb.table('search_api_usage').upsert(
            rows,
            on_conflict='usage_date,source',
        ).execute()
        total = sum(snapshot.values())
        print(f"  📊 Search API usage flushed: {total} queries ({snapshot})")
    except Exception as e:
        print(f"  ⚠️  Failed to flush search usage: {e}")
        # Put counts back so they're not lost
        with _usage_lock:
            for src, cnt in snapshot.items():
                _usage[src] = _usage.get(src, 0) + cnt

    return snapshot


def serper_search(query: str, num: int = 10, source: str = 'other') -> list[dict]:
    """
    Execute a Google search via Serper.dev.

    Returns list of dicts with normalized field names:
        {"title", "url", "snippet", "date"}

    Args:
        query:  Search query string.
        num:    Max results to return.
        source: Label for usage tracking (e.g. 'attribution', 'trigger_detection').
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
        _track(source)
        if resp.status_code != 200:
            return []
        raw_results = resp.json().get('organic', [])
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
