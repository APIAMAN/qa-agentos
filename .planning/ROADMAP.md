# Roadmap: QA AgentOS

## Overview

QA AgentOS Milestone 1 implements the core Run Engine and the first autonomous agent (Test Case Generator). The platform progresses through sequential, verified phases from data model and model gateway to stateful runtime, API endpoints, durable queuing, and end-to-end tracing.

## Phases

- [x] **Phase 1: M1 Phase 1a — Test Harness & Integration Infrastructure** - Established dedicated PostgreSQL test container harness, strict CI verification, and Alembic schema-versus-metadata drift detection.
- [x] **Phase 2: M1 Phase 1b — Database Schema & Data-Driven Registries** - Added Alembic migration 0002, YAML registry loaders, and idempotent sync for models, tools, and agents.
- [x] **Phase 3: M1 Phase 2 — Model Capability Resolution & LiteLLM Gateway Client** - Implemented priority-based model resolution, secure provider allowlist, LiteLLM HTTP client, mock smoke test, and import boundaries.
- [x] **Phase 4: M1 Phase 3 — Tool Registry Dispatcher & Builtins** - Implemented generic tool dispatcher, Draft 2020-12 input/output validation, agent allowlist, permissions, bounded retries, and RunStep persistence.
- [x] **Phase 5: M1 Phase 4 — Generic LangGraph Runtime & Test Case Generator Executor** - Build stateful LangGraph runtime, Postgres checkpointer, repair loop, run event tracking, and direct inline RunExecutor.
- [ ] **Phase 6: M1 Phase 5 — Runs REST API & Idempotency** - Create FastAPI runs endpoints, idempotency key middleware, execution dispatch triggers, and replay API.
- [ ] **Phase 7: M1 Phase 6 — Hatchet Task Engine Dispatcher & Durable Workers** - Wire Hatchet DAG workflows, worker task loops, background dispatch, and crash-recovery sweeper.
- [ ] **Phase 8: M1 Phase 7 — Langfuse Tracing Integration & Exit Demo** - Instrument full LLM observability, cost tracking, token metrics, and end-to-end exit demo verification.

## Phase Details

### Phase 1: M1 Phase 1a — Test Harness & Integration Infrastructure
**Goal**: Establish dedicated PostgreSQL test container harness, strict CI verification, and Alembic schema-versus-metadata drift detection.
**Depends on**: M0 Foundation
**Requirements**: Postgres integration tests, zero schema drift.

### Phase 2: M1 Phase 1b — Database Schema & Data-Driven Registries
**Goal**: Add Alembic migration 0002, YAML registry loaders, and idempotent sync for models, tools, and agents.
**Depends on**: Phase 1
**Requirements**: Data-driven registries, active version immutability.

### Phase 3: M1 Phase 2 — Model Capability Resolution & LiteLLM Gateway Client
**Goal**: Implement priority-based model resolution, secure provider allowlist, LiteLLM HTTP client, mock smoke test, and import boundaries.
**Depends on**: Phase 2
**Requirements**: Dynamic model resolution, LiteLLM client, vendor SDK isolation.

### Phase 4: M1 Phase 3 — Tool Registry Dispatcher & Builtins
**Goal**: Implement generic tool dispatcher, Draft 2020-12 input/output validation, agent allowlist, permissions, bounded retries, and RunStep persistence.
**Depends on**: Phase 3
**Requirements**: Tool dispatch pipeline, util.echo builtin, RunStep audit log.

### Phase 5: M1 Phase 4 — Generic LangGraph Runtime & Test Case Generator Executor
**Goal**: Build the generic LangGraph agent runtime with PostgreSQL checkpointing, Test Case Generator prompt execution via LiteLLM client, structured output self-repair loop, run state event transitions, and an inline executor testable against PostgreSQL and fake models without Hatchet.
**Depends on**: Phase 1, Phase 2, Phase 3, Phase 4
**Requirements**:
- State graph definition with `prepare`, `generate`, `execute_tools`, `validate_output`, and `repair` nodes.
- PostgreSQL checkpointer integration using `thread_id=str(run_id)`.
- Self-repair loop (up to 2 repair attempts on schema failure) producing schema-valid output or terminal `OUTPUT_SCHEMA_INVALID` error.
- Chronological `run_events` emission recording state transitions (`queued -> preparing -> running -> succeeded / failed`).
- Crash resume from checkpoints without re-executing completed nodes.
- Tool loop integration with `ToolDispatcher` for `util.echo`.
- Direct inline `RunExecutor` interface without requiring Hatchet worker background queues.
- 100% vendor SDK isolation (zero direct provider imports).

### Phase 6: M1 Phase 5 — Runs REST API & Idempotency
**Goal**: Create FastAPI runs endpoints, idempotency key middleware, execution dispatch triggers, and replay API.
**Depends on**: Phase 5
**Requirements**: POST /v1/runs, GET /v1/runs/{id}, POST /v1/runs/{id}/replay.

### Phase 7: M1 Phase 6 — Hatchet Task Engine Dispatcher & Durable Workers
**Goal**: Wire Hatchet DAG workflows, worker task loops, background dispatch, and crash-recovery sweeper.
**Depends on**: Phase 6
**Requirements**: Durable task queue, fair queuing, worker crash recovery.

### Phase 8: M1 Phase 7 — Langfuse Tracing Integration & Exit Demo
**Goal**: Instrument full LLM observability, cost tracking, token metrics, and end-to-end exit demo verification.
**Depends on**: Phase 7
**Requirements**: Langfuse traces, token spend reconciliation, end-to-end exit demo.
