"""FastAPI router for QA AgentOS Runs API."""

from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from qa_agentos.core.database import get_db
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.modules.auth.service import require_scope
from qa_agentos.modules.runs.dispatcher import InlineDispatcher, get_dispatcher
from qa_agentos.modules.runs.schemas import (
    RunCreateRequest,
    RunListResponse,
    RunResponse,
    RunStepResponse,
)
from qa_agentos.modules.runs.service import RunsService, resolve_project_id
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/runs", tags=["Runs"])


@router.post("", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    payload: RunCreateRequest,
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("runs:create")),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    dispatcher: InlineDispatcher = Depends(get_dispatcher),
) -> Response:
    """Create and schedule an agent run, with deterministic idempotency support."""
    run, is_existing = await RunsService.create_run(
        db=db,
        identity=identity,
        payload=payload,
        idempotency_key=idempotency_key,
        dispatcher=dispatcher,
        x_project_id=x_project_id,
    )

    status_code = status.HTTP_200_OK if is_existing else status.HTTP_202_ACCEPTED
    content = jsonable_encoder(RunResponse.model_validate(run))
    return JSONResponse(status_code=status_code, content=content)


@router.get("", response_model=RunListResponse)
async def list_runs(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("runs:read")),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
) -> RunListResponse:
    """List runs visible to the authenticated project."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    runs, total = await RunsService.list_runs(
        db=db,
        project_id=project_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return RunListResponse(
        items=[RunResponse.model_validate(r) for r in runs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("runs:read")),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
) -> RunResponse:
    """Retrieve details and lifecycle state of a specific run."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    run = await RunsService.get_run(db=db, run_id=run_id, project_id=project_id)
    return RunResponse.model_validate(run)


@router.get("/{run_id}/steps", response_model=List[RunStepResponse])
async def get_run_steps(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("runs:read")),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
) -> List[RunStepResponse]:
    """Retrieve ordered execution steps for a run."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    steps = await RunsService.get_run_steps(db=db, run_id=run_id, project_id=project_id)
    return [RunStepResponse.model_validate(s) for s in steps]


@router.post("/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("runs:create")),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    dispatcher: InlineDispatcher = Depends(get_dispatcher),
) -> RunResponse:
    """Cancel a queued or running run."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    run = await RunsService.cancel_run(
        db=db,
        run_id=run_id,
        project_id=project_id,
        identity=identity,
        dispatcher=dispatcher,
    )
    return RunResponse.model_validate(run)


@router.post("/{run_id}/replay", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def replay_run(
    run_id: uuid.UUID,
    re_resolve: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("runs:create")),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    dispatcher: InlineDispatcher = Depends(get_dispatcher),
) -> RunResponse:
    """Replay an existing run with identical parameters and pinned or re-resolved models."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    new_run = await RunsService.replay_run(
        db=db,
        run_id=run_id,
        project_id=project_id,
        identity=identity,
        re_resolve=re_resolve,
        dispatcher=dispatcher,
    )
    return RunResponse.model_validate(new_run)
