# QA AgentOS — Milestone 1 Phase 4: LangGraph Runtime & Test Case Generator Executor

> **Planning Document & GSD Context Baseline**  
> **Source of Truth:** `docs/research/QA_AGENTOS_ARCHITECTURE.md` (Sections A, D, E, F, G, H, I, U, V, 1, 3)  
> **Target:** Milestone 1, Phase 4  
> **Current Workspace:** `C:\Projects\qa-agentos`  
> **Current Branch:** `feat/m1-run-engine`

---

## 1. Executive Summary & Context Baseline

QA AgentOS is building its modular-monolith AI QA engineering platform. This context document defines the exact baseline, constraints, and requirements for **Milestone 1, Phase 4**.

### Completed & Verified Milestones

The platform is **not a greenfield project**. All previous phases are fully implemented, verified with 58 automated tests passing against PostgreSQL, and preserved in Git:

1. **M0 Foundation (Verified):**
   - Monorepo structure: `apps/api` (FastAPI), `apps/web` (Next.js 15), `deploy/` (Docker Compose).
   - Core backing services: PostgreSQL 17 + pgvector, Redis, MinIO, Hatchet, LiteLLM.
   - Base authentication: RS256 JWT, API keys with scopes, seeded admin identity.
   - Complete verification report in `docs/m0/M0_VERIFICATION_REPORT.md`.

2. **M1 Phase 1a — Test Harness & Integration Infrastructure (Verified):**
   - Dedicated PostgreSQL test database harness (`TEST_DATABASE_URL`).
   - Strict CI failure mode (Postgres tests cannot silently skip under CI).
   - Alembic schema-versus-metadata drift test (`test_migration_drift.py`).
   - Canonical `uv.lock` lockfile.

3. **M1 Phase 1b — Database Schema & Data-Driven Registries (Verified):**
   - Alembic migration `0002_m1_run_engine.py` (adds `runs`, `run_steps`, `run_events`, `model_calls`, `models`, `agent_versions`, `tool_definitions`, `approvals`, `artifacts`).
   - YAML loaders & sync for `models.yaml`, `tools.yaml`, and `agents/test-case-generator.yaml`.
   - Immutable active versions and SHA-256 prompt hashing.

4. **M1 Phase 2 — Model Resolution & LiteLLM Gateway Client (Verified):**
   - Dynamic model capability resolver (`resolution.py`) with deterministic priority ordering and strict `ProviderPolicy` (mock provider restricted to `ENVIRONMENT == "test"`).
   - Typed `LiteLLMClient` (`client.py`) with OpenAI-compatible `/v1/chat/completions`, JSON Schema structured outputs, and secret-safe error mapping.
   - Verified live smoke test against Docker LiteLLM proxy with mock model `mock-json`.
   - Import boundary enforcement: zero vendor SDKs (`openai`, `anthropic`, `google.genai`, `boto3`) permitted under `qa_agentos/runtime/`.

5. **M1 Phase 3 — Tool Registry Dispatcher & Builtins (Verified):**
   - Generic `ToolDispatcher` (`dispatcher.py`) enforcing:
     `Resolve tool -> check agent allowlist -> validate input (Draft 2020-12) -> enforce permissions -> enforce timeout/retry policy -> execute approved implementation -> validate output -> persist execution step`.
   - Trusted builtin `util.echo` (`util.py`) matching schema in `tools.yaml`.
   - Run step persistence in `run_steps` with idempotent replay caching and conflict detection (`TOOL_STEP_CONFLICT`).
   - 21 comprehensive PostgreSQL integration tests in `apps/api/tests/test_tool_dispatcher.py`.

---

## 2. Phase 4 Objective: Generic LangGraph Runtime + Test Case Generator Executor

Phase 4 bridges the model gateway (Phase 2), tool dispatcher (Phase 3), and database schema into a stateful, auditable, checkpointer-backed agent execution engine.

### Core Deliverables:

