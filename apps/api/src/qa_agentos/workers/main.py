"""Durable Hatchet execution worker for QA AgentOS M1 Phase 6.

Subscribes to Hatchet task engine and processes `agent_run` tasks and `outbox_sweeper` cron sweeps.
Reuses the generic LangGraph runtime via RunExecutor, enforcers idempotent state transitions,
safely handles duplicate deliveries and worker crashes, and distinguishes permanent from retryable errors.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import jsonschema
import jwt
from hatchet_sdk import ClientConfig, Context, Hatchet, NonRetryableException
from pydantic import BaseModel
from qa_agentos.core.config import settings
from qa_agentos.core.database import AsyncSessionLocal
from qa_agentos.core.logging import setup_logging
from qa_agentos.models.domain import Run
from qa_agentos.modules.runs.sweeper import sweep_outbox
from qa_agentos.runtime.events import TERMINAL_STATUSES, RunStateManager
from qa_agentos.runtime.executor import RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ProviderPolicy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

logger = get_logger(__name__)


def get_hatchet_client() -> Hatchet:
    """Initialize Hatchet SDK client with configured or fallback JWT token for tests/dev."""
    if settings.HATCHET_CLIENT_TOKEN and settings.HATCHET_CLIENT_TOKEN.get_secret_value().strip():
        token_str = settings.HATCHET_CLIENT_TOKEN.get_secret_value().strip()
    else:
        # Synthetic local dev/test JWT token allowing module import and mock-based testing without live engine
        token_str = jwt.encode(
            {
                "sub": settings.HATCHET_CLIENT_TENANT_ID or "707d0855-80ab-4e1f-a156-f1c4546cbf52",
                "server_url": settings.HATCHET_CLIENT_SERVER_URL or "http://localhost:8080",
                "grpc_broadcast_address": settings.HATCHET_CLIENT_HOST_PORT or "localhost:7077",
            },
            "dev-insecure-hatchet-jwt-signing-key-minimum-32-chars",
            algorithm="HS256",
        )

    config = ClientConfig(
        token=token_str,
        server_url=settings.HATCHET_CLIENT_SERVER_URL or "http://localhost:8080",
        host_port=settings.HATCHET_CLIENT_HOST_PORT or "localhost:7077",
        tenant_id=settings.HATCHET_CLIENT_TENANT_ID or "707d0855-80ab-4e1f-a156-f1c4546cbf52",
    )
    return Hatchet(config=config)


hatchet = get_hatchet_client()


class AgentRunInput(BaseModel):
    run_id: str


class SweeperInput(BaseModel):
    pass


@hatchet.task(
    name="agent_run",
    input_validator=AgentRunInput,
    execution_timeout=f"{settings.HATCHET_TASK_TIMEOUT_MINUTES}m",
    retries=settings.HATCHET_TASK_RETRIES,
)
async def agent_run_task(input_data: AgentRunInput, ctx: Context) -> Dict[str, Any]:
    """Durable execution task for agent runs."""
    return await execute_agent_run(input_data.model_dump(), ctx, session_factory=AsyncSessionLocal)


async def execute_agent_run(
    input_data: Dict[str, Any],
    ctx: Any,
    *,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    litellm_client: Optional[LiteLLMClient] = None,
    model_resolver: Optional[ModelResolver] = None,
) -> Dict[str, Any]:
    """Execute run with idempotent claims, duplicate delivery detection, and crash recovery."""
    run_id_val = input_data.get("run_id") if isinstance(input_data, dict) else None
    if not run_id_val:
        raise NonRetryableException("Missing 'run_id' in task payload")

    try:
        run_id = uuid.UUID(str(run_id_val))
    except (ValueError, TypeError) as exc:
        raise NonRetryableException(f"Invalid UUID 'run_id': {run_id_val}") from exc

    attempt_number = getattr(ctx, "attempt_number", 1)
    workflow_run_id = getattr(ctx, "workflow_run_id", None)
    logger.info(
        "Worker received agent_run task", run_id=str(run_id), attempt=attempt_number, hatchet_run_id=workflow_run_id
    )

    # 1. Claim run idempotently and verify status
    async with session_factory() as session:
        stmt = select(Run).where(Run.id == run_id)
        if session.bind and session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update()

        run = (await session.execute(stmt)).scalar_one_or_none()
        if not run:
            logger.error("Run not found in database", run_id=str(run_id))
            raise NonRetryableException(f"Run {run_id} does not exist")

        # Terminal check: Never re-execute a terminal run
        if run.status in TERMINAL_STATUSES:
            logger.info("Run is already in terminal state; skipping execution", run_id=str(run_id), status=run.status)
            return {"run_id": str(run_id), "status": run.status, "message": "Already terminal"}

        # Duplicate delivery vs retry-after-crash detection with monotonic attempt fencing
        current_attempt = run.attempt or 1
        if run.status == "queued":
            # Atomic claim: transition queued -> preparing under row lock
            state_mgr = RunStateManager(session, run)
            await state_mgr.transition_to("preparing")
            run.attempt = attempt_number
            if workflow_run_id and (not run.hatchet_run_id or run.hatchet_run_id.startswith("sweeper-pending")):
                run.hatchet_run_id = str(workflow_run_id)
            await session.commit()
            logger.info("Atomically claimed queued run for execution", run_id=str(run_id), attempt=attempt_number)
        elif run.status in ("preparing", "running"):
            if attempt_number <= current_attempt:
                logger.warning(
                    "Duplicate delivery detected for active run in execution; skipping duplicate execution",
                    run_id=str(run_id),
                    current_attempt=current_attempt,
                    delivery_attempt=attempt_number,
                )
                return {"run_id": str(run_id), "status": run.status, "message": "Duplicate delivery ignored"}
            logger.info(
                "Resuming run after worker crash / retry attempt",
                run_id=str(run_id),
                old_attempt=current_attempt,
                new_attempt=attempt_number,
            )
            run.attempt = attempt_number
            if workflow_run_id and (not run.hatchet_run_id or run.hatchet_run_id.startswith("sweeper-pending")):
                run.hatchet_run_id = str(workflow_run_id)
            await session.commit()
        else:
            logger.warning("Run has unexpected status for execution; aborting", run_id=str(run_id), status=run.status)
            return {"run_id": str(run_id), "status": run.status, "message": f"Unexpected status {run.status}"}

    # 2. Execute via RunExecutor
    client = litellm_client or LiteLLMClient.from_settings(settings)
    policy = ProviderPolicy.from_settings(settings)
    resolver = model_resolver or ModelResolver(policy)

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        if not run:
            raise NonRetryableException(f"Run {run_id} vanished before execution")

        executor = RunExecutor(
            session,
            client,
            model_resolver=resolver,
            assigned_attempt=attempt_number,
        )

        try:
            result = await executor.execute_run(run)
            logger.info(
                "Run execution finished successfully",
                run_id=str(run_id),
                status=result.status,
                tokens_input=result.tokens_input,
                tokens_output=result.tokens_output,
            )
            return {
                "run_id": str(run_id),
                "status": result.status,
                "tokens_input": result.tokens_input,
                "tokens_output": result.tokens_output,
            }
        except (ValueError, KeyError, jsonschema.ValidationError) as perm_exc:
            logger.error("Permanent error encountered during run execution", run_id=str(run_id), error=str(perm_exc))
            raise NonRetryableException(f"Permanent execution failure: {perm_exc}") from perm_exc
        except Exception as trans_exc:
            logger.exception(
                "Retryable error encountered during run execution", run_id=str(run_id), error=str(trans_exc)
            )
            raise


@hatchet.task(
    name="outbox_sweeper",
    input_validator=SweeperInput,
    on_crons=[f"*/{max(1, settings.HATCHET_SWEEPER_INTERVAL_SECONDS // 60)} * * * *"],
    execution_timeout="5m",
)
async def outbox_sweeper_task(input_data: SweeperInput, ctx: Context) -> Dict[str, Any]:
    """Periodic outbox sweeper recovering runs stuck in queued state."""
    from qa_agentos.modules.runs.dispatcher import get_dispatcher

    dispatcher = get_dispatcher()
    recovered = await sweep_outbox(
        session_factory=AsyncSessionLocal,
        dispatcher=dispatcher,
        threshold_seconds=settings.HATCHET_SWEEPER_THRESHOLD_SECONDS,
    )
    return {"recovered": recovered}


def run_worker() -> None:
    """Worker process entry point."""
    setup_logging()
    logger.info("Initializing QA AgentOS durable execution worker", concurrency=settings.HATCHET_WORKER_CONCURRENCY)

    worker = hatchet.worker(
        name="qa-agentos-worker",
        slots=settings.HATCHET_WORKER_CONCURRENCY,
    )

    # Register workflow tasks
    worker.register_workflow(agent_run_task)
    worker.register_workflow(outbox_sweeper_task)

    logger.info("Registered tasks; starting Hatchet worker loop")
    worker.start()


if __name__ == "__main__":
    run_worker()
