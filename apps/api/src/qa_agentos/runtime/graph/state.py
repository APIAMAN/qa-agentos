"""Agent state definition for the generic LangGraph runtime.

Defines the typed dictionary schema representing the persistent and working state
of an agent execution lifecycle.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict, total=False):
    """Unified state dictionary passed between nodes of the generic agent graph."""

    # Run and Agent Identity
    run_id: str
    project_id: str
    agent_id: str
    agent_slug: str
    agent_version_id: str
    agent_version: str

    # Agent Configuration & Schemas
    prompt_template: str
    input_payload: Dict[str, Any]
    output_schema: Dict[str, Any]
    parameters: Dict[str, Any]
    permissions: List[str]
    allowed_tools: List[str]
    model_requirements: Dict[str, Any]

    # Execution State
    messages: List[Dict[str, str]]
    resolved_model: Optional[Dict[str, Any]]
    raw_response: Optional[str]
    parsed_output: Optional[Dict[str, Any]]
    pending_tool_calls: List[Dict[str, Any]]
    tool_results_count: int  # tool results appended so far (absent in older checkpoints = 0)
    step_count: int
    repair_count: int
    max_repairs: int
    max_steps: int

    # Model Usage Metrics
    tokens_input: int
    tokens_output: int
    model_calls_count: int

    # Lifecycle and Outcome
    status: str  # "preparing", "running", "succeeded", "failed"
    error_code: Optional[str]
    error_message: Optional[str]
    error_details: Optional[Dict[str, Any]]
    final_output: Optional[Dict[str, Any]]
