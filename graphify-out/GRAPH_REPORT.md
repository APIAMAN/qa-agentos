# Graph Report - qa-agentos  (2026-11-24)

## Corpus Check
- 136 files · ~89,041 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 12 file(s) not represented in the graph (top: (none) 6, .example 1, .ini 1)

## Summary
- 2199 nodes · 5377 edges · 126 communities (96 shown, 30 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 814 edges (avg confidence: 0.92)
- Token cost: 182,578 input · 0 output

## Graph Freshness
- Built from commit: `a215c26c`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- LangGraph Runtime Nodes
- uuid
- Architecture Research Doc
- Tool Dispatch Tests
- AgentVersion
- Durable Agent Run Execution
- Tool Registry YAML
- DB Session & Security Core
- gateway/errors.py
- Gateway Client Tests
- Tool Dispatcher Errors
- test_m3_reliability.py
- test_audit_regressions.py
- Runs API Tests
- CI Workflow
- RunStateManager
- Runs API Router
- Model Resolution Policy
- conftest.py
- ADRs & M0 Verification
- Run Service & Dispatcher Interface
- Hatchet Dispatcher
- MinIO Artifact Storage
- Web Dev Dependencies
- utc_now
- seed_agents_catalog
- Artifacts API Router
- RunExecutor
- Web Console Pages
- Agents Catalog API
- Registry Loader
- Registry Tests
- Test-Case Output Fields
- workers/main.py
- qa_agentos/main.py
- HatchetDispatcher
- ToolDispatcher
- Web TypeScript Config
- Settings
- M3 — Run Reliability, Recovery & Hardening
- ResolvedModel
- Hatchet Server Env
- Test-Case Coverage Schema
- Test-Case Output Schema
- .chat_completion
- Tracer Protocol
- projects/router.py
- Test-Case Generator Agent
- Artifacts Service
- sync.py
- Any
- Dev Compose Overrides
- Web App Layout
- Test-Case Input Schema
- sanitize_payload
- Spy Tracer Test Double
- Model Registry YAML
- typing
- Broken Tracer Tests
- test_import_boundaries.py
- env.py
- Web Compose Service
- LiteLLM Compose Service
- Test LiteLLM Service
- LiteLLM Config
- Root Package JSON
- Run Cancellation Tests
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
- Run Launcher Page
- environment
- Test Compose Root
- Test Hatchet Migration
- Compose Volumes
- Langfuse Tracer
- tracing.py
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
- hash_password
- test_timed_out_non_idempotent_tool_attempted_once
- worker
- Test-Case Steps
- Test-Case Enum Fields
- .from_settings
- ESLint Config
- Graphify Instructions
- Hatchet Setup Script
- Postgres Init Script
- pnpm Workspace
- Root Python Project
- API Python Project
- hatchet-migrate
- _discard_pending_events
- hashlib
- get_hatchet_client

## God Nodes (most connected - your core abstractions)
1. `Run` - 101 edges
2. `RunExecutor` - 52 edges
3. `Identity` - 52 edges
4. `LiteLLMClient` - 49 edges
5. `ProviderPolicy` - 44 edges
6. `ModelResolver` - 43 edges
7. `ToolDispatcher` - 43 edges
8. `RunStateManager` - 38 edges
9. `AgentVersion` - 36 edges
10. `resolve_project_id()` - 30 edges

## Surprising Connections (you probably didn't know these)
- `2. Run lifecycle and state transitions` --references--> `RunSupersededError`  [INFERRED]
  docs/m3/M3_RELIABILITY.md → apps/api/src/qa_agentos/runtime/events.py
- `Known limitations` --references--> `APIError`  [INFERRED]
  docs/m3/M3_RELIABILITY.md → apps/api/src/qa_agentos/core/errors.py
- `8. Artifacts` --references--> `store_run_output_artifact()`  [EXTRACTED]
  docs/m3/M3_RELIABILITY.md → apps/api/src/qa_agentos/modules/artifacts/service.py
- `6. Stale-run recovery (operations)` --references--> `reap_stale_runs()`  [EXTRACTED]
  docs/m3/M3_RELIABILITY.md → apps/api/src/qa_agentos/modules/runs/recovery.py
- `7. Events and live updates` --references--> `publish_run_event()`  [INFERRED]
  docs/m3/M3_RELIABILITY.md → apps/api/src/qa_agentos/runtime/pubsub.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **LLM Model Routing via LiteLLM (NIM primary, Gemini fallback)** — docs_adr_adr_004_litellm_model_gateway_litellm_proxy, docs_adr_adr_004_litellm_model_gateway_capability_slots, docs_adr_adr_005_nvidia_nim_gateway_nvidia_nim, docs_adr_adr_005_nvidia_nim_gateway_gemini_fallback [EXTRACTED 1.00]
- **QA AgentOS Architecture Decision Records** — docs_adr_adr_001_modular_monolith_modular_monolith, docs_adr_adr_002_langgraph_runtime_langgraph_orchestration, docs_adr_adr_003_data_driven_registries_registries_as_data, docs_adr_adr_004_litellm_model_gateway_litellm_proxy, docs_adr_adr_005_nvidia_nim_gateway_nim_fallback [EXTRACTED 1.00]
- **Five launch agents as registry definitions** — docs_research_qa_agentos_architecture_test_case_generator, docs_research_qa_agentos_architecture_bug_analyzer, docs_research_qa_agentos_architecture_api_testing_agent, docs_research_qa_agentos_architecture_self_healing_agent, docs_research_qa_agentos_architecture_visual_a11y_agent, docs_research_qa_agentos_architecture_agent_registry [EXTRACTED 1.00]
- **Agent -> Tool -> Model -> Artifact -> Report chain** — docs_research_qa_agentos_architecture_langgraph_runtime, docs_research_qa_agentos_architecture_tool_registry, docs_research_qa_agentos_architecture_litellm_gateway, docs_research_qa_agentos_architecture_artifact_storage, docs_research_qa_agentos_architecture_report_service [EXTRACTED 1.00]
- **Single Postgres hosts app, Hatchet, LangGraph, Langfuse schemas** — docs_research_qa_agentos_architecture_postgresql, docs_research_qa_agentos_architecture_hatchet, docs_research_qa_agentos_architecture_postgres_checkpointer, docs_research_qa_agentos_architecture_langfuse, docs_research_qa_agentos_architecture_pgvector [EXTRACTED 1.00]

## Communities (126 total, 30 thin omitted)

### Community 0 - "LangGraph Runtime Nodes"
Cohesion: 0.07
Nodes (23): calculate_model_cost(), build_generic_react_graph(), execute_tools_node(), finalize_node(), _format_user_prompt(), generate_node(), max_steps_exceeded_node(), prepare_node() (+15 more)

### Community 1 - "uuid"
Cohesion: 0.06
Nodes (31): consume_stream_ticket(), create_stream_ticket(), get_redis_client(), get_run_events_channel(), _is_terminal_frame(), subscribe_run_events(), setup_test_context(), test_approvals_cross_project_isolation() (+23 more)

### Community 2 - "Architecture Research Doc"
Cohesion: 0.05
Nodes (53): Architecture Decisions (ADR-001..022), Agent Registry, Agent -> Tool -> Model -> Artifact -> Report flow, API Testing Agent, Approval Service (human-in-the-loop), Content-Addressed Artifact Storage, JWT + API Keys Auth, axe-core Accessibility Engine (+45 more)

### Community 3 - "Tool Dispatch Tests"
Cohesion: 0.08
Nodes (20): ToolInvocationRequest, _seed_test_environment(), test_invalid_input_fails_before_execution(), test_invalid_output_produces_tool_output_invalid(), test_missing_permissions_produce_permission_denied(), test_non_json_serializable_output_fails_safely(), test_permission_denied_and_disallowed_never_execute_builtin(), fake_spy() (+12 more)

### Community 4 - "AgentVersion"
Cohesion: 0.11
Nodes (16): Base, AgentDefinition, AgentVersion, ModelCatalog, Project, seed_live_environment(), cleanup_database_after_each_hatchet_test(), seeded_h_db() (+8 more)

### Community 5 - "Durable Agent Run Execution"
Cohesion: 0.09
Nodes (23): Run, sweep_outbox(), execute_agent_run(), create_mock_gateway(), test_concurrent_sweepers_skip_locked(), test_duplicate_delivery_on_retry_attempt_ignored(), test_duplicate_delivery_while_preparing_drops_duplicate(), test_duplicate_delivery_while_run_queued_drops_duplicate() (+15 more)

### Community 6 - "Tool Registry YAML"
Cohesion: 0.05
Nodes (40): description, type, additionalProperties, properties, required, $schema, title, type (+32 more)

### Community 7 - "DB Session & Security Core"
Cohesion: 0.13
Nodes (18): ForbiddenError, UnauthorizedError, create_access_token(), decode_access_token(), get_rsa_keys(), hash_api_key(), verify_password(), User (+10 more)

### Community 8 - "gateway/errors.py"
Cohesion: 0.22
Nodes (12): LiteLLMClient, TokenUsage, ModelGatewayAuthError, ModelGatewayError, ModelGatewayTimeoutError, ModelGatewayUnavailableError, ModelRateLimitedError, ModelRequestRejectedError (+4 more)

### Community 9 - "Gateway Client Tests"
Cohesion: 0.09
Nodes (14): ChatMessage, _dummy_model(), test_endpoint_path_body_and_auth_header(), test_http_429_rate_limited_mapping(), test_http_5xx_upstream_error_mapping(), test_invalid_json_response(), test_missing_or_malformed_choices(), test_no_api_key_leakage_in_errors_or_repr() (+6 more)

### Community 10 - "Tool Dispatcher Errors"
Cohesion: 0.10
Nodes (11): ToolError, ToolExecutionFailedError, ToolImplementationUnsupportedError, ToolInputInvalidError, ToolNotAllowedError, ToolNotFoundError, ToolOutputInvalidError, ToolPermissionDeniedError (+3 more)

### Community 11 - "test_m3_reliability.py"
Cohesion: 0.09
Nodes (36): always(), script(), attempt_events(), _cleanup(), ctx(), FakeGateway, load(), make_run() (+28 more)

### Community 12 - "test_audit_regressions.py"
Cohesion: 0.06
Nodes (28): ConflictError, Approval, approve_approval(), create_approval(), get_approval(), list_approvals(), reject_approval(), ApprovalCreateRequest (+20 more)

### Community 13 - "Runs API Tests"
Cohesion: 0.09
Nodes (21): create_mock_gateway(), seed_test_harness(), test_cancel_already_terminal_run_is_deterministic(), test_cancel_queued_run(), test_end_to_end_execution_repair_failure(), test_end_to_end_execution_succeeded(), test_get_run_steps_ordered(), test_idempotency_different_agent_returns_409() (+13 more)

### Community 14 - "CI Workflow"
Cohesion: 0.06
Nodes (35): name, runs-on, services, steps, DATABASE_URL, POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER (+27 more)

### Community 15 - "RunStateManager"
Cohesion: 0.10
Nodes (10): RunEvent, InlineDispatcher, commit_and_publish(), publish_committed_run_events(), queue_run_event(), RunStateManager, claim_run(), test_live_events_are_published_only_after_commit() (+2 more)

### Community 16 - "Runs API Router"
Cohesion: 0.12
Nodes (16): cancel_run(), create_events_stream_ticket(), create_run(), get_run(), get_run_steps(), list_runs(), replay_run(), stream_run_events() (+8 more)

### Community 17 - "Model Resolution Policy"
Cohesion: 0.15
Nodes (14): NoModelForCapabilityError, CapabilityRequirement, _rejection_reason(), select_model(), _make_model(), test_changing_priority_changes_selection_without_code_changes(), test_enforces_structured_output_requirement(), test_ignores_non_available_status() (+6 more)

### Community 18 - "conftest.py"
Cohesion: 0.15
Nodes (9): client(), _override_get_db(), db_session(), event_loop(), get_postgres_test_url(), pg_client(), pg_engine(), pg_session() (+1 more)

### Community 19 - "ADRs & M0 Verification"
Cohesion: 0.08
Nodes (25): QA AgentOS API (FastAPI Backend Monolith), Core Platform Domains (Projects, Registries, Model Routing, Runs, Approvals, Artifacts, Reporting), Stateless Execution Workers (Hatchet jobs from PostgreSQL), generic_react Graph Structure, LangGraph interrupt() Human Approval Pause, PostgreSQL Checkpointing, Declarative Agent Definition (system prompt, I/O JSON Schema, tools, capability models), Registry Tables (agent_definitions, agent_versions, tool_definitions, models) (+17 more)

### Community 20 - "Run Service & Dispatcher Interface"
Cohesion: 0.15
Nodes (4): APIError, NotFoundError, RunDispatcher, RunsService

### Community 21 - "Hatchet Dispatcher"
Cohesion: 0.15
Nodes (5): main(), start_live_hatchet_worker(), test_live_duplicate_and_fencing(), test_live_sweeper_recovery(), test_live_worker_execution()

### Community 22 - "MinIO Artifact Storage"
Cohesion: 0.06
Nodes (11): _ensure_setup(), _mask_url(), _normalize_psycopg_url(), postgres_checkpointer(), ArtifactTooLargeError, InvalidArtifactPathError, MinioStorageClient, sanitize_filename() (+3 more)

### Community 23 - "Web Dev Dependencies"
Cohesion: 0.10
Nodes (28): devDependencies, @21st-extension/toolbar@0.5.14, @21st-extension/toolbar-next@0.5.14, @21st-extension/toolbar-react@0.5.14, autoprefixer@^10.4.20, eslint@^8.57.0, eslint-config-next@^15.1.0, postcss@^8.4.49 (+20 more)

### Community 24 - "utc_now"
Cohesion: 0.13
Nodes (9): store_run_output_artifact(), EnqueueDispatcher, fail_run_if_owned(), reap_stale_runs(), ReapResult, _skip_locked(), RunSupersededError, utc_now() (+1 more)

### Community 25 - "seed_agents_catalog"
Cohesion: 0.20
Nodes (8): seed_agents_catalog(), test_agents_api_authorization_matrix(), test_get_agent_by_slug_inactive(), test_get_agent_by_slug_success(), test_get_agent_by_slug_unknown_returns_404(), test_list_agents_authenticated_user(), test_list_agents_include_inactive(), test_list_agents_unauthorized()

### Community 26 - "Artifacts API Router"
Cohesion: 0.14
Nodes (9): create_artifact(), download_artifact(), get_artifact(), list_artifacts(), ArtifactCreateRequest, ArtifactListResponse, ArtifactResponse, Identity (+1 more)

### Community 27 - "RunExecutor"
Cohesion: 0.08
Nodes (26): ModelCall, RunStep, RunExecutionResult, RunExecutor, _on_step_completed(), ModelResolver, ProviderPolicy, _create_mock_client() (+18 more)

### Community 28 - "Web Console Pages"
Cohesion: 0.20
Nodes (19): AgentCatalogPage(), AgentDetailResponse, AgentListItem, ApprovalItem, ApprovalsPage(), ArtifactItem, ArtifactsPage(), formatBytes() (+11 more)

### Community 29 - "Agents Catalog API"
Cohesion: 0.17
Nodes (6): get_agent(), list_agents(), AgentDetailResponse, AgentListItem, AgentVersionResponse, AgentsService

### Community 30 - "Registry Loader"
Cohesion: 0.23
Nodes (11): AgentRegistryItem, load_agents_catalog(), load_models_catalog(), load_registry_catalog(), load_tools_catalog(), ModelRegistryItem, RegistryCatalog, RegistryError (+3 more)

### Community 31 - "Registry Tests"
Cohesion: 0.16
Nodes (9): find_registry_root(), test_active_version_immutability_enforced(), test_bootstrap_demo_data_integration(), test_initial_sync_and_idempotency_postgres(), test_introducing_new_version_preserves_history(), test_invalid_json_schema_fails_validation(), test_missing_or_invalid_tool_reference_fails(), test_registry_root_discovery() (+1 more)

### Community 32 - "Test-Case Output Fields"
Cohesion: 0.10
Nodes (21): description, type, type, description, type, properties, items, type (+13 more)

### Community 33 - "workers/main.py"
Cohesion: 0.08
Nodes (9): RunTerminalStateError, RunTransitionError, agent_run_task(), AgentRunInput, _default_checkpointer(), outbox_sweeper_task(), run_worker(), RunClaim (+1 more)

### Community 34 - "qa_agentos/main.py"
Cohesion: 0.16
Nodes (7): api_error_handler(), setup_logging(), health_check(), lifespan(), get_dispatcher(), set_dispatcher(), close_redis()

### Community 36 - "ToolDispatcher"
Cohesion: 0.11
Nodes (8): ToolDefinition, ToolDispatcher, ToolExecutionContext, ToolExecutionResult, test_failed_tool_step_is_retried_by_next_attempt_with_history(), test_tool_side_effect_not_repeated_across_attempts(), test_attempt_counts_and_latency_recorded(), test_permanent_failure_not_retried()

### Community 37 - "Web TypeScript Config"
Cohesion: 0.11
Nodes (18): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+10 more)

