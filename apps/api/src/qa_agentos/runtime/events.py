"""Run state machine and chronological event persistence for QA AgentOS.

Enforces state transition invariants and records audit entries into `run_events`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from qa_agentos.models.domain import Run, RunEvent

TERMINAL_STATUSES: Set[str] = {"succeeded", "failed", "cancelled"}

ALLOWED_TRANSITIONS: Dict[str, Set[str]] = {
    "queued": {"preparing", "waiting_for_approval", "failed", "cancelled"},
    "preparing": {"running", "waiting_for_approval", "failed", "cancelled"},
    "running": {"waiting_for_approval", "succeeded", "failed", "cancelled"},
    "waiting_for_approval": {"running", "failed", "cancelled"},
}


class RunTerminalStateError(RuntimeError):
    """Raised when attempting to modify or transition an already terminal run."""


class RunTransitionError(ValueError):
    """Raised when an invalid state transition is requested."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStateManager:
    """Manages transactional state transitions and event logging for a Run."""

    def __init__(self, session: AsyncSession, run: Run) -> None:
        self.session = session
        self.run = run
        self._next_seq: Optional[int] = None

    async def _get_next_seq(self) -> int:
        stmt = select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == self.run.id)
        res = await self.session.execute(stmt)
        current_max = res.scalar() or 0
        return current_max + 1

    async def transition_to(
        self,
        to_status: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> RunEvent:
        """Execute a guarded state transition and persist a chronological RunEvent."""
        # Acquire row lock on run to serialize transitions and ensure fresh status
        if self.session.bind and self.session.bind.dialect.name == 'postgresql':
            lock_stmt = select(Run.status).where(Run.id == self.run.id).with_for_update()
            res = await self.session.execute(lock_stmt)
            latest_status = res.scalar_one_or_none()
            if latest_status is not None:
                self.run.status = latest_status

        from_status = self.run.status

        # 1. Guard against modifying a run already in a terminal state
        if from_status in TERMINAL_STATUSES:
            raise RunTerminalStateError(
                f"Cannot transition run {self.run.id} from terminal status '{from_status}' to '{to_status}'."
            )

        # 2. Guard against invalid transition graph edges
        valid_targets = ALLOWED_TRANSITIONS.get(from_status, set())
        if to_status not in valid_targets:
            raise RunTransitionError(f"Invalid transition from '{from_status}' to '{to_status}' for run {self.run.id}.")

        # 3. Determine next sequential sequence number
        seq = await self._get_next_seq()

        # 4. Construct and record event
        event = RunEvent(
            id=uuid.uuid4(),
            run_id=self.run.id,
            seq=seq,
            from_status=from_status,
            to_status=to_status,
            payload=payload or {},
            created_at=utc_now(),
        )
        self.session.add(event)

        # 5. Update Run row timestamps and status
        self.run.status = to_status
        now = utc_now()
        if to_status == "running" and not self.run.started_at:
            self.run.started_at = now
        elif to_status in TERMINAL_STATUSES:
            self.run.finished_at = now

        # Update error payload if present
        if payload:
            if "error_code" in payload:
                self.run.error_code = payload["error_code"]
            if "error_message" in payload:
                self.run.error_message = payload["error_message"]
            if "error_details" in payload:
                self.run.error = payload["error_details"]

        await self.session.flush()

        # Publish event to Redis Pub/Sub
        try:
            from qa_agentos.runtime.pubsub import RunEventPayload, publish_run_event

            pub_payload = RunEventPayload(
                id=str(event.id),
                version="1.0",
                event=f"run.{to_status}",
                run_id=str(self.run.id),
                project_id=str(self.run.project_id),
                timestamp=event.created_at.isoformat(),
                data={
                    "seq": event.seq,
                    "from_status": from_status,
                    "to_status": to_status,
                    "payload": payload or {},
                    "attempt": self.run.attempt or 1,
                    "started_at": self.run.started_at.isoformat() if self.run.started_at else None,
                    "finished_at": self.run.finished_at.isoformat() if self.run.finished_at else None,
                    "error_code": self.run.error_code,
                    "error_message": self.run.error_message,
                },
            )
            await publish_run_event(pub_payload)
        except Exception:
            pass

        return event
