"""Runs service implementing intake, idempotency, retrieval, replay, and cancellation."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

import jsonschema
from fastapi import status
from qa_agentos.core.config import settings
from qa_agentos.core.errors import APIError, NotFoundError
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    ModelCatalog,
    Project,
    Run,
    RunEvent,
    RunStep,
)
from qa_agentos.modules.auth.schemas import Identity
from qa_agentos.modules.runs.dispatcher import RunDispatcher
from qa_agentos.modules.runs.schemas import RunCreateRequest
from qa_agentos.runtime.events import TERMINAL_STATUSES, RunStateManager, utc_now
from qa_agentos.runtime.gateway.errors import NoModelForCapabilityError
from qa_agentos.runtime.gateway.resolution import ModelResolver
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


def compute_input_hash(input_payload: Dict[str, Any]) -> str:
    """Compute canonical SHA-256 hash of the input payload."""
    canonical_json = json.dumps(input_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


async def resolve_project_id(
    db: AsyncSession,
    identity: Identity,
    x_project_id: Optional[uuid.UUID] = None,
) -> uuid.UUID:
    """Determine effective project_id from API key identity, header, or user default with validation."""
    from qa_agentos.core.errors import ForbiddenError, NotFoundError

    if identity.is_api_key and identity.project_id:
        if x_project_id and x_project_id != identity.project_id:
            raise ForbiddenError(message="API key is not authorized for the requested project")
        return identity.project_id

    if x_project_id:
        stmt = select(Project.id).where(Project.id == x_project_id, Project.is_active.is_(True))
        res = await db.execute(stmt)
        if not res.scalar_one_or_none():
            raise NotFoundError(message=f"Project {x_project_id} not found or inactive")
        return x_project_id

    if identity.project_id:
        return identity.project_id

    # Fall back to user's first available active project
    stmt = select(Project.id).where(Project.is_active.is_(True)).order_by(Project.created_at.asc()).limit(1)
    res = await db.execute(stmt)
    proj_id = res.scalar_one_or_none()
    if proj_id:
        return proj_id

    raise APIError(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="PROJECT_REQUIRED",
        message="Project context required for run operation",
    )


class RunsService:
    @staticmethod
    async def create_run(
        db: AsyncSession,
        identity: Identity,
        payload: RunCreateRequest,
        idempotency_key: Optional[str] = None,
        dispatcher: Optional[RunDispatcher] = None,
        x_project_id: Optional[uuid.UUID] = None,
    ) -> Tuple[Run, bool]:
        """Intake and validate a run request, applying strict idempotency rules.

        Returns (run, is_existing).
        """
        project_id = await resolve_project_id(db, identity, x_project_id)
        input_hash = compute_input_hash(payload.input)

        # 1. Resolve agent
        stmt_agent = select(AgentDefinition).where(AgentDefinition.slug == payload.agent)
        res_agent = await db.execute(stmt_agent)
        agent = res_agent.scalar_one_or_none()
        if not agent:
            raise APIError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="AGENT_NOT_FOUND",
                message=f"Agent '{payload.agent}' not found",
            )

        # 2. Resolve version
        if payload.version:
            stmt_ver = select(AgentVersion).where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.version == payload.version,
            )
            res_ver = await db.execute(stmt_ver)
            version = res_ver.scalar_one_or_none()
            if not version or version.status != "active":
                raise APIError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="AGENT_VERSION_NOT_ACTIVE",
                    message=f"Agent version '{payload.version}' is not active or not found",
                )
        else:
            stmt_ver = select(AgentVersion).where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.status == "active",
            )
            res_ver = await db.execute(stmt_ver)
            version = res_ver.scalar_one_or_none()
            if not version:
                raise APIError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="AGENT_VERSION_NOT_ACTIVE",
                    message=f"No active version found for agent '{payload.agent}'",
                )

        # 3. Validate input against agent JSON Schema
        validator = jsonschema.Draft202012Validator(version.input_schema)
        errors = list(validator.iter_errors(payload.input))
        if errors:
            details = [
                {
                    "path": "/" + "/".join(str(p) for p in err.absolute_path),
                    "message": err.message,
                    "validator": err.validator,
                }
                for err in errors
            ]
            raise APIError(
                status_code=422,
                code="AGENT_INPUT_INVALID",
                message="Agent input failed schema validation",
                details=details,
            )

        # 4. Resolve models
        resolver = ModelResolver.from_settings(settings)
        try:
            resolved_models = await resolver.resolve_all(db, version.model_requirements)
        except NoModelForCapabilityError as exc:
            raise APIError(
                status_code=422,
                code="NO_MODEL_FOR_CAPABILITY",
                message=exc.message,
                details=exc.details,
            ) from exc

        resolved_models_snapshot = {slot: m.snapshot() for slot, m in resolved_models.items()}
        resolved_agent_snapshot = {
            "slug": agent.slug,
            "version": version.version,
            "prompt_hash": version.prompt_hash,
        }

        # 5. Check existing idempotency key before inserting
        if idempotency_key:
            stmt_existing = select(Run).where(
                Run.project_id == project_id,
                Run.idempotency_key == idempotency_key,
            )
            res_existing = await db.execute(stmt_existing)
            existing_run = res_existing.scalar_one_or_none()
            if existing_run:
                if existing_run.agent_version_id == version.id and existing_run.input_hash == input_hash:
                    return existing_run, True
                raise APIError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="IDEMPOTENCY_CONFLICT",
                    message="Idempotency key was previously used with different request parameters",
                )

        # 6. Persist run in queued status
        new_run = Run(
            id=uuid.uuid4(),
            project_id=project_id,
            agent_version_id=version.id,
            status="queued",
            input_payload=payload.input,
            input_hash=input_hash,
            idempotency_key=idempotency_key,
            resolved_agent=resolved_agent_snapshot,
            resolved_models=resolved_models_snapshot,
            initiated_by=identity.user_id if not identity.is_api_key else None,
            api_key_id=identity.api_key_id if identity.is_api_key else None,
            queued_at=utc_now(),
        )
        db.add(new_run)

        event = RunEvent(
            id=uuid.uuid4(),
            run_id=new_run.id,
            seq=1,
            from_status=None,
            to_status="queued",
            payload={
                "initiated_by": str(identity.user_id) if not identity.is_api_key else str(identity.api_key_id),
            },
            created_at=utc_now(),
        )
        db.add(event)

        # 7. Commit transaction with concurrency safety
        target_version_id = version.id
        target_input_hash = input_hash
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            if idempotency_key:
                stmt_existing = select(Run).where(
                    Run.project_id == project_id,
                    Run.idempotency_key == idempotency_key,
                )
                res_existing = await db.execute(stmt_existing)
                existing_run = res_existing.scalar_one_or_none()
                if existing_run:
                    if (
                        existing_run.agent_version_id == target_version_id
                        and existing_run.input_hash == target_input_hash
                    ):
                        return existing_run, True
                    raise APIError(
                        status_code=status.HTTP_409_CONFLICT,
                        code="IDEMPOTENCY_CONFLICT",
                        message="Idempotency key was previously used with different request parameters",
                    ) from None
            raise

        await db.refresh(new_run)

        # Publish run.queued event to Redis Pub/Sub
        try:
            from qa_agentos.runtime.pubsub import RunEventPayload, publish_run_event

            pub_payload = RunEventPayload(
                id=str(event.id),
                version="1.0",
                event="run.queued",
                run_id=str(new_run.id),
                project_id=str(new_run.project_id),
                timestamp=event.created_at.isoformat(),
                data={
                    "seq": event.seq,
                    "status": "queued",
                    "agent": payload.agent,
                    "version": version.version,
                    "queued_at": new_run.queued_at.isoformat() if new_run.queued_at else None,
                },
            )
            await publish_run_event(pub_payload)
        except Exception:
            pass

        # 8. Dispatch asynchronously AFTER transaction commit
        if dispatcher:
            dispatcher.dispatch(new_run.id)

        return new_run, False

    @staticmethod
    async def get_run(
        db: AsyncSession,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> Run:
        """Fetch a specific run ensuring strict project isolation."""
        stmt = select(Run).where(Run.id == run_id, Run.project_id == project_id)
        res = await db.execute(stmt)
        run = res.scalar_one_or_none()
        if not run:
            raise NotFoundError(message="Run not found")
        return run

    @staticmethod
    async def list_runs(
        db: AsyncSession,
        project_id: uuid.UUID,
        status_filter: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Run], int]:
        """List runs visible to the authenticated project with pagination."""
        query = select(Run).where(Run.project_id == project_id)
        count_query = select(func.count(Run.id)).where(Run.project_id == project_id)

        if status_filter:
            query = query.where(Run.status == status_filter)
            count_query = count_query.where(Run.status == status_filter)

        total = (await db.execute(count_query)).scalar() or 0
        ordered_query = query.order_by(Run.created_at.desc(), Run.id.desc()).limit(limit).offset(offset)
        runs = (await db.execute(ordered_query)).scalars().all()

        return list(runs), total

    @staticmethod
    async def get_run_steps(
        db: AsyncSession,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> List[RunStep]:
        """Fetch all execution steps for a run in stable step_number order."""
        await RunsService.get_run(db, run_id, project_id)
        stmt = select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.step_number.asc())
        steps = (await db.execute(stmt)).scalars().all()
        return list(steps)

    @staticmethod
    async def cancel_run(
        db: AsyncSession,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
        identity: Identity,
        dispatcher: Optional[RunDispatcher] = None,
    ) -> Run:
        """Guarded cancellation for queued or running runs with race condition recovery."""
        run = await RunsService.get_run(db, run_id, project_id)
        cancelled_by = str(identity.user_id) if not identity.is_api_key else str(identity.api_key_id)

        for _attempt in range(5):
            if run.status in TERMINAL_STATUSES:
                return run

            state_mgr = RunStateManager(db, run)
            try:
                await state_mgr.transition_to("cancelled", payload={"cancelled_by": cancelled_by})
                await db.commit()
                await db.refresh(run)
                break
            except Exception:
                await db.rollback()
                run = await RunsService.get_run(db, run_id, project_id)

        # Notify in-flight dispatcher
        if dispatcher:
            dispatcher.cancel(run.id)

        return run

    @staticmethod
    async def replay_run(
        db: AsyncSession,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
        identity: Identity,
        re_resolve: bool = False,
        dispatcher: Optional[RunDispatcher] = None,
    ) -> Run:
        """Create a new run replaying an existing run's version and input."""
        original_run = await RunsService.get_run(db, run_id, project_id)

        # Pinned models vs re-resolve
        if not re_resolve:
            # Verify pinned models remain available
            for slot, model_data in (original_run.resolved_models or {}).items():
                m_id = model_data.get("model_id")
                m_key = model_data.get("gateway_model_key")
                stmt_m = select(ModelCatalog).where(
                    ModelCatalog.status == "available",
                    ModelCatalog.is_active.is_(True),
                )
                if m_id:
                    stmt_m = stmt_m.where(ModelCatalog.id == uuid.UUID(str(m_id)))
                elif m_key:
                    stmt_m = stmt_m.where(ModelCatalog.gateway_model_key == m_key)

                model_row = (await db.execute(stmt_m)).scalar_one_or_none()
                if not model_row:
                    raise APIError(
                        status_code=status.HTTP_409_CONFLICT,
                        code="MODEL_UNAVAILABLE",
                        message=f"Pinned model for slot '{slot}' is no longer available. Replay with re_resolve=true to select an eligible replacement.",
                    )
            resolved_models_snapshot = original_run.resolved_models
        else:
            version = await db.get(AgentVersion, original_run.agent_version_id)
            if not version:
                raise NotFoundError(message="Agent version not found")
            resolver = ModelResolver.from_settings(settings)
            try:
                resolved_models = await resolver.resolve_all(db, version.model_requirements)
            except NoModelForCapabilityError as exc:
                raise APIError(
                    status_code=422,
                    code="NO_MODEL_FOR_CAPABILITY",
                    message=exc.message,
                    details=exc.details,
                ) from exc
            resolved_models_snapshot = {slot: m.snapshot() for slot, m in resolved_models.items()}

        new_run = Run(
            id=uuid.uuid4(),
            project_id=project_id,
            agent_version_id=original_run.agent_version_id,
            status="queued",
            input_payload=original_run.input_payload,
            input_hash=original_run.input_hash,
            resolved_agent=original_run.resolved_agent,
            resolved_models=resolved_models_snapshot,
            replayed_from=original_run.id,
            initiated_by=identity.user_id if not identity.is_api_key else None,
            api_key_id=identity.api_key_id if identity.is_api_key else None,
            queued_at=utc_now(),
        )
        db.add(new_run)

        event = RunEvent(
            id=uuid.uuid4(),
            run_id=new_run.id,
            seq=1,
            from_status=None,
            to_status="queued",
            payload={"replayed_from": str(original_run.id)},
            created_at=utc_now(),
        )
        db.add(event)

        await db.commit()
        await db.refresh(new_run)

        if dispatcher:
            dispatcher.dispatch(new_run.id)

        return new_run
