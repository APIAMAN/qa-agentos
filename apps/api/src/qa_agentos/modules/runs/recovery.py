"""Run recovery: fenced failure transitions and stale-run reconciliation.

Guarantees that a run never stays in ``preparing``/``running`` (or enqueued-but-unclaimed
``queued``) forever, even when the worker that owned it was killed, timed out, or exhausted
its retries without running any cleanup code.

All operations are idempotent: they lock rows (``FOR UPDATE SKIP LOCKED`` on PostgreSQL),
re-check status under the lock, and only touch runs that still need it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional, Protocol

from qa_agentos.core.config import settings
from qa_agentos.models.domain import Run
from qa_agentos.runtime.events import (
    TERMINAL_STATUSES,
    RunStateManager,
    RunSupersededError,
    RunTerminalStateError,
    commit_and_publish,
    utc_now,
)
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

logger = get_logger(__name__)

ACTIVE_STATUSES = ("preparing", "running")


class EnqueueDispatcher(Protocol):
    async def enqueue(self, run_id: uuid.UUID) -> Optional[str]: ...


async def fail_run_if_owned(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    attempt: int,
    error_code: str,
    error_message: str,
    error_details: Optional[Dict[str, Any]] = None,
) -> bool:
    """Transition a run to ``failed`` unless it is terminal or owned by a newer attempt.

    Uses a fresh session so it works even after the executor's session failed. Returns
    True when this call performed the transition.
    """
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        if run is None or run.status in TERMINAL_STATUSES:
            return False
        payload: Dict[str, Any] = {"error_code": error_code, "error_message": error_message}
        payload["error_details"] = {"attempt": attempt, **(error_details or {})}
        try:
            await RunStateManager(session, run).transition_to("failed", payload=payload, expected_attempt=attempt)
        except (RunTerminalStateError, RunSupersededError) as exc:
            logger.info("Run not failed by this attempt", run_id=str(run_id), attempt=attempt, reason=str(exc))
            return False
        await commit_and_publish(session)
        logger.warning("Run marked failed", run_id=str(run_id), attempt=attempt, error_code=error_code)
        return True


@dataclass
class ReapResult:
    failed: List[uuid.UUID] = field(default_factory=list)
    resume_redispatched: List[uuid.UUID] = field(default_factory=list)
    queued_released: List[uuid.UUID] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.failed) + len(self.resume_redispatched) + len(self.queued_released)


def _skip_locked(stmt: Any, session: AsyncSession) -> Any:
    if session.bind and session.bind.dialect.name == "postgresql":
        return stmt.with_for_update(skip_locked=True)
    return stmt


async def reap_stale_runs(
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: Optional[EnqueueDispatcher] = None,
    *,
    stale_after_seconds: Optional[int] = None,
    limit: int = 50,
) -> ReapResult:
    """Reconcile runs whose owning worker is gone.

    - ``preparing``/``running`` with a stale heartbeat and a pending approval resume:
      the resume dispatch was lost, so it is dispatched again (the claim is single-use).
    - ``preparing``/``running`` with a stale heartbeat otherwise: no live attempt owns the
      run (killed worker, task timeout, exhausted retries). It is failed with
      ``EXECUTION_STALE``; any zombie worker is fenced off by the terminal status.
    - ``queued`` runs that were handed to Hatchet but never claimed within the window:
      their ``hatchet_run_id`` is cleared so the outbox sweeper enqueues them again.
      A duplicate delivery is harmless because claiming a queued run is atomic.
    """
    window = stale_after_seconds if stale_after_seconds is not None else settings.run_stale_after_seconds
    now = utc_now()
    cutoff = now - timedelta(seconds=window)
    result = ReapResult()

    async with session_factory() as session:
        last_seen = func.coalesce(Run.heartbeat_at, Run.updated_at)
        stale_stmt = _skip_locked(
            select(Run).where(Run.status.in_(ACTIVE_STATUSES), last_seen <= cutoff).order_by(last_seen.asc()).limit(limit),
            session,
        )
        for run in (await session.execute(stale_stmt)).scalars().all():
            if run.resume_requested_at is not None:
                run.heartbeat_at = now  # lease: do not re-dispatch again until another window passes
                result.resume_redispatched.append(run.id)
                continue
            last_heartbeat = run.heartbeat_at or run.updated_at
            await RunStateManager(session, run).transition_to(
                "failed",
                payload={
                    "error_code": "EXECUTION_STALE",
                    "error_message": (
                        f"No worker heartbeat for over {window}s; the run was abandoned "
                        "(worker crash, task timeout or exhausted retries)."
                    ),
                    "error_details": {
                        "attempt": run.attempt,
                        "hatchet_run_id": run.hatchet_run_id,
                        "last_heartbeat_at": last_heartbeat.isoformat() if last_heartbeat else None,
                    },
                },
            )
            result.failed.append(run.id)

        unclaimed_stmt = _skip_locked(
            select(Run)
            .where(
                Run.status == "queued",
                Run.hatchet_run_id.is_not(None),
                ~Run.hatchet_run_id.like("sweeper-pending%"),
                or_(Run.queued_at <= cutoff, Run.queued_at.is_(None) & (Run.created_at <= cutoff)),
            )
            .limit(limit),
            session,
        )
        for run in (await session.execute(unclaimed_stmt)).scalars().all():
            logger.warning(
                "Releasing enqueued run that was never claimed", run_id=str(run.id), hatchet_run_id=run.hatchet_run_id
            )
            run.hatchet_run_id = None
            result.queued_released.append(run.id)

        await commit_and_publish(session)

    for run_id in result.resume_redispatched:
        if dispatcher is None:
            break
        try:
            await dispatcher.enqueue(run_id)
            logger.warning("Re-dispatched lost approval resume", run_id=str(run_id))
        except Exception as exc:
            logger.error("Failed to re-dispatch approval resume", run_id=str(run_id), error=str(exc))

    if result.total:
        logger.warning(
            "Stale run reconciliation completed",
            failed=[str(r) for r in result.failed],
            resume_redispatched=[str(r) for r in result.resume_redispatched],
            queued_released=[str(r) for r in result.queued_released],
        )
    return result
