# Graph Report - qa-agentos  (2026-11-25)

## Corpus Check
- 182 files · ~127,539 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 27 file(s) not represented in the graph (top: .log 14, (none) 6, .lock 2)

## Summary
- 3060 nodes · 7653 edges · 202 communities (151 shown, 51 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 1120 edges (avg confidence: 0.92)
- Token cost: 182,578 input · 0 output

## Graph Freshness
- Built from commit: `e3999caa`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- LangGraph Runtime Nodes
- uuid
- Architecture Research Doc
- Tool Dispatch Tests
- AgentVersion
- Durable Agent Run Execution
- tools
- DB Session & Security Core
- gateway/errors.py
- Gateway Client Tests
- Tool Dispatcher Errors
- test_m3_reliability.py
- test_audit_regressions.py
- Runs API Tests
- CI Workflow
- RunStateManager
- Identity
- Model Resolution Policy
- test_reports_api.py
- ADRs & M0 Verification
- .__init__
- test_live_duplicate_and_fencing
- MinIO Artifact Storage
- Web Dev Dependencies
- store_run_output_artifact
- stream_run_events
- Artifacts API Router
- test_observability_accounting.py
- Web Console Pages
- Agents Catalog API
- Registry Loader
- sync_registries
- Test-Case Output Fields
- workers/main.py
- Run
- InlineDispatcher
- test_bug_analyzer.py
- Web TypeScript Config
- Settings
- test_bug_store.py
- ResolvedModel
- Hatchet Server Env
- Test-Case Coverage Schema
- Test-Case Output Schema
- commit_and_publish
- tracing.py
- projects/router.py
- Test-Case Generator Agent
- logparse.py
- reports/service.py
- M3 — Bug Analyzer, reports and duplicate detection
- Dev Compose Overrides
- Web App Layout
- Test-Case Input Schema
- Any
- reports/router.py
- Model Registry YAML
- typing
- Broken Tracer Tests
- test_import_boundaries.py
- conftest.py
- Web Compose Service
- LiteLLM Compose Service
- Test LiteLLM Service
- LiteLLM Config
- Root Package JSON
- test_cancel_running_run_stops_between_nodes
- Web Runtime Dependencies
- Hatchet Migration Service
- MinIO Compose Service
- Postgres Compose Service
- Test Postgres Service
- Builtin Echo Tool
- Run Detail Page
- Compose Service Dependencies
- Worker Compose Env
- Hatchet Setup Service
- LiteLLM Mock Config
- is_permanent_error
- embeddings_benchmark.py
- environment
- Test Compose Root
- Test Hatchet Migration
- Compose Volumes
- setup_test_context
- NoOpTracer
- services
- Caddy Compose Service
- MinIO Bucket Init
- Compose Healthchecks
- Test Hatchet Engine
- Test Healthchecks
- Web NPM Scripts
- postgres
- Test Redis Service
- Test-Case Priority Enum
- Test-Case Type Enum
- Agent Model Requirements
- sync_registries
- approvals/router.py
- worker
- Test-Case Steps
- Test-Case Enum Fields
- bugs/service.py
- ESLint Config
- Graphify Instructions
- Hatchet Setup Script
- Postgres Init Script
- pnpm Workspace
- Root Python Project
- qa-agentos-api
- hatchet-migrate
- test_migration_drift.py
- test_artifacts_api.py
- ToolDispatcher
- test_embedding_config.py
- test_health_endpoint
- test_harness.py
- BugStore
- metrics
- tools.py
- test_bug_report_integration.py
- dataset.yaml
- runner.py
- pytest
- test_report_export.py
- properties
- properties
- seed_agents_catalog
- InlineDispatcher
- report/page.tsx
- GatewayJudge
- properties
- items
- APIError
- evaluate
- properties
- bug-analyzer.yaml
- CallBudget
- embeddings.py
- ResolvedModel
- hashlib
- SpyTracer
- steps
- LiteLLMClient
- artifact_id
- ReportsService
- input_schema
- items
- properties
- LangfuseTracer
- input_schema
- output_schema
- SpanContext
- html.py
- test_end_to_end_execution_succeeded
- subscribe_run_events
- output_schema
- planner
- parameters
- ._check_existing_step
- env.py
- sweeper.py
- confidence
- title
- hash_password
- Bug Report & Log Analyzer System Prompt (v1.0.0)
- timestamp
- severity
- next.config.ts
- error_count
- redactions
- total_lines
- warning_count
- json
- test_gateway_gemini_schema.py
- test_postgres_session_crud_and_rollback
- bug-analyzer-weakened.md

## God Nodes (most connected - your core abstractions)
1. `Run` - 114 edges
2. `LiteLLMClient` - 60 edges
3. `Identity` - 59 edges
4. `RunExecutor` - 51 edges
5. `AgentVersion` - 46 edges
6. `ToolDispatcher` - 45 edges
7. `ProviderPolicy` - 45 edges
8. `ModelResolver` - 45 edges
9. `Settings` - 40 edges
10. `Project` - 36 edges

## Surprising Connections (you probably didn't know these)
- `9. Known limitations` --references--> `test_embedding_call_does_not_hold_the_run_row_lock()`  [INFERRED]
  docs/m3/BUG_ANALYZER.md → apps/api/tests/test_bug_store.py
- `Acceptance evidence (M3)` --references--> `CallBudget`  [INFERRED]
  docs/m3/PLAN.md → evals/qa_evals/budget.py
- `7. Security and isolation` --references--> `test_bug_analyzer_agent_end_to_end()`  [INFERRED]
  docs/m3/BUG_ANALYZER.md → apps/api/tests/test_bug_analyzer.py
- `2. Golden dataset v1` --references--> `test_parser_matches_golden_signatures()`  [INFERRED]
  docs/m3/EVALS.md → apps/api/tests/test_bug_analyzer.py
- `First real-model run (2026-11-25, 2 cases, `gemini-3.5-flash-lite` as agent and judge)` --references--> `generate_node()`  [INFERRED]
  docs/m3/EVALS.md → apps/api/src/qa_agentos/runtime/graph/nodes.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **LLM Model Routing via LiteLLM (NIM primary, Gemini fallback)** — docs_adr_adr_004_litellm_model_gateway_litellm_proxy, docs_adr_adr_004_litellm_model_gateway_capability_slots, docs_adr_adr_005_nvidia_nim_gateway_nvidia_nim, docs_adr_adr_005_nvidia_nim_gateway_gemini_fallback [EXTRACTED 1.00]
- **QA AgentOS Architecture Decision Records** — docs_adr_adr_001_modular_monolith_modular_monolith, docs_adr_adr_002_langgraph_runtime_langgraph_orchestration, docs_adr_adr_003_data_driven_registries_registries_as_data, docs_adr_adr_004_litellm_model_gateway_litellm_proxy, docs_adr_adr_005_nvidia_nim_gateway_nim_fallback [EXTRACTED 1.00]
- **Five launch agents as registry definitions** — docs_research_qa_agentos_architecture_test_case_generator, docs_research_qa_agentos_architecture_bug_analyzer, docs_research_qa_agentos_architecture_api_testing_agent, docs_research_qa_agentos_architecture_self_healing_agent, docs_research_qa_agentos_architecture_visual_a11y_agent, docs_research_qa_agentos_architecture_agent_registry [EXTRACTED 1.00]
- **Agent -> Tool -> Model -> Artifact -> Report chain** — docs_research_qa_agentos_architecture_langgraph_runtime, docs_research_qa_agentos_architecture_tool_registry, docs_research_qa_agentos_architecture_litellm_gateway, docs_research_qa_agentos_architecture_artifact_storage, docs_research_qa_agentos_architecture_report_service [EXTRACTED 1.00]
- **Single Postgres hosts app, Hatchet, LangGraph, Langfuse schemas** — docs_research_qa_agentos_architecture_postgresql, docs_research_qa_agentos_architecture_hatchet, docs_research_qa_agentos_architecture_postgres_checkpointer, docs_research_qa_agentos_architecture_langfuse, docs_research_qa_agentos_architecture_pgvector [EXTRACTED 1.00]

## Communities (202 total, 51 thin omitted)

### Community 0 - "LangGraph Runtime Nodes"
Cohesion: 0.07
Nodes (23): calculate_model_cost(), build_generic_react_graph(), execute_tools_node(), finalize_node(), _format_user_prompt(), generate_node(), max_steps_exceeded_node(), prepare_node() (+15 more)

### Community 1 - "uuid"
Cohesion: 0.12
Nodes (10): test_api_events_invalid_ticket(), test_api_events_stream_unauthorized(), test_api_events_ticket_unauthorized(), test_channel_scoping(), test_publish_run_event_resilience_on_redis_error(), test_publish_run_event_success(), test_run_event_payload_schema_and_sse_formatting(), test_stream_ticket_lifecycle_redis() (+2 more)

### Community 2 - "Architecture Research Doc"
Cohesion: 0.05
Nodes (53): Architecture Decisions (ADR-001..022), Agent Registry, Agent -> Tool -> Model -> Artifact -> Report flow, API Testing Agent, Approval Service (human-in-the-loop), Content-Addressed Artifact Storage, JWT + API Keys Auth, axe-core Accessibility Engine (+45 more)

### Community 3 - "Tool Dispatch Tests"
Cohesion: 0.08
Nodes (23): ToolExecutionContext, ToolInvocationRequest, _seed_test_environment(), test_attempt_counts_and_latency_recorded(), test_invalid_input_fails_before_execution(), test_invalid_output_produces_tool_output_invalid(), test_missing_permissions_produce_permission_denied(), test_non_json_serializable_output_fails_safely() (+15 more)

### Community 4 - "AgentVersion"
Cohesion: 0.20
Nodes (12): Base, AgentDefinition, AgentVersion, Approval, Artifact, ModelCatalog, Project, seed_live_environment() (+4 more)

### Community 5 - "Durable Agent Run Execution"
Cohesion: 0.07
Nodes (23): sweep_outbox(), execute_agent_run(), create_mock_gateway(), test_concurrent_sweepers_skip_locked(), test_duplicate_delivery_on_retry_attempt_ignored(), test_duplicate_delivery_while_preparing_drops_duplicate(), test_duplicate_delivery_while_run_queued_drops_duplicate(), test_enqueue_failure_leaves_run_queued_for_sweeper() (+15 more)

### Community 6 - "tools"
Cohesion: 0.14
Nodes (13): backoff, max_attempts, tools, description, idempotent, implementation, kind, name (+5 more)

### Community 7 - "DB Session & Security Core"
Cohesion: 0.15
Nodes (15): UnauthorizedError, create_access_token(), decode_access_token(), get_rsa_keys(), hash_api_key(), verify_password(), User, get_me() (+7 more)

### Community 8 - "gateway/errors.py"
Cohesion: 0.20
Nodes (15): CompletionResult, invalid(), invalid(), TokenUsage, ModelGatewayAuthError, ModelGatewayError, ModelGatewayTimeoutError, ModelGatewayUnavailableError (+7 more)

### Community 9 - "Gateway Client Tests"
Cohesion: 0.12
Nodes (13): ChatMessage, _dummy_model(), test_endpoint_path_body_and_auth_header(), test_http_429_rate_limited_mapping(), test_http_5xx_upstream_error_mapping(), test_invalid_json_response(), test_missing_or_malformed_choices(), test_no_api_key_leakage_in_errors_or_repr() (+5 more)

### Community 10 - "Tool Dispatcher Errors"
Cohesion: 0.10
Nodes (11): ToolError, ToolExecutionFailedError, ToolImplementationUnsupportedError, ToolInputInvalidError, ToolNotAllowedError, ToolNotFoundError, ToolOutputInvalidError, ToolPermissionDeniedError (+3 more)

### Community 11 - "test_m3_reliability.py"
Cohesion: 0.07
Nodes (41): start_live_hatchet_worker(), always(), script(), attempt_events(), _cleanup(), ctx(), FakeGateway, load() (+33 more)

### Community 12 - "test_audit_regressions.py"
Cohesion: 0.09
Nodes (17): ConflictError, _create_mock_client(), _seed_context(), test_approval_decision_on_cancelled_run_rejected_with_409(), test_approval_durability_pause_and_resume_without_reexecution(), test_approval_rejection_halts_and_blocks_execution(), test_artifact_security_traversal_and_extensions(), test_cross_project_artifact_access_blocked() (+9 more)

### Community 13 - "Runs API Tests"
Cohesion: 0.13
Nodes (16): seed_test_harness(), test_cancel_already_terminal_run_is_deterministic(), test_cancel_queued_run(), test_get_run_steps_ordered(), test_idempotency_different_agent_returns_409(), test_idempotency_same_key_different_input_returns_409(), test_idempotency_same_key_same_request_returns_200(), test_post_runs_forbidden_without_runs_create_scope() (+8 more)

### Community 14 - "CI Workflow"
Cohesion: 0.06
Nodes (35): name, runs-on, services, steps, DATABASE_URL, POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER (+27 more)

### Community 15 - "RunStateManager"
Cohesion: 0.08
Nodes (13): RunEvent, _discard_pending_events(), _promote_pending_events(), publish_committed_run_events(), queue_run_event(), RunStateManager, RunTransitionError, utc_now() (+5 more)

### Community 16 - "Identity"
Cohesion: 0.10
Nodes (15): Identity, RunDispatcher, cancel_run(), create_events_stream_ticket(), create_run(), get_run(), get_run_steps(), list_runs() (+7 more)

### Community 17 - "Model Resolution Policy"
Cohesion: 0.15
Nodes (15): NoModelForCapabilityError, CapabilityRequirement, ProviderPolicy, _rejection_reason(), select_model(), _make_model(), test_changing_priority_changes_selection_without_code_changes(), test_enforces_structured_output_requirement() (+7 more)

### Community 18 - "test_reports_api.py"
Cohesion: 0.31
Nodes (15): _agent_version(), _call(), _event(), _get_report(), _project_with_key(), _run(), _step(), test_failed_run_report() (+7 more)

### Community 19 - "ADRs & M0 Verification"
Cohesion: 0.08
Nodes (25): QA AgentOS API (FastAPI Backend Monolith), Core Platform Domains (Projects, Registries, Model Routing, Runs, Approvals, Artifacts, Reporting), Stateless Execution Workers (Hatchet jobs from PostgreSQL), generic_react Graph Structure, LangGraph interrupt() Human Approval Pause, PostgreSQL Checkpointing, Declarative Agent Definition (system prompt, I/O JSON Schema, tools, capability models), Registry Tables (agent_definitions, agent_versions, tool_definitions, models) (+17 more)

### Community 21 - "test_live_duplicate_and_fencing"
Cohesion: 0.24
Nodes (4): main(), test_live_duplicate_and_fencing(), test_live_sweeper_recovery(), test_live_worker_execution()

### Community 22 - "MinIO Artifact Storage"
Cohesion: 0.09
Nodes (9): ArtifactTooLargeError, InvalidArtifactPathError, MinioStorageClient, sanitize_filename(), StorageError, test_sanitize_filename_prevents_traversal(), _mock_failing_get(), NonRetryableException (+1 more)

### Community 23 - "Web Dev Dependencies"
Cohesion: 0.10
Nodes (28): devDependencies, @21st-extension/toolbar@0.5.14, @21st-extension/toolbar-next@0.5.14, @21st-extension/toolbar-react@0.5.14, autoprefixer@^10.4.20, eslint@^8.57.0, eslint-config-next@^15.1.0, postcss@^8.4.49 (+20 more)

### Community 24 - "store_run_output_artifact"
Cohesion: 0.09
Nodes (18): store_run_output_artifact(), RunSupersededError, fenced_checkpointer(), aput(), aput_writes(), test_checkpoint_fence_blocks_writes_deterministically(), fence(), 10. Configuration (+10 more)

### Community 25 - "stream_run_events"
Cohesion: 0.19
Nodes (7): stream_run_events(), event_generator(), fresh_snapshot(), snapshot_frame(), consume_stream_ticket(), create_stream_ticket(), get_redis_client()

### Community 26 - "Artifacts API Router"
Cohesion: 0.10
Nodes (9): create_artifact(), download_artifact(), get_artifact(), list_artifacts(), ArtifactCreateRequest, ArtifactListResponse, ArtifactResponse, ArtifactsService (+1 more)

### Community 27 - "test_observability_accounting.py"
Cohesion: 0.13
Nodes (14): ModelCall, RunStep, RunExecutionResult, RunExecutor, _on_step_completed(), test_stale_worker_step_persistence_refused(), _create_mock_client(), test_cancellation_safe_telemetry_cleanup() (+6 more)

### Community 28 - "Web Console Pages"
Cohesion: 0.16
Nodes (25): AgentCatalogPage(), AgentDetailResponse, AgentListItem, ApprovalItem, ApprovalsPage(), ArtifactItem, ArtifactsPage(), formatBytes() (+17 more)

### Community 29 - "Agents Catalog API"
Cohesion: 0.17
Nodes (6): get_agent(), list_agents(), AgentDetailResponse, AgentListItem, AgentVersionResponse, AgentsService

### Community 30 - "Registry Loader"
Cohesion: 0.21
Nodes (12): AgentRegistryItem, load_agents_catalog(), load_models_catalog(), load_registry_catalog(), load_tools_catalog(), ModelRegistryItem, RegistryCatalog, RegistryError (+4 more)

### Community 31 - "sync_registries"
Cohesion: 0.16
Nodes (8): find_registry_root(), test_active_version_immutability_enforced(), test_introducing_new_version_preserves_history(), test_invalid_json_schema_fails_validation(), test_missing_or_invalid_tool_reference_fails(), test_prompt_hash_matches_exact_source_file(), test_registry_root_discovery(), test_yaml_parsing_and_schema_validation()

### Community 32 - "Test-Case Output Fields"
Cohesion: 0.10
Nodes (21): description, type, type, description, type, properties, items, type (+13 more)

### Community 33 - "workers/main.py"
Cohesion: 0.07
Nodes (11): RunTerminalStateError, _ensure_setup(), _normalize_psycopg_url(), postgres_checkpointer(), agent_run_task(), AgentRunInput, _default_checkpointer(), get_hatchet_client() (+3 more)

### Community 34 - "Run"
Cohesion: 0.16
Nodes (12): Run, _create_mock_gateway(), _seed_test_project_and_agent(), test_input_validation_failure_transitions_to_failed(), test_postgres_checkpoint_crash_recovery(), test_run_events_and_steps_auditable_persistence(), test_run_executor_self_repair_exhausted(), test_run_executor_self_repair_success() (+4 more)

### Community 36 - "test_bug_analyzer.py"
Cohesion: 0.13
Nodes (21): _json_record(), parse_log_text(), _ctx(), _log(), seed_bug_analyzer_run(), _seed_tool_env(), _session_factory(), test_artifact_parse_reads_bounded_range() (+13 more)

### Community 37 - "Web TypeScript Config"
Cohesion: 0.11
Nodes (18): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+10 more)

