"""
Analytics router — reads from Postgres views directly
GET /api/analytics/cloud-distribution
GET /api/analytics/ai-distribution
GET /api/analytics/recent-funding
GET /api/analytics/signal-effectiveness
GET /api/analytics/provider-changes
GET /api/analytics/summary
"""

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
