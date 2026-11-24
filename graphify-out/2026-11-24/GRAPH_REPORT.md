# Graph Report - qa-agentos  (2026-11-24)

## Corpus Check
- 131 files · ~80,475 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 12 file(s) not represented in the graph (top: (none) 6, .example 1, .ini 1)

## Summary
- 2023 nodes · 4841 edges · 119 communities (92 shown, 27 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 761 edges (avg confidence: 0.93)
- Token cost: 182,578 input · 0 output

## Graph Freshness
- Built from commit: `8110b202`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- LangGraph Runtime Nodes
- Run Events SSE Streaming
- Architecture Research Doc
- Tool Dispatch Tests
- Domain Models & Bootstrap
- Durable Agent Run Execution
- Tool Registry YAML
- DB Session & Security Core
- gateway/errors.py
- Gateway Client Tests
- Tool Dispatcher Errors
- nodes.py
- Audit Regression Tests
- Runs API Tests
- CI Workflow
- Run Events & Artifact Service
- Runs API Router
- Model Resolution Policy
- Migrations & Test Fixtures
- ADRs & M0 Verification
- Run Service & Dispatcher Interface
- Hatchet Dispatcher
- MinIO Artifact Storage
- Web Dev Dependencies
- Run Executor & Cost Accounting
- Approvals API Router
- Artifacts API Router
- Run Executor Tests
- Web Console Pages
- Agents Catalog API
- Registry Loader
- Registry Tests
- Test-Case Output Fields
- Hatchet Worker Entrypoint
- App Entrypoint & Config
- Inline Dispatcher
- Web TypeScript Config
- Settings & Factories
- Approvals Service
- Model Resolver
- Hatchet Server Env
- Test-Case Coverage Schema
- Test-Case Output Schema
- client.py
- Tracer Protocol
- Outbox Recovery Sweeper
- Test-Case Generator Agent
- Artifacts Service
- Run Intake Tests
- pubsub.py
- Dev Compose Overrides
- Web App Layout
- Test-Case Input Schema
- Trace Secret Redaction
- Spy Tracer Test Double
- Model Registry YAML
- sqlalchemy
- Broken Tracer Tests
- Approvals API Tests
- Migration Drift Tests
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
- test_artifacts_api.py
- Run Launcher Page
- environment
- Test Compose Root
- Test Hatchet Migration
- Compose Volumes
- Langfuse Tracer
- NoOp Tracer
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
- worker
- Test-Case Steps
- Test-Case Enum Fields
- Health Endpoint Test
- ESLint Config
- Graphify Instructions
- Hatchet Setup Script
- Postgres Init Script
- pnpm Workspace
- Root Python Project
- API Python Project
- hatchet-migrate
- test_postgres_integration.py
- compute_input_hash

## God Nodes (most connected - your core abstractions)
1. `Run` - 79 edges
2. `Identity` - 55 edges
3. `ToolDispatcher` - 52 edges
4. `LiteLLMClient` - 51 edges
5. `RunExecutor` - 48 edges
6. `ProviderPolicy` - 44 edges
7. `ModelResolver` - 43 edges
8. `AgentVersion` - 32 edges
9. `RunsService` - 32 edges
10. `resolve_project_id()` - 30 edges

## Surprising Connections (you probably didn't know these)
- `graphify Skill Trigger (/graphify)` --conceptually_related_to--> `graphify Knowledge Graph Usage Rules`  [INFERRED]
  .claude/CLAUDE.md → CLAUDE.md
- `Test Case Generator System Prompt v1.0.0` --implements--> `Declarative Agent Definition (system prompt, I/O JSON Schema, tools, capability models)`  [INFERRED]
  prompts/test-case-generator/1.0.0.md → docs/adr/ADR-003-data-driven-registries.md
- `Local Development Quickstart (make up/migrate/bootstrap/dev-api/dev-web)` --conceptually_related_to--> `Docker Compose Stack (9 services)`  [INFERRED]
  README.md → docs/m0/M0_VERIFICATION_REPORT.md
- `API /health Endpoint` --conceptually_related_to--> `QA AgentOS API (FastAPI Backend Monolith)`  [INFERRED]
  docs/m0/M0_VERIFICATION_REPORT.md → apps/api/README.md
- `M0 Foundation Milestone` --conceptually_related_to--> `M0 Verification Report`  [INFERRED]
  README.md → docs/m0/M0_VERIFICATION_REPORT.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **LLM Model Routing via LiteLLM (NIM primary, Gemini fallback)** — docs_adr_adr_004_litellm_model_gateway_litellm_proxy, docs_adr_adr_004_litellm_model_gateway_capability_slots, docs_adr_adr_005_nvidia_nim_gateway_nvidia_nim, docs_adr_adr_005_nvidia_nim_gateway_gemini_fallback [EXTRACTED 1.00]
- **QA AgentOS Architecture Decision Records** — docs_adr_adr_001_modular_monolith_modular_monolith, docs_adr_adr_002_langgraph_runtime_langgraph_orchestration, docs_adr_adr_003_data_driven_registries_registries_as_data, docs_adr_adr_004_litellm_model_gateway_litellm_proxy, docs_adr_adr_005_nvidia_nim_gateway_nim_fallback [EXTRACTED 1.00]
- **Five launch agents as registry definitions** — docs_research_qa_agentos_architecture_test_case_generator, docs_research_qa_agentos_architecture_bug_analyzer, docs_research_qa_agentos_architecture_api_testing_agent, docs_research_qa_agentos_architecture_self_healing_agent, docs_research_qa_agentos_architecture_visual_a11y_agent, docs_research_qa_agentos_architecture_agent_registry [EXTRACTED 1.00]
- **Agent -> Tool -> Model -> Artifact -> Report chain** — docs_research_qa_agentos_architecture_langgraph_runtime, docs_research_qa_agentos_architecture_tool_registry, docs_research_qa_agentos_architecture_litellm_gateway, docs_research_qa_agentos_architecture_artifact_storage, docs_research_qa_agentos_architecture_report_service [EXTRACTED 1.00]
- **Single Postgres hosts app, Hatchet, LangGraph, Langfuse schemas** — docs_research_qa_agentos_architecture_postgresql, docs_research_qa_agentos_architecture_hatchet, docs_research_qa_agentos_architecture_postgres_checkpointer, docs_research_qa_agentos_architecture_langfuse, docs_research_qa_agentos_architecture_pgvector [EXTRACTED 1.00]

## Communities (119 total, 27 thin omitted)

### Community 0 - "LangGraph Runtime Nodes"
Cohesion: 0.05
Nodes (15): RunTerminalStateError, RunExecutionResult, build_generic_react_graph(), _mask_url(), _normalize_psycopg_url(), postgres_checkpointer(), route_after_generate(), route_after_validate() (+7 more)

### Community 1 - "Run Events SSE Streaming"
Cohesion: 0.07
Nodes (20): seed_agents_catalog(), test_agents_api_authorization_matrix(), test_get_agent_by_slug_inactive(), test_get_agent_by_slug_success(), test_get_agent_by_slug_unknown_returns_404(), test_list_agents_authenticated_user(), test_list_agents_include_inactive(), test_list_agents_unauthorized() (+12 more)

### Community 2 - "Architecture Research Doc"
Cohesion: 0.05
Nodes (53): Architecture Decisions (ADR-001..022), Agent Registry, Agent -> Tool -> Model -> Artifact -> Report flow, API Testing Agent, Approval Service (human-in-the-loop), Content-Addressed Artifact Storage, JWT + API Keys Auth, axe-core Accessibility Engine (+45 more)

### Community 3 - "Tool Dispatch Tests"
Cohesion: 0.09
Nodes (23): ToolExecutionContext, ToolInvocationRequest, _seed_test_environment(), test_attempt_counts_and_latency_recorded(), test_invalid_input_fails_before_execution(), test_invalid_output_produces_tool_output_invalid(), test_missing_permissions_produce_permission_denied(), test_non_json_serializable_output_fails_safely() (+15 more)

### Community 4 - "Domain Models & Bootstrap"
Cohesion: 0.14
Nodes (18): Base, AgentDefinition, AgentVersion, Approval, Artifact, ModelCatalog, Project, RunEvent (+10 more)

### Community 5 - "Durable Agent Run Execution"
Cohesion: 0.09
Nodes (23): Run, execute_agent_run(), create_mock_gateway(), NonRetryableException, test_concurrent_sweepers_skip_locked(), test_duplicate_delivery_on_retry_attempt_ignored(), test_duplicate_delivery_while_preparing_drops_duplicate(), test_duplicate_delivery_while_run_queued_drops_duplicate() (+15 more)

### Community 6 - "Tool Registry YAML"
Cohesion: 0.05
Nodes (40): description, type, additionalProperties, properties, required, $schema, title, type (+32 more)

### Community 7 - "DB Session & Security Core"
Cohesion: 0.10
Nodes (24): get_db(), UnauthorizedError, create_access_token(), decode_access_token(), get_rsa_keys(), hash_api_key(), verify_password(), APIKey (+16 more)

### Community 8 - "gateway/errors.py"
Cohesion: 0.14
Nodes (8): ModelGatewayAuthError, ModelGatewayError, ModelGatewayTimeoutError, ModelGatewayUnavailableError, ModelRateLimitedError, ModelRequestRejectedError, ModelStructuredOutputUnsupportedError, ModelUpstreamError

### Community 9 - "Gateway Client Tests"
Cohesion: 0.09
Nodes (16): ChatMessage, LiteLLMClient, StructuredOutputSpec, ResolvedModel, _dummy_model(), test_endpoint_path_body_and_auth_header(), test_http_429_rate_limited_mapping(), test_http_5xx_upstream_error_mapping() (+8 more)

### Community 10 - "Tool Dispatcher Errors"
Cohesion: 0.10
Nodes (16): ToolDispatcher, ToolExecutionResult, ToolError, ToolExecutionFailedError, ToolImplementationUnsupportedError, ToolInputInvalidError, ToolNotAllowedError, ToolNotFoundError (+8 more)

### Community 11 - "nodes.py"
Cohesion: 0.17
Nodes (12): calculate_model_cost(), execute_tools_node(), finalize_node(), _format_user_prompt(), generate_node(), prepare_node(), repair_node(), _safe_end() (+4 more)

### Community 12 - "Audit Regression Tests"
Cohesion: 0.13
Nodes (14): _create_mock_client(), _seed_context(), test_approval_decision_on_cancelled_run_rejected_with_409(), test_approval_durability_pause_and_resume_without_reexecution(), test_approval_rejection_halts_and_blocks_execution(), test_artifact_security_traversal_and_extensions(), test_cross_project_artifact_access_blocked(), test_duplicate_approval_decision_rejected_with_409() (+6 more)

### Community 13 - "Runs API Tests"
Cohesion: 0.12
Nodes (16): seed_test_harness(), test_cancel_already_terminal_run_is_deterministic(), test_cancel_queued_run(), test_get_run_steps_ordered(), test_idempotency_different_agent_returns_409(), test_idempotency_same_key_different_input_returns_409(), test_idempotency_same_key_same_request_returns_200(), test_post_runs_forbidden_without_runs_create_scope() (+8 more)

### Community 14 - "CI Workflow"
Cohesion: 0.06
Nodes (35): name, runs-on, services, steps, DATABASE_URL, POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER (+27 more)

### Community 15 - "Run Events & Artifact Service"
Cohesion: 0.14
Nodes (6): event_generator(), RunTransitionError, utc_now(), _on_step_completed(), publish_run_event(), RunEventPayload

### Community 16 - "Runs API Router"
Cohesion: 0.12
Nodes (15): cancel_run(), create_events_stream_ticket(), create_run(), get_run(), get_run_steps(), list_runs(), replay_run(), stream_run_events() (+7 more)

### Community 17 - "Model Resolution Policy"
Cohesion: 0.15
Nodes (14): NoModelForCapabilityError, CapabilityRequirement, ProviderPolicy, _rejection_reason(), select_model(), _make_model(), test_changing_priority_changes_selection_without_code_changes(), test_enforces_structured_output_requirement() (+6 more)

### Community 18 - "Migrations & Test Fixtures"
Cohesion: 0.08
Nodes (13): client(), _override_get_db(), db_session(), event_loop(), get_postgres_test_url(), pg_client(), pg_engine(), pg_session() (+5 more)

### Community 19 - "ADRs & M0 Verification"
Cohesion: 0.08
Nodes (25): QA AgentOS API (FastAPI Backend Monolith), Core Platform Domains (Projects, Registries, Model Routing, Runs, Approvals, Artifacts, Reporting), Stateless Execution Workers (Hatchet jobs from PostgreSQL), generic_react Graph Structure, LangGraph interrupt() Human Approval Pause, PostgreSQL Checkpointing, Declarative Agent Definition (system prompt, I/O JSON Schema, tools, capability models), Registry Tables (agent_definitions, agent_versions, tool_definitions, models) (+17 more)

### Community 20 - "Run Service & Dispatcher Interface"
Cohesion: 0.14
Nodes (6): APIError, ForbiddenError, NotFoundError, require_scope(), scope_checker(), RunsService

### Community 21 - "Hatchet Dispatcher"
Cohesion: 0.11
Nodes (6): HatchetDispatcher, main(), start_live_hatchet_worker(), test_live_duplicate_and_fencing(), test_live_sweeper_recovery(), test_live_worker_execution()

### Community 22 - "MinIO Artifact Storage"
Cohesion: 0.11
Nodes (7): ArtifactTooLargeError, InvalidArtifactPathError, MinioStorageClient, sanitize_filename(), StorageError, test_sanitize_filename_prevents_traversal(), _mock_failing_get()

### Community 23 - "Web Dev Dependencies"
Cohesion: 0.10
Nodes (28): devDependencies, @21st-extension/toolbar@0.5.14, @21st-extension/toolbar-next@0.5.14, @21st-extension/toolbar-react@0.5.14, autoprefixer@^10.4.20, eslint@^8.57.0, eslint-config-next@^15.1.0, postcss@^8.4.49 (+20 more)

### Community 24 - "Run Executor & Cost Accounting"
Cohesion: 0.19
Nodes (9): ModelCall, _create_mock_client(), test_cancellation_safe_telemetry_cleanup(), test_idempotent_step_redelivery_preserves_accounting(), test_missing_usage_not_fabricated(), test_repair_loop_aggregates_multiple_model_calls_without_double_counting(), test_trace_persisted_and_tokens_cost_reconciled(), test_tracing_failure_does_not_abort_run() (+1 more)

### Community 25 - "Approvals API Router"
Cohesion: 0.14
Nodes (10): approve_approval(), create_approval(), get_approval(), list_approvals(), reject_approval(), ApprovalCreateRequest, ApprovalDecisionRequest, ApprovalListResponse (+2 more)

### Community 26 - "Artifacts API Router"
Cohesion: 0.12
Nodes (7): create_artifact(), download_artifact(), get_artifact(), list_artifacts(), ArtifactCreateRequest, ArtifactListResponse, ArtifactResponse

### Community 27 - "Run Executor Tests"
Cohesion: 0.16
Nodes (14): RunStep, RunExecutor, ModelResolver, _create_mock_gateway(), _seed_test_project_and_agent(), test_input_validation_failure_transitions_to_failed(), test_postgres_checkpoint_crash_recovery(), test_run_events_and_steps_auditable_persistence() (+6 more)

### Community 28 - "Web Console Pages"
Cohesion: 0.20
Nodes (19): AgentCatalogPage(), AgentDetailResponse, AgentListItem, ApprovalItem, ApprovalsPage(), ArtifactItem, ArtifactsPage(), formatBytes() (+11 more)

### Community 29 - "Agents Catalog API"
Cohesion: 0.16
Nodes (6): get_agent(), list_agents(), AgentDetailResponse, AgentListItem, AgentVersionResponse, AgentsService

### Community 30 - "Registry Loader"
Cohesion: 0.18
Nodes (13): AgentRegistryItem, load_agents_catalog(), load_models_catalog(), load_registry_catalog(), load_tools_catalog(), ModelRegistryItem, RegistryCatalog, RegistryError (+5 more)

### Community 31 - "Registry Tests"
Cohesion: 0.16
Nodes (8): find_registry_root(), test_active_version_immutability_enforced(), test_bootstrap_demo_data_integration(), test_introducing_new_version_preserves_history(), test_invalid_json_schema_fails_validation(), test_prompt_hash_matches_exact_source_file(), test_registry_root_discovery(), test_yaml_parsing_and_schema_validation()

### Community 32 - "Test-Case Output Fields"
Cohesion: 0.10
Nodes (21): description, type, type, description, type, properties, items, type (+13 more)

### Community 33 - "Hatchet Worker Entrypoint"
Cohesion: 0.13
Nodes (7): setup_logging(), agent_run_task(), AgentRunInput, get_hatchet_client(), outbox_sweeper_task(), run_worker(), SweeperInput

### Community 34 - "App Entrypoint & Config"
Cohesion: 0.11
Nodes (6): api_error_handler(), health_check(), lifespan(), get_dispatcher(), RunDispatcher, close_redis()

### Community 37 - "Web TypeScript Config"
Cohesion: 0.11
Nodes (18): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+10 more)

