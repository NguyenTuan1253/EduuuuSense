"""Unit tests for TASK-M5-02: question bank generation (US 2.15)."""

from typing import Any

import pytest

from app.repositories.background_job_repo import BackgroundJobRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.question_repo import QuestionRepository
from app.worker.tasks.questions import _parse_questions, run_question_generation

USER_ID = "q2q2q2q2-q2q2-q2q2-q2q2-q2q2q2q2q2q2"
DOC_ID = "q3q3q3q3-q3q3-q3q3-q3q3-q3q3q3q3q3q3"
JOB_ID = "job-1"


class FakeJobRepository(BackgroundJobRepository):
    def __init__(self, session: Any = None) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    async def enqueue(self, *, user_id: str, job_type: str, idempotency_key: str, payload: Any = None) -> dict[str, Any]:
        self.jobs[JOB_ID] = {"id": JOB_ID, "user_id": user_id, "job_type": job_type, "idempotency_key": idempotency_key, "status": "queued", "progress": 0, "result": None, "error_code": None, "error_message": None}
        return dict(self.jobs[JOB_ID])

    async def get_job(self, user_id: str, job_id: str) -> dict[str, Any] | None:
        return None

    async def mark_running(self, job_id: str) -> bool:
        self.jobs[job_id]["status"] = "processing"
        return True

    async def mark_succeeded(self, job_id: str, result_payload: dict[str, Any]) -> bool:
        self.jobs[job_id]["status"] = "succeeded"
        self.jobs[job_id]["result"] = result_payload
        return True

    async def mark_failed(self, job_id: str, error_code: str, error_message: str) -> bool:
        self.jobs[job_id]["status"] = "failed"
        self.jobs[job_id]["error_code"] = error_code
        self.jobs[job_id]["error_message"] = error_message
        return True


class FakeDocumentRepository(DocumentRepository):
    def __init__(self, session: Any = None) -> None:
        self.document = {"id": DOC_ID, "user_id": USER_ID, "status": "embedded", "title": "Vectors", "course_id": None}

    async def get_document(self, user_id: str, document_id: str) -> dict[str, Any] | None:
        return dict(self.document) if self.document["user_id"] == user_id else None


class FakeChunkRepository:
    async def fetch_chunk_vectors(self, user_id: str, document_id: str) -> list[dict[str, Any]]:
        return [{"id": "c1", "chunk_index": 0, "content": "Vectors encode meaning.", "page_number": 1}]


