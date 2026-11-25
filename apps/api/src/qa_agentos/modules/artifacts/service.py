"""Business logic and storage operations for Artifacts."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from qa_agentos.core.errors import NotFoundError
from qa_agentos.models.domain import Artifact, Run
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.runtime.events import queue_run_event, utc_now
from qa_agentos.runtime.pubsub import RunEventPayload, publish_run_event
from qa_agentos.runtime.storage import get_storage_client
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

logger = get_logger(__name__)


class ArtifactsService:
    """Manages project-isolated artifact records and object store operations."""

    @staticmethod
    async def list_artifacts(
        db: AsyncSession,
        project_id: uuid.UUID,
        run_id: Optional[uuid.UUID] = None,
        artifact_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[dict[str, Any]], int]:
        """List artifacts strictly isolated to the project."""
        query = (
            select(Artifact, Run.status.label("run_status"))
            .outerjoin(Run, Artifact.run_id == Run.id)
            .where(Artifact.project_id == project_id)
        )

        if run_id:
            query = query.where(Artifact.run_id == run_id)
        if artifact_type:
            query = query.where(Artifact.artifact_type == artifact_type)

        count_stmt = select(func.count(Artifact.id)).where(Artifact.project_id == project_id)
        if run_id:
            count_stmt = count_stmt.where(Artifact.run_id == run_id)
        if artifact_type:
            count_stmt = count_stmt.where(Artifact.artifact_type == artifact_type)

        total_res = await db.execute(count_stmt)
        total = total_res.scalar() or 0

        query = query.order_by(Artifact.created_at.desc()).offset(offset).limit(limit)
        res = await db.execute(query)
        rows = res.all()

        storage = get_storage_client()
        items = []
        for art, r_status in rows:
            url = storage.get_presigned_download_url(art.minio_key)
            items.append({
                "id": art.id,
                "project_id": art.project_id,
                "run_id": art.run_id,
                "name": art.name,
                "artifact_type": art.artifact_type,
                "minio_key": art.minio_key,
                "content_hash": art.content_hash,
                "file_size_bytes": art.file_size_bytes,
                "created_at": art.created_at,
                "download_url": url,
                "run_status": r_status,
            })

        return items, total

    @staticmethod
    async def get_artifact(
        db: AsyncSession,
        project_id: uuid.UUID,
        artifact_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Fetch single artifact detail with strict project scoping."""
        stmt = (
            select(Artifact, Run.status.label("run_status"))
            .outerjoin(Run, Artifact.run_id == Run.id)
            .where(Artifact.id == artifact_id, Artifact.project_id == project_id)
        )
        res = await db.execute(stmt)
        row = res.first()
        if not row:
            raise NotFoundError(message=f"Artifact {artifact_id} not found in current project")

        art, r_status = row
        storage = get_storage_client()
        url = storage.get_presigned_download_url(art.minio_key)

        return {
            "id": art.id,
            "project_id": art.project_id,
            "run_id": art.run_id,
            "name": art.name,
            "artifact_type": art.artifact_type,
            "minio_key": art.minio_key,
            "content_hash": art.content_hash,
            "file_size_bytes": art.file_size_bytes,
            "created_at": art.created_at,
            "download_url": url,
            "run_status": r_status,
        }

    @staticmethod
    async def create_artifact(
        db: AsyncSession,
        identity: Optional[Identity],
        project_id: uuid.UUID,
        name: str,
        artifact_type: str,
        data: bytes,
        run_id: Optional[uuid.UUID] = None,
        content_type: str = "application/octet-stream",
        *,
        commit: bool = True,
    ) -> Artifact:
        """Upload object to MinIO and record metadata in database.

        The blocking MinIO client runs in a worker thread so uploads never stall the event
        loop. With ``commit=False`` the metadata row joins the caller's transaction and no
        event is published; the caller commits (used for run outputs, so the artifact row
        and the run's terminal state are written atomically).
        """
        if run_id:
            run_stmt = select(Run).where(Run.id == run_id, Run.project_id == project_id)
            run_res = await db.execute(run_stmt)
            if not run_res.scalar_one_or_none():
                raise NotFoundError(message=f"Run {run_id} not found in current project")

        from qa_agentos.core.errors import APIError
        from qa_agentos.runtime.storage import ArtifactTooLargeError, InvalidArtifactPathError, StorageError

        allowed_types = {"screenshot", "log", "report", "json", "trace", "video", "markdown", "text", "binary"}
        if artifact_type not in allowed_types:
            raise APIError(
                status_code=400,
                code="INVALID_ARTIFACT_TYPE",
                message=f"Invalid artifact_type '{artifact_type}'. Allowed types: {sorted(allowed_types)}",
            )

        storage = get_storage_client()
        try:
            minio_key, content_hash, file_size = await asyncio.to_thread(
                storage.upload_artifact_bytes,
                project_id=project_id,
                run_id=run_id,
                name=name,
                data=data,
                content_type=content_type,
            )
        except ArtifactTooLargeError as e:
            raise APIError(status_code=413, code="ARTIFACT_TOO_LARGE", message=str(e)) from e
        except InvalidArtifactPathError as e:
            raise APIError(status_code=400, code="INVALID_FILENAME", message=str(e)) from e
        except StorageError as e:
            logger.error("MinIO storage unavailable during artifact upload", error=str(e))
            raise APIError(status_code=503, code="STORAGE_UNAVAILABLE", message="Storage service temporarily unavailable") from e

        artifact = Artifact(
            id=uuid.uuid4(),
            project_id=project_id,
            run_id=run_id,
            name=name,
            artifact_type=artifact_type,
            minio_key=minio_key,
            content_hash=content_hash,
            file_size_bytes=file_size,
            created_at=utc_now(),
        )
        db.add(artifact)
        await db.flush()
        created_event = (
            RunEventPayload(
                event="artifact.created",
                run_id=str(run_id),
                project_id=str(project_id),
                timestamp=artifact.created_at.isoformat(),
                data={
                    "artifact_id": str(artifact.id),
                    "name": artifact.name,
                    "artifact_type": artifact.artifact_type,
                    "file_size_bytes": artifact.file_size_bytes,
                },
            )
            if run_id
            else None
        )
        if not commit:
            if created_event:
                queue_run_event(db, created_event)  # published after the caller's commit
            return artifact
        await db.commit()
        await db.refresh(artifact)

        if created_event:
            await publish_run_event(created_event)

        logger.info(
            "Recorded artifact in database",
            artifact_id=str(artifact.id),
            name=name,
            size_bytes=file_size,
        )
        return artifact

    @staticmethod
    async def download_artifact_stream(
        db: AsyncSession,
        project_id: uuid.UUID,
        artifact_id: uuid.UUID,
    ) -> Tuple[bytes, str, str]:
        """Fetch raw artifact bytes, content_type, and filename."""
        stmt = select(Artifact).where(Artifact.id == artifact_id, Artifact.project_id == project_id)
        res = await db.execute(stmt)
        art = res.scalar_one_or_none()
        if not art:
            raise NotFoundError(message=f"Artifact {artifact_id} not found in current project")

        from qa_agentos.core.errors import APIError
        from qa_agentos.runtime.storage import StorageError

        storage = get_storage_client()
        try:
            data, content_type = await asyncio.to_thread(storage.get_object_data, art.minio_key)
        except StorageError as e:
            logger.error("MinIO storage unavailable during artifact download", error=str(e))
            raise APIError(status_code=503, code="STORAGE_UNAVAILABLE", message="Storage service temporarily unavailable") from e
        return data, content_type, art.name


async def store_run_output_artifact(
    db: AsyncSession, run: Run, output: Dict[str, Any], name: Optional[str] = None
) -> None:
    """Executor output sink: persist a succeeded run's output as a JSON artifact.

    Adds the artifact row to the caller's transaction (no commit) and raises on failure,
    so the executor can record the outcome on the run's terminal event.
    """
    raw_bytes = json.dumps(output, indent=2).encode("utf-8")
    await ArtifactsService.create_artifact(
        db=db,
        identity=None,
        project_id=run.project_id,
        name=name or f"testcases-{str(run.id)[:8]}.json",
        artifact_type="json",
        data=raw_bytes,
        run_id=run.id,
        content_type="application/json",
        commit=False,
    )
