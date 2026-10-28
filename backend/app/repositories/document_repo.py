"""Document persistence: SQL for the documents aggregate (US 2.11).

Metadata only — file bytes live in MinIO (DECISION-CONFIRMED-06). Ownership
is enforced in every statement.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class DocumentRepository:
    """SQL access for documents (+ tags). No business logic lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def owned_course_exists(self, user_id: str, course_id: str) -> bool:
        """True when the course exists and belongs to the user."""
        async with self._session.begin():
            result = await self._session.execute(
                text("SELECT 1 FROM courses WHERE id = :course_id AND user_id = :user_id"),
                {"course_id": course_id, "user_id": user_id},
            )
            found = result.scalar_one_or_none() is not None
        return found

    async def create_document(
        self,
        *,
        document_id: str,
        user_id: str,
        course_id: str | None,
        title: str,
        original_file_name: str,
        storage_object_key: str,
        file_type: str,
        file_size_bytes: int,
    ) -> dict[str, Any]:
        """Insert a document in 'pending' status (worker processes it later)."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO documents "
                    "(user_id, course_id, title, original_file_name, storage_object_key, "
                    "file_type, file_size_bytes) "
                    "VALUES (:document_id, :user_id, :course_id, :title, :original_file_name, "
                    ":storage_object_key, :file_type, :file_size_bytes) "
                    "RETURNING id, user_id, course_id, title, original_file_name, "
                    "storage_object_key, file_type, file_size_bytes, status, total_chunks, "
                    "chunks_processed, created_at, updated_at"
                ),
                {
                    "document_id": document_id,
                    "user_id": user_id,
                    "course_id": course_id,
                    "title": title,
                    "original_file_name": original_file_name,
                    "storage_object_key": storage_object_key,
                    "file_type": file_type,
                    "file_size_bytes": file_size_bytes,
                },
            )
            row = result.mappings().one()
        return dict(row)

    async def list_documents(
        self,
        user_id: str,
        *,
        course_id: str | None = None,
        status: str | None = None,
        file_type: str | None = None,
        title_query: str | None = None,
        tag_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Owner-scoped listing with optional filters (search basis, M3-06)."""
        clauses = ["c.user_id = :user_id"]
        params: dict[str, Any] = {"user_id": user_id, "limit": limit, "offset": offset}
        if course_id is not None:
            clauses.append("d.course_id = :course_id")
            params["course_id"] = course_id
        if status is not None:
            clauses.append("d.status = :status")
            params["status"] = status
        if file_type is not None:
            clauses.append("d.file_type = :file_type")
            params["file_type"] = file_type
        if title_query:
            clauses.append("d.title ILIKE :title_query")
            params["title_query"] = f"%{title_query}%"
        if tag_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM document_tags dt WHERE dt.document_id = d.id "
                "AND dt.tag_id = :tag_id AND dt.user_id = :user_id)"
            )
            params["tag_id"] = tag_id
        where = " AND ".join(clauses)

        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT d.id, d.user_id, d.course_id, d.title, d.original_file_name, "
                    "d.file_type, d.file_size_bytes, d.status, d.total_chunks, "
                    "d.chunks_processed, d.created_at, d.updated_at, "
                    "c.name AS course_name "
                    f"FROM documents d LEFT JOIN courses c ON c.id = d.course_id "
                    f"WHERE {where} "
                    "ORDER BY d.created_at DESC LIMIT :limit OFFSET :offset"
                ),
                params,
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def get_document(self, user_id: str, document_id: str) -> dict[str, Any] | None:
        """Fetch one owned document including its storage object key."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT id, user_id, course_id, title, original_file_name, "
                    "storage_object_key, file_type, file_size_bytes, status, total_chunks, "
                    "chunks_processed, processing_error_code, created_at, updated_at "
                    "FROM documents WHERE id = :document_id AND user_id = :user_id"
                ),
                {"document_id": document_id, "user_id": user_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def delete_document(self, user_id: str, document_id: str) -> dict[str, Any] | None:
        """Delete an owned document row; returns the object key for cleanup."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "DELETE FROM documents WHERE id = :document_id AND user_id = :user_id "
                    "RETURNING storage_object_key"
                ),
                {"document_id": document_id, "user_id": user_id},
            )
            row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def claim_for_processing(self, document_id: str) -> bool:
        """Atomically claim a pending document (pending -> processing)."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE documents SET status = 'processing', updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :document_id AND status = 'pending'"
                ),
                {"document_id": document_id},
            )
            claimed = int(getattr(result, "rowcount", 0) or 0) > 0
        return claimed

    async def mark_embedded(self, document_id: str, total_chunks: int) -> bool:
        """Mark embedded ONLY when every chunk is persisted (Invariant).

        Sets chunks_processed = total_chunks so the embedded status is valid.
        """
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE documents SET status = 'embedded', total_chunks = :total_chunks, "
                    "chunks_processed = :total_chunks, processing_error_code = NULL, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :document_id AND status = 'processing'"
                ),
                {"document_id": document_id, "total_chunks": total_chunks},
            )
            marked = int(getattr(result, "rowcount", 0) or 0) > 0
        return marked

    async def mark_failed(self, document_id: str, error_code: str) -> bool:
        """Mark a processing document failed with a stable error code."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "UPDATE documents SET status = 'failed', processing_error_code = :error_code, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :document_id AND status IN ('pending', 'processing')"
                ),
                {"document_id": document_id, "error_code": error_code},
            )
            marked = int(getattr(result, "rowcount", 0) or 0) > 0
        return marked

    async def get_or_create_tag(self, user_id: str, name: str) -> dict[str, Any]:
        """Create-or-fetch a user tag; normalized_name drives uniqueness."""
        normalized = name.strip().lower()
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO tags (user_id, name, normalized_name) "
                    "VALUES (:user_id, :name, :normalized_name) "
                    "ON CONFLICT (user_id, normalized_name) DO UPDATE SET name = EXCLUDED.name "
                    "RETURNING id, user_id, name, normalized_name"
                ),
                {"user_id": user_id, "name": name.strip(), "normalized_name": normalized},
            )
            row = result.mappings().one()
        return dict(row)

    async def attach_tag(self, user_id: str, document_id: str, tag_id: str) -> bool:
        """Attach an owned tag to an owned document (idempotent)."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "INSERT INTO document_tags (document_id, tag_id, user_id) "
                    "SELECT :document_id, :tag_id, :user_id "
                    "WHERE EXISTS (SELECT 1 FROM documents WHERE id = :document_id AND user_id = :user_id) "
                    "AND EXISTS (SELECT 1 FROM tags WHERE id = :tag_id AND user_id = :user_id) "
                    "ON CONFLICT (document_id, tag_id) DO NOTHING"
                ),
                {"document_id": document_id, "tag_id": tag_id, "user_id": user_id},
            )
            attached = int(getattr(result, "rowcount", 0) or 0) > 0
        return attached

    async def detach_tag(self, user_id: str, document_id: str, tag_id: str) -> bool:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "DELETE FROM document_tags dt "
                    "USING documents d, tags t "
                    "WHERE dt.document_id = d.id AND dt.tag_id = t.id "
                    "AND dt.user_id = :user_id AND d.user_id = :user_id AND t.user_id = :user_id "
                    "AND dt.document_id = :document_id AND dt.tag_id = :tag_id"
                ),
                {"document_id": document_id, "tag_id": tag_id, "user_id": user_id},
            )
            detached = int(getattr(result, "rowcount", 0) or 0) > 0
        return detached

    async def list_document_tags(self, user_id: str, document_id: str) -> list[dict[str, Any]]:
        """Tags of an owned document."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT t.id, t.name, t.normalized_name "
                    "FROM document_tags dt "
                    "JOIN tags t ON t.id = dt.tag_id AND t.user_id = dt.user_id "
                    "JOIN documents d ON d.id = dt.document_id AND d.user_id = dt.user_id "
                    "WHERE dt.document_id = :document_id AND dt.user_id = :user_id "
                    "ORDER BY t.normalized_name ASC"
                ),
                {"document_id": document_id, "user_id": user_id},
            )
            rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def list_tag_ids_for_document(self, user_id: str, document_id: str) -> list[str]:
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT dt.tag_id FROM document_tags dt "
                    "WHERE dt.document_id = :document_id AND dt.user_id = :user_id"
                ),
                {"document_id": document_id, "user_id": user_id},
            )
            ids = [str(r[0]) for r in result.all()]
        return ids

    async def list_document_ids_for_tag(self, user_id: str, tag_id: str) -> list[str]:
        """IDs of the owner's documents carrying the tag."""
        async with self._session.begin():
            result = await self._session.execute(
                text(
                    "SELECT dt.document_id FROM document_tags dt "
                    "WHERE dt.tag_id = :tag_id AND dt.user_id = :user_id"
                ),
                {"tag_id": tag_id, "user_id": user_id},
            )
            ids = [str(r[0]) for r in result.all()]
        return ids
