"""GroundingChecker: verify answers against retrieved evidence (Invariant 6, US 2.13).

Every generated answer must pass this check. A lexical-overlap heuristic maps
each answer sentence to its best-evidence chunk; sentences with no token
overlap with ANY chunk count as unsupported. Coverage below the threshold
yields the standard refusal ("no relevant information found") — the checker
never fabricates citations and only cites chunks it was given.

Heuristic (not semantic similarity) is a deliberate, disclosed choice: it is
deterministic, testable, and dependency-free; the golden-dataset suite (M4-08)
measures its accuracy.
"""

from dataclasses import dataclass, field
from typing import Any
import re

from app.services.sanitizer import sanitize_untrusted

NO_RELEVANT_INFO_MESSAGE = "No relevant information found in your documents for this question."

DEFAULT_COVERAGE_THRESHOLD = 0.5

_WORD = re.compile(r"[\w']+", re.UNICODE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

# Function words carry no evidence signal; keeping them creates overlap noise
# (an answer sharing only "and"/"with" with a chunk would count as grounded).
_STOPWORDS = {
    "and", "the", "with", "for", "are", "was", "were", "has", "have", "had",
    "not", "but", "all", "any", "can", "may", "its", "his", "her", "our",
    "your", "their", "this", "that", "these", "those", "from", "into", "onto",
    "over", "under", "about", "after", "before", "between", "during",
    "without", "within", "per", "will", "shall", "does", "did", "done",
    "là", "của", "và", "các", "cho", "từ",
}


@dataclass
class Citation:
    document_id: str
    chunk_index: int
    page_number: int | None
    position: int  # the [n] marker the model was given


@dataclass
class GroundingVerdict:
    grounded: bool
    coverage: float
    supported_sentences: int
    total_sentences: int
    citations: list[Citation] = field(default_factory=list)
    refusal_message: str | None = None


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in _WORD.findall(text)
        if len(token) > 2 and token.lower() not in _STOPWORDS
    }


class GroundingChecker:
    """Lexical grounding verification with citation extraction."""

    def __init__(self, coverage_threshold: float = DEFAULT_COVERAGE_THRESHOLD) -> None:
        self.coverage_threshold = coverage_threshold

    def check(
        self,
        answer: str,
        chunks: list[dict[str, Any]],
        *,
        query: str = "",
    ) -> GroundingVerdict:
        """Ground the answer against the numbered evidence chunks.

        ``chunks`` are the SAME dicts given to build_data_block (positions
        match the [n] markers). A query token bonus reduces false refusals
        when the answer paraphrases with the question's key terms.
        """
        if not chunks:
            return self._refusal(total_sentences=self._sentence_count(answer))

        chunk_tokens: list[set[str]] = []
        for chunk in chunks:
            chunk_tokens.append(
                _tokens(sanitize_untrusted(str(chunk.get("content", "")))) | _tokens(str(chunk.get("content", "")))
            )
        query_tokens = _tokens(query)

        sentences = [s.strip() for s in _SENTENCE.split(answer.strip()) if s.strip()]
        if not sentences:
            return self._refusal(total_sentences=0)

        citations: list[Citation] = []
        supported = 0
        for sentence in sentences:
            sentence_tokens = _tokens(sentence)
            if not sentence_tokens:
                supported += 1  # punctuation-only fragments don't count against grounding
                continue
            best_position, best_overlap = self._best_chunk(sentence_tokens, chunk_tokens, query_tokens)
            if best_overlap > 0:
                supported += 1
                chunk = chunks[best_position]
                citation = Citation(
                    document_id=str(chunk.get("document_id", "")),
                    chunk_index=int(chunk.get("chunk_index", 0)),
                    page_number=chunk.get("page_number"),
                    position=best_position + 1,
                )
                if citation not in citations:
                    citations.append(citation)

        coverage = supported / len(sentences)
        grounded = coverage >= self.coverage_threshold
        return GroundingVerdict(
            grounded=grounded,
            coverage=coverage,
            supported_sentences=supported,
            total_sentences=len(sentences),
            citations=citations if grounded else [],
            refusal_message=None if grounded else NO_RELEVANT_INFO_MESSAGE,
        )

    @staticmethod
    def _best_chunk(
        sentence_tokens: set[str], chunk_tokens: list[set[str]], query_tokens: set[str]
    ) -> tuple[int, int]:
        best_position, best_overlap = 0, 0
        for position, tokens in enumerate(chunk_tokens):
            overlap = len(sentence_tokens & tokens)
            # Query-term echoes are weak evidence on their own: they don't
            # create support where the chunk shares nothing with the sentence.
            if overlap > best_overlap:
                best_position, best_overlap = position, overlap
        return best_position, best_overlap

    def _refusal(self, total_sentences: int) -> GroundingVerdict:
        return GroundingVerdict(
            grounded=False,
            coverage=0.0,
            supported_sentences=0,
            total_sentences=total_sentences,
            citations=[],
            refusal_message=NO_RELEVANT_INFO_MESSAGE,
        )

    @staticmethod
    def _sentence_count(answer: str) -> int:
        return len([s for s in _SENTENCE.split(answer.strip()) if s.strip()])
