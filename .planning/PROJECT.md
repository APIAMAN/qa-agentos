# QA AgentOS

## What This Is

QA AgentOS is a single-modular-monolith platform for autonomous, auditable AI QA engineering agents. The platform executes declarative agent definitions over stateful LangGraph workflows, a LiteLLM model gateway, versioned data-driven registries (models, tools, prompts), and PostgreSQL durable checkpoints.

## Core Value

Deterministic, auditable, and replayable agent execution with strict schema validation, robust human-in-the-loop controls, and isolated vendor boundaries.

## Requirements

### Validated

- [x] M0: Full container runtime stack (Postgres 17, pgvector, Redis, MinIO, Hatchet, LiteLLM) and JWT auth.
- [x] M1 Phase 1a: PostgreSQL test harness and Alembic schema-versus-metadata drift verification.
- [x] M1 Phase 1b: Database schema migrations, YAML registries (models, tools, agents), and idempotent DB sync.
- [x] M1 Phase 2: Dynamic model capability resolution, provider allowlist, LiteLLM client with structured outputs, mock container smoke test, and vendor SDK import boundaries.
- [x] M1 Phase 3: Tool Registry Dispatcher with Draft 2020-12 input/output validation, agent allowlist, permission enforcement, bounded timeout/retries, and `RunStep` persistence.

### Active

- [ ] M1 Phase 4: Generic LangGraph Runtime (`generic_react` state graph) with PostgreSQL checkpointer.
- [ ] M1 Phase 4: Test Case Generator prompt execution through LiteLLM client with structured schema validation.
- [ ] M1 Phase 4: Self-repair loop (up to 2 repair attempts on schema failure) and terminal error handling.
- [ ] M1 Phase 4: Ordered run state transitions (`queued -> preparing -> running -> succeeded / failed`) and `run_events` emission.
- [ ] M1 Phase 4: Crash recovery resuming from PostgreSQL checkpoints without re-executing completed nodes.
- [ ] M1 Phase 4: Tool execution routing through `ToolDispatcher` for `util.echo`.
- [ ] M1 Phase 4: Direct, inline execution engine interface (`RunExecutor`) testable against mock models and PostgreSQL.

### Out of Scope (Phase 4 Exclusions)

- Hatchet dispatch, tasks, workers, or sweepers — deferred to Phase 6.
- Runs REST API (`/v1/runs`) — deferred to Phase 5.
- Langfuse tracing integration — deferred to Phase 7.
- Frontend / Next.js console UI — deferred to Milestone 2.
- Self-healing, Playwright, or browser automation — deferred to Milestone 5.

## Context

- Workspace: `C:\Projects\qa-agentos` on branch `feat/m1-run-engine`.
- Backing Services: Docker Compose running PostgreSQL (port 5433 test / 5432 dev) and LiteLLM (port 4000).
- Test Suite: 58 backend pytest tests currently passing with 100% success against PostgreSQL.
- Technical Spec: `docs/research/QA_AGENTOS_ARCHITECTURE.md` and `docs/research/M1_PHASE4_GSD_CONTEXT.md`.
