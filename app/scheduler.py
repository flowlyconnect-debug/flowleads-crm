"""Central APScheduler job registration for FlowLeads."""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler: BlockingScheduler | None = None
_flask_app = None


def configure_scheduler_app(app) -> None:
    """Called from create_app so jobs are bound to the running application."""
    global _flask_app
    _flask_app = app


def register_scheduler_jobs(scheduler: BlockingScheduler, app) -> None:
    """Register all scheduled jobs on the given scheduler instance."""

    def run_daily_backup():
        with app.app_context():
            from app.backups.services import BackupServiceError, create_backup

            try:
                create_backup(triggered_by_user_id=None)
                logger.info("Scheduled backup completed")
            except BackupServiceError as exc:
                logger.error("Scheduled backup failed: %s", exc.message)

    def run_task_reminders():
        with app.app_context():
            from app.tasks.services import TaskService

            try:
                count = TaskService.send_reminders()
                if count:
                    logger.info("Sent %s task reminder(s)", count)
            except Exception:
                logger.exception("Task reminder job failed")

    def run_no_contact_auto_tasks():
        with app.app_context():
            from app.tasks.services import TaskService

            try:
                count = TaskService.run_no_contact_auto_tasks()
                if count:
                    logger.info("Created %s no-contact auto task(s)", count)
            except Exception:
                logger.exception("No-contact auto-task job failed")

    def run_segment_count_refresh():
        with app.app_context():
            from app.extensions import db
            from app.segments.services import SegmentService

            try:
                updated = SegmentService.refresh_counts()
                db.session.commit()
                if updated:
                    logger.info("Refreshed lead_count_cache for %s segment(s)", updated)
            except Exception:
                db.session.rollback()
                logger.exception("Segment count refresh job failed")

    def run_sequence_due_steps():
        with app.app_context():
            from app.sequences.services import SequenceService

            try:
                count = SequenceService.process_due_steps()
                if count:
                    logger.info("Processed %s sequence due step(s)", count)
            except Exception:
                logger.exception("Sequence due steps job failed")

    def run_sequence_segment_match():
        with app.app_context():
            from app.extensions import db
            from app.sequences.services import SequenceService

            try:
                count = SequenceService.process_segment_match_enrollments()
                db.session.commit()
                if count:
                    logger.info("Segment-match enrolled %s lead(s)", count)
            except Exception:
                db.session.rollback()
                logger.exception("Sequence segment match job failed")

    scheduler.add_job(
        run_daily_backup,
        CronTrigger(hour=2, minute=0, timezone="UTC"),
        id="daily_backup",
        replace_existing=True,
    )
    scheduler.add_job(
        run_task_reminders,
        IntervalTrigger(minutes=15),
        id="task_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        run_no_contact_auto_tasks,
        CronTrigger(hour=3, minute=0, timezone="UTC"),
        id="no_contact_auto_tasks",
        replace_existing=True,
    )
    scheduler.add_job(
        run_segment_count_refresh,
        IntervalTrigger(hours=1),
        id="segment_count_refresh",
        replace_existing=True,
    )
    scheduler.add_job(
        run_sequence_due_steps,
        IntervalTrigger(minutes=10),
        id="sequence_due_steps",
        replace_existing=True,
    )
    scheduler.add_job(
        run_sequence_segment_match,
        IntervalTrigger(hours=1),
        id="sequence_segment_match",
        replace_existing=True,
    )


def create_scheduler(app=None) -> BlockingScheduler:
    global _scheduler, _flask_app
    if _scheduler is not None:
        return _scheduler

    app = app or _flask_app
    if app is None:
        raise RuntimeError("Scheduler app not configured. Call create_app() first.")

    scheduler = BlockingScheduler(timezone="UTC")
    register_scheduler_jobs(scheduler, app)
    _scheduler = scheduler
    return scheduler


def run_scheduler(app) -> None:
    """Start blocking scheduler (for dedicated process / CLI)."""
    import os

    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return
    scheduler = create_scheduler(app)
    logger.info("Starting scheduler (backups, tasks, segments, sequences)")
    scheduler.start()