### Community 38 - "Settings & Factories"
Cohesion: 0.13
Nodes (5): Settings, _normalize_base_url(), test_settings_fails_fast_on_hatchet_missing_token(), test_settings_passes_with_inline_dispatcher(), test_noop_tracer_when_unconfigured_honest_none()

### Community 39 - "Approvals Service"
Cohesion: 0.18
Nodes (3): ConflictError, ApprovalsService, RunStateManager

### Community 42 - "Hatchet Server Env"
Cohesion: 0.12
Nodes (16): HATCHET_DATABASE_URL, SERVER_AUTH_COOKIE_DOMAIN, SERVER_AUTH_COOKIE_SECRETS, SERVER_DATABASE_URL, SERVER_ENCRYPTION_JWT_PRIVATE_KEYSET_FILE, SERVER_ENCRYPTION_JWT_PUBLIC_KEYSET_FILE, SERVER_ENCRYPTION_MASTER_KEYSET_FILE, SERVER_POSTGRES_DATABASE (+8 more)

### Community 43 - "Test-Case Coverage Schema"
Cohesion: 0.15
Nodes (15): additionalProperties, properties, required, type, items, type, type, coverage_analysis (+7 more)

### Community 44 - "Test-Case Output Schema"
Cohesion: 0.13
Nodes (15): additionalProperties, required, output_schema, additionalProperties, properties, required, $schema, title (+7 more)

