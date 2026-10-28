"""Document upload/list/delete business logic (US 2.11).

Validation (proposed defaults from DECISION-PROP-03, pending PO approval):
- Allowed file types: PDF, DOCX, PNG, JPEG — checked by extension, declared
  content type, AND magic bytes (defense against mislabeled uploads).
- Max 20 MB per file, max 10 files per upload request.
- PDF page count is validated during extraction (worker milestone), not here.
"""

from typing import Any

from app.clients.storage_client import StorageClient, run_storage_op
from app.repositories.document_repo import DocumentRepository

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES_PER_REQUEST = 10

# extension -> (allowed content types, magic-byte prefixes)
ALLOWED_TYPES: dict[str, tuple[set[str], tuple[bytes, ...]]] = {
    "pdf": ({"application/pdf"}, (b"%PDF",)),
    "docx": (
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        (b"PK\x03\x04",),
    ),
    "png": ({"image/png"}, (b"\x89PNG\r\n\x1a\n",)),
    "jpg": ({"image/jpeg"}, (b"\xff\xd8\xff",)),
    "jpeg": ({"image/jpeg"}, (b"\xff\xd8\xff",)),
}


class DocumentValidationError(Exception):
    """Raised when an uploaded file violates the upload policy."""


class DocumentNotFoundError(Exception):
    """Raised when a document does not exist for the requesting owner."""


class CourseNotFoundError(Exception):
    """Raised when the target course is missing or not owned by the uploader."""


def validate_upload(file_name: str, content_type: str, data: bytes) -> str:
    """Validate one upload; returns the normalized file type (extension).

    Raises DocumentValidationError with a safe, non-internal message.
    """
    extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if extension not in ALLOWED_TYPES:
        raise DocumentValidationError(
            f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_TYPES))}."
        )
    allowed_content_types, magics = ALLOWED_TYPES[extension]

    if len(data) > MAX_FILE_BYTES:
        raise DocumentValidationError("File exceeds the 20 MB size limit.")
    if len(data) == 0:
        raise DocumentValidationError("File is empty.")
    if content_type and content_type not in allowed_content_types:
        raise DocumentValidationError("Declared content type does not match the file type.")
    if not any(data.startswith(magic) for magic in magics):
        raise DocumentValidationError("File content does not match its declared type.")

    return extension


def build_object_key(user_id: str, document_id: str, file_name: str) -> str:
    """Private-bucket key layout: {user}/{document}/{sanitized filename}."""
    safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in file_name)[-120:]
    return f"{user_id}/{document_id}/{safe_name}"


class DocumentService:
    """Document use cases for the authenticated owner."""

    def __init__(self, document_repo: DocumentRepository, storage: StorageClient) -> None:
        self._document_repo = document_repo
        self._storage = storage

    @classmethod
    def from_session(cls, session: Any, storage: StorageClient | None = None) -> "DocumentService":
        """Compose the service over one session (storage injected for tests)."""
        return cls(
            document_repo=DocumentRepository(session),
            storage=storage or StorageClient(),
        )

    async def upload_documents(
        self,
        user_id: str,
        *,
        title: str,
        course_id: str | None,
        files: list[tuple[str, str, bytes]],  # (file_name, content_type, bytes)
    ) -> list[dict[str, Any]]:
        """Validate, store, and register each uploaded file as a pending document."""
        if not files:
            raise DocumentValidationError("No files provided.")
        if len(files) > MAX_FILES_PER_REQUEST:
            raise DocumentValidationError(
                f"Too many files: max {MAX_FILES_PER_REQUEST} per upload request."
            )
        if course_id is not None and not await self._document_repo.owned_course_exists(
            user_id, course_id
        ):
            raise CourseNotFoundError("Course not found.")

        normalized: list[tuple[str, str, bytes]] = []
        for file_name, content_type, data in files:
            file_type = validate_upload(file_name, content_type, data)
            normalized.append((file_name, file_type, data))

        created: list[dict[str, Any]] = []
        for file_name, file_type, data in normalized:
            from uuid import uuid4

            document_id = str(uuid4())
            object_key = build_object_key(user_id, document_id, file_name)
            await run_storage_op(
                self._storage.upload_bytes,
                object_key,
                data,
                content_type=_content_type_for(file_type),
            )
            try:
                row = await self._document_repo.create_document(
                    document_id=document_id,
                    user_id=user_id,
                    course_id=course_id,
                    title=title or file_name,
                    original_file_name=file_name,
                    storage_object_key=object_key,
                    file_type=file_type,
                    file_size_bytes=len(data),
                )
            except Exception:
                # Compensate: never orphan bytes in the private bucket.
                await run_storage_op(self._storage.remove_object, object_key)
                raise
            row["id"] = str(row["id"])
            created.append(row)
        return created

    async def list_documents(
        self,
        user_id: str,
        *,
        course_id: str | None = None,
        status: str | None = None,
        file_type: str | None = None,
        title_query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return await self._document_repo.list_documents(
            user_id,
            course_id=course_id,
            status=status,
            file_type=file_type,
            title_query=title_query,
            limit=min(limit, 100),
            offset=max(offset, 0),
        )

    async def get_document_download_url(self, user_id: str, document_id: str) -> dict[str, Any]:
        """Ownership-checked presigned GET (invariant: never serve bytes directly)."""
        document = await self._document_repo.get_document(user_id, document_id)
        if document is None:
            raise DocumentNotFoundError("Document not found.")
        url = await run_storage_op(self._storage.presigned_get_url, document["storage_object_key"])
        return {
            "document_id": str(document["id"]),
            "file_name": document["original_file_name"],
            "download_url": url,
        }

    async def delete_document(self, user_id: str, document_id: str) -> dict[str, Any]:
        """Delete metadata and the stored object (owner-checked)."""
        deleted = await self._document_repo.delete_document(user_id, document_id)
        if deleted is None:
            raise DocumentNotFoundError("Document not found.")
        await run_storage_op(self._storage.remove_object, deleted["storage_object_key"])
        return {"deleted": True}


def _content_type_for(file_type: str) -> str:
    mapping = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
    }
    return mapping[file_type]
