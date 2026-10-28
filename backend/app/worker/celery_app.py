"""Celery worker application (Invariant: long AI jobs never run in requests).

The Worker is the only consumer of queued background work; Celery Beat (see
M5-08) only enqueues. Broker/backend come from settings (Redis). Tests run
with ``task_always_eager`` — no live Redis is required.
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "edusense-worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.worker.tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Retry with backoff for broker hiccups; task-level retries are declared
    # per task with max_retries (AGENTS.md section 9).
    broker_connection_retry_on_startup=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Queue routing: document pipeline vs AI jobs vs emails (extensible).
    task_default_queue="edusense_default",
    task_routes={
        "app.worker.tasks.documents.*": {"queue": "edusense_documents"},
        "app.worker.tasks.ai.*": {"queue": "edusense_ai"},
        "app.worker.tasks.emails.*": {"queue": "edusense_emails"},
    },
)