### Community 38 - "Settings"
Cohesion: 0.16
Nodes (3): Settings, test_settings_fails_fast_on_hatchet_missing_token(), test_settings_passes_with_inline_dispatcher()

### Community 39 - "M3 — Run Reliability, Recovery & Hardening"
Cohesion: 0.12
Nodes (14): NonRetryableException, 10. Test coverage, 1. Guarantees, 2. Run lifecycle and state transitions, 4. Checkpoint strategy, 5. Failure classification, 6. Stale-run recovery (operations), 8. Artifacts (+6 more)

### Community 42 - "Hatchet Server Env"
Cohesion: 0.12
Nodes (16): HATCHET_DATABASE_URL, SERVER_AUTH_COOKIE_DOMAIN, SERVER_AUTH_COOKIE_SECRETS, SERVER_DATABASE_URL, SERVER_ENCRYPTION_JWT_PRIVATE_KEYSET_FILE, SERVER_ENCRYPTION_JWT_PUBLIC_KEYSET_FILE, SERVER_ENCRYPTION_MASTER_KEYSET_FILE, SERVER_POSTGRES_DATABASE (+8 more)

### Community 43 - "Test-Case Coverage Schema"
Cohesion: 0.15
Nodes (15): additionalProperties, properties, required, type, items, type, type, coverage_analysis (+7 more)

