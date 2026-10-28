"""Document ingestion task: extract -> chunk -> embed -> persist (US 2.10).

Runs in Celery (never in requests). Passes through the IdempotencyGuard;
status transitions are pending -> processing -> embedded|failed, with
'embedded' valid only when chunks_processed == total_chunks (Invariant).
Chunk persistence is retry-safe via (document_id, chunk_index) UPSERT.
"""

import asyncio
from typing import Any

from app.core.config import settings
from app.core.database import async_session_factory
from app.providers.embedding_adapter import (
    EmbeddingAdapter,
    EmbeddingDimensionError,
    ProviderNotConfiguredError,
)
from app.repositories.chunk_repo import ChunkRepository
from app.repositories.document_repo import DocumentRepository
from app.services.chunking_service import chunk_document
from app.services.extraction_service import ExtractionError, extract_text
from app.worker.celery_app import celery_app
from app.worker.idempotency import GuardOutcome, TaskIdempotencyRepository, run_guarded


async def ingest_document(
    document_id: str,
    user_id: str,
    *,
    storage: Any | None = None,
    adapter: EmbeddingAdapter | None = None,
    document_repo: DocumentRepository | None = None,
    chunk_repo: ChunkRepository | None = None,
    guard_repo: Any | None = None,
) -> dict[str, Any]:
    """Run one ingestion attempt. Collaborators injectable for tests.

    When every collaborator is injected, no database session is opened (the
    guard repository is part of the injection); the production path composes
    all repositories over one session.
    """
    from app.clients.storage_client import StorageClient

    embedding_adapter = adapter or EmbeddingAdapter()
    storage_client = storage or StorageClient()

    if document_repo is not None and chunk_repo is not None and guard_repo is not None:
        async def work() -> dict[str, Any]:
            return await _do_ingest(
                document_id,
                user_id,
                document_repo=document_repo,
                chunk_repo=chunk_repo,
                embedding_adapter=embedding_adapter,
                storage_client=storage_client,
            )

        outcome = await run_guarded(
            guard_repo, f"document_ingestion:{document_id}", "document_ingestion", work
        )
        return _outcome_to_dict(outcome)

    async with async_session_factory() as session:
        document_repository = document_repo or DocumentRepository(session)
        chunk_repository = chunk_repo or ChunkRepository(session)
        session_guard = TaskIdempotencyRepository(session)

        async def work_session() -> dict[str, Any]:
            return await _do_ingest(
                document_id,
                user_id,
                document_repo=document_repository,
                chunk_repo=chunk_repository,
                embedding_adapter=embedding_adapter,
                storage_client=storage_client,
            )

        outcome = await run_guarded(
            session_guard, f"document_ingestion:{document_id}", "document_ingestion", work_session
        )
        return _outcome_to_dict(outcome)


async def _do_ingest(
    document_id: str,
    user_id: str,
    *,
    document_repo: DocumentRepository,
    chunk_repo: ChunkRepository,
    embedding_adapter: EmbeddingAdapter,
    storage_client: Any,
) -> dict[str, Any]:
    from app.clients.storage_client import run_storage_op

    document = await document_repo.get_document(user_id, document_id)
    if document is None:
        return {"status": "not_found"}
    if not await document_repo.claim_for_processing(document_id):
        return {"status": "not_pending", "current": document["status"]}

    try:
        data = await run_storage_op(storage_client.download_bytes, document["storage_object_key"])
        extraction = extract_text(document["file_type"], data)
    except ExtractionError as exc:
        await document_repo.mark_failed(document_id, exc.code)
        return {"status": "failed", "error_code": exc.code}

    chunks = chunk_document(extraction)
    if not chunks:
        await document_repo.mark_failed(document_id, "empty_document")
        return {"status": "failed", "error_code": "empty_document"}

    try:
        embeddings = await embedding_adapter.embed_texts([chunk.content for chunk in chunks])
    except (ProviderNotConfiguredError, EmbeddingDimensionError):
        await document_repo.mark_failed(document_id, "provider_error")
        return {"status": "failed", "error_code": "provider_error"}

    # Persistence-boundary guard (defense in depth): the vector column dimension
    # comes from configuration; wrong-sized vectors never reach the database.
    expected_dimension = settings.gemini_embedding_dimension
    if any(len(vector) != expected_dimension for vector in embeddings):
        await document_repo.mark_failed(document_id, "provider_error")
        return {"status": "failed", "error_code": "provider_error"}

    await chunk_repo.delete_chunks(document_id)
    await chunk_repo.upsert_chunks(
        document_id,
        [
            (chunk.index, chunk.content, chunk.page_number, chunk.token_count, embedding)
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        ],
    )
    await document_repo.mark_embedded(document_id, total_chunks=len(chunks))
    return {"status": "embedded", "total_chunks": len(chunks)}


def _outcome_to_dict(outcome: GuardOutcome) -> dict[str, Any]:
    if not outcome.executed:
        return {"status": "skipped", "reason": outcome.skipped_reason}
    result = outcome.result if isinstance(outcome.result, dict) else {}
    return {"status": result.get("status", "unknown"), "detail": result}


@celery_app.task(
    bind=True,
    name="app.worker.tasks.documents.ingest_document",
    max_retries=5,
    default_retry_delay=30,
)
def ingest_document_task(self: Any, document_id: str, user_id: str) -> dict[str, Any]:
    """Celery entry point. Provider configuration errors do not retry."""
    try:
        return asyncio.run(ingest_document(document_id, user_id))
    except (ProviderNotConfiguredError, EmbeddingDimensionError):
        raise
    except Exception as exc:  # noqa: BLE001 — transient: retry with backoff
        raise self.retry(exc=exc) from exc
