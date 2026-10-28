"""Celery Beat schedule (US 2.19): reminder scans run on timers only.

Beat NEVER calls task handlers directly — it enqueues through the broker.
Intervals are PROPOSED DEFAULTS pending PO approval (no document specifies
scan cadence): deadline scans hourly, exam scans every 6 hours.
"""

from app.worker.celery_app import celery_app

celery_app.conf.beat_schedule = {
    "deadline-reminder-scan": {
        "task": "app.worker.tasks.emails.deadline_reminder_scan",
        "schedule": 3600.0,  # hourly
    },
    "exam-reminder-scan": {
        "task": "app.worker.tasks.emails.exam_reminder_scan",
        "schedule": 21600.0,  # every 6 hours
    },
}
