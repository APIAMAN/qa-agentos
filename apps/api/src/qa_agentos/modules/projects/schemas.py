import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(..., max_length=255)
    slug: str = Field(..., max_length=255)
    description: Optional[str] = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class APIKeyCreate(BaseModel):
    name: str = Field(..., max_length=255)
    scopes: List[str] = Field(default_factory=lambda: ["runs:read", "runs:create"])


class APIKeyResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    key_prefix: str
    scopes: List[str]
    created_at: datetime
    raw_key: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