### Community 44 - "Test-Case Output Schema"
Cohesion: 0.13
Nodes (15): additionalProperties, required, output_schema, additionalProperties, properties, required, $schema, title (+7 more)

### Community 45 - ".chat_completion"
Cohesion: 0.27
Nodes (5): CompletionResult, invalid(), _optional_int(), _parse_usage(), StructuredOutputSpec

### Community 47 - "projects/router.py"
Cohesion: 0.30
Nodes (8): APIKey, create_project(), create_project_api_key(), list_projects(), APIKeyCreate, APIKeyResponse, ProjectCreate, ProjectResponse

### Community 48 - "Test-Case Generator Agent"
Cohesion: 0.14
Nodes (13): allowed_tools, description, kind, name, parameters, max_repairs, max_steps, temperature (+5 more)

### Community 49 - "Artifacts Service"
Cohesion: 0.24
Nodes (3): Artifact, ArtifactsService, get_storage_client()

### Community 50 - "sync.py"
Cohesion: 0.24
Nodes (4): utc_now(), RegistryImmutabilityError, sync_registries(), SyncResult

### Community 52 - "Dev Compose Overrides"
Cohesion: 0.16
Nodes (13): build, command, volumes, context, dockerfile, profiles, NEXT_PUBLIC_API_URL, build (+5 more)