1. **Generic LangGraph Runtime (`apps/api/src/qa_agentos/runtime/graph/`):**
   - State graph definition (`AgentState`) containing: `run_id`, `agent_version`, `input_data`, `model_snapshot`, `messages`, `step_count`, `repair_count`, `output_data`, `error`.
   - Nodes:
     - `prepare`: Initialize state, resolve model requirements via `ModelResolver`.
     - `generate` / `plan`: Construct prompt and execute model call via `LiteLLMClient`.
     - `execute_tools` (conditional): Route tool invocations through `ToolDispatcher`.
     - `validate_output`: Validate model response against `agent_version.output_schema`.
     - `repair`: On schema violation, feed validation errors back into model prompt if `repair_count < max_repairs`.
   - State transitions & conditional routing.

2. **PostgreSQL Checkpointer Integration:**
   - Durable checkpointing using `AsyncPostgresSaver` (or SQLAlchemy session checkpointer) keyed by `thread_id=str(run_id)`.
   - Resume semantics: in case of interruption or crash, resuming with `thread_id=run_id` reloads state without re-executing already completed nodes.

3. **Run State Machine & Event Tracking:**
   - Transitions: `queued -> preparing -> running -> succeeded` (or `failed`).
   - Emits auditable transition events to the `run_events` table (`seq`, `from_status`, `to_status`, `payload`).
   - Records total token usage (`tokens_input`, `tokens_output`) and error metadata on `runs`.

4. **Inline Execution Engine (`apps/api/src/qa_agentos/runtime/executor.py`):**
   - High-level `RunExecutor` interface allowing direct, synchronous/async execution of a run without requiring Hatchet background queues.
   - Accepts a `run_id` or `Run` row, executes the graph, updates database state, and returns the terminal result.

---

## 3. Existing Interfaces & Dependencies to Reuse

Do NOT re-implement or duplicate these existing modules:
- **Model Resolution:** `qa_agentos.runtime.gateway.resolution.ModelResolver`
- **Gateway Client:** `qa_agentos.runtime.gateway.client.LiteLLMClient`
- **Tool Dispatcher:** `qa_agentos.runtime.tools.dispatcher.ToolDispatcher`
- **Agent Definitions:** `qa_agentos.models.domain.AgentVersion`, `AgentDefinition`
- **Run Entities:** `qa_agentos.models.domain.Run`, `RunStep`, `RunEvent`
- **Schema Validation:** `jsonschema.Draft202012Validator`

---

## 4. Phase 4 Acceptance Criteria & Verification Matrix

The implementation is complete only when all 8 criteria pass:

1. **Schema-Valid Generation:** A valid model response produces a `succeeded` run with output validated against `Test Case Generator`'s `output_schema`.
2. **Self-Repair Loop Success:** Two malformed responses followed by a valid response succeed after exactly two repair attempts.
3. **Repair Exhaustion Failure:** Three malformed responses produce terminal run status `failed` with error code `OUTPUT_SCHEMA_INVALID` and exactly three model calls.
4. **Run Event Ordering:** `run_events` rows are persisted in strict chronological sequence with correct `from_status` and `to_status` values.
5. **Crash Resume via Checkpoint:** A crash injected after the `generate` node resumes from PostgreSQL checkpoints using `run_id` without re-running the completed `generate` node.
6. **Generic Tool Loop Execution:** The graph's tool loop executes `util.echo` through `ToolDispatcher` when configured.
7. **Clean Test Harness:** Unit/integration tests use injectable mock/fake model gateways and real PostgreSQL for persistence and checkpointer tests.
8. **Vendor SDK Isolation:** Zero direct imports of `openai`, `anthropic`, `google.genai`, or other provider SDKs in runtime code.

---

## 5. Explicit Phase 4 Exclusions (Do Not Implement)

To preserve strict milestone boundaries, the following are strictly excluded:
- **No Hatchet integration:** No Hatchet workers, tasks, workflows, or sweepers. Keep the executor directly callable inline.
- **No Runs REST API:** No `/v1/runs` HTTP endpoints, idempotency middleware, or replay endpoints.
- **No Langfuse tracing:** No Langfuse SDK integration or tracing callbacks.
- **No Frontend changes:** No Next.js pages, components, or UI code.
- **No Browser / Playwright runner:** No browser runner containers or self-healing pipelines.
- **No Phase 5+ features.**
