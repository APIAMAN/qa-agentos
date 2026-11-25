# M3 — Bug Analyzer, Reports and Eval Gates: execution plan

Source of truth: `docs/research/QA_AGENTOS_ARCHITECTURE.md` §U (roadmap) and §V (acceptance criteria).
The already-delivered reliability work is **M3-R** (`docs/m3/M3_RELIABILITY.md`); M3 reuses it and
does not modify it.

Statuses: `Backlog` · `Ready` · `In Progress` · `Blocked` · `In Review` · `Done`.
A task is `Done` only when its acceptance checks have actually been run and passed.

## Acceptance criteria (§V, M3)

| # | Criterion | Covered by |
|---|---|---|
| AC1 | A known log fixture produces a report with correct severity classification on ≥ 9/10 golden cases | T4, T5, T6, T9, T10 |
| AC2 | A duplicate bug submission links to the existing cluster (pgvector similarity above threshold) rather than creating a new draft | T7, T8 |
| AC3 | Reports render with embedded artifacts and a stable, shareable URL; JSON export validates against the report schema | T1, T2, T3, T9 |
| AC4 | A PR that degrades the analyzer's G-Eval faithfulness score below threshold fails the `agent-evals` CI job | T10, T11 |

## Decisions

| # | Decision | Status |
|---|---|---|
| D1 | Embedding model/provider | **Done**: Google Gemini `gemini-embedding-001`, 1536 dims, `SEMANTIC_SIMILARITY`, via LiteLLM; verified live; threshold 0.95; HNSW index (migration 0006). Enabling in a deployment is an operator decision |
| D2 | Pinned G-Eval judge model | **Open**: proposed `gemini-2.5-flash-lite` is 404 for this account; `gemini-3.5-flash-lite` verified reachable (preflight); needs approval + verified price |
| D3 | CI secrets / paid runs | **Decided (zero budget)**: GitHub Actions deterministic only, no secrets, live eval refused under CI; real-model evaluation is local (`evals/run.py --allow-live`) |
| D4 | Golden fixture source | **Decided**: synthetic, sanitized fixtures first; real sanitized examples later |

## Tasks

| # | Task | Depends on | Status |
|---|---|---|---|
| T1 | Report JSON: schema, builder, `GET /v1/runs/{id}/report` | — | Done |
| T2 | Report export: HTML renderer, versioned report artifacts, `reports` table, `POST /v1/runs/{id}/report:export` | T1 | Done |
| T3 | Console report page `/runs/[id]/report` | T1 | Done |
| T4 | `log.parse` deterministic tool (worker-injected implementation) | — | Done |
| T5 | Golden datasets v1 (synthetic, sanitized log bundles + labels) | T4 | Done |
| T6 | Bug Analyzer agent (YAML + prompt) | T4 | Done |
| T7 | Embeddings gateway (`/v1/embeddings`, `embedder` alias, cost recording, fake embedder for tests) | D1 | Done |
| T8 | Bug store and duplicate detection (`bug_clusters`, `bug_reports` with vector column, post-run sink) | T6, T7 | Done |
| T9 | Bug section in the report | T2, T8 | Done |
| T10 | Eval harness (severity accuracy, G-Eval faithfulness, thresholds, keyless self-test) | T5, T6, D2 | In Review (local live AC1/AC4 blocked: agent model unavailable, D2 open) |
| T11 | `agent-evals` CI workflow (path-filtered) | T10, D3 | In Review (deterministic-only by D3; CI run on GitHub not yet observed) |
| T12 | Docs and ADR notes | all | Done |

Order: T1 → T2 → T3, with T4 → T5/T6 in parallel; T7 (after D1) → T8 → T9; T10 → T11.

### T1 — Report JSON

**Problem.** A run's evidence is spread over `runs`, `run_events`, `run_steps`, `model_calls`,
`approvals` and `artifacts`; there is no single, schema-validated report of a run.

**Outcome.** `GET /v1/runs/{id}/report` returns one JSON document for any run of any agent
(succeeded, failed, cancelled, retried or still in progress), validated against a versioned JSON
Schema before it is returned.

**Files.** New `apps/api/src/qa_agentos/modules/reports/` (`report.schema.json`, `service.py`,
`router.py`); `main.py` registers the router; new `apps/api/tests/test_reports_api.py`.

**Acceptance checks.**
- Report contains run summary, verdict, status timeline, step timeline (every attempt), model
  usage and cost, approvals, artifact references, and error details for failed runs.