### Community 38 - "Settings"
Cohesion: 0.11
Nodes (4): Settings, FileStorage, LiveResult, run_live()

### Community 39 - "test_bug_store.py"
Cohesion: 0.18
Nodes (19): HashingEmbedder, bug_report_for(), _agent_version(), _analyzed_run(), _LockProbeEmbedder, _project(), _record(), test_1536_dim_index_exists_and_neighbour_search_uses_it() (+11 more)

### Community 42 - "Hatchet Server Env"
Cohesion: 0.12
Nodes (16): HATCHET_DATABASE_URL, SERVER_AUTH_COOKIE_DOMAIN, SERVER_AUTH_COOKIE_SECRETS, SERVER_DATABASE_URL, SERVER_ENCRYPTION_JWT_PRIVATE_KEYSET_FILE, SERVER_ENCRYPTION_JWT_PUBLIC_KEYSET_FILE, SERVER_ENCRYPTION_MASTER_KEYSET_FILE, SERVER_POSTGRES_DATABASE (+8 more)

### Community 43 - "Test-Case Coverage Schema"
Cohesion: 0.15
Nodes (15): additionalProperties, properties, required, type, items, type, type, coverage_analysis (+7 more)

### Community 44 - "Test-Case Output Schema"
Cohesion: 0.13
Nodes (15): additionalProperties, required, output_schema, additionalProperties, properties, required, $schema, title (+7 more)

