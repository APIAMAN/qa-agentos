# Phase 5 Plan: M1 Phase 4 — Generic LangGraph Runtime & Test Case Generator Executor

> **Phase**: 5 of 8 (M1 Phase 4)  
> **Target**: Generic LangGraph Runtime + Test Case Generator + Inline RunExecutor (without Hatchet)  
> **Source of Truth**: `docs/research/QA_AGENTOS_ARCHITECTURE.md` & `docs/research/M1_PHASE4_GSD_CONTEXT.md`  
> **Status**: [x] Completed & Verified (73 passing backend tests)

---

## 1. Objectives & Boundaries

### Primary Objective
Implement a stateful, auditable, and checkpointer-backed agent runtime that executes the **Test Case Generator** agent using declarative configuration, LiteLLM gateway, ToolDispatcher, and PostgreSQL persistence.

### Explicit Phase 4 Exclusions (Preserved)
- **No Hatchet integration**: No Hatchet workflows, workers, sweepers, or queues. Execution runs directly inline via `RunExecutor`.
- **No Runs REST API**: No `/v1/runs` endpoints or HTTP controllers.
- **No Langfuse tracing**: No Langfuse callbacks or observability exports.
- **No Frontend changes**: No UI modifications.
- **No Browser / Playwright**: No browser automation.

---

## 2. Granular Task Breakdown

### Task 4.1: Agent State Schema & Graph Topology [x] COMPLETED
- **Files**: `apps/api/src/qa_agentos/runtime/graph/state.py`, `apps/api/src/qa_agentos/runtime/graph/builder.py`
- **Delivered**:
  - `AgentState` typed dictionary tracking run identity, messages, parsed output, tool calls, token usage, repair counts, and status.
  - LangGraph `StateGraph(AgentState)` compiled with prepare, generate, execute_tools, validate_output, repair, and finalize nodes.
- **Verification**: `test_runtime_graph.py` topology tests passing.

### Task 4.2: Model Resolution & Prompt Generation Node [x] COMPLETED
- **Files**: `apps/api/src/qa_agentos/runtime/graph/nodes.py`
- **Delivered**:
  - Model resolution through existing `ModelResolver` and `ProviderPolicy`.
  - LiteLLM client prompt execution using `ChatMessage` and `StructuredOutputSpec`.
  - Zero vendor SDK imports verified via `test_import_boundaries.py`.
- **Verification**: `test_run_executor_success_valid_schema` passing.

### Task 4.3: JSON Schema Validation & Self-Repair Node [x] COMPLETED
- **Files**: `apps/api/src/qa_agentos/runtime/graph/nodes.py`
- **Delivered**:
  - `Draft202012Validator` output validation against `AgentVersion.output_schema`.
  - Self-repair feedback loop allowing up to 2 repair attempts.
  - Terminal failure transition to `failed / OUTPUT_SCHEMA_INVALID` when repairs are exhausted.
- **Verification**: `test_run_executor_self_repair_success` and `test_run_executor_self_repair_exhausted` passing.

### Task 4.4: Tool Loop Integration via ToolDispatcher [x] COMPLETED
- **Files**: `apps/api/src/qa_agentos/runtime/graph/nodes.py`
- **Delivered**:
  - Tool execution routing through `ToolDispatcher`.
  - Input/output validation, permissions checks, and `RunStep` persistence.
  - Verification with `util.echo` and empty allowlist rejection on Test Case Generator.
- **Verification**: `test_runtime_tool_loop_with_echo` and `test_test_case_generator_cannot_invoke_tools` passing.

### Task 4.5: PostgreSQL Durable Checkpointing & Resume Mechanics [x] COMPLETED
- **Files**: `apps/api/src/qa_agentos/runtime/graph/checkpointer.py`
- **Delivered**:
  - `postgres_checkpointer` async context manager backed by `AsyncPostgresSaver` with connection pooling.
  - Checkpoint tables isolated in the `langgraph` schema, preserving zero Alembic migration drift.
  - Crash resume from checkpoint without re-running completed `generate` node.
- **Verification**: `test_postgres_checkpoint_crash_recovery` and `test_alembic_schema_matches_metadata_postgres` passing.

### Task 4.6: Run State Machine & Event Audit Logging [x] COMPLETED
- **Files**: `apps/api/src/qa_agentos/runtime/events.py`
- **Delivered**:
  - `RunStateManager` enforcing guarded transitions: `queued -> preparing -> running -> succeeded / failed`.
  - Chronological persistence to `run_events` with increasing `seq` (1, 2, 3...).
  - Terminal state protection raising `RunTerminalStateError`.
- **Verification**: `test_run_events_and_steps_auditable_persistence` and `test_terminal_run_rejects_further_execution` passing.

### Task 4.7: Direct Inline Execution Engine (`RunExecutor`) [x] COMPLETED
- **Files**: `apps/api/src/qa_agentos/runtime/executor.py`
- **Delivered**:
  - `RunExecutor.execute_run()` executing runs inline without Hatchet or API endpoints.
  - Step persistence in `run_steps` and model calls in `model_calls`.
  - Checkpoint resumption support by passing `graph_input=None` when `status == "running"`.
- **Verification**: All 9 integration tests passing in `test_run_executor.py`.

### Task 4.8: Comprehensive Acceptance & Integration Test Suite [x] COMPLETED
- **Files**: `apps/api/tests/test_run_executor.py`, `apps/api/tests/test_runtime_graph.py`
- **Delivered**:
  - 15 new unit and integration tests covering all 10 acceptance criteria.
  - Entire backend test suite passes: 73 passed, 0 failed, 0 skipped.
- **Verification**: 100% green pytest suite.

---

## 3. Acceptance Criteria & Test Verification Matrix

| # | Acceptance Criterion | Test Identifier | Result |
|---|----------------------|-----------------|--------|
| **AC-1** | Valid model response produces `succeeded` run matching `output_schema` | `test_run_executor_success_valid_schema` | **PASSED** |
| **AC-2** | Two malformed responses followed by valid response succeed after 2 repairs | `test_run_executor_self_repair_success` | **PASSED** |
| **AC-3** | Three malformed responses produce `failed / OUTPUT_SCHEMA_INVALID` | `test_run_executor_self_repair_exhausted` | **PASSED** |
| **AC-4** | `run_events` persisted in strict transition order (`queued -> preparing -> running -> succeeded`) | `test_run_events_and_steps_auditable_persistence` | **PASSED** |
| **AC-5** | Crash after `generate` resumes from checkpoint without re-running node | `test_postgres_checkpoint_crash_recovery` | **PASSED** |
| **AC-6** | Generic tool loop invokes `util.echo` through `ToolDispatcher` | `test_runtime_tool_loop_with_echo` | **PASSED** |
| **AC-7** | Test Case Generator cannot invoke tools (empty tool allowlist) | `test_test_case_generator_cannot_invoke_tools` | **PASSED** |
| **AC-8** | Inline executor works against persisted run records without Hatchet or REST API | `test_run_executor_success_valid_schema` | **PASSED** |
| **AC-9** | Model calls, execution steps, and terminal failures remain auditable | `test_run_events_and_steps_auditable_persistence` | **PASSED** |
| **AC-10**| Provider SDK import-boundary checks continue to pass | `test_import_boundaries.py` | **PASSED** |