### Community 45 - "client.py"
Cohesion: 0.21
Nodes (6): CompletionResult, invalid(), _optional_int(), _parse_usage(), TokenUsage, ModelResponseInvalidError

### Community 48 - "Test-Case Generator Agent"
Cohesion: 0.14
Nodes (13): allowed_tools, description, kind, name, parameters, max_repairs, max_steps, temperature (+5 more)

### Community 50 - "Run Intake Tests"
Cohesion: 0.18
Nodes (6): set_dispatcher(), create_mock_gateway(), test_end_to_end_execution_repair_failure(), test_end_to_end_execution_succeeded(), test_post_runs_returns_before_blocked_executor_finishes(), test_post_runs_valid_intake_returns_202_promptly()

### Community 51 - "pubsub.py"
Cohesion: 0.23
Nodes (4): get_redis_client(), get_run_events_channel(), subscribe_run_events(), _listener()

### Community 52 - "Dev Compose Overrides"
Cohesion: 0.16
Nodes (13): build, command, volumes, context, dockerfile, profiles, NEXT_PUBLIC_API_URL, build (+5 more)

### Community 53 - "Web App Layout"
Cohesion: 0.22
Nodes (7): metadata, RootLayout(), AuthProvider(), DevToolbar(), TwentyFirstToolbar, nextConfig, next

