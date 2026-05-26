"""APScheduler setup for scheduled backups."""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: BlockingScheduler | None = None


def create_scheduler(app) -> BlockingScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = BlockingScheduler(timezone="UTC")

    def run_daily_backup():
        with app.app_context():
            from app.backups.services import BackupServiceError, create_backup

            try:
                create_backup(triggered_by_user_id=None)
                logger.info("Scheduled backup completed")
            except BackupServiceError as exc:
                logger.error("Scheduled backup failed: %s", exc.message)

    scheduler.add_job(
        run_daily_backup,
        CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="daily_backup",
        replace_existing=True,
    )
    _scheduler = scheduler
    return scheduler


def run_scheduler(app) -> None:
    """Start blocking scheduler (for dedicated process / CLI)."""
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return
    scheduler = create_scheduler(app)
    logger.info("Starting backup scheduler (daily 02:00 UTC)")
    scheduler.start()