- Every returned report validates against `report.schema.json`.
- Cost and token totals equal the sums over `model_calls`.
- Retried runs show each attempt's steps and the `attempt.retry` / `attempt.resume` history.
- A run of another project returns 404; an API key asking for another project returns 403;
  missing `runs:read` scope is rejected.
- Ruff, Mypy and the full pytest suite pass; M3-R files untouched.

### T2–T12

Defined in the approved M3 plan; acceptance checks are recorded here when each task starts.

## Progress log

| Date | Task | Result |
|---|---|---|
| 2026-11-25 | Plan | Created from the approved M3 plan |
| 2026-11-25 | T1 | Implemented (`modules/reports/`, router registered in `main.py`, `tests/test_reports_api.py`). Verified: report tests 10/10 (SQLite + PostgreSQL); full backend suite 204 passed / 0 failed (`uv run --locked pytest apps/api/tests` with the HANDOFF test environment); Ruff and Mypy pass; Ponytail review 2 findings, both fixed. All T1 acceptance checks met; awaiting review before T2. |
| 2026-11-25 | T1 | **Approved by Aman** on the verification above; status set to Done. Not yet committed. |
| 2026-11-25 | T2, T3 | Wave 1. T2: `reports` table (migration 0004), HTML renderer (escaped, CSP, no scripts), `POST /v1/runs/{id}/report:export` (content-hash dedupe: unchanged evidence returns the existing version), `GET /v1/runs/{id}/reports[/{version}]`; embedded text artifacts redacted. T3: `/runs/[id]/report` page + link from run page; `?version=N` renders that stored export. Verified: report tests 17/17, migration drift 3/3, Ruff, Mypy; frontend typecheck/lint/build pass (node:20, pnpm 9.15.9). |
| 2026-11-25 | T4, T5, T6 | Wave 2. T4: deterministic `log.parse` (runtime/tools/builtin/logparse.py; Python/JVM/Node/Go/JSON-lines/leveled/access logs; redaction of keys, tokens, JWTs, passwords, emails, card numbers; stable `sig_` signatures ignoring ids/timestamps/line numbers; 2 MiB artifact / 64 KiB inline caps; MinIO range read) registered in tools.yaml; storage-aware implementation injected via new optional `RunExecutor(tool_implementations=...)` (modules/bugs/tools.py), project-scoped artifact reads. T5: 14 synthetic sanitized fixtures + dataset.yaml (hand labels, frozen signatures, duplicate and must-not-merge pairs). T6: agents/bug-analyzer.yaml + prompts/bug-analyzer/1.0.0.md. Found pre-existing gap: registry sync never sets ToolDefinition.required_permission (DB default "member"), so the agent declares `member`. Verified: 73 tests (parser golden x14, redaction x10, dispatcher, end-to-end agent on PostgreSQL incl. no raw secret reaching the model, registry, export, existing dispatcher suite); Ruff, Mypy pass. |
| 2026-11-25 | T7, T8, T9 | Wave 3. T7: provider-independent `Embedder` (runtime/gateway/embeddings.py): `GatewayEmbedder` via LiteLLM `/v1/embeddings` (typed errors, timeout, input validation, L2-normalised, dimension check, cost from config), deterministic `HashingEmbedder` for tests; settings EMBEDDING_* + DUPLICATE_SIMILARITY_THRESHOLD; LiteLLM alias `embedder` → candidate nvidia/nv-embed-v1. Benchmark and dimension lock BLOCKED (no provider credentials; D1 open), so T7 stays In Review. Finding: nv-embed-v1 is 4096-dim, above pgvector's 2000-dim ANN index limit, so the column is untyped `vector` + model/dimension columns (exact scan per project+model; ANN index deferred to D1). T8: `bug_clusters`/`bug_reports` (migration 0005), exact signature → embedding (threshold + structural guard) → new cluster, match evidence, grounding against this run's log.parse, idempotent per run, race-safe first sighting, severity escalation, embedding outage degrades to exact matching; default `run_output_sink` (modules/runs/output.py) wired into worker + inline dispatcher. T9: report schema 1.1 optional `bug` section (cluster, related bugs), HTML + console. Verified: full backend suite 267 passed / 0 failed (HANDOFF env, worker stopped); Ruff, Mypy pass. |
| 2026-11-25 | T10, T11, T12 | Wave 4-5. T10: `evals/` separate uv project + lockfile (DeepEval 4.3.5 kept out of `uv.lock`; adding it to the workspace would have downgraded `click` 8.5.0 -> 8.3.3), deterministic metrics (severity, category, signature, grounding, duplicate precision/recall via production matching), G-Eval faithfulness via a LiteLLM judge alias, modes reference/replay/live (live runs the real agent in-process with a spend cap), exit codes 0/1/2 (an unrun mandatory gate is never a pass), embeddings benchmark for D1. T11: `.github/workflows/agent-evals.yml` (deterministic job always; live job opt-in via `AGENT_EVALS_LIVE`, secrets, judge alias check, spend cap, timeout, concurrency, no forks); actionlint clean; deterministic job steps re-run in a clean Linux container: pass. T12: docs/m3/BUG_ANALYZER.md, docs/m3/EVALS.md, ADR-003/004 notes, dossier status note, HANDOFF, .env.example. |
| 2026-11-25 | Review | Ponytail review of T2-T12: 4 findings, all fixed: embedding HTTP call no longer runs while the run row is locked (bug store split into prepare/record; PostgreSQL lock test with negative control), JSON-lines time window linear, judge and test simplified. |
| 2026-11-25 | Final verification | Backend 269 passed / 0 failed (HANDOFF env, worker stopped); Ruff + Mypy (73 files) pass; `uv sync --locked` unchanged (118 packages); migration drift pass; 0004/0005 upgrade -> downgrade(0003) -> upgrade cycle clean; evals 11 passed; reference gate exit 0; degraded replay exit 1; frontend typecheck/lint/build pass; worker rebuilt: live Hatchet suite Tests 1-3 PASS, worker kill mid-run -> attempt.retry -> succeeded; real MinIO round trip (log.parse range read, report export JSON+HTML, download, dedupe, presigned URL) pass. No commit, no push. |

