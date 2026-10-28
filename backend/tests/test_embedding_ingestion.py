"""Unit tests for TASK-M3-05: embedding generation & pgvector ingestion (US 2.10)."""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.embedding_adapter import (
    EmbeddingAdapter,
    EmbeddingDimensionError,
    ProviderNotConfiguredError,
)
from app.repositories.chunk_repo import ChunkRepository
from app.repositories.document_repo import DocumentRepository
from app.worker.idempotency import TaskIdempotencyRepository
from app.worker.tasks.documents import ingest_document

DOC_ID = "b5b5b5b5-b5b5-b5b5-b5b5-b5b5b5b5b5b5"
USER_ID = "c6c6c6c6-c6c6-c6c6-c6c6-c6c6c6c6c6c6"
DIM = 768


class FakeEmbeddingAdapter(EmbeddingAdapter):
    def __init__(self, dimension: int = DIM, error: Exception | None = None) -> None:
        self.dimension = dimension
        self.error = error
        self.batches: list[list[str]] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        if self.error is not None:
            raise self.error
        return [[0.1] * self.dimension for _ in texts]


class FakeStorage:
    def __init__(self, data: bytes = b"%PDF-1.4 fake") -> None:
        self.data = data

    def download_bytes(self, object_key: str) -> bytes:
        return self.data


class FakeDocumentRepository(DocumentRepository):
    def __init__(self, session: AsyncSession | None = None, *, document: dict[str, Any] | None = None) -> None:
        self.document = document
        self.status = document["status"] if document else None
        self.error_code: str | None = None
        self.total_chunks = 0

    async def get_document(self, user_id: str, document_id: str) -> dict[str, Any] | None:
        if self.document and self.document["user_id"] == user_id:
            return dict(self.document)
        return None

    async def claim_for_processing(self, document_id: str) -> bool:
        if self.status == "pending":
            self.status = "processing"
            return True
        return False

    async def mark_embedded(self, document_id: str, total_chunks: int) -> bool:
        if self.status != "processing":
            return False
        self.status = "embedded"
        self.total_chunks = total_chunks
        return True

    async def mark_failed(self, document_id: str, error_code: str) -> bool:
        if self.status in ("pending", "processing"):
            self.status = "failed"
            self.error_code = error_code
            return True
        return False


class FakeChunkRepository(ChunkRepository):
    def __init__(self, session: AsyncSession | None = None) -> None:
        self.chunks: dict[str, list[dict[str, Any]]] = {}
        self.upsert_count = 0

    async def upsert_chunks(self, document_id: str, chunks: list[Any]) -> int:
        self.upsert_count += 1
        self.chunks[document_id] = [
            {"chunk_index": c[0], "content": c[1], "page_number": c[2], "token_count": c[3], "dim": len(c[4])}
            for c in chunks
        ]
        return len(chunks)

    async def delete_chunks(self, document_id: str) -> int:
        return len(self.chunks.pop(document_id, []))


class FakeGuardRepository:
    """Guard repo tracking the done/failed lifecycle for skip semantics."""

    def __init__(self) -> None:
        self.state = "none"

    async def find(self, key: str) -> dict[str, Any] | None:
        if self.state == "none":
            return None
        return {"state": self.state}

    async def try_begin(self, key: str, task_type: str) -> str | None:
        if self.state == "done":
            return None
        self.state = "in_progress"
        return "row"

    async def mark_done(self, key: str, result: dict[str, Any] | None = None) -> None:
        self.state = "done"

    async def mark_failed(self, key: str, error_code: str | None = None) -> None:
        self.state = "failed"


def make_document(status: str = "pending") -> dict[str, Any]:
    return {
        "id": DOC_ID,
        "user_id": USER_ID,
        "file_type": "pdf",
        "status": status,
        "storage_object_key": f"{USER_ID}/{DOC_ID}/file.pdf",
    }


PDF_TEXT_BYTES = b"%PDF-1.4 not really parsed in unit tests (fake adapter path)"


@pytest.mark.asyncio
async def test_ingestion_happy_path_marks_embedded_with_all_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """pending -> processing -> embedded; chunks_processed == total_chunks."""
    monkeypatch.setattr(
        "app.services.extraction_service.PdfReader",
        __import__("pypdf").PdfReader,
    )
    # Use a plain-text-ish flow instead: build a real one-page PDF via pypdf writer.
    from tests.test_text_extraction import make_pdf

    document_repo = FakeDocumentRepository(document=make_document())
    chunk_repo = FakeChunkRepository()
    storage = FakeStorage(data=make_pdf(["Single page about vectors. It explains pgvector storage."]))
    adapter = FakeEmbeddingAdapter()

    result = await ingest_document(
        DOC_ID,
        USER_ID,
        storage=storage,
        adapter=adapter,
        document_repo=document_repo,
        chunk_repo=chunk_repo,
        guard_repo=FakeGuardRepository(),
    )

    assert result["status"] == "embedded"
    assert document_repo.status == "embedded"
    assert document_repo.total_chunks == len(chunk_repo.chunks[DOC_ID])
    assert chunk_repo.chunks[DOC_ID][0]["dim"] == DIM
    assert adapter.batches and adapter.batches[0][0].strip()