### Community 45 - "commit_and_publish"
Cohesion: 0.18
Nodes (7): EnqueueDispatcher, fail_run_if_owned(), reap_stale_runs(), ReapResult, _skip_locked(), commit_and_publish(), What changed (and why)

### Community 46 - "tracing.py"
Cohesion: 0.13
Nodes (4): get_tracer(), reset_tracer(), Tracer, test_noop_tracer_when_unconfigured_honest_none()

### Community 47 - "projects/router.py"
Cohesion: 0.30
Nodes (8): APIKey, create_project(), create_project_api_key(), list_projects(), APIKeyCreate, APIKeyResponse, ProjectCreate, ProjectResponse

### Community 48 - "Test-Case Generator Agent"
Cohesion: 0.14
Nodes (13): allowed_tools, description, kind, name, parameters, max_repairs, max_steps, temperature (+5 more)

### Community 49 - "logparse.py"
Cohesion: 0.15
Nodes (11): _mask_url(), _Collector, _level_of(), make_signature(), _normalize_frame(), normalize_message(), _parse_ts(), _redact() (+3 more)

### Community 50 - "reports/service.py"
Cohesion: 0.17
Nodes (13): _add_call(), _bug_section(), _finish_usage(), _id(), _is_aware_timestamp(), _model_usage(), _new_usage(), report_content_hash() (+5 more)