### Community 53 - "Web App Layout"
Cohesion: 0.22
Nodes (7): metadata, RootLayout(), AuthProvider(), DevToolbar(), TwentyFirstToolbar, nextConfig, next

### Community 54 - "Test-Case Input Schema"
Cohesion: 0.15
Nodes (13): description, type, input_schema, additionalProperties, properties, required, $schema, title (+5 more)

### Community 55 - "sanitize_payload"
Cohesion: 0.27
Nodes (4): sanitize_payload(), _sanitize_string(), test_error_message_secret_redaction(), test_metadata_and_payload_sanitization_redaction()

### Community 57 - "Model Registry YAML"
Cohesion: 0.15
Nodes (12): models, capabilities, context_window, cost_input_per_1m, cost_output_per_1m, gateway_model_key, max_output_tokens, name (+4 more)

### Community 58 - "typing"
Cohesion: 0.09
Nodes (8): upgrade(), downgrade(), upgrade(), upgrade(), EnqueueDispatcher, utc_now(), publish_run_event(), RunEventPayload

### Community 60 - "test_import_boundaries.py"
Cohesion: 0.27
Nodes (3): _get_imports_from_file(), test_gateway_client_uses_httpx_only(), test_no_provider_sdk_imports_in_runtime()

### Community 61 - "env.py"
Cohesion: 0.33
Nodes (5): do_run_migrations(), include_object(), run_async_migrations(), run_migrations_offline(), run_migrations_online()

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
Cohesion: 0.18
Nodes (10): api_base, api_key, mock_response, model, model_list, litellm_params, model_name, router_settings (+2 more)

