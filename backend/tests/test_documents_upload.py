"""Unit tests for TASK-M3-02: document metadata & upload validation (US 2.11)."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.documents import get_document_service
from app.core.security import create_access_token
from app.main import app
from app.repositories.document_repo import DocumentRepository
from app.clients.storage_client import StorageClient
from app.services.document_service import (
    DocumentService,
    DocumentValidationError,
    validate_upload,
)

USER_ID = "d1d1d1d1-d1d1-d1d1-d1d1-d1d1d1d1d1d1"
OTHER_ID = "e2e2e2e2-e2e2-e2e2-e2e2-e2e2e2e2e2e2"
COURSE_ID = "f3f3f3f3-f3f3-f3f3-f3f3-f3f3f3f3f3f3"
DOC_ID = "a4a4a4a4-a4a4-a4a4-a4a4-a4a4a4a4a4a4"

PDF_BYTES = b"%PDF-1.4 minimal pdf body"
PNG_BYTES = b"\x89PNG\r\n\x1a\nimage-data"
DOCX_BYTES = b"PK\x03\x04word-document-data"
JPEG_BYTES = b"\xff\xd8\xffjpeg-data"


class FakeStorage(StorageClient):
    """In-memory object store double (typed as the real client)."""

    def __init__(self, client: Any = None) -> None:
        self._client = client  # type: ignore[assignment]  # bypass the SDK constructor

        self.objects: dict[str, bytes] = {}
        self.removed: list[str] = []
        self.presigned: list[str] = []

    def upload_bytes(self, object_key: str, data: bytes, content_type: str, length: int | None = None) -> str:
        self.objects[object_key] = data
        return object_key

    def presigned_get_url(self, object_key: str, expires_minutes: int = 15) -> str:
        self.presigned.append(object_key)
        return f"https://minio/signed/{object_key}"

    def remove_object(self, object_key: str) -> None:
        self.removed.append(object_key)


class FakeDocumentRepository(DocumentRepository):
    def __init__(self, session: AsyncSession | None = None) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.course_owners: dict[str, str] = {COURSE_ID: USER_ID}

    async def owned_course_exists(self, user_id: str, course_id: str) -> bool:
        return self.course_owners.get(course_id) == user_id

    async def create_document(self, **kwargs: Any) -> dict[str, Any]:
        row = {
            "id": kwargs["document_id"],
            "user_id": kwargs["user_id"],
            "course_id": kwargs.get("course_id"),
            "title": kwargs["title"],
            "original_file_name": kwargs["original_file_name"],
            "storage_object_key": kwargs["storage_object_key"],
            "file_type": kwargs["file_type"],
            "file_size_bytes": kwargs["file_size_bytes"],
            "status": "pending",
            "total_chunks": 0,
            "chunks_processed": 0,
            "created_at": "2026-10-28T00:00:00+00:00",
        }
        self.documents[row["id"]] = row
        return dict(row)

    async def list_documents(self, user_id: str, **filters: Any) -> list[dict[str, Any]]:
        rows = [d for d in self.documents.values() if d["user_id"] == user_id]
        if filters.get("course_id"):
            rows = [d for d in rows if d["course_id"] == filters["course_id"]]
        if filters.get("status"):
            rows = [d for d in rows if d["status"] == filters["status"]]
        if filters.get("title_query"):
            rows = [d for d in rows if filters["title_query"].lower() in d["title"].lower()]
        return [dict(d) for d in rows]

    async def get_document(self, user_id: str, document_id: str) -> dict[str, Any] | None:
        row = self.documents.get(document_id)
        if row and row["user_id"] == user_id:
            return dict(row)
        return None

    async def delete_document(self, user_id: str, document_id: str) -> dict[str, Any] | None:
        row = self.documents.get(document_id)
        if not row or row["user_id"] != user_id:
            return None
        del self.documents[document_id]
        self.deleted.append(document_id)
        return {"storage_object_key": row["storage_object_key"]}


@pytest_asyncio.fixture
async def client_and_fakes() -> AsyncGenerator[tuple[AsyncClient, FakeDocumentRepository, FakeStorage], None]:
    repo = FakeDocumentRepository()
    storage = FakeStorage()

    async def override_document_service() -> DocumentService:
        return DocumentService(document_repo=repo, storage=storage)

    app.dependency_overrides[get_document_service] = override_document_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo, storage
    app.dependency_overrides.clear()


def auth(user_id: str = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def upload_files(**kwargs: Any) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", ("report.pdf", PDF_BYTES, "application/pdf"))


@pytest.mark.asyncio
async def test_upload_pdf_creates_pending_document_in_private_layout(
    client_and_fakes: tuple[AsyncClient, FakeDocumentRepository, FakeStorage],
) -> None:
    client, repo, storage = client_and_fakes
    response = await client.post(
        "/api/v1/documents",
        files=[upload_files()],
        data={"title": "ML Report", "course_id": COURSE_ID},
        headers=auth(),
    )

    assert response.status_code == 201
    data = response.json()["data"][0]
    assert data["status"] == "pending"
    assert data["file_type"] == "pdf"
    assert data["title"] == "ML Report"
    assert data["course_id"] == COURSE_ID
    key = list(storage.objects)[0]
    assert key.startswith(f"{USER_ID}/{data['id']}/")


@pytest.mark.asyncio
async def test_upload_accepts_docx_png_jpeg_magic_bytes(
    client_and_fakes: tuple[AsyncClient, FakeDocumentRepository, FakeStorage],
) -> None:
    client, _, storage = client_and_fakes
    response = await client.post(
        "/api/v1/documents",
        files=[
            ("files", ("notes.docx", DOCX_BYTES, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
            ("files", ("diagram.png", PNG_BYTES, "image/png")),
            ("files", ("scan.jpg", JPEG_BYTES, "image/jpeg")),
        ],
        headers=auth(),
    )

    assert response.status_code == 201
    assert [d["file_type"] for d in response.json()["data"]] == ["docx", "png", "jpg"]
    assert len(storage.objects) == 3


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_extension(
    client_and_fakes: tuple[AsyncClient, FakeDocumentRepository, FakeStorage],
) -> None:
    client, repo, storage = client_and_fakes
    response = await client.post(
        "/api/v1/documents",
        files=[("files", ("virus.exe", b"MZ\x90\x00", "application/octet-stream"))],
        headers=auth(),
    )

    assert response.status_code == 422
    assert "Unsupported file type" in response.json()["error"]["message"]
    assert repo.documents == {}
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_upload_rejects_magic_mismatch(
    client_and_fakes: tuple[AsyncClient, FakeDocumentRepository, FakeStorage],
) -> None:
    """A text file renamed .pdf must not pass (content/type agreement)."""
    client, repo, _ = client_and_fakes
    response = await client.post(
        "/api/v1/documents",
        files=[("files", ("fake.pdf", b"just plain text", "application/pdf"))],
        headers=auth(),
    )

    assert response.status_code == 422
    assert "does not match" in response.json()["error"]["message"]
    assert repo.documents == {}


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file(
    client_and_fakes: tuple[AsyncClient, FakeDocumentRepository, FakeStorage],
) -> None:
    client, _, _ = client_and_fakes
    big = b"%PDF" + b"x" * (20 * 1024 * 1024)
    response = await client.post(
        "/api/v1/documents",
        files=[("files", ("big.pdf", big, "application/pdf"))],
        headers=auth(),
    )

    assert response.status_code == 422
    assert "20 MB" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_upload_rejects_more_than_ten_files(
    client_and_fakes: tuple[AsyncClient, FakeDocumentRepository, FakeStorage],
) -> None:
    client, _, _ = client_and_fakes
    many = [("files", (f"f{i}.pdf", PDF_BYTES, "application/pdf")) for i in range(11)]
    response = await client.post("/api/v1/documents", files=many, headers=auth())

    assert response.status_code == 422
    assert "max 10" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_upload_to_foreign_course_is_404(
    client_and_fakes: tuple[AsyncClient, FakeDocumentRepository, FakeStorage],
) -> None:
    client, repo, storage = client_and_fakes
    response = await client.post(
        "/api/v1/documents",
        files=[upload_files()],
        data={"course_id": "00000000-0000-0000-0000-000000000000"},
        headers=auth(),
    )

    assert response.status_code == 404
    assert repo.documents == {}
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_download_returns_presigned_url_only_for_owner(
    client_and_fakes: tuple[AsyncClient, FakeDocumentRepository, FakeStorage],
) -> None:
    client, repo, storage = client_and_fakes
    created = await client.post(
        "/api/v1/documents", files=[upload_files()], headers=auth()
    )
    doc_id = created.json()["data"][0]["id"]

    own = await client.get(f"/api/v1/documents/{doc_id}/download", headers=auth())
    foreign = await client.get(f"/api/v1/documents/{doc_id}/download", headers=auth(OTHER_ID))

    assert own.status_code == 200
    assert own.json()["data"]["download_url"].startswith("https://minio/signed/")
    assert storage.presigned == [list(storage.objects)[0]]
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_removes_metadata_and_object(
    client_and_fakes: tuple[AsyncClient, FakeDocumentRepository, FakeStorage],
) -> None:
    client, repo, storage = client_and_fakes
    created = await client.post("/api/v1/documents", files=[upload_files()], headers=auth())
    doc_id = created.json()["data"][0]["id"]
    object_key = list(storage.objects)[0]

    response = await client.delete(f"/api/v1/documents/{doc_id}", headers=auth())

    assert response.status_code == 200
    assert repo.deleted == [doc_id]
    assert storage.removed == [object_key]
    foreign = await client.delete(f"/api/v1/documents/{doc_id}", headers=auth(OTHER_ID))
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_list_documents_filters(
    client_and_fakes: tuple[AsyncClient, FakeDocumentRepository, FakeStorage],
) -> None:
    client, repo, _ = client_and_fakes
    created = await client.post(
        "/api/v1/documents",
        files=[upload_files()],
        data={"course_id": COURSE_ID, "title": "My Report"},
        headers=auth(),
    )
    doc = created.json()["data"][0]

    by_course = await client.get(
        f"/api/v1/documents?course_id={COURSE_ID}", headers=auth()
    )
    by_status = await client.get("/api/v1/documents?status=pending", headers=auth())
    by_query = await client.get("/api/v1/documents?q=report", headers=auth())
    by_wrong_status = await client.get("/api/v1/documents?status=embedded", headers=auth())

    assert len(by_course.json()["data"]) == 1
    assert len(by_status.json()["data"]) == 1
    assert len(by_query.json()["data"]) == 1
    assert by_wrong_status.json()["data"] == []
    assert by_course.json()["data"][0]["id"] == doc["id"]


@pytest.mark.asyncio
async def test_validate_upload_unit_edges() -> None:
    """Direct unit coverage of the validation contract."""
    assert validate_upload("a.pdf", "application/pdf", PDF_BYTES) == "pdf"
    with pytest.raises(DocumentValidationError):
        validate_upload("noext", "application/pdf", PDF_BYTES)
    with pytest.raises(DocumentValidationError):
        validate_upload("empty.pdf", "application/pdf", b"")
