"""Execution nodes for the Generic ReAct LangGraph agent runtime.

All nodes are stateless async functions receiving and returning partial state updates.
All LLM invocations route strictly through LiteLLMClient.
Observability spans and generations are created for each step with safe exception isolation.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

import jsonschema
from langchain_core.runnables import RunnableConfig

from qa_agentos.runtime.gateway.client import (
    ChatMessage,
    LiteLLMClient,
    StructuredOutputSpec,
)
from qa_agentos.runtime.gateway.resolution import ResolvedModel, calculate_model_cost
from qa_agentos.runtime.graph.state import AgentState
from qa_agentos.runtime.tools.dispatcher import (
    ToolDispatcher,
    ToolExecutionContext,
    ToolInvocationRequest,
)


def _format_user_prompt(input_payload: Dict[str, Any]) -> str:
    """Format input payload into the initial user prompt."""
    return f"Input Requirements & Specifications:\n```json\n{json.dumps(input_payload, indent=2)}\n```"


def _safe_start_span(tracer: Any, trace_id: Optional[str], name: str, **kwargs: Any) -> Any:
    if not tracer or not trace_id or not getattr(tracer, "is_enabled", False):
        return None
    try:
        return tracer.start_span(trace_id=trace_id, name=name, **kwargs)
    except Exception:
        return None


def _safe_start_generation(tracer: Any, trace_id: Optional[str], name: str, **kwargs: Any) -> Any:
    if not tracer or not trace_id or not getattr(tracer, "is_enabled", False):
        return None
    try:
        return tracer.start_generation(trace_id=trace_id, name=name, **kwargs)
    except Exception:
        return None


def _safe_end(ctx: Any, **kwargs: Any) -> None:
    if not ctx:
        return
    try:
        ctx.end(**kwargs)
    except Exception:
        pass


async def prepare_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Initialize agent messages and execution state."""
    from qa_agentos.core.config import settings

    configurable = config.get("configurable", {})
    tracer = configurable.get("tracer")
    trace_id = configurable.get("trace_id")
    span = _safe_start_span(
        tracer,
        trace_id,
        "node:prepare",
        metadata={
            "run_id": state.get("run_id"),
            "agent": state.get("agent_slug"),
            "agent_version": state.get("agent_version"),
        },
        input_data={"messages_count": len(state.get("messages", []))},
    )

    try:
        assigned_attempt = configurable.get("assigned_attempt", 1)
        debug_delay = state.get("parameters", {}).get("debug_delay_seconds", 0)
        if debug_delay and assigned_attempt == 1 and settings.ENVIRONMENT != "production":
            await asyncio.sleep(float(debug_delay))

        resolved_model = state.get("resolved_model") or configurable.get("resolved_model")

        updates: Dict[str, Any] = {
            "step_count": state.get("step_count", 0),
            "repair_count": state.get("repair_count", 0),
            "max_repairs": state.get("parameters", {}).get("max_repairs", 2),
            "max_steps": state.get("parameters", {}).get("max_steps", 10),
            "tokens_input": state.get("tokens_input", 0),
            "tokens_output": state.get("tokens_output", 0),
            "model_calls_count": state.get("model_calls_count", 0),
            "status": "running",
        }

        if resolved_model:
            updates["resolved_model"] = resolved_model

        # Initialize messages if not already present
        if not state.get("messages"):
            messages: List[Dict[str, str]] = []
            prompt_template = state.get("prompt_template", "")
            if prompt_template:
                messages.append({"role": "system", "content": prompt_template})

            input_payload = state.get("input_payload", {})
            messages.append({"role": "user", "content": _format_user_prompt(input_payload)})
            updates["messages"] = messages

        _safe_end(span, output_data={"step_count": updates["step_count"]})
        return updates
    except (Exception, asyncio.CancelledError) as exc:
        err_msg = "Task was cancelled" if isinstance(exc, asyncio.CancelledError) else str(exc)
        _safe_end(span, error=err_msg)
        raise


def _tool_request_turn(state: AgentState) -> bool:
    """True while the agent may still request a tool: it has tools, no tool result has come back
    yet, and the step budget fits this model call plus one tool call (see ``route_after_generate``).

    On such a turn the strict final-output schema must not be sent: providers that enforce it
    (e.g. Gemini) make the ``{"tool_calls": [...]}`` request impossible to express.
    """
    return (
        bool(state.get("allowed_tools"))
        and not state.get("tool_results_count")
        and state.get("step_count", 0) + 2 <= state.get("max_steps", 10)
    )


