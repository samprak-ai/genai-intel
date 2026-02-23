"""
APScheduler — weekly pipeline cron
Started/stopped via FastAPI lifespan in main.py.
Also exposes pause/resume for the API layer.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BackgroundScheduler(timezone="UTC")


def _weekly_pipeline_job():
    """Runs every Monday at 06:00 UTC"""
    from pipeline import Pipeline
    print("[scheduler] Starting weekly pipeline run")
    p = Pipeline(dry_run=False)
    p.run_weekly(days_back=7)
    print("[scheduler] Weekly pipeline run complete")


scheduler.add_job(
    _weekly_pipeline_job,
    CronTrigger(day_of_week="mon", hour=6, minute=0),
    id="weekly_pipeline",
    replace_existing=True,
    misfire_grace_time=3600,   # allow up to 1h late start if server was down
)
