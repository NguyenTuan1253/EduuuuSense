"""Study plan generation worker task (US 2.16) + item lifecycle (M5-06)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import async_session_factory
from app.providers.embedding_adapter import ProviderNotConfiguredError
from app.providers.gemini_adapter import GeminiAdapter
from app.providers.usage_logger import UsageLogger
from app.repositories.ai_usage_repo import AiUsageRepository
from app.repositories.background_job_repo import BackgroundJobRepository
from app.services.quota_service import QuotaExhaustedError, QuotaService, QuotaUnavailableError
from app.services.study_plan_service import parse_plan_items
from app.worker.celery_app import celery_app
from app.worker.idempotency import TaskIdempotencyRepository, run_guarded
from app.worker.tasks.ai import _outcome_dict, _redis_client


async def run_study_plan_generation(
    job_id: str,
    user_id: str,
    name: str,
    start_date: str,
    end_date: str,
    *,
    plan_code: str = "free",
    plan_repo: Any | None = None,
    job_repo: Any | None = None,
    gemini: Any | None = None,
    quota: Any | None = None,
    usage_logger: Any | None = None,
    guard_repo: Any | None = None,
) -> dict[str, Any]:
    """Generate a study plan with items spread across the requested window."""
    if all(dep is not None for dep in (plan_repo, job_repo, gemini, quota, usage_logger, guard_repo)):
        assert gemini is not None and guard_repo is not None  # narrowing
        outcome = await run_guarded(
            guard_repo,  # type: ignore[arg-type]
            f"study_plan:{user_id}:{start_date}:{end_date}",
            "study_plan_generation",
            lambda: _do_generate(
                job_id, user_id, name, start_date, end_date, plan_code,
                plan_repo, job_repo, gemini, quota, usage_logger,
            ),
        )
        return _outcome_dict(outcome)

    async with async_session_factory() as session:
        plan_repository = plan_repo or __import__(
            "app.repositories.study_plan_repo", fromlist=["StudyPlanRepository"]
        ).StudyPlanRepository(session)
        job_repository = job_repo or BackgroundJobRepository(session)
        gemini_adapter = gemini or GeminiAdapter()
        quota_service = quota or QuotaService(
            redis=_redis_client(),
            quota_repo=__import__(
                "app.repositories.quota_repo", fromlist=["QuotaPolicyRepository"]
            ).QuotaPolicyRepository(session),
        )
        logger = usage_logger or UsageLogger(AiUsageRepository(session))
        session_guard = TaskIdempotencyRepository(session)

        async def work() -> dict[str, Any]:
            return await _do_generate(
                job_id, user_id, name, start_date, end_date, plan_code,
                plan_repository, job_repository, gemini_adapter, quota_service, logger,
            )

        outcome = await run_guarded(session_guard, f"study_plan:{user_id}:{start_date}:{end_date}", "study_plan_generation", work)
        return _outcome_dict(outcome)


async def _do_generate(
    job_id: str,
    user_id: str,
    name: str,
    start_date: str,
    end_date: str,
    plan_code: str,
    plan_repo: Any,
    job_repo: Any,
    gemini: Any,
    quota: Any,
    usage_logger: Any,
) -> dict[str, Any]:
    await job_repo.mark_running(job_id)
    gemini.on_usage = usage_logger.adapter_hook(user_id, "study_plan_generation")
    course_ids = await plan_repo.owned_course_ids(user_id)

    try:
        await quota.reserve(user_id, plan_code)
        course_list = ", ".join(course_ids) if course_ids else "no courses yet"
        instruction = (
            f"Create a weekly study plan '{name}' from {start_date} to {end_date} in Vietnamese. "
            f"The student's course ids: {course_list}. "
            'Return ONLY a JSON array of items: [{"title": "...", "description": "...", '
            '"scheduled_start": "YYYY-MM-DDTHH:MM:SSZ", "scheduled_end": "...", '
            '"course_id": "<one of the ids or null>"}]. Use 3-10 items.'
        )
        result = await gemini.generate(
            GeminiAdapter.build_prompt(instruction, None),
            system="You are EduSense's study planner.",
            usage_context={"user_id": user_id, "feature": "study_plan_generation"},
        )
    except QuotaExhaustedError:
        await job_repo.mark_failed(job_id, "quota_exhausted", "Daily AI quota exceeded.")
        return {"status": "failed", "error_code": "quota_exhausted"}
    except QuotaUnavailableError:
        await job_repo.mark_failed(job_id, "quota_unavailable", "Quota service unavailable.")
        return {"status": "failed", "error_code": "quota_unavailable"}
    except Exception:  # noqa: BLE001 — provider failures refund the reservation
        await quota.refund(user_id)
        await job_repo.mark_failed(job_id, "provider_error", "Generation failed after retries.")
        return {"status": "failed", "error_code": "provider_error"}

    items = parse_plan_items(result.text, course_ids, start_date, end_date)
    if not items:
        await quota.refund(user_id)
        await job_repo.mark_failed(job_id, "invalid_model_output", "Model returned no usable plan items.")
        return {"status": "failed", "error_code": "invalid_model_output"}

    plan = await plan_repo.create_plan_with_items(
        user_id=user_id,
        name=name,
        description=f"AI-generated study plan ({start_date}..{end_date})",
        start_date=start_date,
        end_date=end_date,
        generated_by_ai=True,
        items=items,
    )
    await job_repo.mark_succeeded(
        job_id, {"plan_id": str(plan["id"]), "item_count": plan["item_count"], "ai_generated": True}
    )
    return {"status": "succeeded"}


@celery_app.task(
    name="app.worker.tasks.study_plan.generation_job", max_retries=3, default_retry_delay=60
)
def study_plan_generation_job(
    job_id: str, user_id: str, name: str, start_date: str, end_date: str, plan_code: str
) -> dict[str, Any]:
    try:
        return asyncio.run(
            run_study_plan_generation(
                job_id, user_id, name, start_date, end_date, plan_code=plan_code
            )
        )
    except ProviderNotConfiguredError:
        raise
    except Exception as exc:  # noqa: BLE001 — transient transport errors retry
        raise study_plan_generation_job.retry(exc=exc) from exc


def default_plan_window(start: datetime | None = None, weeks: int = 2) -> tuple[str, str]:
    """Helper for API defaults: a two-week window starting Monday."""
    reference = start or datetime.now(tz=UTC)
    window_start = reference - timedelta(days=reference.weekday())
    return window_start.date().isoformat(), (window_start + timedelta(weeks=weeks)).date().isoformat()
