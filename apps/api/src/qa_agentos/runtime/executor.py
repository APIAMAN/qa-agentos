"""High-level RunExecutor interface for direct, inline execution of Agent runs.

Executes runs through the generic LangGraph runtime, model gateway, tool dispatcher,
and checkpointer without requiring Hatchet or HTTP background queues.

Reliability contract (M3):
- Every write is fenced by ``assigned_attempt``: once a newer attempt has claimed the run,
  this executor may no longer record steps or terminal states.
- Step and model-call records are committed inside the node, before LangGraph saves the
  superstep checkpoint, so application history is never behind the checkpoint.
- Each attempt records its own step rows; retries never overwrite earlier attempts.
- Live events are published only after the commit that makes them true.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, cast

import jsonschema
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from structlog import get_logger

from qa_agentos.core.tracing import Tracer, get_tracer
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    ModelCall,
    Run,
    RunStep,
)
from qa_agentos.runtime.events import (
    TERMINAL_STATUSES,
    RunStateManager,
    RunSupersededError,
    RunTerminalStateError,
    commit_and_publish,
    queue_run_event,
    utc_now,
)
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ResolvedModel
from qa_agentos.runtime.graph.builder import build_generic_react_graph
from qa_agentos.runtime.graph.checkpointer import fenced_checkpointer
from qa_agentos.runtime.graph.state import AgentState
from qa_agentos.runtime.tools.dispatcher import ToolCallable, ToolDispatcher

logger = get_logger(__name__)

#: Persists a succeeded run's output somewhere durable (e.g. an artifact). It must only add
#: rows to the given session and never commit. It may return a dict of extra fields recorded on
#: the run's ``succeeded`` event. Failure handling: if it raises, the run still succeeds and the
#: event records ``artifact_status: failed``; the default sink (modules/runs/output.py) never
#: raises and reports each step's outcome in the returned dict instead.
OutputSink = Callable[[AsyncSession, Run, Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]

DEFAULT_MAX_STEPS = 10
DEFAULT_MAX_REPAIRS = 2


def graph_recursion_limit(parameters: Optional[Dict[str, Any]]) -> int:
    """LangGraph superstep budget large enough that ``max_steps``/``max_repairs`` bind first.

    prepare + (generate, execute_tools) per tool step + (validate, repair, generate) per repair
    + final validate/finalize, with headroom.
    """
    params = parameters or {}
    max_steps = int(params.get("max_steps", DEFAULT_MAX_STEPS))
    max_repairs = int(params.get("max_repairs", DEFAULT_MAX_REPAIRS))
    return 2 * max_steps + 3 * max_repairs + 10


@dataclass(frozen=True)
class RunExecutionResult:
    """Terminal outcome of an executed run."""

    run_id: uuid.UUID
    status: str  # "succeeded", "failed", "running", "waiting_for_approval", "superseded", "cancelled"
    output_payload: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    model_calls_count: int = 0
    repair_count: int = 0
    interrupted: bool = False


class RunExecutor:
    """Executes persisted runs inline against the generic LangGraph runtime."""

    def __init__(
        self,
        session: AsyncSession,
        litellm_client: LiteLLMClient,
        *,
        model_resolver: Optional[ModelResolver] = None,
        checkpointer: Optional[BaseCheckpointSaver[Any]] = None,
        assigned_attempt: int = 1,
        tracer: Optional[Tracer] = None,
        output_sink: Optional[OutputSink] = None,
        tool_implementations: Optional[Dict[str, ToolCallable]] = None,
    ) -> None:
        self.session = session
        self.litellm_client = litellm_client
        self.model_resolver = model_resolver
        self.checkpointer = checkpointer
        self.assigned_attempt = assigned_attempt
        self.tracer = tracer or get_tracer()
        self.output_sink = output_sink
        #: Extra/overriding tool implementations (e.g. storage-aware ones injected by the worker).
        self.tool_implementations = tool_implementations

    def _superseded(self, run: Run) -> RunExecutionResult:
        logger.warning(
            "Execution superseded by newer attempt; aborting stale worker",
            run_id=str(run.id),
            current_attempt=run.attempt,
            assigned_attempt=self.assigned_attempt,
        )
        return RunExecutionResult(
            run_id=run.id,
            status="superseded",
            error_code="STALE_WORKER_SUPERSEDED",
            error_message=f"Superseded by attempt {run.attempt}",
        )

    async def _rollback(self, run: Run) -> None:
        """Discard this transaction and reload the run (a rollback expires loaded attributes)."""
        await self.session.rollback()
        await self.session.refresh(run)

    async def _fail(self, state_mgr: RunStateManager, run: Run, code: str, message: str) -> RunExecutionResult:
        """Durably fail the run (fenced by attempt) and return the matching result."""
        try:
            await state_mgr.transition_to(
                "failed",
                payload={"error_code": code, "error_message": message},
                expected_attempt=self.assigned_attempt,
            )
        except RunSupersededError:
            await self._rollback(run)
            return self._superseded(run)
        await commit_and_publish(self.session)
        return RunExecutionResult(run_id=run.id, status="failed", error_code=code, error_message=message)

    async def execute_run(
        self,
        run_or_id: uuid.UUID | Run,
        *,
        interrupt_after: Optional[Sequence[str]] = None,
    ) -> RunExecutionResult:
        """Execute a Run transactionally through the LangGraph runtime."""
        # 1. Load Run entity
        if isinstance(run_or_id, uuid.UUID):
            run = await self.session.get(Run, run_or_id)
            if not run:
                raise ValueError(f"Run {run_or_id} not found")
        else:
            run = run_or_id

        # 2. Check for terminal status
        state_mgr = RunStateManager(self.session, run)

        # Ensure Langfuse trace ID is generated and persisted on run only when enabled
        if not run.langfuse_trace_id:
            if getattr(self.tracer, "is_enabled", False):
                try:
                    run.langfuse_trace_id = self.tracer.create_trace_id()
                except Exception as exc:
                    logger.warning("Failed to generate trace ID from tracer", error=str(exc))
                    run.langfuse_trace_id = None
            else:
                run.langfuse_trace_id = None
        if run.status in TERMINAL_STATUSES:
            raise RunTerminalStateError(f"Run {run.id} is already in terminal state '{run.status}'.")

        if run.status == "waiting_for_approval":
            logger.info("Run is waiting for approval; pausing execution until decided", run_id=str(run.id))
            return RunExecutionResult(
                run_id=run.id,
                status="waiting_for_approval",
                interrupted=True,
            )

        # 3. Load AgentVersion and AgentDefinition
        agent_version = await self.session.get(AgentVersion, run.agent_version_id)
        if not agent_version:
            raise ValueError(f"AgentVersion {run.agent_version_id} not found")

        agent_def = await self.session.get(AgentDefinition, agent_version.agent_id)
        agent_slug = agent_def.slug if agent_def else "agent"

        # 4. Handle initial transitions if run is currently queued
        await self.session.refresh(run, ["status", "attempt"])
        if run.status in TERMINAL_STATUSES:
            return RunExecutionResult(run_id=run.id, status=run.status)
        if (run.attempt or 1) > self.assigned_attempt:
            return self._superseded(run)

        is_resuming = run.status == "running"
        resolved_model: Optional[ResolvedModel] = None
        if run.status in ("queued", "preparing"):
            if run.status == "queued":
                await state_mgr.transition_to("preparing", expected_attempt=self.assigned_attempt)
                await commit_and_publish(self.session)

            # Validate input payload against agent_version.input_schema
            validator = jsonschema.Draft202012Validator(agent_version.input_schema)
            input_errors = list(validator.iter_errors(run.input_payload))
            if input_errors:
                err_msg = "; ".join(e.message for e in input_errors)
                return await self._fail(
                    state_mgr, run, "INPUT_SCHEMA_INVALID", f"Input validation failed: {err_msg}"
                )

            # Resolve model
            if run.resolved_models and "planner" in run.resolved_models:
                resolved_model = ResolvedModel.from_snapshot(run.resolved_models["planner"], "planner")
            elif self.model_resolver:
                reqs = agent_version.model_requirements or {}
                resolved_model = await self.model_resolver.resolve(self.session, reqs, "planner")
                run.resolved_models = {"planner": resolved_model.snapshot()}
                run.resolved_agent = {
                    "slug": agent_slug,
                    "version": agent_version.version,
                    "prompt_hash": agent_version.prompt_hash,
                }
            else:
                return await self._fail(
                    state_mgr,
                    run,
                    "MODEL_RESOLUTION_UNAVAILABLE",
                    "No model resolver provided and no pre-resolved model available",
                )

            # Transition preparing -> running
            run.heartbeat_at = utc_now()
            await state_mgr.transition_to("running", expected_attempt=self.assigned_attempt)
            await commit_and_publish(self.session)
        else:
            # Resuming run: load the pinned model from the run row
            if run.resolved_models and "planner" in run.resolved_models:
                resolved_model = ResolvedModel.from_snapshot(run.resolved_models["planner"], "planner")
            else:
                reqs = agent_version.model_requirements or {}
                if self.model_resolver:
                    resolved_model = await self.model_resolver.resolve(self.session, reqs, "planner")
                else:
                    raise ValueError(f"Run {run.id} has no resolved model")

        # 5. Wire step persistence hook (attempt-scoped idempotent upsert with attempt fence)
        async def _on_step_completed(
            step_type: str,
            node: str,
            step_number: int,
            input_data: Dict[str, Any],
            output_data: Dict[str, Any],
            tokens_input: Optional[int] = None,
            tokens_output: Optional[int] = None,
            gateway_model_key: str = "",
            cost_usd: float = 0.0,
            langfuse_generation_id: Optional[str] = None,
            langfuse_observation_id: Optional[str] = None,
        ) -> None:
            # Atomic attempt fence check under row lock
            stmt_run = select(Run.attempt, Run.status).where(Run.id == run.id)
            if self.session.bind and self.session.bind.dialect.name == "postgresql":
                stmt_run = stmt_run.with_for_update()
            res = await self.session.execute(stmt_run)
            row = res.first()
            if not row:
                logger.warning("Run vanished during step persistence; aborting step write", run_id=str(run.id))
                return
            current_attempt, current_status = row
            if current_status in TERMINAL_STATUSES:
                logger.warning(
                    "Run already in terminal state; refusing to write step", run_id=str(run.id), status=current_status
                )
                return
            if (current_attempt or 1) > self.assigned_attempt:
                logger.warning(
                    "Run attempt superseded by newer attempt; refusing to persist step from stale worker",
                    run_id=str(run.id),
                    current_attempt=current_attempt,
                    assigned_attempt=self.assigned_attempt,
                )
                return

            # Upsert RunStep idempotently within this attempt; other attempts keep their own rows.
            stmt_step = select(RunStep).where(
                RunStep.run_id == run.id,
                RunStep.step_number == step_number,
                RunStep.attempt == self.assigned_attempt,
            )
            existing_step = (await self.session.execute(stmt_step)).scalar_one_or_none()
            if existing_step:
                existing_step.output_data = output_data
                existing_step.tokens_input = cast(Any, tokens_input)
                existing_step.tokens_output = cast(Any, tokens_output)
                existing_step.status = "completed"
                existing_step.finished_at = utc_now()
                if langfuse_observation_id:
                    existing_step.langfuse_observation_id = langfuse_observation_id
                step = existing_step
            else:
                step = RunStep(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    step_number=step_number,
                    step_type=step_type,
                    node=node,
                    attempt=self.assigned_attempt,
                    input_data=input_data,
                    output_data=output_data,
                    status="completed",
                    tokens_input=cast(Any, tokens_input),
                    tokens_output=cast(Any, tokens_output),
                    langfuse_observation_id=langfuse_observation_id,
                    finished_at=utc_now(),
                )
                self.session.add(step)
            await self.session.flush()

            # Persist ModelCall record idempotently (one per step row, i.e. per attempt)
            stmt_mc = select(ModelCall).where(ModelCall.run_id == run.id, ModelCall.step_id == step.id)
            existing_mc = (await self.session.execute(stmt_mc)).scalar_one_or_none()
            if existing_mc:
                existing_mc.tokens_input = cast(Any, tokens_input)
                existing_mc.tokens_output = cast(Any, tokens_output)
                existing_mc.gateway_model_key = gateway_model_key
                existing_mc.cost_usd = cost_usd
                existing_mc.status = "succeeded"
                if langfuse_generation_id:
                    existing_mc.langfuse_generation_id = langfuse_generation_id
            else:
                model_call = ModelCall(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    step_id=step.id,
                    model_id=resolved_model.model_id if resolved_model else None,
                    capability="planner",
                    gateway_model_key=gateway_model_key,
                    tokens_input=cast(Any, tokens_input),
                    tokens_output=cast(Any, tokens_output),
                    cost_usd=cost_usd,
                    status="succeeded",
                    langfuse_generation_id=langfuse_generation_id,
                )
                self.session.add(model_call)
            await self.session.flush()

            # Reconcile run totals from every persisted model call (all attempts = real spend)
            await self._reconcile_totals(run)
            run.heartbeat_at = utc_now()

            from qa_agentos.runtime.pubsub import RunEventPayload

            queue_run_event(
                self.session,
                RunEventPayload(
                    id=str(step.id),
                    version="1.0",
                    event="step.completed",
                    run_id=str(run.id),
                    project_id=str(run.project_id),
                    timestamp=step.finished_at.isoformat() if step.finished_at else utc_now().isoformat(),
                    data={
                        "step_id": str(step.id),
                        "step_number": step.step_number,
                        "step_type": step.step_type,
                        "node": step.node,
                        "status": step.status,
                        "attempt": step.attempt,
                        "tokens_input": step.tokens_input,
                        "tokens_output": step.tokens_output,
                        "input_data": step.input_data,
                        "output_data": step.output_data,
                    },
                ),
            )
            # Durable before LangGraph checkpoints this superstep: history never lags the checkpoint.
            await commit_and_publish(self.session)

        # 6. Build and compile graph
        dispatcher = ToolDispatcher(
            self.session, custom_implementations=self.tool_implementations, commit_on_record=True
        )
        compiled_graph = build_generic_react_graph(
            checkpointer=self._fenced(self.checkpointer, run),
            interrupt_after=interrupt_after,
        )

        configurable: Dict[str, Any] = {
            "thread_id": str(run.id),
            "litellm_client": self.litellm_client,
            "resolved_model": resolved_model,
            "tool_dispatcher": dispatcher,
            "agent_version_entity": agent_version,
            "on_step_completed": _on_step_completed,
            "assigned_attempt": self.assigned_attempt,
            "tracer": self.tracer,
            "trace_id": run.langfuse_trace_id,
        }
        runnable_config = RunnableConfig(
            configurable=configurable,
            recursion_limit=graph_recursion_limit(agent_version.parameters),
        )

        # 7. Execute graph (resume from the durable checkpoint when one exists)
        has_checkpoint = False
        if is_resuming and self.checkpointer:
            try:
                cp = await self.checkpointer.aget(runnable_config)
                has_checkpoint = cp is not None
            except Exception as exc:
                logger.warning("Checkpoint lookup failed; restarting graph from START", run_id=str(run.id), error=str(exc))
                has_checkpoint = False

        final_state: Dict[str, Any] = {}
        graph_finished_before_crash = False
        if has_checkpoint:
            snapshot = await compiled_graph.aget_state(runnable_config)
            if not snapshot.next:
                # The graph reached END but the worker died before finalizing: reuse the saved state.
                final_state = dict(snapshot.values)
                graph_finished_before_crash = True
                logger.info("Checkpoint already at END; finalizing without re-running nodes", run_id=str(run.id))

        graph_input: Optional[AgentState] = None
        if not has_checkpoint:
            graph_input = cast(
                AgentState,
                {
                    "run_id": str(run.id),
                    "project_id": str(run.project_id),
                    "agent_id": str(agent_version.agent_id),
                    "agent_slug": agent_slug,
                    "agent_version_id": str(agent_version.id),
                    "agent_version": agent_version.version,
                    "prompt_template": agent_version.prompt_template,
                    "input_payload": run.input_payload,
                    "output_schema": agent_version.output_schema,
                    "parameters": agent_version.parameters,
                    "permissions": agent_version.permissions,
                    "allowed_tools": agent_version.allowed_tools,
                    "model_requirements": agent_version.model_requirements,
                    "resolved_model": resolved_model.snapshot() if resolved_model else None,
                },
            )

        try:
            if not graph_finished_before_crash:
                async for state_snapshot in compiled_graph.astream(
                    graph_input, config=runnable_config, stream_mode="values"
                ):
                    final_state = state_snapshot
                    run.heartbeat_at = utc_now()
                    await commit_and_publish(self.session)
                    await self.session.refresh(run, ["status", "attempt"])
                    if run.status in TERMINAL_STATUSES:
                        return RunExecutionResult(
                            run_id=run.id,
                            status=run.status,
                            tokens_input=final_state.get("tokens_input", 0),
                            tokens_output=final_state.get("tokens_output", 0),
                            model_calls_count=final_state.get("model_calls_count", 0),
                            repair_count=final_state.get("repair_count", 0),
                        )
                    if run.status == "waiting_for_approval":
                        logger.info("Run transitioned to waiting_for_approval; pausing execution", run_id=str(run.id))
                        return RunExecutionResult(
                            run_id=run.id,
                            status="waiting_for_approval",
                            interrupted=True,
                            tokens_input=final_state.get("tokens_input", 0),
                            tokens_output=final_state.get("tokens_output", 0),
                            model_calls_count=final_state.get("model_calls_count", 0),
                            repair_count=final_state.get("repair_count", 0),
                        )
                    if (run.attempt or 1) > self.assigned_attempt:
                        return self._superseded(run)
        except RunSupersededError:
            # Raised by the checkpoint fence: a newer attempt claimed the run mid-superstep.
            await self._rollback(run)
            return self._superseded(run)
        except GraphRecursionError:
            # Defence in depth: max_steps normally ends the graph first (see graph builder).
            await self._rollback(run)
            return await self._fail(
                state_mgr,
                run,
                "MAX_STEPS_EXCEEDED",
                "Agent exceeded its execution budget (LangGraph recursion limit reached).",
            )
        except asyncio.CancelledError:
            logger.info("Run execution cancelled during graph streaming", run_id=str(run.id))
            raise
        finally:
            try:
                self.tracer.flush()
            except Exception:
                pass

        # 8. Check if graph was interrupted
        if interrupt_after:
            status = final_state.get("status")
            if status == "running":
                return RunExecutionResult(
                    run_id=run.id,
                    status="running",
                    tokens_input=final_state.get("tokens_input", 0),
                    tokens_output=final_state.get("tokens_output", 0),
                    model_calls_count=final_state.get("model_calls_count", 0),
                    repair_count=final_state.get("repair_count", 0),
                    interrupted=True,
                )

        # 9. Handle terminal or paused state
        await self.session.refresh(run, ["status", "attempt"])
        if run.status == "waiting_for_approval":
            return RunExecutionResult(
                run_id=run.id,
                status="waiting_for_approval",
                interrupted=True,
                tokens_input=final_state.get("tokens_input", 0),
                tokens_output=final_state.get("tokens_output", 0),
                model_calls_count=final_state.get("model_calls_count", 0),
                repair_count=final_state.get("repair_count", 0),
            )
        if run.status in TERMINAL_STATUSES:
            return RunExecutionResult(
                run_id=run.id,
                status=run.status,
                tokens_input=final_state.get("tokens_input", 0),
                tokens_output=final_state.get("tokens_output", 0),
                model_calls_count=final_state.get("model_calls_count", 0),
                repair_count=final_state.get("repair_count", 0),
            )
        if (run.attempt or 1) > self.assigned_attempt:
            return self._superseded(run)

        await self._reconcile_totals(run)
        status = final_state.get("status")

        try:
            if status == "succeeded":
                final_out = final_state.get("final_output")
                run.output_payload = final_out
                success_payload: Dict[str, Any] = {"summary": final_out.get("summary") if final_out else ""}
                if final_out and self.output_sink:
                    try:
                        sink_extra = await self.output_sink(self.session, run, final_out)
                        success_payload["artifact_status"] = "stored"
                        if sink_extra:
                            success_payload.update(sink_extra)
                    except Exception as art_err:
                        # The output itself is persisted on the run row; the artifact is a derived
                        # copy. Record the failure in the run history instead of hiding it.
                        logger.error(
                            "Failed to store run output artifact", error=str(art_err), run_id=str(run.id)
                        )
                        success_payload["artifact_status"] = "failed"
                        success_payload["artifact_error"] = type(art_err).__name__
                await state_mgr.transition_to(
                    "succeeded", payload=success_payload, expected_attempt=self.assigned_attempt
                )
                await commit_and_publish(self.session)
                return RunExecutionResult(
                    run_id=run.id,
                    status="succeeded",
                    output_payload=final_out,
                    tokens_input=run.tokens_input,
                    tokens_output=run.tokens_output,
                    cost_usd=run.cost_usd,
                    model_calls_count=final_state.get("model_calls_count", 0),
                    repair_count=final_state.get("repair_count", 0),
                )

            err_code = final_state.get("error_code") or "RUN_FAILED"
            err_msg = final_state.get("error_message") or "Agent execution failed"
            err_details = final_state.get("error_details") or {}
            await state_mgr.transition_to(
                "failed",
                payload={
                    "error_code": err_code,
                    "error_message": err_msg,
                    "error_details": err_details,
                },
                expected_attempt=self.assigned_attempt,
            )
            await commit_and_publish(self.session)
            return RunExecutionResult(
                run_id=run.id,
                status="failed",
                error_code=err_code,
                error_message=err_msg,
                tokens_input=run.tokens_input,
                tokens_output=run.tokens_output,
                cost_usd=run.cost_usd,
                model_calls_count=final_state.get("model_calls_count", 0),
                repair_count=final_state.get("repair_count", 0),
            )
        except RunSupersededError:
            await self._rollback(run)
            return self._superseded(run)

    def _fenced(
        self, checkpointer: Optional[BaseCheckpointSaver[Any]], run: Run
    ) -> Optional[BaseCheckpointSaver[Any]]:
        """Fence checkpoint writes by attempt (PostgreSQL deployments).

        Uses a separate pooled connection, not ``self.session``: LangGraph persists checkpoints
        in the background, concurrently with node code that uses the session.
        """
        bind = self.session.bind
        if checkpointer is None or not isinstance(bind, AsyncEngine) or bind.dialect.name != "postgresql":
            return checkpointer
        engine: AsyncEngine = bind
        run_id = run.id
        assigned = self.assigned_attempt

        async def fence() -> None:
            async with engine.connect() as conn:
                current = (await conn.execute(select(Run.attempt).where(Run.id == run_id))).scalar_one_or_none()
            if current is not None and (current or 1) > assigned:
                logger.warning(
                    "Refusing checkpoint write from superseded attempt",
                    run_id=str(run_id),
                    current_attempt=current,
                    assigned_attempt=assigned,
                )
                raise RunSupersededError(f"Run {run_id} is owned by attempt {current}; attempt {assigned} is stale.")

        return fenced_checkpointer(checkpointer, fence)

    async def _reconcile_totals(self, run: Run) -> None:
        totals_stmt = select(
            func.coalesce(func.sum(ModelCall.tokens_input), 0),
            func.coalesce(func.sum(ModelCall.tokens_output), 0),
            func.coalesce(func.sum(ModelCall.cost_usd), 0.0),
        ).where(ModelCall.run_id == run.id)
        totals = (await self.session.execute(totals_stmt)).one()
        run.tokens_input = int(totals[0])
        run.tokens_output = int(totals[1])
        run.cost_usd = round(float(totals[2]), 6)
