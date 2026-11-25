# HANDOFF: M3 (Bug Analyzer, reports, eval gates)

Branch `feat/m3-reliability`, **uncommitted**. Plan, status and evidence: [docs/m3/PLAN.md](docs/m3/PLAN.md).
Design: [docs/m3/BUG_ANALYZER.md](docs/m3/BUG_ANALYZER.md); evals and CI: [docs/m3/EVALS.md](docs/m3/EVALS.md).
The M3-R reliability handoff follows below and is unchanged.

## M3: what changed

| Area | Files |
|---|---|
| Reports | `modules/reports/` (schema 1.1, builder, HTML, export, versions), migration `0004_m3_reports`, `main.py` (+2 lines) |
| Log parser tool | `runtime/tools/builtin/logparse.py`, `tools.yaml` (`log.parse`), `runtime/tools/dispatcher.py` (+mapping), `runtime/storage.py` (optional `max_bytes` range read) |
| Tool injection | `runtime/executor.py` (optional `tool_implementations`; sink may return extra event fields), `modules/bugs/tools.py`, `workers/main.py`, `modules/runs/dispatcher.py` |
| Agent | `agents/bug-analyzer.yaml`, `prompts/bug-analyzer/1.0.0.md` |
| Embeddings | `runtime/gateway/embeddings.py`, `runtime/gateway/client.py` (`embeddings()`), `core/config.py` (EMBEDDING_*, DUPLICATE_SIMILARITY_THRESHOLD), `deploy/litellm/config.yaml` (`embedder` alias) |
| Bugs | `modules/bugs/{matching,service}.py`, `models/domain.py` (`Report`, `BugCluster`, `BugReport`), migrations `0005_m3_bugs` + `0006_m3_bug_embedding_index`, `modules/runs/output.py` (default output sink), `modules/artifacts/service.py` (optional artifact name) |
| Console | `apps/web/app/runs/[id]/report/page.tsx` (new), `apps/web/app/runs/[id]/page.tsx` (Report link) |
| Evals | `evals/` (own `pyproject.toml` + `uv.lock`), root `pyproject.toml` (`exclude = ["evals"]`), `.gitignore` (`evals/results/`) |
| CI | `.github/workflows/agent-evals.yml` |
| Tests | `test_reports_api.py`, `test_report_export.py`, `test_bug_analyzer.py`, `test_bug_store.py`, `test_bug_report_integration.py`, `test_registry.py` (counts follow the catalog), `evals/tests/test_harness.py` |

## M3: how to verify

Same services and environment as M3-R below (HANDOFF env line, worker stopped during pytest), plus:

```bash
uv run --locked pytest apps/api/tests -q                      # backend incl. M3
uv sync --project evals --locked && (cd evals && uv run --locked pytest -q)
uv run --project evals python evals/run.py --mode reference   # exit 0
pnpm install --frozen-lockfile && pnpm --filter web typecheck && pnpm --filter web lint && pnpm --filter web build
```

## M3: still open

0. Embeddings are disabled by default (`EMBEDDINGS_ENABLED=false`): duplicate detection is exact-signature only.
   Enabling requires `EMBEDDING_COST_PER_1M_TOKENS` > 0 (Gemini: 0.15). Security follow-up (L2): make
   `ToolDefinition.required_permission` a registry field.
1. D1 done: Gemini `gemini-embedding-001` (1536 dims, SEMANTIC_SIMILARITY) via the LiteLLM `embedder` alias,
   verified live; `DUPLICATE_SIMILARITY_THRESHOLD=0.95`; HNSW index migration `0006_m3_bug_embedding_index`.
   Enabling embeddings in a deployment stays an operator decision (`EMBEDDINGS_ENABLED=true`, cost 0.15).
2. Evals: GitHub Actions is deterministic only (D3, zero budget). AC1/AC4 run locally with
   `evals/run.py --mode live|ac4 --allow-live` (docs/m3/EVALS.md §5). Blocked: `gemini-2.5-flash`
   (agent) and `gemini-2.5-flash-lite` (judge) are 404 for this account; approve replacements
   (`gemini-3.8-flash`, `gemini-3.5-flash-lite` reachable) with verified prices.
3. Review and commit. Suggested split: reports (T1-T3), parser/agent/datasets (T4-T6), embeddings/bugs (T7-T9), evals/CI (T10-T11), docs.

---

# HANDOFF — M3 Reliability (for Antigravity)

Branch: `feat/m3-reliability` (from `chore/m1-integration-infra` @ `a215c26`). **Uncommitted** —
nothing has been committed or pushed; review the diff, then commit.

Design and semantics: [docs/m3/M3_RELIABILITY.md](docs/m3/M3_RELIABILITY.md). Read it before
changing the worker, executor, events or approvals.

## What changed (and why)

