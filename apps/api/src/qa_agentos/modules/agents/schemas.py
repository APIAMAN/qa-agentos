"""Agents module schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentVersionResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    version: str
    status: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    permissions: List[str] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    model_requirements: Dict[str, Any] = Field(default_factory=dict)
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    prompt_hash: Optional[str] = None
    activated_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentListItem(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: Optional[str] = None
    agent_type: str
    is_active: bool
    created_at: datetime
    version: Optional[str] = None
    status: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None
    permissions: List[str] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    model_requirements: Optional[Dict[str, Any]] = None
    available_versions: List[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class AgentDetailResponse(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: Optional[str] = None
    agent_type: str
    is_active: bool
    created_at: datetime
    version: Optional[str] = None
    status: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None
    permissions: List[str] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    model_requirements: Optional[Dict[str, Any]] = None
    active_version: Optional[AgentVersionResponse] = None
    versions: List[AgentVersionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
