"""High-level RunExecutor interface for direct, inline execution of Agent runs.

Executes runs through the generic LangGraph runtime, model gateway, tool dispatcher,
and PostgreSQL checkpointer without requiring Hatchet or HTTP background queues.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, cast

import jsonschema
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
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
    RunTerminalStateError,
    utc_now,
)
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ResolvedModel
from qa_agentos.runtime.graph.builder import build_generic_react_graph
from qa_agentos.runtime.graph.state import AgentState
from qa_agentos.runtime.tools.dispatcher import ToolDispatcher

logger = get_logger(__name__)


@dataclass(frozen=True)
class RunExecutionResult:
    """Terminal outcome of an executed run."""

    run_id: uuid.UUID
    status: str  # "succeeded", "failed", "running"
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
    ) -> None:
        self.session = session
        self.litellm_client = litellm_client
        self.model_resolver = model_resolver
        self.checkpointer = checkpointer
        self.assigned_attempt = assigned_attempt
        self.tracer = tracer or get_tracer()

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
        if run.status in ("succeeded", "failed", "cancelled"):
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
        await self.session.refresh(run, ["status"])
        if run.status in TERMINAL_STATUSES:
            return RunExecutionResult(run_id=run.id, status=run.status)

        is_resuming = run.status == "running"
        if run.status in ("queued", "preparing"):
            if run.status == "queued":
                await state_mgr.transition_to("preparing")
                await self.session.commit()

            # Validate input payload against agent_version.input_schema
            validator = jsonschema.Draft202012Validator(agent_version.input_schema)
            input_errors = list(validator.iter_errors(run.input_payload))
            if input_errors:
                err_msg = "; ".join(e.message for e in input_errors)
                await state_mgr.transition_to(
                    "failed",
                    payload={
                        "error_code": "INPUT_SCHEMA_INVALID",
                        "error_message": f"Input validation failed: {err_msg}",
                    },
                )
                return RunExecutionResult(
                    run_id=run.id,
                    status="failed",
                    error_code="INPUT_SCHEMA_INVALID",
                    error_message=f"Input validation failed: {err_msg}",
                )

            # Resolve model
            resolved_model: Optional[ResolvedModel] = None
            if run.resolved_models and "planner" in run.resolved_models:
                # Pre-resolved snapshot
                rm_dict = run.resolved_models["planner"]
                model_id_val = rm_dict.get("model_id") or rm_dict.get("catalog_id")
                cost_in = rm_dict.get("cost_input_per_1m")
                cost_out = rm_dict.get("cost_output_per_1m")
                resolved_model = ResolvedModel(
                    capability=rm_dict.get("capability", "planner"),
                    model_id=uuid.UUID(str(model_id_val)) if model_id_val else uuid.uuid4(),
                    provider=rm_dict["provider"],
                    name=rm_dict.get("name") or rm_dict.get("model_name", "mock-model"),
                    gateway_model_key=rm_dict["gateway_model_key"],
                    capabilities=tuple(rm_dict.get("capabilities", ["reasoning", "json"])),
                    structured_output=rm_dict.get("structured_output", True),
                    context_window=rm_dict.get("context_window", 128000),
                    max_output_tokens=rm_dict.get("max_output_tokens"),
                    priority=rm_dict.get("priority", 1),
                    cost_input_per_1m=float(cost_in) if cost_in is not None else None,
                    cost_output_per_1m=float(cost_out) if cost_out is not None else None,
                )
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
                await state_mgr.transition_to(
                    "failed",
                    payload={
                        "error_code": "MODEL_RESOLUTION_UNAVAILABLE",
                        "error_message": "No model resolver provided and no pre-resolved model available",
                    },
                )
                return RunExecutionResult(
                    run_id=run.id,
                    status="failed",
                    error_code="MODEL_RESOLUTION_UNAVAILABLE",
                    error_message="No model resolver provided",
                )

            # Transition preparing -> running
            await state_mgr.transition_to("running")
            await self.session.commit()
        else:
            # Resuming run: load pre-resolved model from run row
            if run.resolved_models and "planner" in run.resolved_models:
                rm_dict = run.resolved_models["planner"]
                model_id_val = rm_dict.get("model_id") or rm_dict.get("catalog_id")
                cost_in = rm_dict.get("cost_input_per_1m")
                cost_out = rm_dict.get("cost_output_per_1m")
                resolved_model = ResolvedModel(
                    capability=rm_dict.get("capability", "planner"),
                    model_id=uuid.UUID(str(model_id_val)) if model_id_val else uuid.uuid4(),
                    provider=rm_dict["provider"],
                    name=rm_dict.get("name") or rm_dict.get("model_name", "mock-model"),
                    gateway_model_key=rm_dict["gateway_model_key"],
                    capabilities=tuple(rm_dict.get("capabilities", ["reasoning", "json"])),
                    structured_output=rm_dict.get("structured_output", True),
                    context_window=rm_dict.get("context_window", 128000),
                    max_output_tokens=rm_dict.get("max_output_tokens"),
                    priority=rm_dict.get("priority", 1),
                    cost_input_per_1m=float(cost_in) if cost_in is not None else None,
                    cost_output_per_1m=float(cost_out) if cost_out is not None else None,
                )
            else:
                reqs = agent_version.model_requirements or {}
                if self.model_resolver:
                    resolved_model = await self.model_resolver.resolve(self.session, reqs, "planner")
                else:
                    raise ValueError(f"Run {run.id} has no resolved model")

        # 5. Wire step persistence hook (idempotent upsert with attempt fence check)
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

            # Upsert RunStep idempotently with actual assigned attempt
            stmt_step = select(RunStep).where(RunStep.run_id == run.id, RunStep.step_number == step_number)
            existing_step = (await self.session.execute(stmt_step)).scalar_one_or_none()
            if existing_step:
                existing_step.output_data = output_data
                existing_step.tokens_input = cast(Any, tokens_input)
                existing_step.tokens_output = cast(Any, tokens_output)
                existing_step.status = "completed"
                existing_step.attempt = self.assigned_attempt
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

            # Persist ModelCall record idempotently
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

            # Reconcile run totals from all persisted model calls for this run
            totals_stmt = select(
                func.coalesce(func.sum(ModelCall.tokens_input), 0),
                func.coalesce(func.sum(ModelCall.tokens_output), 0),
                func.coalesce(func.sum(ModelCall.cost_usd), 0.0),
            ).where(ModelCall.run_id == run.id)
            totals = (await self.session.execute(totals_stmt)).one()
            run.tokens_input = int(totals[0])
            run.tokens_output = int(totals[1])
            run.cost_usd = round(float(totals[2]), 6)
            await self.session.flush()

            # Publish step.completed event to Redis Pub/Sub
            try:
                from qa_agentos.runtime.pubsub import RunEventPayload, publish_run_event

                step_payload = RunEventPayload(
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
                )
                await publish_run_event(step_payload)
            except Exception:
                pass

        # 6. Build and compile graph
        dispatcher = ToolDispatcher(self.session)
        compiled_graph = build_generic_react_graph(
            checkpointer=self.checkpointer,
            interrupt_after=interrupt_after,
        )

        config: Dict[str, Any] = {
            "configurable": {
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
        }

        runnable_config = RunnableConfig(configurable=config["configurable"])

        # 7. Execute graph (pass None when resuming from checkpoint)
        has_checkpoint = False
        if is_resuming and self.checkpointer:
            try:
                cp = await self.checkpointer.aget(runnable_config)
                if cp is not None:
                    has_checkpoint = True
            except Exception:
                has_checkpoint = False

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

        final_state: Dict[str, Any] = {}
        try:
            async for state_snapshot in compiled_graph.astream(
                graph_input, config=runnable_config, stream_mode="values"
            ):
                final_state = state_snapshot
                await self.session.commit()
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
            # Check if execution stopped before reaching a terminal status
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
            logger.warning(
                "Cannot finalize terminal state; superseded by newer attempt",
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
        # Reconcile run totals from all persisted model calls for this run
        totals_stmt = select(
            func.coalesce(func.sum(ModelCall.tokens_input), 0),
            func.coalesce(func.sum(ModelCall.tokens_output), 0),
            func.coalesce(func.sum(ModelCall.cost_usd), 0.0),
        ).where(ModelCall.run_id == run.id)
        totals = (await self.session.execute(totals_stmt)).one()
        run.tokens_input = int(totals[0])
        run.tokens_output = int(totals[1])
        run.cost_usd = round(float(totals[2]), 6)

        try:
            self.tracer.flush()
        except Exception:
            pass
        status = final_state.get("status")

        if status == "succeeded":
            final_out = final_state.get("final_output")
            run.output_payload = final_out
            await state_mgr.transition_to(
                "succeeded",
                payload={"summary": final_out.get("summary") if final_out else ""},
            )
            if final_out:
                try:
                    import json

                    from qa_agentos.modules.artifacts.service import ArtifactsService
                    from qa_agentos.modules.auth.schemas import Identity
                    identity = Identity(
                        user_id=None,
                        project_id=run.project_id,
                        role="system",
                        scopes=["*"],
                        is_api_key=False,
                    )
                    raw_bytes = json.dumps(final_out, indent=2).encode("utf-8")
                    await ArtifactsService.create_artifact(
                        db=self.session,
                        identity=identity,
                        project_id=run.project_id,
                        name=f"testcases-{str(run.id)[:8]}.json",
                        artifact_type="json",
                        data=raw_bytes,
                        run_id=run.id,
                        content_type="application/json",
                    )
                except Exception as art_err:
                    logger.warning("Failed to auto-generate run artifact", error=str(art_err), run_id=str(run.id))
            await self.session.commit()
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
        else:
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
            )
            await self.session.commit()
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
