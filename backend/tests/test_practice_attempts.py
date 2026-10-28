"""Unit tests for TASK-M5-04: practice attempts & score tracking (US 2.15)."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.practice import get_practice_service
from app.core.security import create_access_token
from app.main import app
from app.repositories.practice_repo import PracticeRepository
from app.services.grading_service import GradingService
from app.services.practice_service import PracticeService

USER_ID = "p1p1p1p1-p1p1-p1p1-p1p1-p1p1p1p1p1p1"
OTHER_ID = "p2p2p2p2-p2p2-p2p2-p2p2-p2p2p2p2p2p2"
BANK_ID = "p3p3p3p3-p3p3-p3p3-p3p3-p3p3p3p3p3p3"
ATTEMPT_ID = "p4p4p4p4-p4p4-p4p4-p4p4-p4p4p4p4p4p4"
Q_MCQ = "p5p5p5p5-p5p5-p5p5-p5p5-p5p5p5p5p5p5"
Q_ESSAY = "p6p6p6p6-p6p6-p6p6-p6p6-p6p6p6p6p6p6"

QUESTIONS: list[dict[str, Any]] = [
    {
        "id": Q_MCQ,
        "question_type": "multiple_choice",
        "content": "Vector là gì?",
        "options": ["A. Số", "B. Mảng số biểu diễn nghĩa", "C. Ảnh", "D. Âm thanh"],
        "correct_answer": "B",
        "difficulty": "easy",
    },
    {
        "id": Q_ESSAY,
        "question_type": "essay",
        "content": "Trình bày khái niệm pgvector.",
        "options": None,
        "correct_answer": "pgvector là phần mở rộng PostgreSQL cho vector similarity search.",
        "difficulty": "medium",
    },
]


class FakePracticeRepository(PracticeRepository):
    def __init__(self, session: Any = None) -> None:
        self.bank_owner = USER_ID
        self.attempts: dict[str, dict[str, Any]] = {}
        self.results: dict[str, list[dict[str, Any]]] = {}

    async def bank_question_snapshot(self, user_id: str, bank_id: str) -> list[dict[str, Any]] | None:
        if self.bank_owner != user_id:
            return None
        return [dict(q) for q in QUESTIONS]

    async def create_attempt(self, *, user_id: str, bank_id: str, max_score: float) -> dict[str, Any]:
        self.attempts[ATTEMPT_ID] = {
            "id": ATTEMPT_ID,
            "user_id": user_id,
            "question_bank_id": bank_id,
            "status": "in_progress",
            "total_score": None,
            "max_score": max_score,
            "started_at": "2026-10-28T00:00:00+00:00",
            "submitted_at": None,
            "graded_at": None,
        }
        return dict(self.attempts[ATTEMPT_ID])

    async def get_attempt(self, user_id: str, attempt_id: str) -> dict[str, Any] | None:
        row = self.attempts.get(attempt_id)
        if row and row["user_id"] == user_id:
            return dict(row)
        return None

    async def upsert_answer_result(self, record: dict[str, Any]) -> str:
        self.results.setdefault(record["attempt_id"], []).append(dict(record))
        return f"result-{len(self.results[record['attempt_id']])}"

    async def finalize_attempt(self, attempt_id: str, total_score: float) -> bool:
        row = self.attempts.get(attempt_id)
        if not row or row["status"] != "in_progress":
            return False
        row["status"] = "graded"
        row["total_score"] = total_score
        row["submitted_at"] = "2026-10-28T01:00:00+00:00"
        row["graded_at"] = "2026-10-28T01:00:00+00:00"
        return True

    async def list_attempt_results(self, user_id: str, attempt_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.results.get(attempt_id, [])]


class FakeGradingService(GradingService):
    def __init__(self, score: float = 3.0) -> None:
        super().__init__(gemini=object())  # type: ignore[arg-type]
        self.score = score  # bypass the adapter entirely

    async def grade_essay(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "score": self.score,
            "max_score": kwargs["max_score"],
            "feedback": "Trả lời khá.",
            "rationale": "Đúng ý chính.",
            "rubric_breakdown": None,
            "grader_model": "grader-test",
        }


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakePracticeRepository], None]:
    repo = FakePracticeRepository()

    async def override_practice_service() -> PracticeService:
        return PracticeService(practice_repo=repo, grading=FakeGradingService())

    app.dependency_overrides[get_practice_service] = override_practice_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


def auth(user_id: str = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.mark.asyncio
async def test_start_attempt_creates_in_progress_with_split_scores(
    client_and_repo: tuple[AsyncClient, FakePracticeRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.post(
        "/api/v1/practice/attempts", json={"question_bank_id": BANK_ID}, headers=auth()
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["status"] == "in_progress"
    assert int(data["question_count"]) == 2
    assert float(data["per_question_max"]) == 5.0


@pytest.mark.asyncio
async def test_start_attempt_for_foreign_bank_is_404(
    client_and_repo: tuple[AsyncClient, FakePracticeRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.post(
        "/api/v1/practice/attempts", json={"question_bank_id": BANK_ID}, headers=auth(OTHER_ID)
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_submit_grades_mcq_auto_and_essay_via_grader(
    client_and_repo: tuple[AsyncClient, FakePracticeRepository],
) -> None:
    client, repo = client_and_repo
    await client.post("/api/v1/practice/attempts", json={"question_bank_id": BANK_ID}, headers=auth())

    response = await client.post(
        f"/api/v1/practice/attempts/{ATTEMPT_ID}/submit",
        json={
            "answers": [
                {"question_id": Q_MCQ, "selected_option": "B. Mảng số biểu diễn nghĩa"},
                {"question_id": Q_ESSAY, "answer_text": "pgvector hỗ trợ tìm kiếm vector trong PostgreSQL."},
            ]
        },
        headers=auth(),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "graded"
    assert float(data["total_score"]) == 8.0  # 5 (MCQ) + 3 (essay fake)

    mcq = next(r for r in repo.results[ATTEMPT_ID] if r["question_id"] == Q_MCQ)
    assert float(mcq["score"]) == 5.0
    assert mcq["grader_model"] == "auto"
    essay = next(r for r in repo.results[ATTEMPT_ID] if r["question_id"] == Q_ESSAY)
    assert essay["grader_model"] == "grader-test"
    assert essay["reference_answer"].startswith("pgvector")  # stored for review


@pytest.mark.asyncio
async def test_mcq_wrong_answer_scores_zero_with_correct_answer_feedback(
    client_and_repo: tuple[AsyncClient, FakePracticeRepository],
) -> None:
    client, repo = client_and_repo
    await client.post("/api/v1/practice/attempts", json={"question_bank_id": BANK_ID}, headers=auth())

    await client.post(
        f"/api/v1/practice/attempts/{ATTEMPT_ID}/submit",
        json={"answers": [{"question_id": Q_MCQ, "selected_option": "A"}]},
        headers=auth(),
    )

    mcq = repo.results[ATTEMPT_ID][0]
    assert float(mcq["score"]) == 0.0
    assert "Đáp án đúng: B" in str(mcq["feedback"])


@pytest.mark.asyncio
async def test_double_submission_is_409(
    client_and_repo: tuple[AsyncClient, FakePracticeRepository],
) -> None:
    client, _ = client_and_repo
    await client.post("/api/v1/practice/attempts", json={"question_bank_id": BANK_ID}, headers=auth())
    payload = {"answers": [{"question_id": Q_MCQ, "selected_option": "B"}]}

    first = await client.post(f"/api/v1/practice/attempts/{ATTEMPT_ID}/submit", json=payload, headers=auth())
    second = await client.post(f"/api/v1/practice/attempts/{ATTEMPT_ID}/submit", json=payload, headers=auth())

    assert first.status_code == 200
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_submit_with_unknown_question_is_422(
    client_and_repo: tuple[AsyncClient, FakePracticeRepository],
) -> None:
    client, _ = client_and_repo
    await client.post("/api/v1/practice/attempts", json={"question_bank_id": BANK_ID}, headers=auth())

    response = await client.post(
        f"/api/v1/practice/attempts/{ATTEMPT_ID}/submit",
        json={"answers": [{"question_id": "00000000-0000-0000-0000-000000000000", "selected_option": "B"}]},
        headers=auth(),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_attempt_detail_owner_scoped(
    client_and_repo: tuple[AsyncClient, FakePracticeRepository],
) -> None:
    client, _ = client_and_repo
    await client.post("/api/v1/practice/attempts", json={"question_bank_id": BANK_ID}, headers=auth())
    await client.post(
        f"/api/v1/practice/attempts/{ATTEMPT_ID}/submit",
        json={"answers": [{"question_id": Q_MCQ, "selected_option": "B"}]},
        headers=auth(),
    )

    own = await client.get(f"/api/v1/practice/attempts/{ATTEMPT_ID}", headers=auth())
    foreign = await client.get(f"/api/v1/practice/attempts/{ATTEMPT_ID}", headers=auth(OTHER_ID))

    assert own.status_code == 200
    assert len(own.json()["data"]["results"]) == 1
    assert foreign.status_code == 404
