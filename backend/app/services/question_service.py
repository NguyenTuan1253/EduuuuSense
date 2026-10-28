"""Question bank orchestration (US 2.15)."""

from typing import Any

from app.repositories.background_job_repo import BackgroundJobRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.question_repo import QuestionRepository


class QuestionBankService:
    """Question-bank orchestration for the authenticated owner."""

    def __init__(
        self,
        job_repo: BackgroundJobRepository,
        document_repo: DocumentRepository,
        question_repo: QuestionRepository,
        enqueue: Any | None = None,
    ) -> None:
        self._job_repo = job_repo
        self._document_repo = document_repo
        self._question_repo = question_repo
        self._enqueue = enqueue or self._default_enqueue

    @classmethod
    def from_session(cls, session: Any, enqueue: Any | None = None) -> "QuestionBankService":
        return cls(
            job_repo=BackgroundJobRepository(session),
            document_repo=DocumentRepository(session),
            question_repo=QuestionRepository(session),
            enqueue=enqueue,
        )

    @staticmethod
    def _default_enqueue(job_id: str, user_id: str, document_id: str, count: int, question_type: str, plan_code: str) -> None:
        from app.worker.tasks.questions import question_generation_job

        question_generation_job.delay(job_id, user_id, document_id, count, question_type, plan_code)

    async def request_generation(
        self,
        user_id: str,
        document_id: str,
        *,
        count: int,
        question_type: str,
        plan_code: str = "free",
    ) -> dict[str, Any]:
        document = await self._document_repo.get_document(user_id, document_id)
        if document is None:
            raise FileNotFoundError("Document not found.")
        if document["status"] != "embedded":
            raise ValueError(f"Document is not ready (status: {document['status']}).")
        job = await self._job_repo.enqueue(
            user_id=user_id,
            job_type="question_generation",
            idempotency_key=f"question_generation:{document_id}:{question_type}",
        )
        if job["status"] == "queued":
            self._enqueue(str(job["id"]), user_id, document_id, count, question_type, plan_code)
        return job

    async def list_banks(self, user_id: str) -> list[dict[str, Any]]:
        return await self._question_repo.list_banks(user_id)

    async def get_bank_questions(self, user_id: str, bank_id: str) -> list[dict[str, Any]]:
        return await self._question_repo.get_bank_questions(user_id, bank_id)
