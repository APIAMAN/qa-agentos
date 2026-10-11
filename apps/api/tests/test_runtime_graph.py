"""Unit tests for the generic LangGraph runtime graph topology and nodes."""

from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableConfig
from qa_agentos.runtime.graph.builder import build_generic_react_graph
from qa_agentos.runtime.graph.nodes import (
    prepare_node,
    route_after_generate,
    route_after_validate,
    validate_output_node,
)
from qa_agentos.runtime.graph.state import AgentState


def test_graph_topology_compilation():
    """Verify that the generic react graph compiles with all required nodes and edges."""
    compiled = build_generic_react_graph()
    nodes = list(compiled.nodes.keys())
    assert "prepare" in nodes
    assert "generate" in nodes
    assert "execute_tools" in nodes
    assert "validate_output" in nodes
    assert "repair" in nodes
    assert "finalize" in nodes


def test_routing_after_generate_with_tools():
    """Verify routing routes to execute_tools when pending_tool_calls is populated."""
    state_with_tools: AgentState = {
        "pending_tool_calls": [{"tool": "util.echo", "args": {"message": "hi"}}],
    }
    assert route_after_generate(state_with_tools) == "execute_tools"

    state_no_tools: AgentState = {
        "pending_tool_calls": [],
    }
    assert route_after_generate(state_no_tools) == "validate_output"


def test_routing_after_validate_terminal_vs_repair():
    """Verify routing routes to finalize on terminal states and repair on retryable failures."""
    assert route_after_validate({"status": "succeeded"}) == "finalize"
    assert route_after_validate({"status": "failed"}) == "finalize"
    assert route_after_validate({"status": "running"}) == "repair"


@pytest.mark.asyncio
async def test_prepare_node_initializes_state_and_messages():
    """Verify prepare_node sets default counters and initial system + user messages."""
    initial_state: AgentState = {
        "agent_slug": "test-agent",
        "prompt_template": "System prompt instructions.",
        "input_payload": {"requirements": "Requirement 1"},
        "parameters": {"max_repairs": 3, "max_steps": 8},
    }
    config = RunnableConfig(configurable={})
    updates = await prepare_node(initial_state, config)

    assert updates["status"] == "running"
    assert updates["step_count"] == 0
    assert updates["repair_count"] == 0
    assert updates["max_repairs"] == 3
    assert updates["max_steps"] == 8
    assert len(updates["messages"]) == 2
    assert updates["messages"][0]["role"] == "system"
    assert updates["messages"][0]["content"] == "System prompt instructions."
    assert updates["messages"][1]["role"] == "user"


@pytest.mark.asyncio
async def test_validate_output_node_success():
    """Verify validate_output_node transitions to succeeded when output adheres to schema."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["result"],
        "properties": {"result": {"type": "string"}},
    }
    state: AgentState = {
        "output_schema": schema,
        "parsed_output": {"result": "success"},
        "repair_count": 0,
        "max_repairs": 2,
    }
    config = RunnableConfig(configurable={})
    updates = await validate_output_node(state, config)

    assert updates["status"] == "succeeded"
    assert updates["final_output"] == {"result": "success"}
    assert updates["error_code"] is None


@pytest.mark.asyncio
async def test_validate_output_node_exhaustion():
    """Verify validate_output_node transitions to failed when repairs reach max_repairs."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["result"],
    }
    state: AgentState = {
        "output_schema": schema,
        "parsed_output": {"wrong": 123},
        "repair_count": 2,  # Already at limit
        "max_repairs": 2,
        "raw_response": '{"wrong": 123}',
    }
    config = RunnableConfig(configurable={})
    updates = await validate_output_node(state, config)

    assert updates["status"] == "failed"
    assert updates["error_code"] == "OUTPUT_SCHEMA_INVALID"
    assert "Output schema validation failed after 2 repair attempts" in updates["error_message"]
