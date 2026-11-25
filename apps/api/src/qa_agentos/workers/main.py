"""Durable Hatchet execution worker for QA AgentOS M1 Phase 6.

Subscribes to Hatchet task engine and processes `agent_run` tasks and `outbox_sweeper` cron sweeps.
Reuses the generic LangGraph runtime via RunExecutor, enforcers idempotent state transitions,
safely handles duplicate deliveries and worker crashes, and distinguishes permanent from retryable errors.
"""

from __future__ import annotations

import uuid
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import jsonschema
import jwt
from hatchet_sdk import ClientConfig, Context, Hatchet, NonRetryableException
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel
from qa_agentos.core.config import settings
from qa_agentos.core.database import AsyncSessionLocal
from qa_agentos.core.errors import APIError
from qa_agentos.core.logging import setup_logging
from qa_agentos.models.domain import Run
from qa_agentos.modules.bugs.tools import make_tool_implementations
from qa_agentos.modules.runs.output import run_output_sink
from qa_agentos.modules.runs.recovery import fail_run_if_owned, reap_stale_runs
from qa_agentos.modules.runs.sweeper import sweep_outbox
from qa_agentos.runtime.events import (
    TERMINAL_STATUSES,
    RunStateManager,
    RunTerminalStateError,
    commit_and_publish,
    utc_now,
)
from qa_agentos.runtime.executor import OutputSink, RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.errors import ModelGatewayError
from qa_agentos.runtime.gateway.resolution import ModelResolver, ProviderPolicy
from qa_agentos.runtime.graph.checkpointer import postgres_checkpointer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger
from structlog.contextvars import bind_contextvars, unbind_contextvars

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


@dataclass(frozen=True)
class RunClaim:
    """Outcome of trying to take ownership of a run for one Hatchet delivery."""

    owned: bool
    fencing_attempt: int = 0
    response: Optional[Dict[str, Any]] = None


def is_permanent_error(exc: BaseException) -> bool:
    """Errors that cannot succeed on retry; retrying them only repeats billable work."""
    if isinstance(exc, ModelGatewayError):
        return not exc.retryable
    return isinstance(exc, (ValueError, KeyError, jsonschema.ValidationError, GraphRecursionError))


