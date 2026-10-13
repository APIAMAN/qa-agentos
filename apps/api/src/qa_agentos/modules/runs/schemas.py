import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class RunCreateRequest(BaseModel):
    agent: str = Field(..., description="Agent slug (e.g. test-case-generator)")
    version: Optional[str] = Field(None, description="Optional agent version (e.g. 1.0.0)")
    input: Dict[str, Any] = Field(..., description="Input payload conforming to agent input schema")


class RunResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    agent_version_id: uuid.UUID
    status: str
    input_payload: Dict[str, Any]
    output_payload: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    error: Optional[Dict[str, Any]] = None
    resolved_agent: Optional[Dict[str, Any]] = None
    resolved_models: Dict[str, Any] = Field(default_factory=dict)
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    replayed_from: Optional[uuid.UUID] = None
    idempotency_key: Optional[str] = None
    attempt: int = 1

    model_config = ConfigDict(from_attributes=True)


class RunListResponse(BaseModel):
    items: List[RunResponse]
    total: int
    limit: int
    offset: int


class RunStepResponse(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    step_number: int
    step_type: str
    node: str
    attempt: int
    input_data: Dict[str, Any]
    output_data: Optional[Dict[str, Any]] = None
    status: str
    latency_ms: Optional[int] = None
    tokens_input: int = 0
    tokens_output: int = 0
    error: Optional[Dict[str, Any]] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
