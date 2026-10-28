"""Practice attempts & score tracking (US 2.15).

MCQ answers are auto-graded against the stored correct_answer; essay answers
go through the GradingService (M5-03). The attempt total is the sum of
per-question scores, persisted atomically with the graded status.
"""

from typing import Any

from app.repositories.practice_repo import PracticeRepository
from app.services.grading_service import GradingService


class AttemptNotFoundError(Exception):
    """Raised when an attempt does not exist for the requesting owner."""


class AttemptAlreadySubmittedError(Exception):
    """Raised when a submitted attempt is submitted again."""


def _selected_letter(selected: str | None, options: Any) -> str | None:
    """Normalize an MCQ selection to its letter, accepting index or text."""
    if not selected:
        return None
    text = str(selected).strip()
    if len(text) == 1 and text.isalpha():
        return text.upper()
    if options and isinstance(options, list):
        for position, option in enumerate(options):
            if str(option).strip() == text:
                return chr(ord("A") + position)
    return text.upper()[:1]


class PracticeService:
    """Practice use cases for the authenticated owner."""

    def __init__(self, practice_repo: PracticeRepository, grading: GradingService) -> None:
        self._practice_repo = practice_repo
        self._grading = grading

    @classmethod
    def from_session(cls, session: Any, grading: GradingService | None = None) -> "PracticeService":
        """Compose over one session (grader injectable for tests)."""
        return cls(
            practice_repo=PracticeRepository(session),
            grading=grading or GradingService.from_session(session),
        )

    async def start_attempt(self, user_id: str, bank_id: str) -> dict[str, Any]:
        """Create an in-progress attempt from an owned question bank."""
        questions = await self._practice_repo.bank_question_snapshot(user_id, bank_id)
        if questions is None:
            raise AttemptNotFoundError("Question bank not found.")
        if not questions:
            raise ValueError("The question bank has no questions to practice.")

        # Equal split of the default 10-point scale across questions.
        max_score = round(10.0, 2)
        per_question = round(max_score / len(questions), 2)
        attempt = await self._practice_repo.create_attempt(
            user_id=user_id, bank_id=bank_id, max_score=max_score
        )
        return {**attempt, "per_question_max": per_question, "question_count": len(questions)}

    async def submit_attempt(
        self, user_id: str, attempt_id: str, answers: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Grade all answers (MCQ auto, essay via grader) and finalize.

        Raises AttemptAlreadySubmittedError on double submission; a grader
        failure propagates for the router to map (attempt stays in_progress,
        already-written results remain for the retry).
        """
        attempt = await self._practice_repo.get_attempt(user_id, attempt_id)
        if attempt is None:
            raise AttemptNotFoundError("Attempt not found.")
        if attempt["status"] != "in_progress":
            raise AttemptAlreadySubmittedError("This attempt was already submitted.")
        if not answers:
            raise ValueError("No answers submitted.")

        snapshot = await self._practice_repo.bank_question_snapshot(
            user_id, str(attempt["question_bank_id"])
        )
        assert snapshot is not None  # attempt exists => bank was accessible
        by_id = {str(q["id"]): q for q in snapshot}
        per_question = round(float(attempt["max_score"] or 10.0) / max(1, len(snapshot)), 2)

        unknown = [a for a in answers if str(a.get("question_id")) not in by_id]
        if unknown:
            raise ValueError("Answers reference questions outside this attempt's bank.")

        total = 0.0
        for answer in answers:
            question = by_id[str(answer["question_id"])]
            if question["question_type"] == "multiple_choice":
                score, feedback, rationale, breakdown = self._grade_mcq(question, answer, per_question)
                grader_model = "auto"
            else:
                graded = await self._grading.grade_essay(
                    question=str(question["content"]),
                    reference_answer=question.get("correct_answer"),
                    answer_text=str(answer.get("answer_text") or ""),
                    max_score=per_question,
                )
                score = float(graded["score"])
                feedback = graded["feedback"]
                rationale = graded["rationale"]
                breakdown = graded["rubric_breakdown"]
                grader_model = graded["grader_model"]

            total += score
            await self._practice_repo.upsert_answer_result(
                {
                    "attempt_id": attempt_id,
                    "question_id": str(answer["question_id"]),
                    "answer_text": answer.get("answer_text"),
                    "score": score,
                    "max_score": per_question,
                    "feedback": feedback,
                    "rationale": rationale,
                    "rubric_breakdown": breakdown,
                    "reference_answer": question.get("correct_answer"),
                    "grader_model": grader_model,
                }
            )

        finalized = await self._practice_repo.finalize_attempt(attempt_id, round(total, 2))
        if not finalized:
            raise AttemptAlreadySubmittedError("This attempt was already submitted.")

        updated = await self._practice_repo.get_attempt(user_id, attempt_id)
        assert updated is not None
        return updated

    @staticmethod
    def _grade_mcq(
        question: dict[str, Any], answer: dict[str, Any], per_question: float
    ) -> tuple[float, str, str, Any]:
        selected = _selected_letter(answer.get("selected_option"), question.get("options"))
        correct = str(question.get("correct_answer") or "").strip().upper()[:1]
        is_correct = selected is not None and selected == correct
        score = per_question if is_correct else 0.0
        feedback = "Chính xác." if is_correct else f"Sai. Đáp án đúng: {correct}."
        return score, feedback, "", None

    async def get_attempt_detail(self, user_id: str, attempt_id: str) -> dict[str, Any]:
        attempt = await self._practice_repo.get_attempt(user_id, attempt_id)
        if attempt is None:
            raise AttemptNotFoundError("Attempt not found.")
        results = await self._practice_repo.list_attempt_results(user_id, attempt_id)
        return {"attempt": attempt, "results": results}