async def generate_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Execute model generation via LiteLLM client."""
    configurable = config.get("configurable", {})
    client: LiteLLMClient = configurable["litellm_client"]
    resolved_model_dict = state.get("resolved_model") or configurable.get("resolved_model")

    if isinstance(resolved_model_dict, ResolvedModel):
        resolved_model = resolved_model_dict
    elif isinstance(resolved_model_dict, dict):
        resolved_model = ResolvedModel.from_snapshot(resolved_model_dict, "planner")
    else:
        raise ValueError("No valid resolved_model provided in state or config")

    # Build ChatMessage sequence
    chat_messages = [ChatMessage(role=m["role"], content=m["content"]) for m in state.get("messages", [])]

    # Strict final-output schema, except on a tool-request turn: there JSON object mode keeps the
    # reply parseable as either a tool request or a report (validate_output still checks the schema).
    output_schema = state.get("output_schema")
    tool_turn = bool(output_schema) and _tool_request_turn(state)
    structured_output: Optional[StructuredOutputSpec] = None
    if output_schema and not tool_turn:
        schema_name = f"{state.get('agent_slug', 'agent')}_output".replace("-", "_")
        structured_output = StructuredOutputSpec(
            name=schema_name,
            schema=output_schema,
            strict=True,
        )

    tracer = configurable.get("tracer")
    trace_id = configurable.get("trace_id")
    span = _safe_start_span(
        tracer,
        trace_id,
        "node:generate",
        metadata={
            "run_id": state.get("run_id"),
            "agent": state.get("agent_slug"),
            "agent_version": state.get("agent_version"),
            "repair_count": state.get("repair_count", 0),
        },
        input_data={"messages_count": len(chat_messages)},
    )
    gen = _safe_start_generation(
        tracer,
        trace_id,
        f"model:{resolved_model.gateway_model_key}",
        model=resolved_model.name,
        parent_id=getattr(span, "id", None),
        metadata={
            "run_id": state.get("run_id"),
            "agent": state.get("agent_slug"),
            "agent_version": state.get("agent_version"),
            "attempt": state.get("repair_count", 0) + 1,
        },
        input_data={"messages_count": len(chat_messages)},
        model_parameters={"temperature": state.get("parameters", {}).get("temperature", 0.1)},
    )

    try:
        # Call model gateway
        result = await client.chat_completion(
            model=resolved_model,
            messages=chat_messages,
            temperature=state.get("parameters", {}).get("temperature", 0.1),
            structured_output=structured_output,
            json_mode=tool_turn,
        )

        # Track tokens and usage accurately from provider response; do not fabricate missing tokens
        usage_obj = result.usage
        prompt_tokens: Optional[int] = usage_obj.prompt_tokens if usage_obj is not None else None
        completion_tokens: Optional[int] = usage_obj.completion_tokens if usage_obj is not None else None

        calculated_cost = calculate_model_cost(
            prompt_tokens,
            completion_tokens,
            resolved_model.cost_input_per_1m,
            resolved_model.cost_output_per_1m,
        )
        new_tokens_input = state.get("tokens_input", 0) + (prompt_tokens or 0)
        new_tokens_output = state.get("tokens_output", 0) + (completion_tokens or 0)
        new_model_calls_count = state.get("model_calls_count", 0) + 1
        new_step_count = state.get("step_count", 0) + 1

        usage_details = (
            {
                "input": prompt_tokens or 0,
                "output": completion_tokens or 0,
                "total": (prompt_tokens or 0) + (completion_tokens or 0),
            }
            if (prompt_tokens is not None or completion_tokens is not None)
            else None
        )

        _safe_end(
            gen,
            output_data=result.content,
            usage=usage_details,
            cost={"total": calculated_cost},
        )
        _safe_end(span, output_data={"raw_response_len": len(result.content)})

        # Append assistant response to messages
        updated_messages = list(state.get("messages", []))
        updated_messages.append({"role": "assistant", "content": result.content})

        # Check for tool call requests or parse output
        pending_tool_calls: List[Dict[str, Any]] = []
        parsed_output: Optional[Dict[str, Any]] = None

        try:
            raw_parsed = json.loads(result.content)
            if isinstance(raw_parsed, dict):
                # Check if this is a tool invocation request
                if "tool_calls" in raw_parsed and isinstance(raw_parsed["tool_calls"], list):
                    pending_tool_calls = raw_parsed["tool_calls"]
                elif "tool" in raw_parsed and "args" in raw_parsed:
                    pending_tool_calls = [
                        {
                            "tool": raw_parsed["tool"],
                            "version": raw_parsed.get("version", "1.0.0"),
                            "input_data": raw_parsed["args"],
                        }
                    ]
                else:
                    parsed_output = raw_parsed
            else:
                parsed_output = None
        except Exception:
            parsed_output = None

        # Step callback hook for persistence if configured
        step_hook = configurable.get("on_step_completed")
        if step_hook and callable(step_hook):
            await step_hook(
                step_type="planner",
                node="generate",
                step_number=new_step_count,
                input_data={"messages_count": len(chat_messages)},
                output_data={"raw_response": result.content},
                tokens_input=prompt_tokens,
                tokens_output=completion_tokens,
                gateway_model_key=resolved_model.gateway_model_key,
                cost_usd=calculated_cost,
                langfuse_generation_id=getattr(gen, "id", None),
                langfuse_observation_id=getattr(span, "id", None),
            )

        return {
            "raw_response": result.content,
            "parsed_output": parsed_output,
            "pending_tool_calls": pending_tool_calls,
            "messages": updated_messages,
            "tokens_input": new_tokens_input,
            "tokens_output": new_tokens_output,
            "model_calls_count": new_model_calls_count,
            "step_count": new_step_count,
        }
    except (Exception, asyncio.CancelledError) as exc:
        err_msg = "Task was cancelled" if isinstance(exc, asyncio.CancelledError) else str(exc)
        _safe_end(gen, error=err_msg)
        _safe_end(span, error=err_msg)
        raise


def route_after_generate(state: AgentState) -> str:
    """Conditional edge after generation.

    Tool calls are only executed while they fit in the step budget (``max_steps``); a model
    that keeps requesting tools ends the run with ``MAX_STEPS_EXCEEDED`` instead of looping
    until LangGraph's recursion limit aborts it.
    """
    pending = state.get("pending_tool_calls") or []
    if pending:
        max_steps = state.get("max_steps", 10)
        if state.get("step_count", 0) + len(pending) > max_steps:
            return "max_steps_exceeded"
        return "execute_tools"
    return "validate_output"


async def max_steps_exceeded_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Terminal failure when the agent's step budget is exhausted."""
    max_steps = state.get("max_steps", 10)
    return {
        "status": "failed",
        "pending_tool_calls": [],
        "error_code": "MAX_STEPS_EXCEEDED",
        "error_message": f"Agent exceeded its step budget of {max_steps} steps.",
        "error_details": {
            "max_steps": max_steps,
            "step_count": state.get("step_count", 0),
            "requested_tool_calls": len(state.get("pending_tool_calls") or []),
        },
    }


