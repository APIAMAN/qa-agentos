"""FastAPI router for Artifact storage and Gallery endpoints."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Response, status
from qa_agentos.core.database import get_db
from qa_agentos.modules.artifacts.schemas import (
    ArtifactCreateRequest,
    ArtifactListResponse,
    ArtifactResponse,
)
from qa_agentos.modules.artifacts.service import ArtifactsService
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.modules.auth.service import require_scope
from qa_agentos.modules.runs.service import resolve_project_id
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("", response_model=ArtifactListResponse)
async def list_artifacts(
    run_id: Optional[uuid.UUID] = Query(None, alias="run_id"),
    artifact_type: Optional[str] = Query(None, alias="type"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("artifacts:read")),
) -> ArtifactListResponse:
    """List artifact metadata filtered by run_id or type with strict project isolation."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    items, total = await ArtifactsService.list_artifacts(
        db=db,
        project_id=project_id,
        run_id=run_id,
        artifact_type=artifact_type,
        limit=limit,
        offset=offset,
    )
    return ArtifactListResponse(
        items=[ArtifactResponse(**item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: uuid.UUID,
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("artifacts:read")),
) -> ArtifactResponse:
    """Retrieve metadata and presigned download URL for a specific artifact."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    item = await ArtifactsService.get_artifact(
        db=db,
        project_id=project_id,
        artifact_id=artifact_id,
    )
    return ArtifactResponse(**item)


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: uuid.UUID,
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("artifacts:read")),
) -> Response:
    """Direct stream download fallback for an artifact."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    data, content_type, filename = await ArtifactsService.download_artifact_stream(
        db=db,
        project_id=project_id,
        artifact_id=artifact_id,
    )
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.post("", response_model=ArtifactResponse, status_code=status.HTTP_201_CREATED)
async def create_artifact(
    payload: ArtifactCreateRequest,
    run_id: Optional[uuid.UUID] = Query(None, alias="run_id"),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("artifacts:write")),
) -> ArtifactResponse:
    """Upload and record a new artifact for a project or run."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    raw_data = payload.content.encode("utf-8")
    art = await ArtifactsService.create_artifact(
        db=db,
        identity=identity,
        project_id=project_id,
        name=payload.name,
        artifact_type=payload.artifact_type,
        data=raw_data,
        run_id=run_id,
        content_type=payload.content_type,
    )
    item = await ArtifactsService.get_artifact(db, project_id, art.id)
    return ArtifactResponse(**item)