### Community 54 - "Test-Case Input Schema"
Cohesion: 0.15
Nodes (13): description, type, input_schema, additionalProperties, properties, required, $schema, title (+5 more)

### Community 55 - "Trace Secret Redaction"
Cohesion: 0.20
Nodes (5): GenerationContext, sanitize_metadata(), sanitize_payload(), _sanitize_string(), test_metadata_and_payload_sanitization_redaction()

### Community 57 - "Model Registry YAML"
Cohesion: 0.15
Nodes (12): models, capabilities, context_window, cost_input_per_1m, cost_output_per_1m, gateway_model_key, max_output_tokens, name (+4 more)

### Community 58 - "sqlalchemy"
Cohesion: 0.15
Nodes (9): do_run_migrations(), include_object(), run_async_migrations(), run_migrations_offline(), run_migrations_online(), upgrade(), downgrade(), upgrade() (+1 more)

### Community 60 - "Approvals API Tests"
Cohesion: 0.47
Nodes (7): setup_test_context(), test_approvals_cross_project_isolation(), test_approve_decision_resumes_run(), test_create_approval_and_run_pause(), test_duplicate_decision_conflict(), test_list_approvals_unauthorized(), test_reject_decision_cancels_run()

### Community 61 - "Migration Drift Tests"
Cohesion: 0.20
Nodes (5): include_object(), test_alembic_schema_matches_metadata_postgres(), _check_drift(), test_drift_detector_catches_schema_drift(), test_models_defined_properly()

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

