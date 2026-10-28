"""Unit tests for TASK-M3-06: document search/filter & tag management (US 2.11)."""

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
from app.services.document_service import DocumentService

USER_ID = "07770777-0777-0777-0777-077777777777"
OTHER_ID = "08880888-0888-0888-0888-088888888888"
DOC_A = "19999999-9999-9999-9999-999999999991"
DOC_B = "19999999-9999-9999-9999-999999999992"
TAG_CS = "29999999-9999-9999-9999-999999999991"


class FakeDocumentRepository(DocumentRepository):
    """In-memory documents + tags store."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.tags: dict[str, dict[str, Any]] = {}
        self.attachments: set[tuple[str, str]] = set()  # (document_id, tag_id)

    async def get_document(self, user_id: str, document_id: str) -> dict[str, Any] | None:
        row = self.documents.get(document_id)
        if row and row["user_id"] == user_id:
            return dict(row)
        return None

    async def get_or_create_tag(self, user_id: str, name: str) -> dict[str, Any]:
        normalized = name.strip().lower()
        existing = next(
            (t for t in self.tags.values() if t["user_id"] == user_id and t["normalized_name"] == normalized),
            None,
        )
        if existing:
            return dict(existing)
        tag_id = f"tag-{len(self.tags) + 1}"
        self.tags[tag_id] = {
            "id": tag_id,
            "user_id": user_id,
            "name": name.strip(),
            "normalized_name": normalized,
        }
        return dict(self.tags[tag_id])

    async def attach_tag(self, user_id: str, document_id: str, tag_id: str) -> bool:
        if document_id not in self.documents or tag_id not in self.tags:
            return False
        key = (document_id, tag_id)
        if key in self.attachments:
            return False
        self.attachments.add(key)
        return True

    async def detach_tag(self, user_id: str, document_id: str, tag_id: str) -> bool:
        document = self.documents.get(document_id)
        if not document or document["user_id"] != user_id:
            return False  # mirrors the ownership join in the real SQL
        if (document_id, tag_id) in self.attachments:
            self.attachments.discard((document_id, tag_id))
            return True
        return False

    async def list_document_tags(self, user_id: str, document_id: str) -> list[dict[str, Any]]:
        document = self.documents.get(document_id)
        if not document or document["user_id"] != user_id:
            return []
        names = sorted(
            self.tags[tag_id]["normalized_name"]
            for doc_id, tag_id in self.attachments
            if doc_id == document_id
        )
        by_name = {t["normalized_name"]: t for t in self.tags.values()}
        return [dict(by_name[name]) for name in names]

    async def list_documents(self, user_id: str, **filters: Any) -> list[dict[str, Any]]:
        rows = [d for d in self.documents.values() if d["user_id"] == user_id]
        tag_id = filters.get("tag_id")
        if tag_id:
            tagged = {doc_id for doc_id, tid in self.attachments if tid == tag_id}
            rows = [d for d in rows if d["id"] in tagged]
        if filters.get("title_query"):
            q = filters["title_query"].lower()
            rows = [d for d in rows if q in d["title"].lower()]
        return [dict(d) for d in rows]


def make_document(doc_id: str, user_id: str, title: str) -> dict[str, Any]:
    return {
        "id": doc_id,
        "user_id": user_id,
        "course_id": None,
        "title": title,
        "original_file_name": f"{title}.pdf",
        "storage_object_key": f"{user_id}/{doc_id}/f.pdf",
        "file_type": "pdf",
        "file_size_bytes": 100,
        "status": "embedded",
        "total_chunks": 2,
        "chunks_processed": 2,
        "created_at": "2026-10-28T00:00:00+00:00",
    }


@pytest_asyncio.fixture
async def client_and_repo() -> AsyncGenerator[tuple[AsyncClient, FakeDocumentRepository], None]:
    repo = FakeDocumentRepository()
    repo.documents[DOC_A] = make_document(DOC_A, USER_ID, "Machine Learning Notes")
    repo.documents[DOC_B] = make_document(DOC_B, USER_ID, "Calculus Summary")

    async def override_document_service() -> DocumentService:
        return DocumentService(document_repo=repo, storage=type("S", (), {"upload_bytes": staticmethod(lambda *a, **k: "")})())

    app.dependency_overrides[get_document_service] = override_document_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    app.dependency_overrides.clear()


def auth(user_id: str = USER_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.mark.asyncio
async def test_search_by_title_query_is_owner_scoped(
    client_and_repo: tuple[AsyncClient, FakeDocumentRepository],
) -> None:
    client, _ = client_and_repo
    own = await client.get("/api/v1/documents?q=machine", headers=auth())
    other = await client.get("/api/v1/documents?q=machine", headers=auth(OTHER_ID))

    assert [d["id"] for d in own.json()["data"]] == [DOC_A]
    assert other.json()["data"] == []


@pytest.mark.asyncio
async def test_attach_tag_normalizes_and_is_idempotent(
    client_and_repo: tuple[AsyncClient, FakeDocumentRepository],
) -> None:
    client, repo = client_and_repo
    first = await client.post(
        f"/api/v1/documents/{DOC_A}/tags", json={"name": "  ML  "}, headers=auth()
    )
    second = await client.post(
        f"/api/v1/documents/{DOC_A}/tags", json={"name": "ml"}, headers=auth()
    )

    assert first.status_code == second.status_code == 201
    tag = first.json()["data"]["tag"]
    assert tag["normalized_name"] == "ml"
    assert len(repo.tags) == 1  # same tag reused, not duplicated
    assert len(repo.attachments) == 1


@pytest.mark.asyncio
async def test_attach_tag_to_foreign_document_is_404(
    client_and_repo: tuple[AsyncClient, FakeDocumentRepository],
) -> None:
    client, _ = client_and_repo
    response = await client.post(
        f"/api/v1/documents/{DOC_A}/tags", json={"name": "ml"}, headers=auth(OTHER_ID)
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_document_tags(
    client_and_repo: tuple[AsyncClient, FakeDocumentRepository],
) -> None:
    client, _ = client_and_repo
    await client.post(f"/api/v1/documents/{DOC_A}/tags", json={"name": "ML"}, headers=auth())
    await client.post(f"/api/v1/documents/{DOC_A}/tags", json={"name": "exam"}, headers=auth())
    # malformed body -> 422
    malformed = await client.post(f"/api/v1/documents/{DOC_A}/tags", json={}, headers=auth())

    listed = await client.get(f"/api/v1/documents/{DOC_A}/tags", headers=auth())

    assert malformed.status_code == 422
    assert listed.status_code == 200
    assert [t["normalized_name"] for t in listed.json()["data"]] == ["exam", "ml"]


@pytest.mark.asyncio
async def test_detach_tag_and_foreign_isolation(
    client_and_repo: tuple[AsyncClient, FakeDocumentRepository],
) -> None:
    client, repo = client_and_repo
    attached = await client.post(
        f"/api/v1/documents/{DOC_A}/tags", json={"name": "ml"}, headers=auth()
    )
    tag_id = attached.json()["data"]["tag"]["id"]

    foreign = await client.delete(f"/api/v1/documents/{DOC_A}/tags/{tag_id}", headers=auth(OTHER_ID))
    assert foreign.status_code == 404

    removed = await client.delete(f"/api/v1/documents/{DOC_A}/tags/{tag_id}", headers=auth())
    assert removed.status_code == 200
    assert removed.json()["data"]["detached"] is True

    again = await client.delete(f"/api/v1/documents/{DOC_A}/tags/{tag_id}", headers=auth())
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_filter_documents_by_tag(
    client_and_repo: tuple[AsyncClient, FakeDocumentRepository],
) -> None:
    client, _ = client_and_repo
    await client.post(f"/api/v1/documents/{DOC_A}/tags", json={"name": "ml"}, headers=auth())

    tagged = await client.get("/api/v1/documents?tag=ml", headers=auth())
    untagged = await client.get("/api/v1/documents?tag=nonexistent", headers=auth())

    assert [d["id"] for d in tagged.json()["data"]] == [DOC_A]
    assert untagged.json()["data"] == []


@pytest.mark.asyncio
async def test_combined_filters_title_and_tag(
    client_and_repo: tuple[AsyncClient, FakeDocumentRepository],
) -> None:
    client, _ = client_and_repo
    await client.post(f"/api/v1/documents/{DOC_A}/tags", json={"name": "ml"}, headers=auth())
    await client.post(f"/api/v1/documents/{DOC_B}/tags", json={"name": "ml"}, headers=auth())

    combined = await client.get("/api/v1/documents?tag=ml&q=calculus", headers=auth())

    assert [d["id"] for d in combined.json()["data"]] == [DOC_B]