@pytest.mark.asyncio
async def test_extraction_failure_marks_document_failed_with_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unextractable content (image) -> status failed + ocr_not_configured."""
    document_repo = FakeDocumentRepository(document=make_document())
    document_repo.document["file_type"] = "png"  # type: ignore[index]
    chunk_repo = FakeChunkRepository()

    result = await ingest_document(
        DOC_ID,
        USER_ID,
        storage=FakeStorage(PNG_BYTES),
        adapter=FakeEmbeddingAdapter(),
        document_repo=document_repo,
        chunk_repo=chunk_repo,
        guard_repo=FakeGuardRepository(),
    )

    assert result["status"] == "failed"
    assert document_repo.status == "failed"
    assert document_repo.error_code == "ocr_not_configured"
    assert chunk_repo.chunks == {}


PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"


@pytest.mark.asyncio
async def test_provider_failure_marks_document_failed_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Embedding provider failure -> failed/provider_error (no retry storm)."""
    from tests.test_text_extraction import make_pdf

    document_repo = FakeDocumentRepository(document=make_document())
    chunk_repo = FakeChunkRepository()

    result = await ingest_document(
        DOC_ID,
        USER_ID,
        storage=FakeStorage(make_pdf(["Some content to chunk."])),
        adapter=FakeEmbeddingAdapter(error=ProviderNotConfiguredError("no key")),
        document_repo=document_repo,
        chunk_repo=chunk_repo,
        guard_repo=FakeGuardRepository(),
    )

    assert result["status"] == "failed"
    assert document_repo.error_code == "provider_error"


@pytest.mark.asyncio
async def test_dimension_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vectors not matching the configured 768 dimension never reach storage."""
    from tests.test_text_extraction import make_pdf

    document_repo = FakeDocumentRepository(document=make_document())
    chunk_repo = FakeChunkRepository()

    result = await ingest_document(
        DOC_ID,
        USER_ID,
        storage=FakeStorage(make_pdf(["Content."])),
        adapter=FakeEmbeddingAdapter(dimension=1536),
        document_repo=document_repo,
        chunk_repo=chunk_repo,
        guard_repo=FakeGuardRepository(),
    )

    assert result["status"] == "failed"
    assert document_repo.error_code == "provider_error"
    assert chunk_repo.chunks == {}


@pytest.mark.asyncio
async def test_reingestion_after_done_is_skipped_by_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard semantics: a done ingestion is never re-executed (duplicate safe)."""
    from tests.test_text_extraction import make_pdf

    document_repo = FakeDocumentRepository(document=make_document())
    chunk_repo = FakeChunkRepository()
    storage = FakeStorage(make_pdf(["Content for embedding."]))

    first = await ingest_document(
        DOC_ID, USER_ID, storage=storage, adapter=FakeEmbeddingAdapter(),
        document_repo=document_repo, chunk_repo=chunk_repo,
        guard_repo=FakeGuardRepository(),
    )
    assert first["status"] == "embedded"

    # A done guard blocks the second delivery entirely.
    class DoneGuardRepo(FakeGuardRepository, TaskIdempotencyRepository):
        def __init__(self) -> None:
            TaskIdempotencyRepository.__init__(self, session=None)  # type: ignore[arg-type]
            self.state = "done"

    async def work() -> dict[str, Any]:  # pragma: no cover — must not run
        raise AssertionError("must not re-execute")

    from app.worker.idempotency import run_guarded

    outcome = await run_guarded(
        DoneGuardRepo(), f"document_ingestion:{DOC_ID}", "document_ingestion", work
    )
    assert outcome.executed is False
    assert outcome.skipped_reason == "done" 


@pytest.mark.asyncio
async def test_vector_literal_format_matches_pgvector() -> None:
    """The pgvector literal renders as [a,b,c] with float precision."""
    literal = ChunkRepository.to_vector_literal([0.123456789, 1.0, -2.5])
    assert literal.startswith("[") and literal.endswith("]")
    assert "," in literal
    parts = literal[1:-1].split(",")
    assert len(parts) == 3
    assert float(parts[0]) == pytest.approx(0.123456789, abs=1e-9)


@pytest.mark.asyncio
async def test_embedding_adapter_fails_closed_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No GEMINI_API_KEY -> ProviderNotConfiguredError (never a silent empty)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "gemini_api_key", None)
    adapter = EmbeddingAdapter()

    with pytest.raises(ProviderNotConfiguredError):
        await adapter.embed_texts(["text"])


@pytest.mark.asyncio
async def test_embedding_adapter_validates_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider returning wrong-dimension vectors raises EmbeddingDimensionError."""
    from app.core.config import settings

    class FakeClient:
        class models:  # noqa: N801
            @staticmethod
            def embed_content(model: str, contents: list[str]) -> Any:
                class Item:
                    values = [0.1] * 1536

                class Response:
                    embeddings = [Item()]

                return Response()

    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    adapter = EmbeddingAdapter(client=FakeClient())

    with pytest.raises(EmbeddingDimensionError):
        await adapter.embed_texts(["text"])