### Community 51 - "M3 — Bug Analyzer, reports and duplicate detection"
Cohesion: 0.06
Nodes (30): 1. Flow, 2. `log.parse` (deterministic tool), 3. Bug Analyzer agent, 4. Bug store and duplicate detection, 6. Reports (T1, T2, T9), 7. Security and isolation, 8. Acceptance status, 9. Known limitations (+22 more)

### Community 52 - "Dev Compose Overrides"
Cohesion: 0.16
Nodes (13): build, command, volumes, context, dockerfile, profiles, NEXT_PUBLIC_API_URL, build (+5 more)

### Community 53 - "Web App Layout"
Cohesion: 0.33
Nodes (5): metadata, RootLayout(), AuthProvider(), DevToolbar(), TwentyFirstToolbar

### Community 54 - "Test-Case Input Schema"
Cohesion: 0.15
Nodes (13): description, type, input_schema, additionalProperties, properties, required, $schema, title (+5 more)

### Community 55 - "Any"
Cohesion: 0.23
Nodes (5): sanitize_metadata(), sanitize_payload(), _sanitize_string(), test_error_message_secret_redaction(), test_metadata_and_payload_sanitization_redaction()

### Community 56 - "reports/router.py"
Cohesion: 0.12
Nodes (11): get_db(), ForbiddenError, require_scope(), scope_checker(), export_run_report(), get_run_report(), get_run_report_version(), list_run_report_versions() (+3 more)

