"""Pydantic schemas for the Approvals module."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ApprovalCreateRequest(BaseModel):
    """Payload to request operator approval for a run action."""

    run_id: uuid.UUID
    action_type: str = Field(..., max_length=100, description="Category of action requiring approval (e.g., tool_execution, destructive_action, deploy)")
    prompt_text: str = Field(..., description="Contextual explanation and details requiring operator decision")


class ApprovalDecisionRequest(BaseModel):
    """Payload to record operator decision on an approval request."""

    decision_note: Optional[str] = Field(None, description="Optional audit note or reasoning provided by operator")


class ApprovalResponse(BaseModel):
    """Canonical representation of an approval request."""

    id: uuid.UUID
    run_id: uuid.UUID
    project_id: uuid.UUID
    action_type: str
    prompt_text: str
    status: str  # pending, approved, rejected
    decision_by: Optional[uuid.UUID] = None
    decision_note: Optional[str] = None
    created_at: datetime
    decided_at: Optional[datetime] = None
    run_summary: Optional[str] = None

    model_config = {"from_attributes": True}


class ApprovalListResponse(BaseModel):
    """Paginated list of approval requests."""

    items: List[ApprovalResponse]
    total: int
    limit: int
    offset: int
