"""Question-bank persistence: SQL for question_banks and questions (US 2.15)."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class QuestionRepository:
    """SQL access for the question aggregate. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def owned_document_exists(self, user_id: str, document_id: str) -> bool:
        async with self._session.begin():
            result = await self._session.execute(
                text("SELECT 1 FROM documents WHERE id = :document_id AND user_id = :user_id"),
                {"document_id": document_id, "user_id": user_id},
            )
            found = result.scalar_one_or_none() is not None
        return found

    async def create_bank_with_questions(
        self,
        *,
        user_id: str,
        document_id: str,
        course_id: str | None,
        name: str,
        questions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Atomically create the bank and its generated questions."""
        async with self._session.begin():
            bank_result = await self._session.execute(
                text(
                    "INSERT INTO question_banks (user_id, course_id, document_id, name) "
                    "VALUES (:user_id, :course_id, :document_id, :name) "
                    "RETURNING id, user_id, course_id, document_id, name, created_at"
                ),
                {
                    "user_id": user_id,
                    "course_id": course_id,
                    "document_id": document_id,
                    "name": name,
                },
            )
            bank = dict(bank_result.mappings().one())

            for question in questions:
                await self._session.execute(
                    text(
                        "INSERT INTO questions (question_bank_id, question_type, content, "
                        "options, correct_answer, explanation, difficulty) "
                        "VALUES (:bank_id, :question_type, :content, :options::jsonb, "
                        ":correct_answer, :explanation, :difficulty)"
                    ),
                    {
                        "bank_id": bank["id"],
                        "question_type": question["question_type"],
                        "content": question["content"],
                        "options": question.get("options"),
                        "correct_answer": question.get("correct_answer"),
                        "explanation": question.get("explanation"),
                        "difficulty": question.get("difficulty", "medium"),
                    },
                )
        bank["question_count"] = len(questions)
        return bank

    async def list_banks(self, user_id: str) -> list[dict[str, Any]]:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT qb.id, qb.user_id, qb.course_id, qb.document_id, qb.name, "
                    "qb.created_at, count(q.id) AS question_count "
                    "FROM question_banks qb LEFT JOIN questions q ON q.question_bank_id = qb.id "
                    "WHERE qb.user_id = :user_id GROUP BY qb.id ORDER BY qb.created_at DESC"
                ),
                {"user_id": user_id},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def get_bank_questions(self, user_id: str, bank_id: str) -> list[dict[str, Any]]:
        """Owner-scoped questions of one bank; MCQ correct answers included."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT q.id, q.question_bank_id, q.question_type, q.content, q.options, "
                    "q.correct_answer, q.explanation, q.difficulty "
                    "FROM questions q JOIN question_banks qb ON qb.id = q.question_bank_id "
                    "WHERE q.question_bank_id = :bank_id AND qb.user_id = :user_id "
                    "ORDER BY q.created_at ASC"
                ),
                {"bank_id": bank_id, "user_id": user_id},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]
