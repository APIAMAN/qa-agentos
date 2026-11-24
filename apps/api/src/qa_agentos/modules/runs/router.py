"""FastAPI router for QA AgentOS Runs API."""

from __future__ import annotations

import uuid
from typing import AsyncGenerator, List, Optional

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from qa_agentos.core.database import get_db
from qa_agentos.models.domain import Run
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.modules.auth.service import require_scope
from qa_agentos.modules.runs.dispatcher import RunDispatcher, get_dispatcher
from qa_agentos.modules.runs.schemas import (
    RunCreateRequest,
    RunListResponse,
    RunResponse,
    RunStepResponse,
    StreamTicketResponse,
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
    dispatcher: RunDispatcher = Depends(get_dispatcher),
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
    dispatcher: RunDispatcher = Depends(get_dispatcher),
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
    dispatcher: RunDispatcher = Depends(get_dispatcher),
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


@router.post("/{run_id}/events/ticket")
async def create_events_stream_ticket(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    identity: Identity = Depends(require_scope("runs:read")),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
) -> StreamTicketResponse:
    """Issue a secure, short-lived single-use ticket for EventSource SSE streaming."""
    project_id = await resolve_project_id(db, identity, x_project_id)
    await RunsService.get_run(db=db, run_id=run_id, project_id=project_id)

    from qa_agentos.runtime.pubsub import create_stream_ticket

    user_identifier = str(identity.user_id) if not identity.is_api_key else str(identity.api_key_id)
    ticket = await create_stream_ticket(user_id=user_identifier, project_id=str(project_id), run_id=str(run_id), ttl_seconds=60)
    return StreamTicketResponse(ticket=ticket, expires_in=60)


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: uuid.UUID,
    ticket: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_project_id: Optional[uuid.UUID] = Header(None, alias="X-Project-Id"),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Stream live run and step lifecycle events via Server-Sent Events (SSE)."""
    from qa_agentos.core.errors import UnauthorizedError
    from qa_agentos.modules.auth.service import get_current_identity
    from qa_agentos.runtime.events import TERMINAL_STATUSES, utc_now
    from qa_agentos.runtime.pubsub import RunEventPayload, consume_stream_ticket, subscribe_run_events

    project_id: Optional[uuid.UUID] = None

    if ticket:
        ticket_data = await consume_stream_ticket(ticket)
        if not ticket_data:
            raise UnauthorizedError(message="Invalid or expired stream ticket")
        if ticket_data.get("run_id") and ticket_data["run_id"] != str(run_id):
            raise UnauthorizedError(message="Stream ticket is not authorized for this run")
        project_id = uuid.UUID(ticket_data["project_id"])
    else:
        bearer_token: Optional[str] = None
        if authorization and authorization.startswith("Bearer "):
            bearer_token = authorization.split("Bearer ", 1)[1].strip()

        identity = await get_current_identity(bearer_token=bearer_token, x_api_key=x_api_key, db=db)
        project_id = await resolve_project_id(db, identity, x_project_id)

    run = await RunsService.get_run(db=db, run_id=run_id, project_id=project_id)
    run_pk = run.id
    project_key = str(project_id)
    # End the request transaction so its pooled connection is released: the stream can stay
    # open for the whole run, and holding a connection per open page would exhaust the pool.
    await db.commit()

    def snapshot_frame(current: Run) -> str:
        if current.status in TERMINAL_STATUSES:
            payload = RunEventPayload(
                event=f"run.{current.status}",
                run_id=str(current.id),
                project_id=project_key,
                timestamp=(current.finished_at or utc_now()).isoformat(),
                data={
                    "status": current.status,
                    "tokens_input": current.tokens_input,
                    "tokens_output": current.tokens_output,
                    "cost_usd": current.cost_usd,
                    "error_message": current.error_message,
                },
            )
        else:
            payload = RunEventPayload(
                event=f"run.{current.status}",
                run_id=str(current.id),
                project_id=project_key,
                timestamp=(current.started_at or current.created_at or utc_now()).isoformat(),
                data={"status": current.status, "attempt": current.attempt or 1},
            )
        return payload.format_sse()

    async def fresh_snapshot() -> List[str]:
        # Read state *after* subscribing, so a transition published in between is not lost.
        current = await db.get(Run, run_pk, populate_existing=True)
        frames = [snapshot_frame(current)] if current else []
        await db.commit()  # release the connection again
        return frames

    async def event_generator() -> AsyncGenerator[str, None]:
        if run.status in TERMINAL_STATUSES:
            yield snapshot_frame(run)
            return
        async for frame in subscribe_run_events(
            project_id=project_key, run_id=str(run_id), after_subscribe=fresh_snapshot
        ):
            yield frame

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
