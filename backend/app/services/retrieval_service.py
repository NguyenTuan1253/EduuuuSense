"""Permission-scoped vector retrieval (Invariant 9, US 2.13).

The ownership predicate lives INSIDE the retrieval SQL — the query never runs
unscoped and filters only after fetching. Only 'embedded' documents are
searchable; the top_k cap bounds cost. Query embedding is produced by the
shared EmbeddingAdapter (Invariant 4).
"""

from typing import Any

from app.providers.embedding_adapter import EmbeddingAdapter
from app.repositories.chunk_repo import ChunkRepository

# Ownership scope is part of the retrieval SQL itself (Invariant 9):
# every candidate row joins documents and filters d.user_id = :user_id.
RETRIEVAL_SQL = """
SELECT dc.id, dc.document_id, dc.chunk_index, dc.content, dc.page_number,
       dc.embedding <=> :query_vector::vector AS distance
FROM document_chunks dc
JOIN documents d ON d.id = dc.document_id
WHERE d.user_id = :user_id
  AND d.status = 'embedded'
  {extra_filters}
ORDER BY dc.embedding <=> :query_vector::vector
LIMIT :top_k
"""


class RetrievalService:
    """Document-grounded retrieval for the authenticated owner."""

    def __init__(
        self,
        chunk_repo: ChunkRepository,
        embedding_adapter: EmbeddingAdapter,
        max_top_k: int = 10,
    ) -> None:
        self._chunk_repo = chunk_repo
        self._embedding = embedding_adapter
        self._max_top_k = max_top_k

    @classmethod
    def from_session(cls, session: Any) -> "RetrievalService":
        """Compose retrieval over one session (production wiring)."""
        return cls(chunk_repo=ChunkRepository(session), embedding_adapter=EmbeddingAdapter())

    async def retrieve(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int = 5,
        course_id: str | None = None,
        document_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Embed the query and fetch the most similar OWNED chunks.

        ``top_k`` is clamped to the configured maximum; ownership and the
        embedded-status filter are part of the SQL, never post-filtering.
        """
        if not query or not query.strip():
            raise ValueError("Retrieval query must not be blank.")
        effective_top_k = max(1, min(top_k, self._max_top_k))
        vectors = await self._embedding.embed_texts([query.strip()])
        query_vector = vectors[0]

        extra_filters = ""
        params: dict[str, Any] = {
            "user_id": user_id,
            "query_vector": ChunkRepository.to_vector_literal(query_vector),
            "top_k": effective_top_k,
        }
        if course_id is not None:
            extra_filters += "  AND d.course_id = :course_id\n"
            params["course_id"] = course_id
        if document_ids:
            extra_filters += "  AND d.id = ANY(:document_ids)\n"
            params["document_ids"] = list(document_ids)

        sql = RETRIEVAL_SQL.format(extra_filters=extra_filters)
        return await self._chunk_repo.fetch_similar_chunks(sql, params)
