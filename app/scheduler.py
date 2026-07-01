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

    def run_automation_no_activity():
        with app.app_context():
            from app.automations.services import AutomationEngine
            from app.extensions import db

            try:
                count = AutomationEngine.run_no_activity_checks()
                db.session.commit()
                if count:
                    logger.info("Ran lead_no_activity automations for %s lead(s)", count)
            except Exception:
                db.session.rollback()
                logger.exception("Automation no-activity job failed")

    def run_automation_task_overdue():
        with app.app_context():
            from app.automations.services import AutomationEngine
            from app.extensions import db

            try:
                count = AutomationEngine.run_task_overdue_checks()
                db.session.commit()
                if count:
                    logger.info("Ran task_overdue automations for %s task(s)", count)
            except Exception:
                db.session.rollback()
                logger.exception("Automation task-overdue job failed")

    def run_webhook_retry_deliveries():
        with app.app_context():
            from app.extensions import db
            from app.webhooks.services import WebhookService

            try:
                count = WebhookService.retry_pending_deliveries()
                db.session.commit()
                if count:
                    logger.info("Retried %s webhook delivery(s)", count)
            except Exception:
                db.session.rollback()
                logger.exception("Webhook retry job failed")

    def run_webhook_task_overdue():
        with app.app_context():
            from app.extensions import db
            from app.webhooks.services import WebhookService

            try:
                count = WebhookService.dispatch_task_overdue_events()
                db.session.commit()
                if count:
                    logger.info("Dispatched %s webhook task.overdue event(s)", count)
            except Exception:
                db.session.rollback()
                logger.exception("Webhook task.overdue job failed")

    def run_monthly_gdpr_retention():
        with app.app_context():
            from app.gdpr.jobs import monthly_gdpr_retention

            try:
                count = monthly_gdpr_retention()
                if count:
                    logger.info("GDPR retention anonymized %s lead(s)", count)
            except Exception:
                logger.exception("Monthly GDPR retention job failed")

    def run_gdpr_export_processor():
        with app.app_context():
            from app.gdpr.jobs import gdpr_export_processor

            try:
                count = gdpr_export_processor()
                if count:
                    logger.info("Processed %s GDPR export request(s)", count)
            except Exception:
                logger.exception("GDPR export processor job failed")

    def run_calendar_sync():
        with app.app_context():
            from app.calendar.services import CalendarService

            try:
                count = CalendarService.run_hourly_sync()
                if count:
                    logger.info("Calendar sync updated %s event(s)", count)
            except Exception:
                logger.exception("Calendar hourly sync job failed")

    def run_proposal_expiry():
        with app.app_context():
            from app.extensions import db
            from app.proposals.services import ProposalService

            try:
                count = ProposalService.expire_old_proposals()
                db.session.commit()
                if count:
                    logger.info("Expired %s proposal(s)", count)
            except Exception:
                db.session.rollback()
                logger.exception("Proposal expiry job failed")

    def run_weekly_predictions():
        with app.app_context():
            from app.analytics.prediction import run_weekly_batch_predictions

            try:
                run_weekly_batch_predictions(app)
            except Exception:
                logger.exception("Weekly prediction batch job failed")

    def run_stream_health_check():
        with app.app_context():
            from app.streams.services import StreamHealthService

            try:
                StreamHealthService.check_all_orgs()
            except Exception:
                logger.exception("Stream health check job failed")

    def run_lead_health_check():
        with app.app_context():
            from app.streams.services import LeadHealthService

            try:
                LeadHealthService.check_all_orgs()
            except Exception:
                logger.exception("Lead health check job failed")

    def run_create_daily_search_jobs():
        with app.app_context():
            from app.extensions import db
            from app.search.job_scheduler import create_daily_search_jobs

            try:
                created = create_daily_search_jobs()
                if created:
                    logger.info("Created %s daily search job(s)", created)
            except Exception:
                db.session.rollback()
                logger.exception("Daily search job creation failed")

    def run_create_weekly_search_jobs():
        with app.app_context():
            from app.extensions import db
            from app.search.job_scheduler import create_weekly_search_jobs

            try:
                created = create_weekly_search_jobs()
                if created:
                    logger.info("Created %s weekly search job(s)", created)
            except Exception:
                db.session.rollback()
                logger.exception("Weekly search job creation failed")

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
    scheduler.add_job(
        run_automation_no_activity,
        CronTrigger(hour=4, minute=0, timezone="UTC"),
        id="automation_no_activity",
        replace_existing=True,
    )
    scheduler.add_job(
        run_automation_task_overdue,
        IntervalTrigger(hours=1),
        id="automation_task_overdue",
        replace_existing=True,
    )
    scheduler.add_job(
        run_webhook_retry_deliveries,
        IntervalTrigger(minutes=5),
        id="webhook_retry_deliveries",
        replace_existing=True,
    )
    scheduler.add_job(
        run_webhook_task_overdue,
        IntervalTrigger(hours=1),
        id="webhook_task_overdue",
        replace_existing=True,
    )
    scheduler.add_job(
        run_monthly_gdpr_retention,
        CronTrigger(day=1, hour=5, minute=0, timezone="UTC"),
        id="monthly_gdpr_retention",
        replace_existing=True,
    )
    scheduler.add_job(
        run_gdpr_export_processor,
        IntervalTrigger(minutes=15),
        id="gdpr_export_processor",
        replace_existing=True,
    )
    scheduler.add_job(
        run_calendar_sync,
        IntervalTrigger(hours=1),
        id="calendar_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        run_proposal_expiry,
        CronTrigger(hour=1, minute=30, timezone="UTC"),
        id="proposal_expiry",
        replace_existing=True,
    )
    scheduler.add_job(
        run_weekly_predictions,
        CronTrigger(day_of_week="sun", hour=23, minute=0, timezone="UTC"),
        id="weekly_predictions",
        replace_existing=True,
    )
    scheduler.add_job(
        run_stream_health_check,
        CronTrigger(hour=8, minute=0, timezone="UTC"),
        id="stream_health_check",
        replace_existing=True,
    )
    scheduler.add_job(
        run_lead_health_check,
        CronTrigger(hour=8, minute=0, timezone="UTC"),
        id="lead_health_check",
        replace_existing=True,
    )
    scheduler.add_job(
        run_create_daily_search_jobs,
        CronTrigger(hour=6, minute=0, timezone="UTC"),
        id="create_search_jobs",
        replace_existing=True,
    )
    scheduler.add_job(
        run_create_weekly_search_jobs,
        CronTrigger(day_of_week="mon", hour=6, minute=0, timezone="UTC"),
        id="create_weekly_search_jobs",
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
    logger.info("Starting scheduler (backups, tasks, segments, sequences, search)")
    scheduler.start()
