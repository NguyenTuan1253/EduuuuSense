"""Document endpoints (US 2.11): multipart upload, list/search, download, delete."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.core.database import async_session_factory
from app.services.document_service import (
    CourseNotFoundError,
    DocumentNotFoundError,
    DocumentService,
    DocumentValidationError,
)
from app.services.user_service import get_current_user_id

router = APIRouter(prefix="/documents", tags=["documents"])


async def get_document_service(
    user_id: Annotated[str, Depends(get_current_user_id)],
) -> AsyncGenerator[DocumentService, None]:
    """Request-scoped DocumentService composed over one session + storage."""
    async with async_session_factory() as session:
        yield DocumentService.from_session(session)


class DocumentData(BaseModel):
    id: str
    course_id: str | None = None
    title: str
    original_file_name: str
    file_type: str
    file_size_bytes: int
    status: str
    total_chunks: int
    chunks_processed: int
    created_at: str | None = None


class DocumentListResponse(BaseModel):
    success: bool = True
    data: list[DocumentData]


class DocumentResponse(BaseModel):
    success: bool = True
    data: list[DocumentData]


class DownloadResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class DeleteResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


def _to_data(row: dict[str, Any]) -> DocumentData:
    return DocumentData(
        id=str(row["id"]),
        course_id=str(row["course_id"]) if row.get("course_id") else None,
        title=row["title"],
        original_file_name=row["original_file_name"],
        file_type=row["file_type"],
        file_size_bytes=int(row["file_size_bytes"]),
        status=row["status"],
        total_chunks=int(row.get("total_chunks") or 0),
        chunks_processed=int(row.get("chunks_processed") or 0),
        created_at=str(row["created_at"]) if row.get("created_at") is not None else None,
    )


def _map_service_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, DocumentValidationError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, CourseNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, DocumentNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    raise exc


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_documents(
    files: list[UploadFile],
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[DocumentService, Depends(get_document_service)],
    title: str | None = Form(default=None, max_length=255),
    course_id: str | None = Form(default=None),
) -> DocumentResponse:
    """Upload 1..10 documents (PDF/DOCX/PNG/JPEG, <=20MB each)."""
    payloads: list[tuple[str, str, bytes]] = []
    for upload in files:
        data = await upload.read()
        payloads.append((upload.filename or "unnamed", upload.content_type or "", data))

    try:
        rows = await service.upload_documents(
            user_id, title=title or "", course_id=course_id, files=payloads
        )
    except (DocumentValidationError, CourseNotFoundError) as exc:
        raise _map_service_errors(exc) from exc
    return DocumentResponse(data=[_to_data(r) for r in rows])


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[DocumentService, Depends(get_document_service)],
    course_id: str | None = Query(default=None),
    doc_status: str | None = Query(
        default=None, alias="status", pattern="^(pending|processing|embedded|failed)$"
    ),
    file_type: str | None = Query(default=None, pattern="^(pdf|docx|png|jpg|jpeg)$"),
    title_query: str | None = Query(default=None, alias="q", max_length=100),
    tag: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> DocumentListResponse:
    rows = await service.list_documents(
        user_id,
        course_id=course_id,
        status=doc_status,
        file_type=file_type,
        title_query=title_query,
        tag_name=tag,
        limit=limit,
        offset=offset,
    )
    return DocumentListResponse(data=[_to_data(r) for r in rows])


class TagAttachmentResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]


class TagListResponse(BaseModel):
    success: bool = True
    data: list[dict[str, Any]]


class TagAttachRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/{document_id}/tags", response_model=TagListResponse)
async def list_document_tags(
    document_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> TagListResponse:
    try:
        tags = await service.list_document_tags(user_id, document_id)
    except DocumentNotFoundError as exc:
        raise _map_service_errors(exc) from exc
    return TagListResponse(data=tags)


@router.post("/{document_id}/tags", response_model=TagAttachmentResponse, status_code=status.HTTP_201_CREATED)
async def attach_tag(
    document_id: str,
    payload: TagAttachRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> TagAttachmentResponse:
    """Create-or-get + attach; repeated calls are idempotent."""
    try:
        result = await service.attach_tag(user_id, document_id, payload.name)
    except DocumentValidationError as exc:
        raise _map_service_errors(exc) from exc
    except DocumentNotFoundError as exc:
        raise _map_service_errors(exc) from exc
    return TagAttachmentResponse(data=result)


@router.delete("/{document_id}/tags/{tag_id}", response_model=DeleteResponse)
async def detach_tag(
    document_id: str,
    tag_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DeleteResponse:
    try:
        result = await service.detach_tag(user_id, document_id, tag_id)
    except DocumentNotFoundError as exc:
        raise _map_service_errors(exc) from exc
    return DeleteResponse(data=result)


@router.get("/{document_id}/download", response_model=DownloadResponse)
async def download_document(
    document_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DownloadResponse:
    """Ownership-checked presigned GET URL — bytes are never proxied here."""
    try:
        result = await service.get_document_download_url(user_id, document_id)
    except DocumentNotFoundError as exc:
        raise _map_service_errors(exc) from exc
    return DownloadResponse(data=result)


@router.delete("/{document_id}", response_model=DeleteResponse)
async def delete_document(
    document_id: str,
    user_id: Annotated[str, Depends(get_current_user_id)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DeleteResponse:
    try:
        result = await service.delete_document(user_id, document_id)
    except DocumentNotFoundError as exc:
        raise _map_service_errors(exc) from exc
    return DeleteResponse(data=result)
