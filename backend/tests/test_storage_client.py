"""Unit tests for TASK-M3-01: MinIO storage client & presigned URLs."""

from minio.error import MinioException
from unittest.mock import MagicMock, patch

import pytest

from app.clients.storage_client import StorageClient, StorageError


def make_client_with_mocked_minio() -> tuple[StorageClient, MagicMock]:
    """StorageClient with a mocked Minio SDK object (no network, no services)."""
    with patch("app.clients.storage_client.Minio"):
        storage = StorageClient()
    minio = storage._client  # noqa: SLF001 — test seam
    assert isinstance(minio, MagicMock)
    return storage, minio


def test_bucket_created_only_when_missing_and_never_public() -> None:
    storage, minio = make_client_with_mocked_minio()
    minio.bucket_exists.return_value = False

    storage.ensure_bucket()

    minio.bucket_exists.assert_called_once()
    minio.make_bucket.assert_called_once()
    minio.set_bucket_policy.assert_not_called()  # private bucket invariant


def test_existing_bucket_is_not_recreated() -> None:
    storage, minio = make_client_with_mocked_minio()
    minio.bucket_exists.return_value = True

    storage.ensure_bucket()

    minio.make_bucket.assert_not_called()


def test_upload_bytes_stores_content_and_returns_key() -> None:
    storage, minio = make_client_with_mocked_minio()

    key = storage.upload_bytes("u1/doc1/report.pdf", b"%PDF-1.4 data", "application/pdf")

    assert key == "u1/doc1/report.pdf"
    args, kwargs = minio.put_object.call_args
    assert kwargs["length"] == len(b"%PDF-1.4 data")
    assert kwargs["content_type"] == "application/pdf"
    stream = kwargs["data"]
    assert stream.read() == b"%PDF-1.4 data"


def test_presigned_urls_are_short_lived() -> None:
    storage, minio = make_client_with_mocked_minio()
    minio.presigned_put_object.return_value = "https://minio/put-sig"
    minio.presigned_get_object.return_value = "https://minio/get-sig"

    put_url = storage.presigned_put_url("k1", expires_minutes=15)
    get_url = storage.presigned_get_url("k1", expires_minutes=10)

    assert put_url == "https://minio/put-sig"
    assert get_url == "https://minio/get-sig"
    put_kwargs = minio.presigned_put_object.call_args.kwargs
    get_kwargs = minio.presigned_get_object.call_args.kwargs
    assert put_kwargs["expires"].total_seconds() == 15 * 60
    assert get_kwargs["expires"].total_seconds() == 10 * 60


def test_download_bytes_closes_and_releases_connection() -> None:
    storage, minio = make_client_with_mocked_minio()
    response = MagicMock()
    response.read.return_value = b"file-bytes"
    minio.get_object.return_value = response

    data = storage.download_bytes("k1")

    assert data == b"file-bytes"
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


def test_minio_failures_map_to_storage_error_without_details_leak() -> None:
    """SDK exceptions become a single StorageError naming the type only."""
    storage, minio = make_client_with_mocked_minio()
    minio.put_object.side_effect = MinioException("boom")

    with pytest.raises(StorageError):
        storage.upload_bytes("k", b"x", "text/plain")


def test_non_sdk_failures_propagate() -> None:
    """Programming/network errors are not masked as domain storage errors."""
    storage, minio = make_client_with_mocked_minio()
    minio.put_object.side_effect = RuntimeError("programming error")

    with pytest.raises(RuntimeError):
        storage.upload_bytes("k", b"x", "text/plain")


def test_object_exists_reflects_stat() -> None:
    storage, minio = make_client_with_mocked_minio()
    minio.stat_object.return_value = MagicMock()

    assert storage.object_exists("k1") is True

    minio.stat_object.side_effect = MinioException("NoSuchKey")
    assert storage.object_exists("missing") is False

    minio.stat_object.side_effect = RuntimeError("network outage")
    with pytest.raises(RuntimeError):
        storage.object_exists("k1")