### Community 57 - "Model Registry YAML"
Cohesion: 0.15
Nodes (12): models, capabilities, context_window, cost_input_per_1m, cost_output_per_1m, gateway_model_key, max_output_tokens, name (+4 more)

### Community 58 - "typing"
Cohesion: 0.11
Nodes (6): upgrade(), downgrade(), upgrade(), upgrade(), upgrade(), upgrade()

### Community 60 - "test_import_boundaries.py"
Cohesion: 0.27
Nodes (3): _get_imports_from_file(), test_gateway_client_uses_httpx_only(), test_no_provider_sdk_imports_in_runtime()

### Community 61 - "conftest.py"
Cohesion: 0.16
Nodes (9): client(), _override_get_db(), db_session(), event_loop(), get_postgres_test_url(), pg_client(), pg_engine(), pg_session() (+1 more)

### Community 62 - "Web Compose Service"
Cohesion: 0.22
Nodes (11): build, context, dockerfile, NEXT_PUBLIC_API_URL, build, container_name, environment, ports (+3 more)

### Community 63 - "LiteLLM Compose Service"
Cohesion: 0.18
Nodes (11): GEMINI_API_KEY, LITELLM_MASTER_KEY, NVIDIA_NIM_API_KEY, command, container_name, environment, image, ports (+3 more)

### Community 64 - "Test LiteLLM Service"
Cohesion: 0.18
Nodes (11): GEMINI_API_KEY, LITELLM_MASTER_KEY, NVIDIA_NIM_API_KEY, command, container_name, environment, image, ports (+3 more)

### Community 65 - "LiteLLM Config"
Cohesion: 0.15
Nodes (12): api_base, api_key, mock_response, model, outputDimensionality, taskType, model_list, litellm_params (+4 more)

### Community 66 - "Root Package JSON"
Cohesion: 0.20
Nodes (10): devDependencies, typescript@^5.7.2, typescript, name, private, scripts, build:web, dev:web (+2 more)

### Community 67 - "test_cancel_running_run_stops_between_nodes"
Cohesion: 0.20
Nodes (3): cleanup_database_after_each_runs_test(), test_cancel_running_run_stops_between_nodes(), test_idempotency_concurrent_submissions_postgres()

### Community 68 - "Web Runtime Dependencies"
Cohesion: 0.20
Nodes (10): dependencies, clsx@^2.1.1, lucide-react@^0.468.0, next@^15.1.0, react@^19.0.0, react-dom@^19.0.0, tailwind-merge@^2.5.5, clsx (+2 more)

### Community 69 - "Hatchet Migration Service"
Cohesion: 0.22
Nodes (9): hatchet-migrate, condition, container_name, depends_on, entrypoint, image, restart, volumes (+1 more)

### Community 70 - "MinIO Compose Service"
Cohesion: 0.20
Nodes (10): MINIO_ROOT_PASSWORD, MINIO_ROOT_USER, command, container_name, environment, image, ports, restart (+2 more)

### Community 71 - "Postgres Compose Service"
Cohesion: 0.20
Nodes (10): POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER, container_name, environment, image, ports, restart (+2 more)

### Community 72 - "Test Postgres Service"
Cohesion: 0.20
Nodes (10): POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER, container_name, environment, image, ports, restart (+2 more)

### Community 74 - "Run Detail Page"
Cohesion: 0.25
Nodes (8): ApprovalItem, ArtifactItem, formatBytes(), RunDetail, RunDetailPage(), RunStepItem, StreamConnectionStatus, TERMINAL_STATUSES

### Community 75 - "Compose Service Dependencies"
Cohesion: 0.22
Nodes (9): condition, api, hatchet, condition, worker, command, container_name, depends_on (+1 more)

### Community 76 - "Worker Compose Env"
Cohesion: 0.27
Nodes (11): environment, DATABASE_URL, HATCHET_CLIENT_HOST_PORT, HATCHET_CLIENT_SERVER_URL, HATCHET_CLIENT_TOKEN, MINIO_ENDPOINT, REDIS_URL, RUN_DISPATCHER (+3 more)

### Community 77 - "Hatchet Setup Service"
Cohesion: 0.22
Nodes (9): hatchet-setup, container_name, depends_on, image, ports, restart, condition, volumes (+1 more)

### Community 78 - "LiteLLM Mock Config"
Cohesion: 0.22
Nodes (8): api_key, mock_response, model, model_list, litellm_params, model_name, router_settings, timeout

### Community 80 - "embeddings_benchmark.py"
Cohesion: 0.15
Nodes (6): benchmark(), main(), run(), Case, load_dataset(), load_outputs()

### Community 81 - "environment"
Cohesion: 0.18
Nodes (11): ENVIRONMENT, HATCHET_CLIENT_HOST_PORT, HATCHET_CLIENT_SERVER_URL, HATCHET_CLIENT_TLS_ROOT_CA_FILE, HATCHET_CLIENT_TLS_SERVER_NAME, HATCHET_CLIENT_TLS_STRATEGY, LITELLM_ENDPOINT, MODEL_PROVIDER_ALLOWLIST (+3 more)

### Community 82 - "Test Compose Root"
Cohesion: 0.25
Nodes (7): name, services, volumes, p6test_hatchet_config, p6test_hatchet_keys, p6test_postgres_data, p6test_redis_data

### Community 83 - "Test Hatchet Migration"
Cohesion: 0.22
Nodes (9): hatchet-migrate, condition, container_name, depends_on, entrypoint, image, restart, volumes (+1 more)

