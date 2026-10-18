"""MinIO and S3-compatible object storage client for QA AgentOS artifacts."""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from datetime import timedelta
from typing import Optional, Tuple

import urllib3
from minio import Minio
from structlog import get_logger

from qa_agentos.core.config import settings

logger = get_logger(__name__)

MAX_ARTIFACT_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB limit per artifact


class StorageError(Exception):
    """Base exception for artifact storage operations."""


class ArtifactTooLargeError(StorageError):
    """Raised when an uploaded artifact exceeds the allowed byte size."""


class InvalidArtifactPathError(StorageError):
    """Raised when an artifact name contains illegal path traversal sequences."""


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal and unsafe characters."""
    if not filename or not filename.strip():
        raise InvalidArtifactPathError("Artifact filename cannot be empty")
    # Convert slashes to underscores
    clean = re.sub(r"[\\/]+", "_", filename).strip()
    # Reject path traversal dots
    if ".." in clean or clean.startswith(".") or clean == ".":
        raise InvalidArtifactPathError(f"Path traversal not allowed: {filename}")
    clean = re.sub(r"[^a-zA-Z0-9_.-]", "_", clean).strip()
    if not clean:
        raise InvalidArtifactPathError(f"Invalid or unsafe artifact filename: {filename}")
    # Disallow executable extensions
    lower = clean.lower()
    dangerous_exts = (".exe", ".bat", ".cmd", ".sh", ".ps1", ".dll", ".so", ".dylib", ".com", ".vbs")
    if any(lower.endswith(ext) for ext in dangerous_exts):
        raise InvalidArtifactPathError(f"Forbidden executable file extension: {filename}")
    return clean[:128]


class MinioStorageClient:
    """Encapsulates interaction with MinIO / S3 object store."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        secure: Optional[bool] = None,
        bucket_name: Optional[str] = None,
    ) -> None:
        ep = endpoint or settings.MINIO_ENDPOINT or "localhost:9005"
        # Auto-fallback to host port forwarder 9005 if 9000 fails on Windows
        if ep in ("localhost:9000", "127.0.0.1:9000"):
            import socket
            sock = socket.socket()
            sock.settimeout(0.3)
            try:
                # If 9000 fails to handshake or 9005 is open, prefer 9005
                if sock.connect_ex(("localhost", 9005)) == 0:
                    ep = "localhost:9005"
            except Exception:
                pass
            finally:
                sock.close()
        self.endpoint = ep
        self.access_key = access_key or settings.MINIO_ROOT_USER or "minioadmin"
        self.secret_key = secret_key or settings.MINIO_ROOT_PASSWORD or "minioadmin"
        self.secure = secure if secure is not None else settings.MINIO_SECURE
        self.bucket = bucket_name or settings.MINIO_BUCKET or "qa-agentos-artifacts"
        self._client: Optional[Minio] = None

    def get_client(self) -> Minio:
        if self._client is None:
            # Allow insecure HTTP on local/dev
            http_client = urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=2.0, read=10.0),
                retries=urllib3.Retry(total=2, backoff_factor=0.2),
            )
            self._client = Minio(
                self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.secure,
                http_client=http_client,
            )
        return self._client

    def ensure_bucket(self) -> None:
        """Create artifact storage bucket if not already present."""
        client = self.get_client()
        try:
            if not client.bucket_exists(self.bucket):
                client.make_bucket(self.bucket)
                logger.info("Created MinIO artifact bucket", bucket=self.bucket)
        except Exception as exc:
            logger.warning("MinIO ensure_bucket probe failed", error=str(exc), bucket=self.bucket)

    def upload_artifact_bytes(
        self,
        project_id: uuid.UUID,
        run_id: Optional[uuid.UUID],
        name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> Tuple[str, str, int]:
        """Upload raw bytes to MinIO and return (minio_key, content_hash, file_size_bytes)."""
        file_size = len(data)
        if file_size > MAX_ARTIFACT_SIZE_BYTES:
            raise ArtifactTooLargeError(
                f"Artifact size {file_size} bytes exceeds maximum limit of {MAX_ARTIFACT_SIZE_BYTES} bytes"
            )

        safe_name = sanitize_filename(name)
        content_hash = hashlib.sha256(data).hexdigest()
        artifact_id = str(uuid.uuid4())[:8]

        run_part = f"runs/{run_id}/" if run_id else "global/"
        minio_key = f"{project_id}/{run_part}artifacts/{artifact_id}_{safe_name}"

        client = self.get_client()
        self.ensure_bucket()

        try:
            client.put_object(
                bucket_name=self.bucket,
                object_name=minio_key,
                data=io.BytesIO(data),
                length=file_size,
                content_type=content_type,
            )
            logger.info(
                "Uploaded artifact to MinIO",
                minio_key=minio_key,
                size_bytes=file_size,
                hash=content_hash[:8],
            )
        except Exception as exc:
            logger.error("Failed to upload artifact to MinIO", error=str(exc), minio_key=minio_key)
            raise StorageError(f"MinIO storage error: {exc}") from exc

        return minio_key, content_hash, file_size

    def get_presigned_download_url(self, minio_key: str, expires_seconds: int = 3600) -> str:
        """Generate a short-lived presigned GET URL for an artifact object."""
        client = self.get_client()
        try:
            return client.get_presigned_url(
                "GET",
                self.bucket,
                minio_key,
                expires=timedelta(seconds=expires_seconds),
            )
        except Exception as exc:
            logger.warning("Failed to generate presigned URL", error=str(exc), minio_key=minio_key)
            return f"/v1/artifacts/download?key={minio_key}"

    def get_object_data(self, minio_key: str) -> Tuple[bytes, str]:
        """Retrieve raw object bytes and content_type from MinIO."""
        client = self.get_client()
        try:
            stat = client.stat_object(self.bucket, minio_key)
            content_type = stat.content_type or "application/octet-stream"
            response = client.get_object(self.bucket, minio_key)
            data = response.read()
            response.close()
            response.release_conn()
            return data, content_type
        except Exception as exc:
            logger.error("Failed to fetch object from MinIO", error=str(exc), minio_key=minio_key)
            raise StorageError(f"MinIO retrieval error: {exc}") from exc


_storage_client: Optional[MinioStorageClient] = None


def get_storage_client() -> MinioStorageClient:
    global _storage_client
    if _storage_client is None:
        _storage_client = MinioStorageClient()
    return _storage_client
