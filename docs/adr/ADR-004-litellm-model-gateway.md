# ADR-004: Model Gateway via LiteLLM Proxy

## Status
Accepted

## Context
Agents in QA AgentOS require dynamic model routing (e.g. planner slot vs classifier slot), spend tracking, virtual keys, rate-limit fallback chains, and single-endpoint abstraction over multiple LLM providers.

## Decision
Route all LLM interactions through a self-hosted **LiteLLM Proxy** instance. Application code interacts exclusively with LiteLLM using standard OpenAI-compatible endpoints.

## Consequences
- **Pros:** Model providers can be swapped via YAML configuration without modifying code; unified token cost and latency logging.
- **Cons:** Additional proxy container in deployment topology.
