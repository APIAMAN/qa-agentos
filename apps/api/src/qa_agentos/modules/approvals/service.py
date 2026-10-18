"""Business logic and state transitions for Approvals."""

from __future__ import annotations

import uuid
from typing import Any, List, Optional, Tuple

from qa_agentos.core.errors import ConflictError, NotFoundError
from qa_agentos.models.domain import Approval, Run
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.runtime.events import RunStateManager, utc_now
from qa_agentos.runtime.pubsub import RunEventPayload, publish_run_event
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

logger = get_logger(__name__)


class ApprovalsService:
    """Manages approval lifecycles, decision audits, and run state resumptions."""

    @staticmethod
    async def create_approval(
        db: AsyncSession,
        identity: Identity,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        action_type: str,
        prompt_text: str,
    ) -> Approval:
        """Create a pending approval request and pause the run."""
        stmt = select(Run).where(Run.id == run_id, Run.project_id == project_id)
        res = await db.execute(stmt)
        run = res.scalar_one_or_none()
        if not run:
            raise NotFoundError(message=f"Run {run_id} not found in current project")

        if run.status in ("succeeded", "failed", "cancelled"):
            raise ConflictError(message=f"Cannot request approval on terminal run {run_id} (status: {run.status})")

        # 2. Transition run to waiting_for_approval if not already
        if run.status != "waiting_for_approval":
            state_mgr = RunStateManager(db, run)
            await state_mgr.transition_to(
                "waiting_for_approval",
                payload={"action_type": action_type, "prompt_text": prompt_text},
            )

        # 3. Create approval record
        approval = Approval(
            id=uuid.uuid4(),
            run_id=run.id,
            action_type=action_type,
            prompt_text=prompt_text,
            status="pending",
            created_at=utc_now(),
        )
        db.add(approval)
        await db.commit()
        await db.refresh(approval)

        # 4. Publish real-time approval event
        await publish_run_event(
            RunEventPayload(
                event="approval.created",
                run_id=str(run.id),
                project_id=str(run.project_id),
                timestamp=approval.created_at.isoformat(),
                data={
                    "approval_id": str(approval.id),
                    "action_type": approval.action_type,
                    "prompt_text": approval.prompt_text,
                    "status": "pending",
                },
            )
        )

        logger.info(
            "Created approval request",
            approval_id=str(approval.id),
            run_id=str(run.id),
            action_type=action_type,
        )
        return approval

    @staticmethod
    async def list_approvals(
        db: AsyncSession,
        project_id: uuid.UUID,
        status_filter: Optional[str] = None,
        run_id: Optional[uuid.UUID] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[dict[str, Any]], int]:
        """List approvals strictly scoped to the project."""
        query = (
            select(Approval, Run.project_id, Run.input_payload)
            .join(Run, Approval.run_id == Run.id)
            .where(Run.project_id == project_id)
        )

        if status_filter:
            query = query.where(Approval.status == status_filter)
        if run_id:
            query = query.where(Approval.run_id == run_id)

        # Count total
        count_stmt = (
            select(func.count(Approval.id))
            .join(Run, Approval.run_id == Run.id)
            .where(Run.project_id == project_id)
        )
        if status_filter:
            count_stmt = count_stmt.where(Approval.status == status_filter)
        if run_id:
            count_stmt = count_stmt.where(Approval.run_id == run_id)

        total_res = await db.execute(count_stmt)
        total = total_res.scalar() or 0

        # Fetch records
        query = query.order_by(Approval.created_at.desc()).offset(offset).limit(limit)
        res = await db.execute(query)
        rows = res.all()

        items = []
        for app, proj_id, input_payload in rows:
            req_text = (input_payload or {}).get("requirements", "")
            summary = req_text[:80] + ("..." if len(req_text) > 80 else "") if req_text else None
            items.append({
                "id": app.id,
                "run_id": app.run_id,
                "project_id": proj_id,
                "action_type": app.action_type,
                "prompt_text": app.prompt_text,
                "status": app.status,
                "decision_by": app.decision_by,
                "decision_note": app.decision_note,
                "created_at": app.created_at,
                "decided_at": app.decided_at,
                "run_summary": summary,
            })

        return items, total

    @staticmethod
    async def get_approval(
        db: AsyncSession,
        project_id: uuid.UUID,
        approval_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Get single approval detail with strict project scoping."""
        stmt = (
            select(Approval, Run.project_id, Run.input_payload)
            .join(Run, Approval.run_id == Run.id)
            .where(Approval.id == approval_id, Run.project_id == project_id)
        )
        res = await db.execute(stmt)
        row = res.first()
        if not row:
            raise NotFoundError(message=f"Approval {approval_id} not found in current project")

        app, proj_id, input_payload = row
        req_text = (input_payload or {}).get("requirements", "")
        summary = req_text[:80] + ("..." if len(req_text) > 80 else "") if req_text else None

        return {
            "id": app.id,
            "run_id": app.run_id,
            "project_id": proj_id,
            "action_type": app.action_type,
            "prompt_text": app.prompt_text,
            "status": app.status,
            "decision_by": app.decision_by,
            "decision_note": app.decision_note,
            "created_at": app.created_at,
            "decided_at": app.decided_at,
            "run_summary": summary,
        }

    @staticmethod
    async def decide_approval(
        db: AsyncSession,
        identity: Identity,
        project_id: uuid.UUID,
        approval_id: uuid.UUID,
        decision: str,  # "approved" or "rejected"
        decision_note: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record operator decision, resume or terminate run, and publish live event."""
        stmt = (
            select(Approval, Run)
            .join(Run, Approval.run_id == Run.id)
            .where(Approval.id == approval_id, Run.project_id == project_id)
        )
        res = await db.execute(stmt)
        row = res.first()
        if not row:
            raise NotFoundError(message=f"Approval {approval_id} not found in current project")

        approval, run = row

        if approval.status != "pending":
            raise ConflictError(
                message=f"Approval {approval_id} already has terminal status '{approval.status}' and cannot be modified"
            )

        if run.status in ("succeeded", "failed", "cancelled"):
            raise ConflictError(
                message=f"Cannot decide approval for terminal run {run.id} (status: {run.status})"
            )

        now = utc_now()
        approval.status = decision
        approval.decision_by = identity.user_id if not identity.is_api_key else None
        approval.decision_note = decision_note
        approval.decided_at = now

        state_mgr = RunStateManager(db, run)
        if decision == "approved":
            if run.status == "waiting_for_approval":
                await state_mgr.transition_to(
                    "running",
                    payload={"approval_id": str(approval.id), "decision_note": decision_note},
                )
        elif decision == "rejected":
            if run.status in ("waiting_for_approval", "running", "preparing", "queued"):
                await state_mgr.transition_to(
                    "cancelled",
                    payload={"approval_id": str(approval.id), "rejection_note": decision_note},
                )

        await db.commit()
        await db.refresh(approval)

        await publish_run_event(
            RunEventPayload(
                event=f"approval.{decision}",
                run_id=str(run.id),
                project_id=str(run.project_id),
                timestamp=now.isoformat(),
                data={
                    "approval_id": str(approval.id),
                    "action_type": approval.action_type,
                    "status": decision,
                    "decision_note": decision_note,
                    "run_status": run.status,
                },
            )
        )

        if decision == "approved":
            try:
                from qa_agentos.modules.runs.dispatcher import get_dispatcher
                dispatcher = get_dispatcher()
                dispatcher.dispatch(run.id)
                logger.info("Dispatched resumed run after approval", run_id=str(run.id))
            except Exception as disp_err:
                logger.warning("Failed to dispatch resumed run after approval", error=str(disp_err), run_id=str(run.id))

        logger.info(
            "Decided approval request",
            approval_id=str(approval.id),
            decision=decision,
            run_id=str(run.id),
        )

        return {
            "id": approval.id,
            "run_id": approval.run_id,
            "project_id": run.project_id,
            "action_type": approval.action_type,
            "prompt_text": approval.prompt_text,
            "status": approval.status,
            "decision_by": approval.decision_by,
            "decision_note": approval.decision_note,
            "created_at": approval.created_at,
            "decided_at": approval.decided_at,
            "run_summary": None,
        }