class FakeQuestionRepository(QuestionRepository):
    def __init__(self, session: Any = None) -> None:
        self.created: list[dict[str, Any]] = []

    async def create_bank_with_questions(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        return {"id": "bank-1", "question_count": len(kwargs["questions"])}


class FakeGemini:
    def __init__(self, text: str, fail: Exception | None = None) -> None:
        self.text = text
        self.fail = fail
        self.on_usage = None
        self.prompts: list[str] = []

    async def generate(self, prompt: str, *, system: str | None = None, usage_context: dict[str, Any] | None = None) -> Any:
        self.prompts.append(prompt)
        if self.fail:
            raise self.fail
        return type("R", (), {"text": self.text, "model_name": "m", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})()


class FakeQuota:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.reserved = 0
        self.refunded = 0

    async def reserve(self, user_id: str, plan_code: str) -> dict[str, Any]:
        if self.fail:
            raise self.fail
        self.reserved += 1
        return {"allowed": True, "remaining": 1, "quota_warning": False}

    async def refund(self, user_id: str) -> None:
        self.refunded += 1


class RecordingUsageLogger:
    def adapter_hook(self, user_id: str, feature: str) -> Any:
        async def on_usage(payload: dict[str, Any]) -> None:
            return None

        return on_usage


class FakeGuardRepository:
    def __init__(self) -> None:
        self.state = "fresh"

    async def find(self, key: str) -> dict[str, Any] | None:
        return None if self.state == "fresh" else {"state": self.state}

    async def try_begin(self, key: str, task_type: str) -> str | None:
        if self.state == "done":
            return None
        self.state = "in_progress"
        return "row"

    async def mark_done(self, key: str, result: Any = None) -> None:
        self.state = "done"

    async def mark_failed(self, key: str, error_code: Any = None) -> None:
        self.state = "failed"


MCQ_JSON = (
    'Here are the questions:\n'
    '[{"content": "Vector là gì?", "options": ["A. Số", "B. Chuỗi", "C. Mảng số biểu diễn nghĩa", "D. Ảnh"], '
    '"correct_answer": "C", "explanation": "Vectors biểu diễn nghĩa.", "difficulty": "easy"}]'
)


@pytest.mark.asyncio
async def test_mcq_generation_creates_bank_and_succeeds() -> None:
    job_repo, question_repo = FakeJobRepository(), FakeQuestionRepository()
    await job_repo.enqueue(user_id=USER_ID, job_type="question_generation", idempotency_key="k")
    gemini = FakeGemini(MCQ_JSON)

    result = await run_question_generation(
        JOB_ID, USER_ID, DOC_ID,
        document_repo=FakeDocumentRepository(), chunk_repo=FakeChunkRepository(),
        job_repo=job_repo, question_repo=question_repo, gemini=gemini,
        quota=FakeQuota(), usage_logger=RecordingUsageLogger(), guard_repo=FakeGuardRepository(),
    )

    assert result["status"] == "succeeded"
    assert job_repo.jobs[JOB_ID]["result"]["question_count"] == 1
    created = question_repo.created[0]
    assert created["questions"][0]["question_type"] == "multiple_choice"
    assert created["questions"][0]["options"][0] == "A. Số"
    prompt = gemini.prompts[0]
    assert "BEGIN_UNTRUSTED_DATA" in prompt  # evidence fenced


@pytest.mark.asyncio
async def test_essay_generation_has_no_options_requirement() -> None:
    essay_json = '[{"content": "Phân tích vai trò của vectors.", "explanation": "...", "difficulty": "medium"}]'
    job_repo, question_repo = FakeJobRepository(), FakeQuestionRepository()
    await job_repo.enqueue(user_id=USER_ID, job_type="question_generation", idempotency_key="k")

    result = await run_question_generation(
        JOB_ID, USER_ID, DOC_ID, question_type="essay",
        document_repo=FakeDocumentRepository(), chunk_repo=FakeChunkRepository(),
        job_repo=job_repo, question_repo=question_repo, gemini=FakeGemini(essay_json),
        quota=FakeQuota(), usage_logger=RecordingUsageLogger(), guard_repo=FakeGuardRepository(),
    )

    assert result["status"] == "succeeded"
    assert question_repo.created[0]["questions"][0]["question_type"] == "essay"


@pytest.mark.asyncio
async def test_unparseable_model_output_fails_job_and_refunds() -> None:
    job_repo, question_repo = FakeJobRepository(), FakeQuestionRepository()
    await job_repo.enqueue(user_id=USER_ID, job_type="question_generation", idempotency_key="k")
    quota = FakeQuota()

    result = await run_question_generation(
        JOB_ID, USER_ID, DOC_ID,
        document_repo=FakeDocumentRepository(), chunk_repo=FakeChunkRepository(),
        job_repo=job_repo, question_repo=question_repo, gemini=FakeGemini("sorry, no json"),
        quota=quota, usage_logger=RecordingUsageLogger(), guard_repo=FakeGuardRepository(),
    )

    assert result["status"] == "failed"
    assert job_repo.jobs[JOB_ID]["error_code"] == "invalid_model_output"
    assert quota.refunded == 1
    assert question_repo.created == []


@pytest.mark.asyncio
async def test_provider_failure_marks_failed_and_refunds() -> None:
    from app.providers.circuit_breaker import CircuitOpenError

    job_repo, question_repo = FakeJobRepository(), FakeQuestionRepository()
    await job_repo.enqueue(user_id=USER_ID, job_type="question_generation", idempotency_key="k")
    quota = FakeQuota()

    result = await run_question_generation(
        JOB_ID, USER_ID, DOC_ID,
        document_repo=FakeDocumentRepository(), chunk_repo=FakeChunkRepository(),
        job_repo=job_repo, question_repo=question_repo, gemini=FakeGemini("", fail=CircuitOpenError("open")),
        quota=quota, usage_logger=RecordingUsageLogger(), guard_repo=FakeGuardRepository(),
    )

    assert result["status"] == "failed"
    assert job_repo.jobs[JOB_ID]["error_code"] == "provider_error"
    assert quota.refunded == 1


@pytest.mark.asyncio
async def test_quota_exhausted_fails_without_refund() -> None:
    from app.services.quota_service import QuotaExhaustedError

    job_repo, _question_repo = FakeJobRepository(), FakeQuestionRepository()
    await job_repo.enqueue(user_id=USER_ID, job_type="question_generation", idempotency_key="k")
    quota = FakeQuota(fail=QuotaExhaustedError("exceeded"))

    await run_question_generation(
        JOB_ID, USER_ID, DOC_ID,
        document_repo=FakeDocumentRepository(), chunk_repo=FakeChunkRepository(),
        job_repo=job_repo, question_repo=_question_repo, gemini=FakeGemini(MCQ_JSON),
        quota=quota, usage_logger=RecordingUsageLogger(), guard_repo=FakeGuardRepository(),
    )

    assert job_repo.jobs[JOB_ID]["error_code"] == "quota_exhausted"
    assert quota.refunded == 0


def test_parse_questions_drops_malformed_entries() -> None:
    raw = (
        '[{"content": "Good?", "options": ["A. x", "B. y"], "correct_answer": "A"},'
        '{"content": ""},'
        '{"content": "No options"},'
        '"not-a-dict"]'
    )
    parsed = _parse_questions(raw, "multiple_choice")
    assert len(parsed) == 1
    assert parsed[0]["difficulty"] == "medium"  # invalid difficulty defaulted