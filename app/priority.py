"""
Engagement Priority — deterministic tier classification for outreach prioritization.

Tier 1 (Engage Now): Raised ≤90 days ago, High propensity, WEAK/Unknown entrenchment
Tier 2 (Watch):      Raised ≤180 days ago, High or Medium propensity
Tier 3 (Track):      Everything else

No API calls. Computable from existing data in attribution_snapshots + funding_events.

Used by:
  - pipeline.py          (_build_snapshot — calculates on every new snapshot)
  - api/routers/pipeline  (daily recalculation via cron — ages tiers over time)
  - scripts/backfill_engagement_tier.py  (one-off backfill for existing records)
"""

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIER1_MAX_DAYS = 90
TIER2_MAX_DAYS = 180
TIER_LABELS = {1: "Engage Now", 2: "Watch", 3: "Track"}
WEAK_ENTRENCHMENT = {"WEAK", "UNKNOWN", None}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class PriorityResult:
    tier: int                    # 1, 2, or 3
    tier_label: str              # "Engage Now", "Watch", "Track"
    tier_rationale: str          # Human-readable explanation for dashboard tooltip


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def calculate_priority(
    funding_announcement_date: Optional[date] = None,
    cloud_propensity: Optional[str] = None,       # "High" / "Medium" / "Low" / None
    cloud_entrenchment: Optional[str] = None,      # "STRONG" / "MODERATE" / "WEAK" / "UNKNOWN" / None
    today: Optional[date] = None,                  # Injectable for testing
) -> PriorityResult:
    """
    Compute engagement tier from funding recency, cloud propensity, and entrenchment.

    Stateless and deterministic — works with raw values, no DB or API calls.
    The `today` parameter allows deterministic testing.
    """
    today = today or date.today()

    if not funding_announcement_date:
        return PriorityResult(3, "Track", "No funding date available")

    # Parse string dates if needed
    if isinstance(funding_announcement_date, str):
        funding_announcement_date = date.fromisoformat(str(funding_announcement_date))

    days_since = (today - funding_announcement_date).days

    # Tier 1 — Engage Now: active decision window
    if (
        days_since <= TIER1_MAX_DAYS
        and cloud_propensity == "High"
        and cloud_entrenchment in WEAK_ENTRENCHMENT
    ):
        entrenchment_label = cloud_entrenchment or "Unknown"
        rationale = (
            f"Raised {days_since}d ago · High propensity · "
            f"{entrenchment_label} entrenchment — "
            f"infrastructure decision likely in progress"
        )
        return PriorityResult(1, "Engage Now", rationale)

    # Tier 2 — Watch: monitor window
    if (
        days_since <= TIER2_MAX_DAYS
        and cloud_propensity in ("High", "Medium")
    ):
        rationale = (
            f"Raised {days_since}d ago · {cloud_propensity} propensity · "
            f"Monitor for trigger events"
        )
        return PriorityResult(2, "Watch", rationale)

    # Tier 3 — Track: everything else
    rationale = (
        f"Raised {days_since}d ago · {cloud_propensity or 'Unknown'} propensity"
    )
    return PriorityResult(3, "Track", rationale)


# ---------------------------------------------------------------------------
# Bulk recalculation (used by cron + backfill)
# ---------------------------------------------------------------------------

def recalculate_all_priorities(dry_run: bool = False) -> dict:
    """
    Recalculate engagement tiers for ALL companies.

    Queries latest_attributions for all rows, fetches announcement dates
    from funding_events, and updates attribution_snapshots.

    Returns summary dict: {"tier_1": N, "tier_2": N, "tier_3": N, "total": N}
    """
    from supabase import create_client

    sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

    # Fetch all companies from the view
    res = sb.table('latest_attributions').select(
        'id, cloud_propensity, cloud_entrenchment'
    ).execute()

    if not res.data:
        print("  ⚠️  No companies found in latest_attributions")
        return {"tier_1": 0, "tier_2": 0, "tier_3": 0, "total": 0}

    startup_ids = [r['id'] for r in res.data]
    company_map = {r['id']: r for r in res.data}

    # Batch-fetch announcement dates from funding_events
    # (latest announcement per startup)
    announcement_map: dict[str, date] = {}
    for i in range(0, len(startup_ids), 50):
        chunk = startup_ids[i:i + 50]
        fe_res = sb.table('funding_events').select(
            'startup_id, announcement_date'
        ).in_('startup_id', chunk).order(
            'announcement_date', desc=True
        ).execute()

        for row in fe_res.data:
            sid = row['startup_id']
            if sid not in announcement_map and row.get('announcement_date'):
                # First result per startup_id is the latest (ordered desc)
                announcement_map[sid] = row['announcement_date']

    # Calculate and update
    counts = {1: 0, 2: 0, 3: 0}
    updated = 0

    for sid, company in company_map.items():
        ann_date = announcement_map.get(sid)
        result = calculate_priority(
            funding_announcement_date=ann_date,
            cloud_propensity=company.get('cloud_propensity'),
            cloud_entrenchment=company.get('cloud_entrenchment'),
        )
        counts[result.tier] += 1

        if not dry_run:
            sb.table('attribution_snapshots').update({
                'engagement_tier': result.tier,
                'engagement_tier_label': result.tier_label,
                'engagement_tier_rationale': result.tier_rationale,
                'tier_last_calculated': datetime.now(timezone.utc).isoformat(),
            }).eq('startup_id', sid).execute()
            updated += 1

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n  📊 {prefix}Engagement tier recalculation complete:")
    print(f"     Tier 1 (Engage Now): {counts[1]}")
    print(f"     Tier 2 (Watch):      {counts[2]}")
    print(f"     Tier 3 (Track):      {counts[3]}")
    print(f"     Total: {sum(counts.values())}")
    if not dry_run:
        print(f"     Updated: {updated} startups")

    return {
        "tier_1": counts[1],
        "tier_2": counts[2],
        "tier_3": counts[3],
        "total": sum(counts.values()),
    }
