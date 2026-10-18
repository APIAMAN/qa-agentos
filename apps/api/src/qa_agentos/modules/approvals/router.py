"""FastAPI router for Approval workflows and Inbox."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, status
from qa_agentos.core.database import get_db
from qa_agentos.modules.approvals.schemas import (
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
    ApprovalListResponse,
    ApprovalResponse,
)
from qa_agentos.modules.approvals.service import ApprovalsService
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.modules.auth.service import require_scope
from qa_agentos.modules.runs.service import resolve_project_id
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("", response_model=ApprovalListResponse)
async def list_approvals(
    status_filter: Optional[str] = Query(None, alias="status"),
    run_id: Optional[uuid.UUID] = Query(None, alias="run_id"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("approvals:read")),
) -> ApprovalListResponse:
    """List operator approval requests filtered by status or run with strict project isolation."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    items, total = await ApprovalsService.list_approvals(
        db=db,
        project_id=project_id,
        status_filter=status_filter,
        run_id=run_id,
        limit=limit,
        offset=offset,
    )
    return ApprovalListResponse(
        items=[ApprovalResponse(**item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: uuid.UUID,
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("approvals:read")),
) -> ApprovalResponse:
    """Retrieve details for a specific approval request."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    item = await ApprovalsService.get_approval(
        db=db,
        project_id=project_id,
        approval_id=approval_id,
    )
    return ApprovalResponse(**item)


@router.post("", response_model=ApprovalResponse, status_code=status.HTTP_201_CREATED)
async def create_approval(
    payload: ApprovalCreateRequest,
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("approvals:write")),
) -> ApprovalResponse:
    """Create a pending approval request for a run action."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    approval = await ApprovalsService.create_approval(
        db=db,
        identity=identity,
        project_id=project_id,
        run_id=payload.run_id,
        action_type=payload.action_type,
        prompt_text=payload.prompt_text,
    )
    return ApprovalResponse(
        id=approval.id,
        run_id=approval.run_id,
        project_id=project_id,
        action_type=approval.action_type,
        prompt_text=approval.prompt_text,
        status=approval.status,
        decision_by=approval.decision_by,
        decision_note=approval.decision_note,
        created_at=approval.created_at,
        decided_at=approval.decided_at,
    )


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve_approval(
    approval_id: uuid.UUID,
    payload: Optional[ApprovalDecisionRequest] = None,
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("approvals:write")),
) -> ApprovalResponse:
    """Approve a pending request, transitioning the run back to running."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    note = payload.decision_note if payload else None
    item = await ApprovalsService.decide_approval(
        db=db,
        identity=identity,
        project_id=project_id,
        approval_id=approval_id,
        decision="approved",
        decision_note=note,
    )
    return ApprovalResponse(**item)


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject_approval(
    approval_id: uuid.UUID,
    payload: Optional[ApprovalDecisionRequest] = None,
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("approvals:write")),
) -> ApprovalResponse:
    """Reject a pending request, transitioning the run to cancelled."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    note = payload.decision_note if payload else None
    item = await ApprovalsService.decide_approval(
        db=db,
        identity=identity,
        project_id=project_id,
        approval_id=approval_id,
        decision="rejected",
        decision_note=note,
    )
    return ApprovalResponse(**item)
