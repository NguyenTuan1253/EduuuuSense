"""Summarization service (US 2.14): enqueue a long AI job, poll its status.

Long AI work NEVER runs in the request: the API creates a background_jobs row
and enqueues the Celery task; the client polls GET /ai/jobs/{job_id}.
"""

from typing import Any

from app.repositories.background_job_repo import BackgroundJobRepository
from app.repositories.document_repo import DocumentRepository


class DocumentNotFoundError(Exception):
    """Raised when a document does not exist for the requesting owner."""


class SummaryService:
    """Summarization job orchestration for the authenticated owner."""

    def __init__(
        self,
        job_repo: BackgroundJobRepository,
        document_repo: DocumentRepository,
        enqueue: Any | None = None,
    ) -> None:
        self._job_repo = job_repo
        self._document_repo = document_repo
        self._enqueue = enqueue or self._default_enqueue

    @classmethod
    def from_session(cls, session: Any, enqueue: Any | None = None) -> "SummaryService":
        """Compose over one session (enqueue injectable for tests)."""
        return cls(
            job_repo=BackgroundJobRepository(session),
            document_repo=DocumentRepository(session),
            enqueue=enqueue,
        )

    @staticmethod
    def _default_enqueue(job_id: str, user_id: str, document_id: str, plan_code: str) -> None:
        from app.worker.tasks.ai import summarization_job

        # Resolve the user's plan for the worker-side quota gate.
        import asyncio

        async def _plan() -> str:
            from app.core.database import async_session_factory
            from app.services.user_service import UserService

            async with async_session_factory() as session:
                return await UserService.from_session(session).get_current_plan_code(user_id)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            plan = asyncio.run(_plan())
        else:  # pragma: no cover — enqueue always happens outside a loop
            _ = loop
            plan = plan_code
        summarization_job.delay(job_id, user_id, document_id, plan)

    async def request_summary(
        self, user_id: str, document_id: str, plan_code: str = "free"
    ) -> dict[str, Any]:
        """Create (or return the existing) summarization job for the document."""
        document = await self._document_repo.get_document(user_id, document_id)
        if document is None:
            raise DocumentNotFoundError("Document not found.")
        if document["status"] != "embedded":
            raise DocumentValidationError(
                "Document is not ready (status: %s)." % document["status"]
            )
        job = await self._job_repo.enqueue(
            user_id=user_id,
            job_type="summarization",
            idempotency_key=f"summarization:{document_id}",
        )
        if job["status"] == "queued":
            self._enqueue(str(job["id"]), user_id, document_id, plan_code)
        return job

    async def get_job(self, user_id: str, job_id: str) -> dict[str, Any]:
        job = await self._job_repo.get_job(user_id, job_id)
        if job is None:
            raise DocumentNotFoundError("Job not found.")
        return job


class DocumentValidationError(Exception):
    """Raised when the document state cannot be summarized yet."""
