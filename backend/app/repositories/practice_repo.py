"""Practice persistence: SQL for practice_attempts and practice_answer_results."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class PracticeRepository:
    """SQL access for the practice aggregate. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bank_question_snapshot(self, user_id: str, bank_id: str) -> list[dict[str, Any]] | None:
        """Questions of an owned bank (None when the bank is foreign/missing)."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT q.id, q.question_type, q.content, q.options, q.correct_answer, "
                    "q.difficulty "
                    "FROM questions q JOIN question_banks qb ON qb.id = q.question_bank_id "
                    "WHERE q.question_bank_id = :bank_id AND qb.user_id = :user_id "
                    "ORDER BY q.created_at ASC"
                ),
                {"bank_id": bank_id, "user_id": user_id},
            )
            rows = result.mappings().all()
            if not rows:
                bank = await self._session.execute(
                    text("SELECT 1 FROM question_banks WHERE id = :bank_id AND user_id = :user_id"),
                    {"bank_id": bank_id, "user_id": user_id},
                )
                if bank.scalar_one_or_none() is None:
                    return None
        return [dict(row) for row in rows]

    async def create_attempt(self, *, user_id: str, bank_id: str, max_score: float) -> dict[str, Any]:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO practice_attempts (user_id, question_bank_id, max_score) "
                    "VALUES (:user_id, :bank_id, :max_score) "
                    "RETURNING id, user_id, question_bank_id, status, total_score, max_score, "
                    "started_at, submitted_at, graded_at"
                ),
                {"user_id": user_id, "bank_id": bank_id, "max_score": max_score},
            )
            row = result.mappings().one()
        return dict(row)

    async def get_attempt(self, user_id: str, attempt_id: str) -> dict[str, Any] | None:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, user_id, question_bank_id, status, total_score, max_score, "
                    "started_at, submitted_at, graded_at "
                    "FROM practice_attempts WHERE id = :attempt_id AND user_id = :user_id"
                ),
                {"attempt_id": attempt_id, "user_id": user_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def upsert_answer_result(self, record: dict[str, Any]) -> str:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO practice_answer_results "
                    "(attempt_id, question_id, answer_text, score, max_score, feedback, "
                    "rationale, rubric_breakdown, reference_answer, grader_model, graded_at) "
                    "VALUES (:attempt_id, :question_id, :answer_text, :score, :max_score, "
                    ":feedback, :rationale, :rubric_breakdown::jsonb, :reference_answer, "
                    ":grader_model, CURRENT_TIMESTAMP) "
                    "ON CONFLICT (attempt_id, question_id) DO UPDATE SET "
                    "answer_text = EXCLUDED.answer_text, score = EXCLUDED.score, "
                    "feedback = EXCLUDED.feedback, rationale = EXCLUDED.rationale, "
                    "rubric_breakdown = EXCLUDED.rubric_breakdown, "
                    "grader_model = EXCLUDED.grader_model, graded_at = CURRENT_TIMESTAMP "
                    "RETURNING id"
                ),
                record,
            )
            row_id = str(result.scalar_one())
        return row_id

    async def finalize_attempt(self, attempt_id: str, total_score: float) -> bool:
        """Mark the attempt submitted+graded with the summed score."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE practice_attempts SET status = 'graded', "
                    "total_score = :total_score, submitted_at = CURRENT_TIMESTAMP, "
                    "graded_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :attempt_id AND status = 'in_progress'"
                ),
                {"attempt_id": attempt_id, "total_score": total_score},
            )
            finalized = int(getattr(result, "rowcount", 0) or 0) > 0
        return finalized

    async def list_attempt_results(self, user_id: str, attempt_id: str) -> list[dict[str, Any]]:
        """Owner-scoped per-question results."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT r.id, r.question_id, r.answer_text, r.score, r.max_score, "
                    "r.feedback, r.rationale, r.rubric_breakdown, r.graded_at "
                    "FROM practice_answer_results r "
                    "JOIN practice_attempts a ON a.id = r.attempt_id "
                    "WHERE r.attempt_id = :attempt_id AND a.user_id = :user_id "
                    "ORDER BY r.created_at ASC"
                ),
                {"attempt_id": attempt_id, "user_id": user_id},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]
