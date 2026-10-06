# ADR-003: Registries as Versioned Data

## Status
Accepted

## Context
Adding new QA agents (e.g., test case generator, bug analyzer, visual regression) or tools should not require rewriting the underlying platform control plane or redeploying custom microservices.

## Decision
Define Agents, Tools, and Models as **database-backed, versioned records** (`agent_definitions`, `agent_versions`, `tool_definitions`, `models`). Agents are declarative data structures containing system prompts, JSON Schemas for I/O, allowed tool lists, and capability model mappings.

## Consequences
- **Pros:** Dynamic registration of new agents via YAML/Database; immutable version history for execution replay.
- **Cons:** Input/output validation must be strictly enforced at API/runtime boundaries using JSON Schema (Pydantic).
