"""High-level RunExecutor interface for direct, inline execution of Agent runs.

Executes runs through the generic LangGraph runtime, model gateway, tool dispatcher,
and PostgreSQL checkpointer without requiring Hatchet or HTTP background queues.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, cast

import jsonschema
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncSession

from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    ModelCall,
    Run,
    RunStep,
)
from qa_agentos.runtime.events import (
    RunStateManager,
    RunTerminalStateError,
    utc_now,
)
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ResolvedModel
from qa_agentos.runtime.graph.builder import build_generic_react_graph
from qa_agentos.runtime.graph.state import AgentState
from qa_agentos.runtime.tools.dispatcher import ToolDispatcher


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
    ) -> None:
        self.session = session
        self.litellm_client = litellm_client
        self.model_resolver = model_resolver
        self.checkpointer = checkpointer

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
        if run.status in ("succeeded", "failed", "cancelled"):
            raise RunTerminalStateError(f"Run {run.id} is already in terminal state '{run.status}'.")

        # 3. Load AgentVersion and AgentDefinition
        agent_version = await self.session.get(AgentVersion, run.agent_version_id)
        if not agent_version:
            raise ValueError(f"AgentVersion {run.agent_version_id} not found")

        agent_def = await self.session.get(AgentDefinition, agent_version.agent_id)
        agent_slug = agent_def.slug if agent_def else "agent"

        # 4. Handle initial transitions if run is currently queued
        is_resuming = run.status == "running"
        if run.status == "queued":
            await state_mgr.transition_to("preparing")

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
        else:
            # Resuming run: load pre-resolved model from run row
            if run.resolved_models and "planner" in run.resolved_models:
                rm_dict = run.resolved_models["planner"]
                model_id_val = rm_dict.get("model_id") or rm_dict.get("catalog_id")
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
                )
            else:
                reqs = agent_version.model_requirements or {}
                if self.model_resolver:
                    resolved_model = await self.model_resolver.resolve(self.session, reqs, "planner")
                else:
                    raise ValueError(f"Run {run.id} has no resolved model")

        # 5. Wire step persistence hook
        async def _on_step_completed(
            step_type: str,
            node: str,
            step_number: int,
            input_data: Dict[str, Any],
            output_data: Dict[str, Any],
            tokens_input: int,
            tokens_output: int,
            gateway_model_key: str,
        ) -> None:
            # Upsert RunStep
            step = RunStep(
                id=uuid.uuid4(),
                run_id=run.id,
                step_number=step_number,
                step_type=step_type,
                node=node,
                attempt=1,
                input_data=input_data,
                output_data=output_data,
                status="completed",
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                finished_at=utc_now(),
            )
            self.session.add(step)
            await self.session.flush()

            # Persist ModelCall record
            model_call = ModelCall(
                id=uuid.uuid4(),
                run_id=run.id,
                step_id=step.id,
                model_id=resolved_model.model_id if resolved_model else None,
                capability="planner",
                gateway_model_key=gateway_model_key,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                cost_usd=0.0,
                status="succeeded",
            )
            self.session.add(model_call)
            await self.session.flush()

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
            }
        }

        runnable_config = RunnableConfig(configurable=config["configurable"])

        # 7. Execute graph (pass None when resuming from checkpoint)
        graph_input: Optional[AgentState] = None
        if not is_resuming:
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

        final_state: Dict[str, Any] = await compiled_graph.ainvoke(graph_input, config=runnable_config)

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

        # 9. Handle terminal state
        status = final_state.get("status")
        tokens_in = final_state.get("tokens_input", 0)
        tokens_out = final_state.get("tokens_output", 0)
        run.tokens_input = tokens_in
        run.tokens_output = tokens_out

        if status == "succeeded":
            final_out = final_state.get("final_output")
            run.output_payload = final_out
            await state_mgr.transition_to(
                "succeeded",
                payload={"summary": final_out.get("summary") if final_out else ""},
            )
            return RunExecutionResult(
                run_id=run.id,
                status="succeeded",
                output_payload=final_out,
                tokens_input=tokens_in,
                tokens_output=tokens_out,
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
            return RunExecutionResult(
                run_id=run.id,
                status="failed",
                error_code=err_code,
                error_message=err_msg,
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                model_calls_count=final_state.get("model_calls_count", 0),
                repair_count=final_state.get("repair_count", 0),
            )