async def execute_tools_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Execute requested tool calls via ToolDispatcher."""
    configurable = config.get("configurable", {})
    tracer = configurable.get("tracer")
    trace_id = configurable.get("trace_id")
    span = _safe_start_span(
        tracer,
        trace_id,
        "node:execute_tools",
        metadata={"run_id": state.get("run_id")},
        input_data={"pending_calls": len(state.get("pending_tool_calls", []))},
    )

    try:
        dispatcher: Optional[ToolDispatcher] = configurable.get("tool_dispatcher")
        agent_version_entity = configurable.get("agent_version_entity")
        if agent_version_entity is None:
            raise RuntimeError("No AgentVersion entity provided in runtime config")

        pending_calls = state.get("pending_tool_calls", [])
        updated_messages = list(state.get("messages", []))
        new_step_count = state.get("step_count", 0)

        for call in pending_calls:
            tool_name = call.get("tool") or call.get("name") or ""
            tool_version = call.get("version", "1.0.0")
            input_data = call.get("input_data") or call.get("args") or {}
            new_step_count += 1

            context = ToolExecutionContext(
                run_id=uuid.UUID(state["run_id"]) if state.get("run_id") else uuid.uuid4(),
                step_number=new_step_count,
                project_id=uuid.UUID(state["project_id"]) if state.get("project_id") else uuid.uuid4(),
                agent_version=agent_version_entity,
                granted_permissions=state.get("permissions", []),
                attempt=int(configurable.get("assigned_attempt", 1)),
            )
            request = ToolInvocationRequest(
                tool_name=tool_name,
                tool_version=tool_version,
                input_data=input_data,
            )

            try:
                if dispatcher is None:
                    raise RuntimeError("No ToolDispatcher provided in runtime config")
                result = await dispatcher.dispatch(request, context)
                updated_messages.append(
                    {
                        "role": "user",
                        "content": f"Tool '{tool_name}' result:\n```json\n{json.dumps(result.output_data, indent=2)}\n```",
                    }
                )
            except Exception as exc:
                updated_messages.append(
                    {
                        "role": "user",
                        "content": f"Tool '{tool_name}' failed with error: {str(exc)}",
                    }
                )

        _safe_end(span, output_data={"step_count": new_step_count})

        return {
            "messages": updated_messages,
            "pending_tool_calls": [],
            "step_count": new_step_count,
            "tool_results_count": state.get("tool_results_count", 0) + len(pending_calls),
        }
    except (Exception, asyncio.CancelledError) as exc:
        err_msg = "Task was cancelled" if isinstance(exc, asyncio.CancelledError) else str(exc)
        _safe_end(span, error=err_msg)
        raise


async def validate_output_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Validate generated output against the agent output schema."""
    configurable = config.get("configurable", {})
    tracer = configurable.get("tracer")
    trace_id = configurable.get("trace_id")
    span = _safe_start_span(
        tracer,
        trace_id,
        "node:validate_output",
        metadata={"run_id": state.get("run_id"), "repair_count": state.get("repair_count", 0)},
    )

    try:
        output_schema = state.get("output_schema")
        parsed_output = state.get("parsed_output")
        repair_count = state.get("repair_count", 0)
        max_repairs = state.get("max_repairs", 2)

        errors: List[str] = []

        if parsed_output is None:
            errors.append("Response is not valid JSON or could not be parsed into an object.")
        elif output_schema:
            validator = jsonschema.Draft202012Validator(output_schema)
            schema_errors = list(validator.iter_errors(parsed_output))
            for err in schema_errors:
                loc = f"at '{'.'.join(str(p) for p in err.path)}': " if err.path else ""
                errors.append(f"{loc}{err.message}")

        if not errors:
            _safe_end(span, output_data={"valid": True})
            return {
                "status": "succeeded",
                "final_output": parsed_output,
                "error_code": None,
                "error_message": None,
                "error_details": None,
            }

        # Validation errors detected
        if repair_count < max_repairs:
            # Initiate repair
            new_repair_count = repair_count + 1
            error_bullets = "\n".join(f"- {e}" for e in errors)
            repair_prompt = (
                f"Your previous response did not adhere to the required output schema ({len(errors)} error(s)):\n"
                f"{error_bullets}\n\n"
                "Please fix all schema violations and produce a strictly schema-compliant JSON response."
            )
            updated_messages = list(state.get("messages", []))
            updated_messages.append({"role": "user", "content": repair_prompt})

            _safe_end(span, output_data={"valid": False, "action": "repair", "errors_count": len(errors)})
            return {
                "repair_count": new_repair_count,
                "messages": updated_messages,
                "status": "running",
                "error_code": "OUTPUT_SCHEMA_INVALID",
                "error_message": f"Schema validation failed on attempt {repair_count + 1}",
                "error_details": {"validation_errors": errors},
            }

        # Repair attempts exhausted
        _safe_end(
            span,
            output_data={"valid": False, "action": "failed", "errors_count": len(errors)},
            error="OUTPUT_SCHEMA_INVALID",
        )
        return {
            "status": "failed",
            "error_code": "OUTPUT_SCHEMA_INVALID",
            "error_message": f"Output schema validation failed after {repair_count} repair attempts.",
            "error_details": {
                "validation_errors": errors,
                "repair_attempts": repair_count,
                "raw_response": state.get("raw_response"),
            },
        }
    except (Exception, asyncio.CancelledError) as exc:
        err_msg = "Task was cancelled" if isinstance(exc, asyncio.CancelledError) else str(exc)
        _safe_end(span, error=err_msg)
        raise