### Community 66 - "Root Package JSON"
Cohesion: 0.20
Nodes (10): devDependencies, typescript@^5.7.2, typescript, name, private, scripts, build:web, dev:web (+2 more)

### Community 67 - "Run Cancellation Tests"
Cohesion: 0.17
Nodes (4): get_db(), cleanup_database_after_each_runs_test(), test_cancel_running_run_stops_between_nodes(), test_idempotency_concurrent_submissions_postgres()

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

### Community 80 - "Run Launcher Page"
Cohesion: 0.39
Nodes (5): RunLauncherInner(), RunLauncherPage(), AgentItem, RunLauncherForm(), RunLauncherFormProps

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

### Community 86 - "tracing.py"
Cohesion: 0.15
Nodes (4): get_tracer(), NoOpTracer, reset_tracer(), test_noop_tracer_when_unconfigured_honest_none()

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

### Community 99 - "hash_password"
Cohesion: 0.27
Nodes (4): bootstrap_demo_data(), _run_bootstrap(), hash_password(), test_auth_flow_and_api_keys()

### Community 100 - "test_timed_out_non_idempotent_tool_attempted_once"
Cohesion: 0.33
Nodes (3): test_timed_out_idempotent_tool_retries_within_bounds(), slow_func(), test_timed_out_non_idempotent_tool_attempted_once()