async def claim_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    hatchet_attempt: int,
    workflow_run_id: Optional[str],
) -> RunClaim:
    """Atomically decide whether this delivery may execute the run.

    ``run.attempt`` is a monotonic fencing token stored in the database; it is bumped on
    every retry/resume claim so older workers are fenced off. A delivery may claim:

    1. a ``queued`` run (first claim);
    2. a ``preparing``/``running`` run with a pending approval resume (single use);
    3. a ``preparing``/``running`` run it already owns, on a higher Hatchet attempt
       (Hatchet retry of the same task run after a crash or transient failure).

    Everything else is a duplicate delivery and is ignored.
    """
    async with session_factory() as session:
        stmt = select(Run).where(Run.id == run_id)
        if session.bind and session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        run = (await session.execute(stmt)).scalar_one_or_none()
        if not run:
            logger.error("Run not found in database", run_id=str(run_id))
            raise NonRetryableException(f"Run {run_id} does not exist")

        if run.status in TERMINAL_STATUSES:
            logger.info("Run is already in terminal state; skipping execution", status=run.status)
            return RunClaim(False, response={"run_id": str(run_id), "status": run.status, "message": "Already terminal"})

        if run.status == "waiting_for_approval":
            logger.info("Run is waiting for approval; nothing to execute")
            return RunClaim(
                False, response={"run_id": str(run_id), "status": run.status, "message": "Waiting for approval"}
            )

        current_attempt = run.attempt or 1
        state_mgr = RunStateManager(session, run)
        claim_kind: Optional[str] = None

        if run.status == "queued":
            await state_mgr.transition_to("preparing")
            run.attempt = max(current_attempt, hatchet_attempt)
            claim_kind = "initial"
        elif run.status in ("preparing", "running"):
            if run.resume_requested_at is not None:
                run.attempt = current_attempt + 1
                run.resume_requested_at = None
                claim_kind = "resume"
            elif run.hatchet_attempt is not None:
                if workflow_run_id == run.hatchet_run_id and hatchet_attempt > run.hatchet_attempt:
                    run.attempt = current_attempt + 1
                    claim_kind = "retry"
            elif hatchet_attempt > current_attempt:
                # Legacy rows (claimed before owner tracking existed): Hatchet attempt == fencing token.
                run.attempt = hatchet_attempt
                claim_kind = "retry"
        else:
            logger.warning("Run has unexpected status for execution; aborting", status=run.status)
            return RunClaim(
                False,
                response={"run_id": str(run_id), "status": run.status, "message": f"Unexpected status {run.status}"},
            )

        if claim_kind is None:
            logger.warning(
                "Duplicate delivery detected for active run; skipping duplicate execution",
                current_attempt=current_attempt,
                owner_hatchet_attempt=run.hatchet_attempt,
                owner_workflow_run_id=run.hatchet_run_id,
            )
            return RunClaim(
                False, response={"run_id": str(run_id), "status": run.status, "message": "Duplicate delivery ignored"}
            )

        if workflow_run_id:
            run.hatchet_run_id = workflow_run_id
        run.hatchet_attempt = hatchet_attempt
        run.heartbeat_at = utc_now()
        if claim_kind != "initial":
            await state_mgr.record_attempt(
                claim_kind,
                {
                    "previous_attempt": current_attempt,
                    "hatchet_attempt": hatchet_attempt,
                    "hatchet_run_id": workflow_run_id,
                },
            )
        fencing_attempt = run.attempt
        await commit_and_publish(session)
        logger.info("Claimed run for execution", claim=claim_kind, fencing_attempt=fencing_attempt)
        return RunClaim(True, fencing_attempt=fencing_attempt)


def _default_checkpointer() -> AbstractAsyncContextManager[Optional[BaseCheckpointSaver[Any]]]:
    if not settings.WORKER_CHECKPOINTER_ENABLED:
        return nullcontext(None)
    return postgres_checkpointer(settings.DATABASE_URL, min_size=1, max_size=1)


