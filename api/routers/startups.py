"""
Startups router
GET  /api/startups              — paginated list with filters
GET  /api/startups/{id}        — full detail + signals + funding events
POST /api/startups              — add company and trigger attribution
PATCH /api/startups/{id}       — update enrichment data
POST /api/startups/{id}/re-attribute — re-run attribution with latest overrides
"""

import threading
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.deps import get_db
from app.core.database import DatabaseClient
from app.models import Startup
from app.attribution.attribution_engine import AttributionEngine

router = APIRouter(prefix="/api/startups", tags=["startups"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class StartupCreate(BaseModel):
    company_name: str
    website: str
    evidence_urls: list[str] = []
    lead_investors: list[str] = []
    founder_background: list[str] = []
    notes: Optional[str] = None


class StartupPatch(BaseModel):
    evidence_urls: Optional[list[str]] = None
    lead_investors: Optional[list[str]] = None
    founder_background: Optional[list[str]] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
def list_startups(
    cloud_provider: Optional[str] = Query(None, description="Filter by cloud provider e.g. AWS"),
    ai_provider: Optional[str] = Query(None, description="Filter by AI provider e.g. OpenAI"),
    search: Optional[str] = Query(None, description="Fuzzy search on company name"),
    date_from: Optional[str] = Query(None, description="Filter snapshot_date >= this date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter snapshot_date <= this date (YYYY-MM-DD)"),
    vertical: Optional[str] = Query(None, description="Filter by vertical classification"),
    cloud_propensity: Optional[str] = Query(None, description="Filter by cloud propensity: High / Medium / Low"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: DatabaseClient = Depends(get_db),
):
    """Paginated company list with optional filters — sourced from latest_attributions view"""
    return db.list_startups(
        cloud_provider=cloud_provider,
        ai_provider=ai_provider,
        search=search,
        date_from=date_from,
        date_to=date_to,
        vertical=vertical,
        cloud_propensity=cloud_propensity,
        page=page,
        per_page=per_page,
    )


@router.get("/{startup_id}")
def get_startup(startup_id: str, db: DatabaseClient = Depends(get_db)):
    """Full startup detail — includes latest snapshot, all signals, funding events, manual override"""
    startup = db.get_startup_by_id(startup_id)
    if not startup:
        raise HTTPException(status_code=404, detail="Startup not found")

    return {
        "startup": startup,
        "snapshot": db.get_latest_snapshot(startup_id),
        "signals": db.get_signals_for_startup(startup_id),
        "funding_events": db.get_funding_events_for_startup(startup_id),
        "manual_override": db.get_manual_override(startup_id),
        "snapshot_history": db.get_snapshot_history(startup_id),
    }


@router.post("", status_code=201)
def create_startup(body: StartupCreate, db: DatabaseClient = Depends(get_db)):
    """
    Add a new company and immediately run attribution.
    Evidence URLs, investors, and founder background are saved as a manual
    override and passed to the attribution engine as Tier 1/3 signals.
    """
    # Upsert startup entity
    startup_row = db.create_startup(Startup(
        canonical_name=body.company_name,
        website=body.website,
    ))
    startup_id = startup_row["id"]

    # Save manual override if enrichment data provided
    if any([body.evidence_urls, body.lead_investors, body.founder_background, body.notes]):
        db.upsert_manual_override(startup_id, {
            "evidence_urls": body.evidence_urls,
            "lead_investors": body.lead_investors,
            "founder_background": body.founder_background,
            "notes": body.notes,
        })

    # Run attribution synchronously (fast enough for single company)
    engine = AttributionEngine()
    cloud_attr, ai_attr = engine.attribute_startup(
        company_name=body.company_name,
        website=body.website,
        lead_investors=body.lead_investors or None,
        founder_background=body.founder_background or None,
        evidence_urls=body.evidence_urls or None,
    )

    # Persist signals
    for attr in [cloud_attr, ai_attr]:
        if attr:
            for signal in attr.signals:
                db.create_signal(startup_id, signal)

    # Persist snapshot
    from pipeline import Pipeline
    p = Pipeline(dry_run=False)
    snapshot = p._build_snapshot(startup_id, cloud_attr, ai_attr)
    db.create_snapshot(snapshot)

    return {
        "startup": startup_row,
        "cloud": cloud_attr.model_dump() if cloud_attr else None,
        "ai": ai_attr.model_dump() if ai_attr else None,
    }


@router.patch("/{startup_id}")
def patch_startup(startup_id: str, body: StartupPatch, db: DatabaseClient = Depends(get_db)):
    """Update manual enrichment data — marks re-attribution as requested"""
    startup = db.get_startup_by_id(startup_id)
    if not startup:
        raise HTTPException(status_code=404, detail="Startup not found")

    override_data = {k: v for k, v in body.model_dump().items() if v is not None}
    override_data["re_attribution_requested"] = True

    updated = db.upsert_manual_override(startup_id, override_data)
    return {"startup_id": startup_id, "override": updated}


@router.post("/{startup_id}/re-attribute")
def re_attribute(startup_id: str, db: DatabaseClient = Depends(get_db)):
    """
    Re-run attribution for a startup using latest manual override data.
    Deletes existing signals and creates a fresh snapshot.
    """
    startup = db.get_startup_by_id(startup_id)
    if not startup:
        raise HTTPException(status_code=404, detail="Startup not found")

    override = db.get_manual_override(startup_id) or {}

    # Delete stale signals
    db.delete_signals_for_startup(startup_id)

    # Run attribution with override enrichment
    engine = AttributionEngine()
    cloud_attr, ai_attr = engine.attribute_startup(
        company_name=startup["canonical_name"],
        website=startup["website"],
        lead_investors=override.get("lead_investors") or None,
        founder_background=override.get("founder_background") or None,
        evidence_urls=override.get("evidence_urls") or None,
    )

    # Persist new signals
    for attr in [cloud_attr, ai_attr]:
        if attr:
            for signal in attr.signals:
                db.create_signal(startup_id, signal)

    # Persist new snapshot
    from pipeline import Pipeline
    p = Pipeline(dry_run=False)
    snapshot = p._build_snapshot(startup_id, cloud_attr, ai_attr)
    db.create_snapshot(snapshot)

    # Mark override as fulfilled
    db.upsert_manual_override(startup_id, {
        "re_attribution_requested": False,
        "re_attributed_at": "now()",
    })

    return {
        "startup_id": startup_id,
        "cloud": cloud_attr.model_dump() if cloud_attr else None,
        "ai": ai_attr.model_dump() if ai_attr else None,
    }