## Acceptance evidence (M3)

| # | Status | Evidence | Missing for full sign-off |
|---|---|---|---|
| AC1 | **NOT VERIFIED (real model)** | Severity gate implemented and mandatory (min 0.90 = 9/10); dataset of 14 labeled cases; gate fails on degraded outputs (exit 1) | A live run with a real model (needs D3 credentials, D2 judge not required for AC1) |
| AC2 | **PASS (fake embedder + real Gemini on reference outputs)** | Exact duplicate 01/02 links by signature; near-duplicate 03/04 links by embedding with recorded evidence; 05/14 never merged; isolation, idempotency, concurrency, outage tests on PostgreSQL + pgvector | Threshold calibration with the D1 model (benchmark script ready) |
| AC3 | **PASS** | Live JSON validated against schema 1.1; versioned JSON + HTML export with embedded, redacted artifacts; stable console URL `/runs/{id}/report` (+ `?version=N`); real MinIO round trip | none |
| AC4 | **PARTIAL** | Gate mechanics proven: scripted low G-Eval score -> exit 1; degraded outputs -> exit 1; no judge -> exit 2; workflow linted and deterministic job verified | Live job with real model + judge, and the weakened-prompt negative run (D2, D3) |
| 2026-11-25 | Audit fixes | Approved audit fixes. H1: `EMBEDDINGS_ENABLED` defaults to false (no provider calls or spend until D1; exact-signature matching only; LiteLLM alias kept). M1: optional `EMBEDDING_INPUT_TYPE` passed as `input_type` only when set (request unchanged otherwise). M2 (decision B): run totals stay LLM-only; enabling gateway embeddings without a positive `EMBEDDING_COST_PER_1M_TOKENS` fails settings validation; embedding spend stays in `model_calls`. L1: OutputSink comment corrected. Evals: live duplicate gates report `not_run` (exit 2) when embeddings are disabled; agent-evals live job opts in and requires the cost variable. Docs: BUG_ANALYZER §5 (defaults, input type, cost, D1 alternatives with dimensions and pgvector limits), §8 acceptance status, §9 limitations; EVALS; .env.example. Deferred by decision: orphaned MinIO test objects, isolated `qa_agentos` test DB, L2 tool-permission security follow-up, `graphify-out/2026-11-25/` (not for this commit), `.graphify_python`. T7/T10/T11 stay In Review; AC1 NOT VERIFIED, AC4 PARTIAL. |
| 2026-11-25 | D1 Gemini | Configured LiteLLM alias `embedder` = `gemini/gemini-embedding-001` with Gemini-native `outputDimensionality: 1536` and `taskType: SEMANTIC_SIMILARITY` (LiteLLM 1.52 rejects OpenAI `dimensions` for Gemini and would forward `input_type` verbatim). App defaults: identity `gemini/gemini-embedding-001:SEMANTIC_SIMILARITY`, 1536 dims, no `dimensions`/`input_type` sent; embeddings stay disabled; cost stays required (Gemini standard tier 0.15 USD / 1M input tokens, documented, not defaulted). Verified with a temporary gateway + dummy key: exact request body to `batchEmbedContents`, typed `MODEL_GATEWAY_AUTH_FAILED` -> exact-signature fallback. Live connectivity, benchmark, threshold, precision/recall and real cost: BLOCKED (key not set locally). T7 stays In Review. |
| 2026-11-25 | D1 live check | Real key (local `.env`, never printed): 1536 dims, normalized by app, ~0.7-1 s/call, ~3e-6 USD per 3 short texts (LiteLLM token estimate x 0.15/1M); benchmark: duplicates >= 0.9887, max non-duplicate 0.9291 (structurally incompatible), suggested threshold 0.886; duplicate gates with real embeddings precision 1.0 / recall 1.0; gateway down -> exact-signature fallback. Fixed evals bug: HTTP client closed in a second event loop (benchmark + runner). Embeddings still disabled by default. |
| 2026-11-25 | T7 | Threshold + index. `DUPLICATE_SIMILARITY_THRESHOLD` default 0.95 (initial Gemini calibration: duplicates 1.000 / 0.9887, highest non-duplicate 0.9291); real-Gemini duplicate gates at 0.95: precision 1.0, recall 1.0. Migration `0006_m3_bug_embedding_index`: partial HNSW expression index on `(embedding::vector(1536)) vector_cosine_ops` where `embedding_dimensions = 1536` (additive; column stays untyped; 0006 down/up with a 1536-dim row present keeps the row byte-identical); neighbour query uses the same cast + literal predicate (EXPLAIN shows the index) and `hnsw.iterative_scan = relaxed_order`. Verified: backend 278 passed / 0 failed; M3-R subset 49 passed; evals 12 passed; 0004-0006 cycle clean; Ruff, Mypy, lock pass. Embeddings stay disabled by default. **T7 Done**; T10/T11 stay In Review. |
| 2026-11-25 | Zero-budget evals | Workflow: live job, secrets and gateway removed; deterministic job + canary that live eval is refused under CI. Local live evaluation: `evals/run.py` modes `preflight`/`live`/`ac4`, `--allow-live` required, refused under CI, `--dry-run` (no network), shared `CallBudget` (call limit, spend cap at env-configured prices, pacing, stop on 429/quota, failed requests counted), per-role cost (agent/judge/embedding), `--cases` + multi-`--outputs` replay for small daily quotas, fixed G-Eval steps (1 judge call per case), DeepEval telemetry off, weakened prompt `evals/prompts/bug-analyzer-weakened.md`, LiteLLM `judge` alias. Real preflight: `gemini-2.5-flash` and `gemini-2.5-flash-lite` 404 (no longer available to new users); `gemini-3.5-flash-lite` 200; `gemini-3.8-flash` 503 (high demand). AC1/AC4 not run: agent model + judge need decisions. |
| 2026-11-25 | First real-model run | Free Tier confirmed (AI Studio). `judge` alias -> `gemini-3.5-flash-lite` (preflight 200). 2-case agent comparison with 3.5 Flash-Lite as agent + judge: **not run**, HTTP 400 on Bug Analyzer `response_format` (`type: [string, null]` unsupported by Gemini via LiteLLM 1.52). 3 calls, $0. Fix awaiting approval; T10/T11 stay In Review. |
| 2026-11-25 | Gemini nullable fix | `client.py` converts `type: [X, "null"]` to `type: X, nullable: true` for Gemini only (copy, idempotent) and logs Google's error message redacted (<=500 chars); `tests/test_gateway_gemini_schema.py`. Re-run case 1: request accepted; quality failed (severity/category/signature wrong, faithfulness 0.10 provisional) because the strict output schema on every turn prevents the `log.parse` tool request. 2 calls, ~$0.001 est., $0 Free Tier. Case 2 not run. New blocker awaiting a runtime decision; T10/T11 stay In Review. |
| 2026-11-25 | Tool-turn fix (Option 1) | `nodes.py`: JSON object mode while a tool request is possible, strict schema after tool results / without tools; `state.py` `tool_results_count`; `tests/test_graph_tool_turns.py`. Live 2-case retest (3.5 Flash-Lite agent + judge): both cases correct severity/category/signature, grounding 1.0, faithfulness 1.0 (provisional), log.parse invoked once each; 6 calls, ~$0.0045 est., $0 Free Tier. Not AC1/AC4 evidence; T10/T11 stay In Review. |
