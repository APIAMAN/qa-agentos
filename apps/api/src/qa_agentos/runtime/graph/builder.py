"""Topology builder for the generic LangGraph agent runtime.

Constructs and compiles the `generic_react` StateGraph with prepare, generate,
execute_tools, validate_output, repair, and finalize nodes.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from qa_agentos.runtime.graph.nodes import (
    execute_tools_node,
    finalize_node,
    generate_node,
    prepare_node,
    repair_node,
    route_after_generate,
    route_after_validate,
    validate_output_node,
)
from qa_agentos.runtime.graph.state import AgentState


def build_generic_react_graph(
    checkpointer: Optional[BaseCheckpointSaver[Any]] = None,
    interrupt_after: Optional[Sequence[str]] = None,
) -> CompiledStateGraph[Any, Any]:
    """Build and compile the generic React StateGraph.

    Logical flow:
    prepare -> generate -> [execute_tools (if tools requested) -> generate | validate_output]
    validate_output -> [finalize -> END (if valid or exhausted) | repair -> generate (if repair_count < max_repairs)]
    """
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("prepare", prepare_node)
    graph.add_node("generate", generate_node)
    graph.add_node("execute_tools", execute_tools_node)
    graph.add_node("validate_output", validate_output_node)
    graph.add_node("repair", repair_node)
    graph.add_node("finalize", finalize_node)

    # Edges
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "generate")

    graph.add_conditional_edges(
        "generate",
        route_after_generate,
        {
            "execute_tools": "execute_tools",
            "validate_output": "validate_output",
        },
    )

    graph.add_edge("execute_tools", "generate")

    graph.add_conditional_edges(
        "validate_output",
        route_after_validate,
        {
            "finalize": "finalize",
            "repair": "repair",
        },
    )

    graph.add_edge("repair", "generate")
    graph.add_edge("finalize", END)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_after=list(interrupt_after) if interrupt_after else None,
    )
