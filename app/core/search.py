"""
Shared web search helper — Google News RSS (free, no API key).

All search callers in the codebase use this module instead of querying a
paid search API directly, so switching/updating the source is a one-file
change. Google News RSS is keyless and free.
"""

import os
import re
import threading
import requests
from datetime import datetime, date, timezone


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


def gnews_search(query: str, num: int = 10, source: str = 'other') -> list[dict]:
    """
    Execute a free Google News RSS search (no API key, no cost).

    Returns entries in a normalized shape:
        {"title", "url", "snippet", "date"}

    Google News RSS is free and keyless. It is best for news-style queries
    (leadership, press, product, partnership) and supports limited operators
    like site:url. It does NOT support advanced `site:domain` operators as
    richly as a full web search API.

    Args:
        query:  Search query string.
        num:    Max results to return (RSS may return fewer; we cap to this).
        source: Label for usage tracking (e.g. 'trigger_leadership').
    """
    if not query:
        return []
    try:
        from urllib.parse import quote_plus
        import feedparser
        q = quote_plus(query)
        rss_url = (
            f'https://news.google.com/rss/search?q={q}'
            f'&hl=en-US&gl=US&ceid=US:en'
        )
        resp = requests.get(
            rss_url, timeout=TIMEOUT,
            headers={'User-Agent': 'Mozilla/5.0'}, verify=False,
        )
        if resp.status_code != 200 or not resp.content:
            return []
        feed = feedparser.parse(resp.content)
        results = []
        for entry in feed.entries[:num]:
            link = entry.get('link', '')
            if not link:
                continue
            results.append({
                'title':   entry.get('title', ''),
                'url':     link,
                'snippet': entry.get('summary', ''),
                'date':    entry.get('published', ''),
            })
        _track(source)
        return results
    except Exception:
        return []


def parse_result_age(date_str: str) -> int:
    """
    Parse a Google News RSS (or relative) date string into approximate days ago.

    Handles:
      - Relative: "2 days ago", "3 hours ago", "1 week ago"
      - Absolute: "Jan 15, 2024", "Mar 3, 2025"
      - RFC-822 (Google News): "Thu, 16 Jul 2026 07:00:00 GMT"

    Returns 999 if unparseable.
    """
    if not date_str:
        return 999
    lower = date_str.lower().strip()

    # --- RFC-822 (Google News RSS published date) ---
    if ',' in date_str and 'GMT' in date_str.upper():
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            if dt is None:
                raise ValueError
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - dt
            return max(0, delta.days)
        except Exception:
            return 999

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
    Map a date string → (strength_label, weight, age_label).

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