### Community 84 - "Compose Volumes"
Cohesion: 0.25
Nodes (8): volumes, caddy_config, caddy_data, hatchet_config, hatchet_keys, minio_data, postgres_data, redis_data

### Community 85 - "setup_test_context"
Cohesion: 0.47
Nodes (7): setup_test_context(), test_approvals_cross_project_isolation(), test_approve_decision_resumes_run(), test_create_approval_and_run_pause(), test_duplicate_decision_conflict(), test_list_approvals_unauthorized(), test_reject_decision_cancels_run()

### Community 87 - "services"
Cohesion: 0.13
Nodes (15): container_name, depends_on, ports, restart, postgres, redis, container_name, depends_on (+7 more)

### Community 88 - "Caddy Compose Service"
Cohesion: 0.29
Nodes (7): container_name, depends_on, image, ports, restart, volumes, caddy

### Community 89 - "MinIO Bucket Init"
Cohesion: 0.29
Nodes (7): minio, condition, container_name, depends_on, entrypoint, image, minio-init

### Community 90 - "Compose Healthchecks"
Cohesion: 0.23
Nodes (13): interval, retries, test, timeout, healthcheck, healthcheck, container_name, healthcheck (+5 more)

### Community 91 - "Test Hatchet Engine"
Cohesion: 0.20
Nodes (10): hatchet-setup, command, container_name, depends_on, image, ports, restart, condition (+2 more)

### Community 92 - "Test Healthchecks"
Cohesion: 0.57
Nodes (7): interval, retries, test, timeout, healthcheck, healthcheck, healthcheck

### Community 93 - "Web NPM Scripts"
Cohesion: 0.33
Nodes (6): scripts, build, dev, lint, start, typecheck

### Community 94 - "postgres"
Cohesion: 0.25
Nodes (8): hatchet, postgres, redis, condition, depends_on, condition, condition, depends_on

### Community 95 - "Test Redis Service"
Cohesion: 0.33
Nodes (6): container_name, image, ports, restart, volumes, redis

### Community 96 - "Test-Case Priority Enum"
Cohesion: 0.40
Nodes (5): default, description, enum, type, format

### Community 97 - "Test-Case Type Enum"
Cohesion: 0.40
Nodes (5): enum, test_types, description, items, type

### Community 98 - "Agent Model Requirements"
Cohesion: 0.40
Nodes (5): model_requirements, planner, capabilities, min_context_window, structured_output

### Community 99 - "sync_registries"
Cohesion: 0.15
Nodes (6): bootstrap_demo_data(), _run_bootstrap(), sync_registries(), SyncResult, test_bootstrap_demo_data_integration(), test_initial_sync_and_idempotency_postgres()

### Community 100 - "approvals/router.py"
Cohesion: 0.14
Nodes (10): approve_approval(), create_approval(), get_approval(), list_approvals(), reject_approval(), ApprovalCreateRequest, ApprovalDecisionRequest, ApprovalListResponse (+2 more)

### Community 101 - "worker"
Cohesion: 0.25
Nodes (8): context, dockerfile, worker, build, container_name, entrypoint, restart, volumes

### Community 102 - "Test-Case Steps"
Cohesion: 0.67
Nodes (3): steps, items, type

### Community 103 - "Test-Case Enum Fields"
Cohesion: 0.67
Nodes (3): type, enum, type

### Community 104 - "bugs/service.py"
Cohesion: 0.19
Nodes (10): BugFingerprint, Candidate, decide_match(), embedding_text(), _frame_keys(), MatchDecision, structural_mismatch(), _fingerprint() (+2 more)

### Community 115 - "hatchet-migrate"
Cohesion: 0.29
Nodes (7): DATABASE_URL, container_name, environment, image, restart, environment, hatchet-migrate

### Community 116 - "test_migration_drift.py"
Cohesion: 0.20
Nodes (5): include_object(), test_alembic_schema_matches_metadata_postgres(), _check_drift(), test_drift_detector_catches_schema_drift(), test_models_defined_properly()

### Community 117 - "test_artifacts_api.py"
Cohesion: 0.47
Nodes (5): setup_test_context(), test_artifact_direct_download_stream(), test_artifacts_cross_project_isolation(), test_create_artifact_and_fetch_presigned_url(), test_list_artifacts_unauthorized()

### Community 118 - "ToolDispatcher"
Cohesion: 0.11
Nodes (6): ToolDefinition, ToolDispatcher, test_tool_side_effect_not_repeated_across_attempts(), test_unknown_tool_rejected(), test_unknown_version_rejected_without_fallback(), test_unsupported_implementation_rejected()

### Community 119 - "test_embedding_config.py"
Cohesion: 0.08
Nodes (18): make_run_output_sink(), default_embedder(), Embedder, embedder_from_settings(), GatewayEmbedder, _capturing_client(), test_cost_rule_applies_only_to_the_gateway_provider(), test_default_sink_with_embeddings_disabled_uses_exact_signatures() (+10 more)

### Community 127 - "test_harness.py"
Cohesion: 0.14
Nodes (16): _aevaluate(), _evaluate(), _scripted_agent_gateway(), _scripted_judge(), post(), test_duplicate_gates_catch_false_merges_and_missed_duplicates(), test_faithfulness_gate_passes_and_fails_with_scripted_judge(), test_invented_evidence_fails_grounding() (+8 more)

### Community 129 - "BugStore"
Cohesion: 0.15
Nodes (12): BugCluster, BugReport, utc_now(), BugStore, parsed_signatures(), _row_fingerprint(), _artifact_name(), run_output_sink() (+4 more)

### Community 132 - "metrics"
Cohesion: 0.08
Nodes (25): min, modes, dataset, min, modes, min, modes, duplicate_threshold (+17 more)