### Community 101 - "worker"
Cohesion: 0.25
Nodes (8): context, dockerfile, worker, build, container_name, entrypoint, restart, volumes

### Community 102 - "Test-Case Steps"
Cohesion: 0.67
Nodes (3): steps, items, type

### Community 103 - "Test-Case Enum Fields"
Cohesion: 0.67
Nodes (3): type, enum, type

### Community 115 - "hatchet-migrate"
Cohesion: 0.29
Nodes (7): DATABASE_URL, container_name, environment, image, restart, environment, hatchet-migrate

### Community 117 - "hashlib"
Cohesion: 0.20
Nodes (3): compute_input_hash(), test_migration_0002_preserves_m0_data(), test_prompt_hash_matches_exact_source_file()

## Knowledge Gaps
- **29 isolated node(s):** `next/core-web-vitals`, `qa-agentos`, `qa-agentos-api`, `graphify Skill Trigger (/graphify)`, `graphify Knowledge Graph Usage Rules` (+24 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 986 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **30 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Run` connect `Durable Agent Run Execution` to `workers/main.py`, `uuid`, `HatchetDispatcher`, `AgentVersion`, `Run Cancellation Tests`, `Tool Dispatch Tests`, `test_m3_reliability.py`, `test_audit_regressions.py`, `Runs API Tests`, `RunStateManager`, `Runs API Router`, `Artifacts Service`, `Run Service & Dispatcher Interface`, `Hatchet Dispatcher`, `utc_now`, `typing`, `RunExecutor`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **Are the 77 inferred relationships involving `Run` (e.g. with `ApprovalsService` and `ArtifactsService`) actually correct?**
  _`Run` has 77 INFERRED edges - model-reasoned connections that need verification._
- **What connects `next/core-web-vitals`, `qa-agentos`, `qa-agentos-api` to the rest of the system?**
  _29 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `LangGraph Runtime Nodes` be split into smaller, more focused modules?**
  _Cohesion score 0.07067307692307692 - nodes in this community are weakly interconnected._
- **Why does `ToolDispatcher` connect `ToolDispatcher` to `LangGraph Runtime Nodes`, `workers/main.py`, `Tool Dispatch Tests`, `AgentVersion`, `test_timed_out_non_idempotent_tool_attempted_once`, `Tool Dispatcher Errors`, `.__init__`, `RunExecutor`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **Are the 39 inferred relationships involving `RunExecutor` (e.g. with `InlineDispatcher` and `AgentDefinition`) actually correct?**
  _`RunExecutor` has 39 INFERRED edges - model-reasoned connections that need verification._
- **Should `uuid` be split into smaller, more focused modules?**
  _Cohesion score 0.056692242114237 - nodes in this community are weakly interconnected._