def route_after_validate(state: AgentState) -> str:
    """Route to finalize if terminal, or repair if another attempt is permitted."""
    status = state.get("status")
    if status in ("succeeded", "failed"):
        return "finalize"
    return "repair"


async def repair_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Repair transition node routing back to generation."""
    configurable = config.get("configurable", {})
    tracer = configurable.get("tracer")
    trace_id = configurable.get("trace_id")
    span = _safe_start_span(tracer, trace_id, "node:repair", metadata={"run_id": state.get("run_id")})
    try:
        _safe_end(span, output_data={"action": "re-entering generate"})
        return {"status": "running"}
    except (Exception, asyncio.CancelledError) as exc:
        err_msg = "Task was cancelled" if isinstance(exc, asyncio.CancelledError) else str(exc)
        _safe_end(span, error=err_msg)
        raise


async def finalize_node(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """Terminal state finalizer."""
    configurable = config.get("configurable", {})
    tracer = configurable.get("tracer")
    trace_id = configurable.get("trace_id")
    span = _safe_start_span(tracer, trace_id, "node:finalize", metadata={"run_id": state.get("run_id")})
    try:
        status = state.get("status")
        _safe_end(span, output_data={"final_status": status})
        return {}
    except (Exception, asyncio.CancelledError) as exc:
        err_msg = "Task was cancelled" if isinstance(exc, asyncio.CancelledError) else str(exc)
        _safe_end(span, error=err_msg)
        raise
