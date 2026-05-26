"""
MVP in-memory enrichment queue.

This queue is not durable across application restarts and is intended for
development/small deployments only. Use a proper job system (e.g. Celery) for production.
"""

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_queue_instance: "EnrichmentQueue | None" = None
_queue_lock = threading.Lock()


@dataclass
class _EnrichmentJob:
    lead_id: int
    retry_count: int = 0


class EnrichmentQueue:
    def __init__(self):
        self._jobs: queue.Queue[_EnrichmentJob] = queue.Queue()
        self._in_flight: set[int] = set()
        self._lock = threading.Lock()
        self._started = False
        self._app = None
        self._workers: list[threading.Thread] = []

    def start(self, app) -> None:
        if self._started:
            return
        if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
            return

        self._app = app
        max_workers = max(1, int(app.config.get("AI_MAX_CONCURRENT_ENRICHMENTS", 3)))
        for i in range(max_workers):
            worker = threading.Thread(
                target=self._worker_loop,
                name=f"ai-enrichment-worker-{i}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)
        self._started = True
        logger.info("AI enrichment queue started with %s workers.", max_workers)

    def enqueue(self, lead_id: int, *, retry_count: int = 0) -> None:
        with self._lock:
            if lead_id in self._in_flight and retry_count == 0:
                return
            self._in_flight.add(lead_id)
        self._jobs.put(_EnrichmentJob(lead_id=lead_id, retry_count=retry_count))

    def _release(self, lead_id: int) -> None:
        with self._lock:
            self._in_flight.discard(lead_id)

    def _schedule_retry(self, lead_id: int, retry_count: int) -> None:
        delay = int(
            self._app.config.get("AI_ENRICHMENT_RETRY_DELAY_SECONDS", 60)
            if self._app
            else 60
        )

        def _retry():
            time.sleep(delay)
            self.enqueue(lead_id, retry_count=retry_count)

        threading.Thread(target=_retry, name=f"ai-enrichment-retry-{lead_id}", daemon=True).start()

    def _worker_loop(self) -> None:
        from app.ai.services import AIEnrichmentService

        while True:
            job = self._jobs.get()
            release = True
            try:
                if not self._app:
                    continue
                with self._app.app_context():
                    service = AIEnrichmentService()
                    success = service.enrich_lead(job.lead_id, retry_count=job.retry_count)
                    if not success:
                        max_retries = int(
                            self._app.config.get("AI_ENRICHMENT_MAX_RETRIES", 2)
                        )
                        if job.retry_count < max_retries:
                            self._schedule_retry(job.lead_id, job.retry_count + 1)
                            release = False
            except Exception:
                logger.exception(
                    "Unexpected error in enrichment worker for lead %s",
                    job.lead_id,
                )
            finally:
                if release:
                    self._release(job.lead_id)
                self._jobs.task_done()


def get_enrichment_queue() -> EnrichmentQueue:
    global _queue_instance
    with _queue_lock:
        if _queue_instance is None:
            _queue_instance = EnrichmentQueue()
        return _queue_instance


def init_enrichment_queue(app) -> None:
    if not app.config.get("AI_ENRICHMENT_ENABLED"):
        return
    try:
        get_enrichment_queue().start(app)
    except Exception:
        logger.exception("Failed to start AI enrichment queue.")