### Community 133 - "tools.py"
Cohesion: 0.26
Nodes (5): make_log_parse(), log_parse_with_artifacts(), make_tool_implementations(), get_storage_client(), log_parse()

### Community 134 - "test_bug_report_integration.py"
Cohesion: 0.15
Nodes (9): validate_report(), _scripted_gateway(), _key_for(), _run_bug_analyzer(), test_bug_section_end_to_end(), test_duplicate_report_links_related_bug(), test_non_bug_runs_have_no_bug_section(), test_schema_accepts_v1_0_documents() (+1 more)

### Community 135 - "dataset.yaml"
Cohesion: 0.09
Nodes (22): cases, expected, id, log, note, dataset, distinct, a (+14 more)

### Community 136 - "runner.py"
Cohesion: 0.13
Nodes (13): BudgetExceeded, ac1_verdict(), budgeted_client(), _git_sha(), live_problems(), main(), evaluate_outputs(), live_outputs() (+5 more)

### Community 137 - "pytest"
Cohesion: 0.19
Nodes (6): _embedding_gateway(), test_gateway_embedder_normalizes_and_costs(), handler(), test_gateway_embedder_timeout_and_input_validation(), test_gateway_embedder_typed_errors(), test_enabled_gateway_embeddings_require_a_positive_cost_rate()

### Community 138 - "test_report_export.py"
Cohesion: 0.39
Nodes (10): _export(), fake_storage(), FakeStorage, _seed(), test_changed_evidence_creates_next_version(), test_export_creates_versioned_json_and_html(), test_export_isolation_and_scopes(), test_repeated_export_returns_same_version() (+2 more)

### Community 139 - "properties"
Cohesion: 0.11
Nodes (18): minimum, type, description, type, type, properties, type, pattern (+10 more)

### Community 140 - "properties"
Cohesion: 0.12
Nodes (17): maxLength, type, enum, type, properties, pattern, type, affected_component (+9 more)

### Community 141 - "seed_agents_catalog"
Cohesion: 0.20
Nodes (8): seed_agents_catalog(), test_agents_api_authorization_matrix(), test_get_agent_by_slug_inactive(), test_get_agent_by_slug_success(), test_get_agent_by_slug_unknown_returns_404(), test_list_agents_authenticated_user(), test_list_agents_include_inactive(), test_list_agents_unauthorized()

### Community 143 - "report/page.tsx"
Cohesion: 0.21
Nodes (14): Badge(), BugSection, Empty(), fmtDuration(), fmtTime(), ReportVersion, RunReport, RunReportPage() (+6 more)

### Community 144 - "GatewayJudge"
Cohesion: 0.13
Nodes (4): faithfulness_scores(), GatewayJudge, judge_from_env(), JudgeUnavailable

### Community 145 - "properties"
Cohesion: 0.15
Nodes (13): description, maxLength, type, description, maxLength, type, properties, description (+5 more)

### Community 146 - "items"
Cohesion: 0.17
Nodes (12): items, maxItems, type, additionalProperties, maxLength, required, type, evidence (+4 more)

### Community 147 - "APIError"
Cohesion: 0.13
Nodes (9): api_error_handler(), APIError, NotFoundError, setup_logging(), health_check(), lifespan(), get_dispatcher(), set_dispatcher() (+1 more)

### Community 148 - "evaluate"
Cohesion: 0.20
Nodes (11): Dataset, evaluate(), _rate(), subset(), CaseScore, cluster_cases(), deterministic_scores(), duplicate_metrics() (+3 more)

### Community 149 - "properties"
Cohesion: 0.18
Nodes (11): properties, description, type, description, type, message, metadata, text (+3 more)

### Community 150 - "bug-analyzer.yaml"
Cohesion: 0.20
Nodes (9): allowed_tools, description, kind, name, permissions, prompt_file, slug, status (+1 more)

### Community 151 - "CallBudget"
Cohesion: 0.21
Nodes (3): CallBudget, test_call_limit_spend_cap_and_quota_stop_further_calls(), test_roles_are_separated()

### Community 152 - "embeddings.py"
Cohesion: 0.20
Nodes (5): PreparedBug, cosine(), EmbeddingResult, l2_normalize(), validate_embedding_inputs()

### Community 153 - "ResolvedModel"
Cohesion: 0.20
Nodes (6): gemini_compatible_schema(), _optional_int(), _parse_usage(), StructuredOutputSpec, ResolvedModel, test_original_schema_not_mutated_and_idempotent()

### Community 156 - "steps"
Cohesion: 0.06
Nodes (34): concurrency, cancel-in-progress, group, env, name, runs-on, steps, timeout-minutes (+26 more)

### Community 158 - "artifact_id"
Cohesion: 0.22
Nodes (9): description, format, type, artifact_id, name, size_bytes, source, properties (+1 more)

### Community 159 - "ReportsService"
Cohesion: 0.29
Nodes (4): Report, _embeddable_artifact_content(), _report_by_hash(), ReportsService

### Community 160 - "input_schema"
Cohesion: 0.25
Nodes (8): additionalProperties, oneOf, required, $schema, title, type, required, input_schema

### Community 161 - "items"
Cohesion: 0.25
Nodes (8): additionalProperties, properties, required, type, signatures, items, maxItems, type

### Community 162 - "properties"
Cohesion: 0.29
Nodes (7): maxLength, type, properties, detail, signature, pattern, type

### Community 164 - "input_schema"
Cohesion: 0.33
Nodes (6): input_schema, additionalProperties, required, $schema, title, type

### Community 165 - "output_schema"
Cohesion: 0.33
Nodes (6): output_schema, additionalProperties, required, $schema, title, type

### Community 167 - "html.py"
Cohesion: 0.39
Nodes (7): _bug_section(), _e(), _json_block(), _kv(), render_report_html(), _table(), test_html_renderer_escapes_every_value()

