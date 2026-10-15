"""Outbox-style recovery sweeper for unassigned or failed-enqueue runs.

Scans for runs in `status='queued'` with `hatchet_run_id IS NULL` older than the threshold,
and retries durable task dispatching. Uses `FOR UPDATE SKIP LOCKED` on PostgreSQL to ensure
overlapping or distributed sweeper runs never contend or duplicate enqueues.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Protocol

from qa_agentos.core.config import settings
from qa_agentos.models.domain import Run
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

logger = get_logger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EnqueueDispatcher(Protocol):
    async def enqueue(self, run_id: uuid.UUID) -> Optional[str]: ...


async def sweep_outbox(
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: EnqueueDispatcher,
    *,
    threshold_seconds: Optional[int] = None,
    limit: int = 50,
) -> int:
    """Find queued runs with hatchet_run_id IS NULL older than threshold and retry enqueueing.

    Uses `FOR UPDATE SKIP LOCKED` on PostgreSQL so that concurrent/overlapping
    sweeper executions skip locked rows rather than blocking or double-processing.
    """
    sec = threshold_seconds if threshold_seconds is not None else settings.HATCHET_SWEEPER_THRESHOLD_SECONDS
    cutoff = utc_now() - timedelta(seconds=sec)

    logger.info("Starting outbox sweep", threshold_seconds=sec, cutoff=cutoff.isoformat())
    recovered = 0

    now_epoch = int(utc_now().timestamp())
    lease_duration = max(sec, 30)
    lease_expiry = now_epoch + lease_duration

    # 1. Claim batch of stuck runs under short-lived transaction
    async with session_factory() as session:
        stmt = (
            select(Run)
            .where(
                Run.status == "queued",
                Run.created_at <= cutoff,
                or_(
                    Run.hatchet_run_id.is_(None),
                    Run.hatchet_run_id.like("sweeper-pending-%"),
                    and_(
                        Run.hatchet_run_id.like("sweeper-pending:%"),
                        Run.hatchet_run_id <= f"sweeper-pending:{now_epoch}:~",
                    ),
                ),
            )
            .order_by(Run.created_at.asc())
            .limit(limit)
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        res = await session.execute(stmt)
        candidates: List[Run] = list(res.scalars().all())

        # Verify lease expiry for pending markers
        stuck_runs: List[Run] = []
        for r in candidates:
            if r.hatchet_run_id and r.hatchet_run_id.startswith("sweeper-pending:"):
                parts = r.hatchet_run_id.split(":")
                if len(parts) >= 2:
                    try:
                        exp = int(parts[1])
                        if exp > now_epoch:
                            continue  # Active lease held by another sweeper; skip
                    except ValueError:
                        pass
            stuck_runs.append(r)

        if not stuck_runs:
            logger.info("Outbox sweep completed; no stuck runs found")
            return 0

        logger.warning(
            "Outbox sweep discovered unassigned queued runs",
            count=len(stuck_runs),
            run_ids=[str(r.id) for r in stuck_runs],
        )

        stuck_run_ids = [run.id for run in stuck_runs]
        # Mark with a transient claim marker and explicit lease expiry
        for run in stuck_runs:
            run.hatchet_run_id = f"sweeper-pending:{lease_expiry}:{run.id}"
        await session.commit()

    # 2. Enqueue each run outside of the query transaction (avoids row lock deadlocks)
    for run_id in stuck_run_ids:
        logger.info("Retrying enqueue for stuck run", run_id=str(run_id))
        try:
            hatchet_run_id = await dispatcher.enqueue(run_id)
            if hatchet_run_id:
                async with session_factory() as session:
                    update_stmt = update(Run).where(Run.id == run_id).values(hatchet_run_id=str(hatchet_run_id))
                    await session.execute(update_stmt)
                    await session.commit()
                recovered += 1
                logger.info("Successfully recovered run", run_id=str(run_id), hatchet_run_id=hatchet_run_id)
            else:
                logger.warning("Enqueue retry failed or returned no id", run_id=str(run_id))
                async with session_factory() as session:
                    clear_stmt = update(Run).where(Run.id == run_id).values(hatchet_run_id=None)
                    await session.execute(clear_stmt)
                    await session.commit()
        except Exception as exc:
            logger.exception("Unexpected error during outbox sweep enqueue", run_id=str(run_id), error=str(exc))
            try:
                async with session_factory() as session:
                    clear_stmt = update(Run).where(Run.id == run_id).values(hatchet_run_id=None)
                    await session.execute(clear_stmt)
                    await session.commit()
            except Exception:
                pass

    logger.info("Outbox sweep finished", recovered_count=recovered)
    return recovered
