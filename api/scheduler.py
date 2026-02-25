"""
APScheduler — daily pipeline cron
Started/stopped via FastAPI lifespan in main.py.
Also exposes pause/resume for the API layer.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BackgroundScheduler(timezone="UTC")


def _daily_pipeline_job():
    """Runs every day at 01:00 UTC (≈ 5PM PT / 6PM PDT)"""
    from pipeline import Pipeline
    print("[scheduler] Starting daily pipeline run")
    p = Pipeline(dry_run=False)
    p.run_weekly(days_back=3)
    print("[scheduler] Daily pipeline run complete")


scheduler.add_job(
    _daily_pipeline_job,
    CronTrigger(hour=1, minute=0),   # 01:00 UTC = ~5PM PT, after day's announcements are published
    id="daily_pipeline",
    replace_existing=True,
    misfire_grace_time=3600,   # allow up to 1h late start if server was down
)
