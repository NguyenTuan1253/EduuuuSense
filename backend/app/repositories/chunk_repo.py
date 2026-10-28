"""Chunk persistence: SQL for document_chunks with pgvector storage (US 2.10).

Retry-safe UPSERT on the (document_id, chunk_index) uniqueness rule. Vector
values are passed as pgvector literal text with an explicit cast; ownership
checks ride on the documents join where relevant.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ChunkRepository:
    """SQL access for document chunks. No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def to_vector_literal(vector: list[float]) -> str:
        """Render an embedding as a pgvector literal: '[0.1,0.2,...]'."""
        return "[" + ",".join(f"{value:.10g}" for value in vector) + "]"

    async def upsert_chunks(
        self, document_id: str, chunks: list[tuple[int, str, int | None, int | None, list[float]]]
    ) -> int:
        """Insert or update chunks atomically; returns the number persisted.

        Each item is (chunk_index, content, page_number, token_count, embedding).
        Safe to re-run: (document_id, chunk_index) is UPSERTed in one
        transaction (retry-safe per the architecture's chunk identity rule).
        """
        if not chunks:
            return 0
        async with self._session.begin():
            for chunk_index, content, page_number, token_count, embedding in chunks:
                await self._session.execute(
                    text(
                        "INSERT INTO document_chunks "
                        "(document_id, chunk_index, content, page_number, token_count, embedding) "
                        "VALUES (:document_id, :chunk_index, :content, :page_number, "
                        ":token_count, :embedding::vector) "
                        "ON CONFLICT (document_id, chunk_index) DO UPDATE SET "
                        "content = EXCLUDED.content, "
                        "page_number = EXCLUDED.page_number, "
                        "token_count = EXCLUDED.token_count, "
                        "embedding = EXCLUDED.embedding, "
                        "updated_at = CURRENT_TIMESTAMP"
                    ),
                    {
                        "document_id": document_id,
                        "chunk_index": chunk_index,
                        "content": content,
                        "page_number": page_number,
                        "token_count": token_count,
                        "embedding": self.to_vector_literal(embedding),
                    },
                )
        return len(chunks)

    async def count_chunks(self, document_id: str) -> int:
        async with self._session.begin():
            result = await self._session.execute(
                text("SELECT count(*) FROM document_chunks WHERE document_id = :document_id"),
                {"document_id": document_id},
            )
            count = int(result.scalar_one() or 0)
        return count

    async def delete_chunks(self, document_id: str) -> int:
        """Remove all chunks of a document (re-ingestion cleanup)."""
        async with self._session.begin():
            result = await self._session.execute(
                text("DELETE FROM document_chunks WHERE document_id = :document_id"),
                {"document_id": document_id},
            )
            deleted = int(getattr(result, "rowcount", 0) or 0)
        return deleted

    async def fetch_chunk_vectors(
        self, user_id: str, document_id: str
    ) -> list[dict[str, Any]]:
        """Owner-scoped chunk metadata (no vectors) for verification/tests."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT dc.id, dc.chunk_index, dc.content, dc.page_number, dc.token_count "
                    "FROM document_chunks dc JOIN documents d ON d.id = dc.document_id "
                    "WHERE dc.document_id = :document_id AND d.user_id = :user_id "
                    "ORDER BY dc.chunk_index ASC"
                ),
                {"document_id": document_id, "user_id": user_id},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]