async def execute_agent_run(
    input_data: Dict[str, Any],
    ctx: Any,
    *,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    litellm_client: Optional[LiteLLMClient] = None,
    model_resolver: Optional[ModelResolver] = None,
    checkpointer_factory: Optional[Callable[[], AbstractAsyncContextManager[Any]]] = None,
    output_sink: Optional[OutputSink] = run_output_sink,
    tool_implementations: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute a run with fenced claims, durable checkpoints and guaranteed terminal states."""
    run_id_val = input_data.get("run_id") if isinstance(input_data, dict) else None
    if not run_id_val:
        raise NonRetryableException("Missing 'run_id' in task payload")

    try:
        run_id = uuid.UUID(str(run_id_val))
    except (ValueError, TypeError) as exc:
        raise NonRetryableException(f"Invalid UUID 'run_id': {run_id_val}") from exc

    hatchet_attempt = int(getattr(ctx, "attempt_number", 1) or 1)
    raw_wf = getattr(ctx, "workflow_run_id", None)
    workflow_run_id = str(raw_wf) if raw_wf else None
    bind_contextvars(run_id=str(run_id), hatchet_attempt=hatchet_attempt, hatchet_run_id=workflow_run_id)
    try:
        logger.info("Worker received agent_run task")

        # 1. Claim the run (fenced, idempotent)
        claim = await claim_run(
            session_factory, run_id, hatchet_attempt=hatchet_attempt, workflow_run_id=workflow_run_id
        )
        if not claim.owned:
            return claim.response or {"run_id": str(run_id)}
        fencing_attempt = claim.fencing_attempt
        bind_contextvars(attempt=fencing_attempt)

        # 2. Execute via RunExecutor with a durable checkpointer
        client = litellm_client or LiteLLMClient.from_settings(settings)
        resolver = model_resolver or ModelResolver(ProviderPolicy.from_settings(settings))
        cp_factory = checkpointer_factory or _default_checkpointer

        try:
            async with cp_factory() as checkpointer:
                async with session_factory() as session:
                    run = await session.get(Run, run_id)
                    if not run:
                        raise NonRetryableException(f"Run {run_id} vanished before execution")
                    executor = RunExecutor(
                        session,
                        client,
                        model_resolver=resolver,
                        checkpointer=checkpointer,
                        assigned_attempt=fencing_attempt,
                        output_sink=output_sink,
                        tool_implementations=(
                            tool_implementations
                            if tool_implementations is not None
                            else make_tool_implementations(session_factory)
                        ),
                    )
                    result = await executor.execute_run(run)
        except NonRetryableException:
            raise
        except RunTerminalStateError:
            logger.info("Run became terminal before execution started")
            return {"run_id": str(run_id), "status": "terminal", "message": "Already terminal"}
        except Exception as exc:
            # Only our APIError codes are meaningful; e.g. SQLAlchemy errors carry a doc-link ``code``.
            error_code = exc.code if isinstance(exc, APIError) else type(exc).__name__
            if is_permanent_error(exc):
                logger.error("Permanent error during run execution", error_code=error_code, error=str(exc))
                await fail_run_if_owned(
                    session_factory,
                    run_id,
                    attempt=fencing_attempt,
                    error_code=error_code,
                    error_message=f"Permanent execution failure: {exc}",
                    error_details={"error_type": type(exc).__name__, "retryable": False},
                )
                raise NonRetryableException(f"Permanent execution failure: {exc}") from exc
            if hatchet_attempt >= settings.hatchet_max_attempts:
                logger.error("Retries exhausted for run", error_code=error_code, error=str(exc))
                await fail_run_if_owned(
                    session_factory,
                    run_id,
                    attempt=fencing_attempt,
                    error_code="RETRIES_EXHAUSTED",
                    error_message=f"Execution failed after {hatchet_attempt} attempts: {exc}",
                    error_details={"last_error_code": error_code, "error_type": type(exc).__name__},
                )
                raise NonRetryableException(f"Retries exhausted: {exc}") from exc
            logger.warning(
                "Retryable error during run execution; Hatchet will retry",
                error_code=error_code,
                error=str(exc),
                remaining_attempts=settings.hatchet_max_attempts - hatchet_attempt,
            )
            raise

        logger.info(
            "Run execution finished",
            status=result.status,
            tokens_input=result.tokens_input,
            tokens_output=result.tokens_output,
            cost_usd=result.cost_usd,
        )
        return {
            "run_id": str(run_id),
            "status": result.status,
            "tokens_input": result.tokens_input,
            "tokens_output": result.tokens_output,
            "cost_usd": result.cost_usd,
        }
    finally:
        unbind_contextvars("run_id", "hatchet_attempt", "hatchet_run_id", "attempt")


@hatchet.task(
    name="outbox_sweeper",
    input_validator=SweeperInput,
    on_crons=[f"*/{max(1, settings.HATCHET_SWEEPER_INTERVAL_SECONDS // 60)} * * * *"],
    execution_timeout="5m",
)
async def outbox_sweeper_task(input_data: SweeperInput, ctx: Context) -> Dict[str, Any]:
    """Periodic reconciliation: re-enqueue unassigned queued runs and recover stale runs."""
    from qa_agentos.modules.runs.dispatcher import get_dispatcher

    dispatcher = get_dispatcher()
    recovered = await sweep_outbox(
        session_factory=AsyncSessionLocal,
        dispatcher=dispatcher,
        threshold_seconds=settings.HATCHET_SWEEPER_THRESHOLD_SECONDS,
    )
    reaped = await reap_stale_runs(AsyncSessionLocal, dispatcher)
    return {
        "recovered": recovered,
        "stale_failed": len(reaped.failed),
        "resume_redispatched": len(reaped.resume_redispatched),
        "queued_released": len(reaped.queued_released),
    }


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