### Community 168 - "test_end_to_end_execution_succeeded"
Cohesion: 0.17
Nodes (5): create_mock_gateway(), test_end_to_end_execution_repair_failure(), test_end_to_end_execution_succeeded(), test_post_runs_returns_before_blocked_executor_finishes(), test_post_runs_valid_intake_returns_202_promptly()

### Community 169 - "subscribe_run_events"
Cohesion: 0.20
Nodes (6): get_run_events_channel(), _is_terminal_frame(), subscribe_run_events(), _listener(), test_subscriber_cleanup_on_cancellation(), run_subscriber()

### Community 170 - "output_schema"
Cohesion: 0.33
Nodes (6): additionalProperties, required, $schema, title, type, output_schema

### Community 171 - "planner"
Cohesion: 0.40
Nodes (5): model_requirements, planner, capabilities, min_context_window, structured_output

### Community 172 - "parameters"
Cohesion: 0.40
Nodes (5): parameters, max_repairs, max_steps, output_sinks, temperature

### Community 174 - "env.py"
Cohesion: 0.33
Nodes (5): do_run_migrations(), include_object(), run_async_migrations(), run_migrations_offline(), run_migrations_online()

### Community 176 - "confidence"
Cohesion: 0.50
Nodes (4): maximum, minimum, type, confidence

### Community 177 - "title"
Cohesion: 0.50
Nodes (4): title, maxLength, minLength, type

### Community 179 - "Bug Report & Log Analyzer System Prompt (v1.0.0)"
Cohesion: 0.50
Nodes (3): Bug Report & Log Analyzer System Prompt (v1.0.0), Step 1 - Parse the logs (always first), Step 2 - Write the bug report

### Community 180 - "timestamp"
Cohesion: 0.50
Nodes (4): timestamp, description, format, type

### Community 181 - "severity"
Cohesion: 0.67
Nodes (3): severity, enum, type

### Community 183 - "error_count"
Cohesion: 0.67
Nodes (3): minimum, type, error_count

### Community 184 - "redactions"
Cohesion: 0.67
Nodes (3): redactions, minimum, type

### Community 185 - "total_lines"
Cohesion: 0.67
Nodes (3): total_lines, minimum, type

### Community 186 - "warning_count"
Cohesion: 0.67
Nodes (3): warning_count, minimum, type

### Community 188 - "json"
Cohesion: 0.13
Nodes (16): BudgetTransport, Price, prices_from_env(), _ok(), _run(), _scripted_agent(), _scripted_judge_post(), test_ac4_passes_only_when_weakened_prompt_is_caught() (+8 more)

### Community 191 - "test_gateway_gemini_schema.py"
Cohesion: 0.15
Nodes (6): _model(), _sent_schema(), test_gemini_request_uses_nullable_for_all_four_fields(), test_non_gemini_providers_unchanged(), test_non_nullable_fields_unchanged(), test_upstream_message_kept_and_redacted()

## Knowledge Gaps
- **30 isolated node(s):** `next/core-web-vitals`, `qa-agentos`, `qa-agentos-api`, `qa-agentos-evals`, `graphify Skill Trigger (/graphify)` (+25 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1343 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **51 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Run` connect `Run` to `BugStore`, `Tool Dispatch Tests`, `AgentVersion`, `Durable Agent Run Execution`, `test_bug_report_integration.py`, `test_m3_reliability.py`, `test_audit_regressions.py`, `Runs API Tests`, `InlineDispatcher`, `RunStateManager`, `Identity`, `test_reports_api.py`, `APIError`, `.__init__`, `test_live_duplicate_and_fencing`, `store_run_output_artifact`, `stream_run_events`, `Artifacts API Router`, `embeddings.py`, `test_observability_accounting.py`, `ReportsService`, `workers/main.py`, `InlineDispatcher`, `test_bug_analyzer.py`, `Settings`, `test_bug_store.py`, `test_end_to_end_execution_succeeded`, `commit_and_publish`, `sweeper.py`, `reports/service.py`, `typing`, `test_cancel_running_run_stops_between_nodes`, `setup_test_context`, `approvals/router.py`, `bugs/service.py`, `test_artifacts_api.py`, `test_embedding_config.py`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Are the 82 inferred relationships involving `Run` (e.g. with `ApprovalsService` and `ArtifactsService`) actually correct?**
  _`Run` has 82 INFERRED edges - model-reasoned connections that need verification._
- **What connects `next/core-web-vitals`, `qa-agentos`, `qa-agentos-api` to the rest of the system?**
  _30 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `LangGraph Runtime Nodes` be split into smaller, more focused modules?**
  _Cohesion score 0.07219662058371736 - nodes in this community are weakly interconnected._
- **Why does `LiteLLMClient` connect `LiteLLMClient` to `LangGraph Runtime Nodes`, `Durable Agent Run Execution`, `test_bug_report_integration.py`, `gateway/errors.py`, `Gateway Client Tests`, `pytest`, `test_m3_reliability.py`, `test_audit_regressions.py`, `runner.py`, `InlineDispatcher`, `.__init__`, `test_live_duplicate_and_fencing`, `embeddings.py`, `ResolvedModel`, `test_observability_accounting.py`, `workers/main.py`, `Run`, `InlineDispatcher`, `Settings`, `test_end_to_end_execution_succeeded`, `typing`, `json`, `test_gateway_gemini_schema.py`, `test_embedding_config.py`, `test_harness.py`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Are the 32 inferred relationships involving `LiteLLMClient` (e.g. with `InlineDispatcher` and `RunExecutor`) actually correct?**
  _`LiteLLMClient` has 32 INFERRED edges - model-reasoned connections that need verification._
- **Should `uuid` be split into smaller, more focused modules?**
  _Cohesion score 0.12169312169312169 - nodes in this community are weakly interconnected._