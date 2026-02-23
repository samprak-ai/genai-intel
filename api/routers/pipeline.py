"""
Pipeline router — trigger and monitor pipeline runs
GET  /api/pipeline/runs         — list recent runs
GET  /api/pipeline/runs/{id}   — single run detail + logs
POST /api/pipeline/trigger      — start a new pipeline run in background
GET  /api/pipeline/status       — is a run currently active?
"""

import threading
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel

from api.deps import get_db
from app.core.database import DatabaseClient

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

# Simple in-process run state — tracks the active background run
_active_run: dict = {}


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class TriggerRequest(BaseModel):
    days_back: int = 7
    limit: Optional[int] = None
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/status")
def pipeline_status():
    """Returns whether a pipeline run is currently active and its run_id"""
    return {
        "is_running": bool(_active_run),
        "run_id": _active_run.get("run_id"),
        "started_at": _active_run.get("started_at"),
    }


@router.get("/runs")
def list_runs(
    limit: int = 20,
    db: DatabaseClient = Depends(get_db),
):
    """List recent pipeline runs, newest first"""
    return db.list_weekly_runs(limit=limit)


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    stage: Optional[str] = None,
    level: Optional[str] = None,
    db: DatabaseClient = Depends(get_db),
):
    """Single run detail with structured logs — filterable by stage and level"""
    run = db.get_weekly_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    logs = db.get_logs_for_run(run_id, level=level, stage=stage)
    return {"run": run, "logs": logs}


@router.post("/trigger", status_code=202)
def trigger_pipeline(body: TriggerRequest, background_tasks: BackgroundTasks):
    """
    Start a full weekly pipeline run in a background thread.
    Returns immediately with the run_id — poll /api/pipeline/runs/{run_id}
    or /api/pipeline/status for progress.
    Returns 409 if a run is already in progress.
    """
    if _active_run:
        raise HTTPException(
            status_code=409,
            detail=f"A pipeline run is already in progress (run_id={_active_run.get('run_id')})",
        )

    background_tasks.add_task(
        _run_pipeline_background,
        days_back=body.days_back,
        limit=body.limit,
        dry_run=body.dry_run,
    )

    return {"message": "Pipeline run started", "status": "accepted"}


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

def _run_pipeline_background(days_back: int, limit: Optional[int], dry_run: bool):
    """Runs the full pipeline in a background thread and tracks active state"""
    from datetime import datetime
    from pipeline import Pipeline

    _active_run["started_at"] = datetime.now().isoformat()

    try:
        p = Pipeline(dry_run=dry_run)
        run = p.run_weekly(days_back=days_back, limit=limit)
        _active_run["run_id"] = run.id
    finally:
        _active_run.clear()