### Community 79 - "test_artifacts_api.py"
Cohesion: 0.40
Nodes (5): setup_test_context(), test_artifact_direct_download_stream(), test_artifacts_cross_project_isolation(), test_create_artifact_and_fetch_presigned_url(), test_list_artifacts_unauthorized()

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
Cohesion: 0.25
Nodes (4): bootstrap_demo_data(), _run_bootstrap(), hash_password(), test_auth_flow_and_api_keys()

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

## Knowledge Gaps
- **29 isolated node(s):** `qa-agentos-api`, `next/core-web-vitals`, `qa-agentos`, `graphify Skill Trigger (/graphify)`, `graphify Knowledge Graph Usage Rules` (+24 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 937 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **27 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Run` connect `Durable Agent Run Execution` to `LangGraph Runtime Nodes`, `Tool Dispatch Tests`, `Domain Models & Bootstrap`, `Audit Regression Tests`, `Runs API Tests`, `Run Events & Artifact Service`, `Run Service & Dispatcher Interface`, `Hatchet Dispatcher`, `Run Executor & Cost Accounting`, `Run Executor Tests`, `Hatchet Worker Entrypoint`, `App Entrypoint & Config`, `Inline Dispatcher`, `Approvals Service`, `Outbox Recovery Sweeper`, `Artifacts Service`, `Run Intake Tests`, `sqlalchemy`, `Approvals API Tests`, `Run Cancellation Tests`, `test_artifacts_api.py`?**
  _High betweenness centrality (0.032) - this node is a cross-community bridge._
- **Are the 61 inferred relationships involving `Run` (e.g. with `ApprovalsService` and `ArtifactsService`) actually correct?**
  _`Run` has 61 INFERRED edges - model-reasoned connections that need verification._
- **What connects `qa-agentos-api`, `next/core-web-vitals`, `qa-agentos` to the rest of the system?**
  _29 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `LangGraph Runtime Nodes` be split into smaller, more focused modules?**
  _Cohesion score 0.05191256830601093 - nodes in this community are weakly interconnected._
- **Why does `RunExecutor` connect `Run Executor Tests` to `LangGraph Runtime Nodes`, `Hatchet Worker Entrypoint`, `App Entrypoint & Config`, `Inline Dispatcher`, `Domain Models & Bootstrap`, `Durable Agent Run Execution`, `DB Session & Security Core`, `Approvals Service`, `Gateway Client Tests`, `Tool Dispatcher Errors`, `Audit Regression Tests`, `Tracer Protocol`, `Run Events & Artifact Service`, `Artifacts Service`, `Hatchet Dispatcher`, `Run Executor & Cost Accounting`?**
  _High betweenness centrality (0.024) - this node is a cross-community bridge._
- **Are the 34 inferred relationships involving `Identity` (e.g. with `get_agent()` and `list_agents()`) actually correct?**
  _`Identity` has 34 INFERRED edges - model-reasoned connections that need verification._
- **Should `Run Events SSE Streaming` be split into smaller, more focused modules?**
  _Cohesion score 0.07256894049346879 - nodes in this community are weakly interconnected._