"""Pydantic schemas for the Artifacts module."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ArtifactCreateRequest(BaseModel):
    """Direct upload payload for text or base64 serialized artifact."""

    name: str = Field(..., max_length=255)
    artifact_type: str = Field(..., max_length=100, description="screenshot, log, report, json, trace, video")
    content: str = Field(..., description="Raw text or UTF-8 serialized content")
    content_type: str = Field("application/json", max_length=100)


class ArtifactResponse(BaseModel):
    """Canonical representation of a stored artifact."""

    id: uuid.UUID
    project_id: uuid.UUID
    run_id: Optional[uuid.UUID] = None
    name: str
    artifact_type: str
    minio_key: str
    content_hash: str
    file_size_bytes: int
    created_at: datetime
    download_url: Optional[str] = None
    run_status: Optional[str] = None

    model_config = {"from_attributes": True}


class ArtifactListResponse(BaseModel):
    """Paginated list of artifacts."""

    items: List[ArtifactResponse]
    total: int
    limit: int
    offset: int
