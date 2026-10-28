"""MinIO object storage client (DECISION-CONFIRMED-06).

The bucket is private: no public policy is ever applied, and all client
access goes through this backend via presigned URLs or authorized endpoints.
The database stores metadata and object keys only — never file bytes.
"""

import asyncio
from datetime import timedelta
from typing import Any

from minio import Minio
from minio.error import MinioException

from app.core.config import settings


class StorageError(Exception):
    """Raised when an object-storage operation fails."""


class StorageClient:
    """MinIO operations for the private documents bucket. Injectable for tests."""

    def __init__(self, client: Minio | None = None) -> None:
        self._bucket = settings.minio_bucket_name
        self._client = client or Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def ensure_bucket(self) -> None:
        """Create the private bucket when missing. Never sets a public policy."""
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
        except MinioException as exc:
            raise StorageError(f"Bucket setup failed: {type(exc).__name__}") from exc

    def upload_bytes(
        self, object_key: str, data: bytes, content_type: str, length: int | None = None
    ) -> str:
        """Store bytes at ``object_key``; returns the key (DB stores keys only)."""
        import io

        try:
            self._client.put_object(
                self._bucket,
                object_key,
                data=io.BytesIO(data),
                length=length if length is not None else len(data),
                content_type=content_type,
            )
        except MinioException as exc:
            raise StorageError(f"Upload failed: {type(exc).__name__}") from exc
        return object_key

    def presigned_put_url(self, object_key: str, expires_minutes: int = 15) -> str:
        """Short-lived PUT URL for direct-to-MinIO uploads from the client."""
        try:
            return self._client.presigned_put_object(
                self._bucket, object_key, expires=timedelta(minutes=expires_minutes)
            )
        except MinioException as exc:
            raise StorageError(f"Presign failed: {type(exc).__name__}") from exc

    def presigned_get_url(self, object_key: str, expires_minutes: int = 15) -> str:
        """Short-lived GET URL; ownership must be authorized by the caller."""
        try:
            return self._client.presigned_get_object(
                self._bucket, object_key, expires=timedelta(minutes=expires_minutes)
            )
        except MinioException as exc:
            raise StorageError(f"Presign failed: {type(exc).__name__}") from exc

    def download_bytes(self, object_key: str) -> bytes:
        """Read an object fully (used by the extraction worker)."""
        try:
            response = self._client.get_object(self._bucket, object_key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except MinioException as exc:
            raise StorageError(f"Download failed: {type(exc).__name__}") from exc

    def remove_object(self, object_key: str) -> None:
        try:
            self._client.remove_object(self._bucket, object_key)
        except MinioException as exc:
            raise StorageError(f"Delete failed: {type(exc).__name__}") from exc

    def object_exists(self, object_key: str) -> bool:
        """True when the object stats; a missing object (S3 error) means False.

        Non-SDK failures propagate: a network outage is not evidence that an
        object is missing.
        """
        try:
            self._client.stat_object(self._bucket, object_key)
            return True
        except MinioException:
            return False


async def run_storage_op(op: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking storage operation off the event loop."""
    return await asyncio.to_thread(op, *args, **kwargs)
