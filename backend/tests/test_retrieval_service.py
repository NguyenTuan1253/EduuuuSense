"""Unit tests for TASK-M4-05: permission-scoped vector retrieval (Invariant 9)."""

from typing import Any

import pytest

from app.providers.embedding_adapter import EmbeddingAdapter
from app.repositories.chunk_repo import ChunkRepository
from app.services.retrieval_service import RETRIEVAL_SQL, RetrievalService

USER_ID = "f1f1f1f1-f1f1-f1f1-f1f1-f1f1f1f1f1f1"
OTHER_ID = "f2f2f2f2-f2f2-f2f2-f2f2-f2f2f2f2f2f2"


class FakeEmbeddingAdapter(EmbeddingAdapter):
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.queries.extend(texts)
        return [[0.25] * 768 for _ in texts]


class FakeChunkRepository(ChunkRepository):
    def __init__(self, session: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch_similar_chunks(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((sql, dict(params)))
        return [
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "chunk_index": 0,
                "content": "Relevant content",
                "page_number": 1,
                "distance": 0.12,
            }
        ]


def make_service() -> tuple[RetrievalService, FakeChunkRepository, FakeEmbeddingAdapter]:
    chunk_repo = FakeChunkRepository()
    embedding = FakeEmbeddingAdapter()
    return RetrievalService(chunk_repo=chunk_repo, embedding_adapter=embedding), chunk_repo, embedding


def test_retrieval_sql_scopes_ownership_inside_the_query() -> None:
    """Invariant 9: the SQL itself carries the user predicate — no post-filter."""
    assert ":user_id" in RETRIEVAL_SQL
    assert "d.user_id = :user_id" in RETRIEVAL_SQL
    assert "JOIN documents d ON d.id = dc.document_id" in RETRIEVAL_SQL
    assert "d.status = 'embedded'" in RETRIEVAL_SQL
    assert "LIMIT :top_k" in RETRIEVAL_SQL


@pytest.mark.asyncio
async def test_retrieve_passes_subject_scope_and_clamps_top_k() -> None:
    service, repo, embedding = make_service()

    result = await service.retrieve(USER_ID, "what is pgvector?", top_k=999)

    assert result[0]["content"] == "Relevant content"
    assert embedding.queries == ["what is pgvector?"]
    sql, params = repo.calls[0]
    assert params["user_id"] == USER_ID
    assert params["top_k"] == 10  # clamped to the configured maximum


@pytest.mark.asyncio
async def test_retrieve_applies_course_and_document_filters_in_sql() -> None:
    service, repo, _ = make_service()

    await service.retrieve(
        USER_ID,
        "query",
        top_k=3,
        course_id="course-1",
        document_ids=["doc-1", "doc-2"],
    )

    sql, params = repo.calls[0]
    assert params["course_id"] == "course-1"
    assert params["document_ids"] == ["doc-1", "doc-2"]
    assert "d.course_id = :course_id" in sql
    assert "d.id = ANY(:document_ids)" in sql


@pytest.mark.asyncio
async def test_retrieve_rejects_blank_query_before_embedding() -> None:
    service, repo, embedding = make_service()

    with pytest.raises(ValueError):
        await service.retrieve(USER_ID, "   ")

    assert repo.calls == []
    assert embedding.queries == []


@pytest.mark.asyncio
async def test_query_vector_is_never_user_supplied() -> None:
    """The vector comes from embedding the query text — never from the client."""
    service, repo, _ = make_service()
    await service.retrieve(USER_ID, "legit question")
    _, params = repo.calls[0]
    assert params["query_vector"].startswith("[0.25,")