| Area | Files | Change |
|---|---|---|
| Schema | `alembic/versions/0003_m3_run_reliability.py`, `models/domain.py` | `runs.resume_requested_at`, `runs.hatchet_attempt`, `runs.heartbeat_at`, index `ix_runs_status_heartbeat_at`; `run_steps` unique → `(run_id, step_number, attempt)` |
| Worker | `workers/main.py` | Row-locked claim protocol with DB fencing token; PostgreSQL checkpointer; permanent vs retryable classification; terminal `failed` on permanent error / last retry; reaper wired into the sweeper cron; log context (`run_id`, `attempt`, `hatchet_*`) |
| Recovery | `modules/runs/recovery.py` (new) | `fail_run_if_owned`, `reap_stale_runs` (stale → `EXECUTION_STALE`, lost resume → re-dispatch, unclaimed queued → release) |
| Executor | `runtime/executor.py` | Attempt-scoped step rows, commit before checkpoint, heartbeat, resume-at-END, recursion limit, fenced terminal transitions, injected `OutputSink`, publish-after-commit; fixes uncommitted `INPUT_SCHEMA_INVALID` / `MODEL_RESOLUTION_UNAVAILABLE` failures |
| Events | `runtime/events.py` | Event outbox on the session (published after commit, discarded on rollback); `expected_attempt` fence; `record_attempt` |
| Graph | `runtime/graph/{nodes,builder,checkpointer}.py` | `max_steps` enforced (`MAX_STEPS_EXCEEDED`); `ResolvedModel.from_snapshot`; advisory-locked one-time checkpoint setup |
| Gateway | `runtime/gateway/{errors,resolution}.py` | `retryable` flag; deterministic snapshot loader (no invented `model_id`) |
| Tools | `runtime/tools/dispatcher.py` | Cross-attempt idempotency (completed calls never re-run), commit-on-record, step `attempt` = run attempt, failed-record errors logged |
| Approvals | `modules/approvals/service.py` | `FOR UPDATE` on decisions; durable resume intent; publish after commit |
| Dispatch | `modules/runs/{dispatcher,sweeper}.py` | Tracked background tasks; `hatchet_run_id` written only while `queued`; inline interruption → `EXECUTION_INTERRUPTED` |
| Artifacts | `modules/artifacts/service.py` | MinIO off the event loop; transactional write (`commit=False`); `store_run_output_artifact` sink; failures recorded on the terminal event |
| SSE | `modules/runs/router.py`, `runtime/pubsub.py` | Subscribe-before-snapshot; DB connection released while streaming; `GETDEL` tickets; dev-only Redis 6381 probe gated off in production |
| Web | `apps/web/app/runs/[id]/page.tsx` | Stream no longer reconnects on every status change |
| Config | `core/config.py` | `RUN_STALE_AFTER_SECONDS`, `WORKER_CHECKPOINTER_ENABLED`, `hatchet_max_attempts` |
| Tests | `tests/test_m3_reliability.py` (new, 27), `conftest.py`, `test_hatchet_durable.py`, `test_run_events_sse.py` | See below |
| Docs | `docs/m3/M3_RELIABILITY.md` (new), `docs/adr/ADR-002-langgraph-runtime.md` | Reliability semantics; ADR implementation note |

Contract changes (intentional): a retried attempt writes its **own** `run_steps` rows instead of
overwriting attempt 1 (`test_worker_crash_recovery_resumes_from_checkpoint` updated accordingly);
tool `RunStep.attempt` now means run attempt (internal tool retries stay in
`ToolExecutionResult.attempt_count` / `error.tool_attempts`).

Sign-off additions: checkpoint fence (`runtime/graph/checkpointer.py::fenced_checkpointer`, wired in
`runtime/executor.py`), `ToolExecutionContext.idempotency_key`, `uv.lock` metadata fix (one line:
`hatchet-sdk` specifier `>=0.30.0` -> `==1.42.1`, no package changes), live suite Test 3 rewritten.

## How to verify

```bash
docker compose -p qa_p6_test -f deploy/docker-compose.test.yml up -d postgres redis litellm
docker compose -f deploy/docker-compose.yml up -d minio minio-init
export PYTHONPATH=apps/api/src   TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5434/qa_agentos_test   DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5434/qa_agentos   REDIS_URL=redis://localhost:6380/0 LITELLM_ENDPOINT=http://localhost:4001 MINIO_ENDPOINT=localhost:9000
uv sync --locked --all-packages --all-extras
uv run --locked ruff check apps/api
uv run --locked mypy apps/api/src
docker stop p6test_worker   # its cron reaper shares qa_agentos_test and would race the reaper tests
uv run --locked pytest apps/api/tests -q
docker start p6test_worker
```

Live Hatchet suite (inside the compose network; rebuild the worker after code changes):

```bash
docker compose -p qa_p6_test -f deploy/docker-compose.test.yml build worker
docker compose -p qa_p6_test -f deploy/docker-compose.test.yml up -d --no-deps --force-recreate worker
docker compose -p qa_p6_test -f deploy/docker-compose.test.yml run --rm --no-deps -T --entrypoint sh worker   -c 'export HATCHET_CLIENT_TOKEN="$(cat /hatchet/config/token.txt)" && cd /app && python tests/live_integration_suite.py'
```

Timeout / exhaustion / reaper scenarios were run with a scratch compose override that set
`HATCHET_TASK_TIMEOUT_MINUTES=1`, `HATCHET_TASK_RETRIES=0|1|2`, `RUN_STALE_AFTER_SECONDS=90`, an
unreachable `LITELLM_ENDPOINT`, and the agent parameter `debug_delay_seconds` (honoured only outside
production, on fencing attempt 1). Results are in `docs/m3/M3_RELIABILITY.md` section 11.

Frontend (no local Node; CI uses Node 20 + pnpm 9):
`pnpm install --frozen-lockfile && pnpm --filter web typecheck && pnpm --filter web lint && pnpm --filter web build`.

## Still to do

1. Review and commit (nothing is committed). Suggested split: (a) `uv.lock` metadata fix,
   (b) M3 implementation + migration + tests, (c) docs.
2. Optional hardening: transactional checkpoint fence (custom saver) to close the millisecond
   check-then-write gap; a dedicated timeout error code for last-attempt timeouts.
3. Business decision: LiteLLM provider fallback (see M3 doc, Known limitations).
4. Metrics once a backend is chosen; consider automating the live scenarios in a CI job with the
   Hatchet stack.
5. Re-baseline the roadmap: the research doc still lists M3 as Bug Analyzer.
6. `next lint` is deprecated (Next 16); migrate to the ESLint CLI separately.
