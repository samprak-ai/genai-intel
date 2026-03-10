"""
Analytics router — reads from Postgres views directly
GET /api/analytics/cloud-distribution
GET /api/analytics/ai-distribution
GET /api/analytics/recent-funding
GET /api/analytics/signal-effectiveness
GET /api/analytics/provider-changes
GET /api/analytics/summary
GET /api/analytics/search-usage
"""

from datetime import date, timedelta
from collections import defaultdict
from fastapi import APIRouter, Depends, Query
from api.deps import get_db
from app.core.database import DatabaseClient

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/cloud-distribution")
def cloud_distribution(db: DatabaseClient = Depends(get_db)):
    """Cloud provider distribution across all tracked startups"""
    return db.get_cloud_distribution()


@router.get("/ai-distribution")
def ai_distribution(db: DatabaseClient = Depends(get_db)):
    """AI provider distribution across all tracked startups"""
    return db.get_ai_distribution()


@router.get("/recent-funding")
def recent_funding(
    limit: int = Query(20, ge=1, le=100),
    db: DatabaseClient = Depends(get_db),
):
    """Recent funding events with cloud + AI attribution"""
    return db.get_recent_funding(limit=limit)


@router.get("/signal-effectiveness")
def signal_effectiveness(db: DatabaseClient = Depends(get_db)):
    """Signal source effectiveness — how many signals each source type contributes"""
    return db.get_signal_effectiveness()


@router.get("/provider-changes")
def provider_changes(
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseClient = Depends(get_db),
):
    """Attribution changes over time — detects cloud and AI provider migrations"""
    return db.get_attribution_changes(limit=limit)


@router.get("/summary")
def summary(db: DatabaseClient = Depends(get_db)):
    """
    Dashboard summary KPIs:
    - Total companies tracked
    - Cloud provider counts
    - AI provider counts
    - Vertical distribution
    - Last pipeline run date + status
    """
    cloud_dist = db.get_cloud_distribution()
    ai_dist    = db.get_ai_distribution()
    vertical_dist = db.get_vertical_distribution()
    latest_run = db.get_latest_run()

    # Count startups visible in the dashboard (respects $10M funding filter via view)
    total_result = db.client.table('latest_attributions').select('id', count='exact').execute()
    total = total_result.count or 0

    # Tier 1 count for Engage Now KPI card
    tier_1_result = db.client.table('latest_attributions').select(
        'id', count='exact'
    ).eq('engagement_tier', 1).execute()
    tier_1_count = tier_1_result.count or 0

    # Active trigger count across all companies
    trigger_result = db.client.table('company_triggers').select(
        'id', count='exact'
    ).execute()
    active_trigger_count = trigger_result.count or 0

    return {
        "total_companies": total,
        "cloud_distribution": cloud_dist,
        "ai_distribution": ai_dist,
        "vertical_distribution": vertical_dist,
        "latest_run": latest_run,
        "tier_1_count": tier_1_count,
        "active_trigger_count": active_trigger_count,
    }


@router.get("/search-usage")
def search_usage(
    days: int = Query(30, ge=1, le=90),
    db: DatabaseClient = Depends(get_db),
):
    """
    Daily Serper API query counts by source, for the last N days.
    Used by the dashboard to monitor search API spend.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = (
        db.client.table('search_api_usage')
        .select('usage_date,source,query_count')
        .gte('usage_date', cutoff)
        .order('usage_date')
        .execute()
    ).data or []

    # Pivot rows into {date: {source: count}} for the chart
    by_date: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[str, int] = defaultdict(int)
    for r in rows:
        d = r['usage_date']
        src = r['source']
        cnt = r['query_count']
        by_date[d][src] = cnt
        totals[src] += cnt

    daily = [
        {
            "usage_date": d,
            "attribution": by_date[d].get("attribution", 0),
            "trigger_detection": by_date[d].get("trigger_detection", 0),
            "other": by_date[d].get("other", 0),
        }
        for d in sorted(by_date.keys())
    ]

    total_queries = sum(totals.values())
    return {
        "daily": daily,
        "totals": dict(totals),
        "total_queries": total_queries,
        "estimated_cost_usd": round(total_queries * 0.0003, 2),
    }
