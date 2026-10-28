"""Chunking service for the ingestion pipeline (DECISION-PROP-02 defaults).

Chunk sizing follows the proposed defaults pending PO approval: child chunks
of ~400-500 tokens with a 60-75 token overlap (the schema's ``document_chunks``
table stores a flat list, so only child chunks are persisted; parent grouping
would require a schema decision). Token counts are estimates (4 chars/token).
"""

import re
from dataclasses import dataclass

from app.services.extraction_service import ExtractionResult, PageText

CHILD_CHUNK_TOKENS = 450
CHILD_OVERLAP_TOKENS = 75
TOKENS_PER_CHAR = 4.0

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n{2,}")


@dataclass
class Chunk:
    index: int  # 0-based, unique per document (schema UNIQUE(document_id, chunk_index))
    content: str
    page_number: int | None
    token_count: int


def estimate_tokens(text: str) -> int:
    """Estimate token count (~4 characters per token for English/Vietnamese)."""
    return max(1, int(len(text) / TOKENS_PER_CHAR))


def chunk_document(extraction: ExtractionResult) -> list[Chunk]:
    """Chunk extracted pages into overlapping child chunks.

    Splits prefer sentence boundaries; empty pages contribute nothing. Chunk
    indexes are sequential across the whole document; ``page_number`` preserves
    provenance for citations (Invariant 6 support). Consecutive chunks share an
    overlap of roughly CHILD_OVERLAP_TOKENS so answers spanning a boundary keep
    their context.
    """
    pieces: list[tuple[str, int | None]] = []  # (sentence, page_number)
    for page in extraction.pages:
        for sentence in _split_page(page):
            pieces.append((sentence, page.number))
    if not pieces:
        return []

    # Cumulative CHARACTER counts (a joining space per piece is added) so the
    # joined-content estimate matches the final chunk estimate exactly.
    piece_chars = [len(text) for text, _ in pieces]
    cumulative = [0]
    for count in piece_chars:
        cumulative.append(cumulative[-1] + count)

    budget_chars = int(CHILD_CHUNK_TOKENS * TOKENS_PER_CHAR)
    overlap_chars = int(CHILD_OVERLAP_TOKENS * TOKENS_PER_CHAR)
    total_chars = cumulative[-1] + len(pieces)  # joining spaces
    chunks: list[Chunk] = []
    window_start = 0
    index = 0
    while window_start < len(pieces) and cumulative[window_start] < total_chars:
        window_end = window_start
        while (
            window_end < len(pieces)
            and cumulative[min(window_end + 1, len(pieces))] - cumulative[window_start]
            + (window_end + 1 - window_start)  # joining spaces
            <= budget_chars
        ):
            window_end += 1
        window_end = max(window_end, window_start + 1)  # always make progress

        content = " ".join(text for text, _ in pieces[window_start:window_end]).strip()
        chunks.append(
            Chunk(
                index=index,
                content=content,
                page_number=pieces[window_start][1],
                token_count=estimate_tokens(content),
            )
        )
        index += 1

        if window_end >= len(pieces):
            break
        # Slide with overlap: keep trailing pieces worth ~CHILD_OVERLAP_TOKENS.
        overlap_start = window_end
        while (
            overlap_start - 1 > window_start
            and cumulative[window_end] - cumulative[overlap_start - 1]
            + (window_end - overlap_start + 1)
            <= overlap_chars
        ):
            overlap_start -= 1
        window_start = overlap_start

    return chunks


def _split_page(page: PageText) -> list[str]:
    """Split one page's text into sentence/paragraph pieces."""
    text = page.text.strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_BOUNDARY.split(text) if p.strip()]
    return parts or [text]
