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

## Implementation note (M3)

- The Bug Analyzer is a pure registry addition (`agents/bug-analyzer.yaml`, `prompts/bug-analyzer/1.0.0.md`,
  `log.parse` in `tools.yaml`); no runtime code is agent-specific.
- Post-run processing is data too: an agent version lists `parameters.output_sinks` (the Bug
  Analyzer declares `[bug_store]`); the default run output sink (`modules/runs/output.py`) runs the
  declared steps.
- Gap: `tools.yaml` cannot set `ToolDefinition.required_permission` (sync leaves the DB default
  `member`), so tool-using agents must grant `member`. Make it a registry field when the next tool
  needs a different requirement.
