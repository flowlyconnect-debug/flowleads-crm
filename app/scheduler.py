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
            from app.extensions import db

            logger.info("Scheduled backup starting")
            try:
                create_backup(triggered_by_user_id=None)
                logger.info("Scheduled backup completed")
            except BackupServiceError as exc:
                logger.error("Scheduled backup failed: %s", exc.message)
            except Exception:
                db.session.rollback()
                logger.exception("Scheduled backup crashed")

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

    def run_lead_digest():
        with app.app_context():
            from app.extensions import db
            from app.streams.services import LeadDigestService

            try:
                sent = LeadDigestService.send_due_digests()
                if sent:
                    logger.info("Sent new-leads digest to %s organization(s)", sent)
            except Exception:
                db.session.rollback()
                logger.exception("Lead digest job failed")

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

    def run_listing_matching():
        """Rebuild matches for changed profiles. Matches then wait on the
        customer's Kohteet page; only another apartment of a housing company
        that already has a lead is joined to that lead here. Leads are created
        automatically only when LISTING_AUTO_CREATE_LEADS is on."""
        with app.app_context():
            from app.extensions import db
            from app.listings.services import (
                auto_create_leads,
                materialize_pending_matches,
                merge_into_existing_leads,
                rematch_profiles,
            )

            try:
                from app.listings.housing import backfill_company_keys, refresh_scores

                backfill_company_keys()  # listings stored before housing-company keys
                matched = rematch_profiles()
                merge_into_existing_leads()
                refresh_scores()  # timing and freshness change with time
                from app.listings.target_services import notify_saved_searches

                notify_saved_searches()
                created = materialize_pending_matches() if auto_create_leads() else 0
                if matched or created:
                    logger.info("Listing matching: %s new match(es), %s lead(s) created", matched, created)
            except Exception:
                db.session.rollback()
                logger.exception("Listing matching failed")

    def run_geocode_listings():
        """Street-address positions for the Kohteet map (GEOCODING_ENABLED)."""
        with app.app_context():
            from app.extensions import db
            from app.listings.geo import geocode_listings

            try:
                found = geocode_listings()
                if found:
                    logger.info("Geocoded %s listing(s)", found)
            except Exception:
                db.session.rollback()
                logger.exception("Geocoding failed")

    def run_listing_retention():
        """Delete listings that left Oikotie long ago (LISTING_RETENTION_DAYS)."""
        with app.app_context():
            from app.extensions import db
            from app.listings.services import purge_removed_listings

            try:
                removed = purge_removed_listings()
                if removed:
                    logger.info("Listing retention: deleted %s removed listing(s)", removed)
            except Exception:
                db.session.rollback()
                logger.exception("Listing retention failed")

    def run_target_digest():
        """Morning summary of new targets for each customer (Kohteet page)."""
        with app.app_context():
            from app.extensions import db
            from app.listings.target_services import send_target_digests

            try:
                count = send_target_digests()
                if count:
                    logger.info("Target digest: notified %s organization(s)", count)
            except Exception:
                db.session.rollback()
                logger.exception("Target digest failed")

    def run_monthly_report():
        """Last month's results to each customer's admins (1st of the month)."""
        with app.app_context():
            from app.analytics.monthly import send_monthly_reports
            from app.extensions import db

            try:
                count = send_monthly_reports()
                if count:
                    logger.info("Monthly report sent to %s organization(s)", count)
            except Exception:
                db.session.rollback()
                logger.exception("Monthly report failed")

    def run_trial_reminders():
        """Tell admins three days before their free trial ends."""
        with app.app_context():
            from app.billing.services import send_trial_reminders
            from app.extensions import db

            try:
                count = send_trial_reminders()
                if count:
                    logger.info("Trial reminders sent to %s organization(s)", count)
            except Exception:
                db.session.rollback()
                logger.exception("Trial reminders failed")

    def run_catalogue_health_check():
        """Email superadmins when crawling or analysis has stopped."""
        with app.app_context():
            from app.extensions import db
            from app.listings.services import alert_catalogue_problems

            try:
                sent = alert_catalogue_problems()
                if sent:
                    logger.warning("Catalogue health alert sent to %s superadmin(s)", sent)
            except Exception:
                db.session.rollback()
                logger.exception("Catalogue health check failed")

    def run_reap_stuck_search_jobs():
        with app.app_context():
            from app.extensions import db
            from app.search.job_scheduler import reap_stuck_running_jobs

            try:
                result = reap_stuck_running_jobs()
                if result["requeued"] or result["failed"]:
                    logger.info(
                        "Stuck-job reaper: requeued %s, failed %s",
                        result["requeued"],
                        result["failed"],
                    )
            except Exception:
                db.session.rollback()
                logger.exception("Stuck search job reaper failed")

    def run_notify_failed_search_jobs():
        with app.app_context():
            from app.extensions import db
            from app.search.job_scheduler import notify_failed_search_jobs

            try:
                count = notify_failed_search_jobs()
                if count:
                    logger.info("Notified superadmins of %s failed search job(s)", count)
            except Exception:
                db.session.rollback()
                logger.exception("Failed search job notification failed")

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
        run_lead_digest,
        CronTrigger(hour=7, minute=0, timezone="UTC"),
        id="lead_digest",
        replace_existing=True,
    )
    if app.config.get("SEARCH_JOBS_ENABLED"):
        # Legacy per-profile SearchJob pipeline (superseded by the listing
        # catalogue below). Off by default.
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
    scheduler.add_job(
        run_listing_matching,
        IntervalTrigger(minutes=10),
        id="listing_matching",
        replace_existing=True,
    )
    if app.config.get("GEOCODING_ENABLED"):
        scheduler.add_job(
            run_geocode_listings,
            IntervalTrigger(minutes=30),
            id="geocode_listings",
            replace_existing=True,
        )
    scheduler.add_job(
        run_listing_retention,
        CronTrigger(hour=3, minute=30, timezone="UTC"),
        id="listing_retention",
        replace_existing=True,
    )
    scheduler.add_job(
        run_target_digest,
        CronTrigger(hour=7, minute=30, timezone="Europe/Helsinki"),
        id="target_digest",
        replace_existing=True,
    )
    scheduler.add_job(
        run_trial_reminders,
        CronTrigger(hour=9, minute=15, timezone="Europe/Helsinki"),
        id="trial_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        run_monthly_report,
        CronTrigger(day=1, hour=8, minute=0, timezone="Europe/Helsinki"),
        id="monthly_report",
        replace_existing=True,
    )
    scheduler.add_job(
        run_catalogue_health_check,
        IntervalTrigger(hours=1),
        id="catalogue_health_check",
        replace_existing=True,
    )
    if app.config.get("SEARCH_JOBS_ENABLED"):
        # housekeeping of the legacy SearchJob pipeline: pointless (two extra
        # queries every 10-15 minutes) while that pipeline is off
        scheduler.add_job(
            run_reap_stuck_search_jobs,
            IntervalTrigger(minutes=10),
            id="reap_stuck_search_jobs",
            replace_existing=True,
        )
        scheduler.add_job(
            run_notify_failed_search_jobs,
            IntervalTrigger(minutes=15),
            id="notify_failed_search_jobs",
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
