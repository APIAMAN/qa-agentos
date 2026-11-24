"""Run state machine and chronological event persistence for QA AgentOS.

Enforces state transition invariants and records audit entries into `run_events`.

Live events (Redis pub/sub) are never published before the database commit that makes
them true. Transitions queue their event on the session; a SQLAlchemy ``after_commit``
hook marks queued events as committed, a rollback discards them, and
:func:`publish_committed_run_events` sends only committed events. Callers invoke it right
after ``session.commit()``. Events are best-effort notifications: persisted run state is
the source of truth.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import event as sa_event
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from qa_agentos.models.domain import Run, RunEvent

TERMINAL_STATUSES: Set[str] = {"succeeded", "failed", "cancelled"}

ALLOWED_TRANSITIONS: Dict[str, Set[str]] = {
    "queued": {"preparing", "waiting_for_approval", "failed", "cancelled"},
    "preparing": {"running", "waiting_for_approval", "failed", "cancelled"},
    "running": {"waiting_for_approval", "succeeded", "failed", "cancelled"},
    "waiting_for_approval": {"running", "failed", "cancelled"},
}

_PENDING_KEY = "qa_agentos.pending_run_events"
_COMMITTED_KEY = "qa_agentos.committed_run_events"


class RunTerminalStateError(RuntimeError):
    """Raised when attempting to modify or transition an already terminal run."""


class RunTransitionError(ValueError):
    """Raised when an invalid state transition is requested."""


class RunSupersededError(RuntimeError):
    """Raised when a stale worker tries to transition a run now owned by a newer attempt."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- event outbox


def queue_run_event(session: AsyncSession, payload: Any) -> None:
    """Queue a live event to be published only after the current transaction commits."""
    session.info.setdefault(_PENDING_KEY, []).append(payload)


@sa_event.listens_for(Session, "after_commit")
def _promote_pending_events(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if pending:
        session.info.setdefault(_COMMITTED_KEY, []).extend(pending)


@sa_event.listens_for(Session, "after_rollback")
def _discard_pending_events(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


async def publish_committed_run_events(session: AsyncSession) -> int:
    """Publish events whose transaction has committed, in order. Returns the count sent."""
    committed: List[Any] = session.info.pop(_COMMITTED_KEY, None) or []
    if not committed:
        return 0
    from qa_agentos.runtime.pubsub import publish_run_event

    for payload in committed:
        await publish_run_event(payload)  # never raises; logs and returns False on failure
    return len(committed)


async def commit_and_publish(session: AsyncSession) -> None:
    """Commit the session, then publish the live events that commit made true."""
    await session.commit()
    await publish_committed_run_events(session)


# --------------------------------------------------------------------------- state machine


class RunStateManager:
    """Manages transactional state transitions and event logging for a Run."""

    def __init__(self, session: AsyncSession, run: Run) -> None:
        self.session = session
        self.run = run

    async def _get_next_seq(self) -> int:
        stmt = select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == self.run.id)
        res = await self.session.execute(stmt)
        current_max = res.scalar() or 0
        return current_max + 1

    async def _lock_and_refresh(self) -> None:
        """Lock the run row (PostgreSQL) and load the latest status and attempt."""
        if self.session.bind and self.session.bind.dialect.name == "postgresql":
            lock_stmt = select(Run.status, Run.attempt).where(Run.id == self.run.id).with_for_update()
            row = (await self.session.execute(lock_stmt)).first()
            if row is not None:
                self.run.status = row[0]
                self.run.attempt = row[1]

    def _append_event(self, from_status: Optional[str], to_status: str, seq: int, payload: Dict[str, Any]) -> RunEvent:
        event = RunEvent(
            id=uuid.uuid4(),
            run_id=self.run.id,
            seq=seq,
            from_status=from_status,
            to_status=to_status,
            payload=payload,
            created_at=utc_now(),
        )
        self.session.add(event)
        return event

    async def transition_to(
        self,
        to_status: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        expected_attempt: Optional[int] = None,
    ) -> RunEvent:
        """Execute a guarded state transition and persist a chronological RunEvent.

        ``expected_attempt`` fences stale workers: if the run has been claimed by a newer
        attempt, the transition is refused with :class:`RunSupersededError`. The check
        happens under the row lock, so it cannot race with the newer attempt's claim.
        """
        await self._lock_and_refresh()
        from_status = self.run.status

        if from_status in TERMINAL_STATUSES:
            raise RunTerminalStateError(
                f"Cannot transition run {self.run.id} from terminal status '{from_status}' to '{to_status}'."
            )
        if expected_attempt is not None and (self.run.attempt or 1) > expected_attempt:
            raise RunSupersededError(
                f"Run {self.run.id} is owned by attempt {self.run.attempt}; attempt {expected_attempt} "
                f"may not transition it to '{to_status}'."
            )

        valid_targets = ALLOWED_TRANSITIONS.get(from_status, set())
        if to_status not in valid_targets:
            raise RunTransitionError(f"Invalid transition from '{from_status}' to '{to_status}' for run {self.run.id}.")

        seq = await self._get_next_seq()
        event = self._append_event(from_status, to_status, seq, payload or {})

        self.run.status = to_status
        now = utc_now()
        if to_status == "running" and not self.run.started_at:
            self.run.started_at = now
        elif to_status in TERMINAL_STATUSES:
            self.run.finished_at = now

        if payload:
            if "error_code" in payload:
                self.run.error_code = payload["error_code"]
            if "error_message" in payload:
                self.run.error_message = payload["error_message"]
            if "error_details" in payload:
                self.run.error = payload["error_details"]

        await self.session.flush()
        self._queue_live_event(event, f"run.{to_status}", from_status, to_status, payload or {})
        return event

    async def record_attempt(self, kind: str, details: Dict[str, Any]) -> RunEvent:
        """Record a retry/resume claim in the run's history without changing its status.

        Written as a same-status RunEvent (``from_status == to_status``) whose payload
        carries ``{"event": "attempt.<kind>", "attempt": N, ...}``.
        """
        seq = await self._get_next_seq()
        status = self.run.status
        payload = {"event": f"attempt.{kind}", "attempt": self.run.attempt, **details}
        event = self._append_event(status, status, seq, payload)
        await self.session.flush()
        self._queue_live_event(event, f"run.attempt.{kind}", status, status, payload)
        return event

    def _queue_live_event(
        self,
        event: RunEvent,
        name: str,
        from_status: Optional[str],
        to_status: str,
        payload: Dict[str, Any],
    ) -> None:
        from qa_agentos.runtime.pubsub import RunEventPayload

        queue_run_event(
            self.session,
            RunEventPayload(
                id=str(event.id),
                version="1.0",
                event=name,
                run_id=str(self.run.id),
                project_id=str(self.run.project_id),
                timestamp=event.created_at.isoformat(),
                data={
                    "seq": event.seq,
                    "from_status": from_status,
                    "to_status": to_status,
                    "payload": payload,
                    "attempt": self.run.attempt or 1,
                    "started_at": self.run.started_at.isoformat() if self.run.started_at else None,
                    "finished_at": self.run.finished_at.isoformat() if self.run.finished_at else None,
                    "error_code": self.run.error_code,
                    "error_message": self.run.error_message,
                },
            ),
        )
