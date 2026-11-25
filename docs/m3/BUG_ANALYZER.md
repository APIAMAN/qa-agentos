# M3 — Bug Analyzer, reports and duplicate detection

Scope: original roadmap M3 (`docs/research/QA_AGENTOS_ARCHITECTURE.md` §U/§V). Execution plan and
verification log: [PLAN.md](PLAN.md). Evaluation gates: [EVALS.md](EVALS.md). Reliability
semantics the pipeline relies on: [M3_RELIABILITY.md](M3_RELIABILITY.md) (M3-R).

## 1. Flow

```
POST /v1/artifacts (log bundle, type log|text|json)
POST /v1/runs {agent: bug-analyzer, input: {log_artifact_id}}
  └─ worker → RunExecutor (generic runtime, unchanged)
       generate  → model asks for log.parse(artifact_id)
       tool      → log.parse (deterministic, sanitized summary; raw log never reaches the model)
       generate  → bug report JSON (validated against the agent output_schema, repaired ≤ 2×)
       succeeded → run_output_sink (same transaction as the succeeded state)
                     ├─ output artifact  bug-analyzer-<run>.json
                     └─ bug_store        exact signature → embedding (+ structural guard) → new cluster
GET  /v1/runs/{id}/report           schema 1.1 JSON incl. "bug" section
POST /v1/runs/{id}/report:export    immutable JSON + HTML version (artifacts)
/runs/{id}/report                   console page (stable URL; ?version=N = that export)
```

Nothing here is a second runtime: the agent is YAML (`agents/bug-analyzer.yaml`,
`prompts/bug-analyzer/1.0.0.md`), the tool is a registry entry (`tools.yaml`), and both run through
the existing dispatcher, gateway, approvals, retries, fencing and reaper.

## 2. `log.parse` (deterministic tool)

Code: `runtime/tools/builtin/logparse.py` (pure) and `modules/bugs/tools.py` (artifact input).

| Aspect | Behaviour |
|---|---|
| Formats | Python tracebacks, JVM traces (`Caused by`), Node errors, Go panics, JSON lines, leveled text, HTTP access logs (5xx) |
| Output | `signatures[]` (id, kind, exception_type, normalized message, level, count, first/last seen, top 5 frames, 3 samples), `primary_signature`, counts, `time_window`, `redactions`, `truncated`; schema in `tools.yaml` |
| Signature | `sig_` + 16 hex of SHA-256(exception type, normalized message, top 3 frames). Ids, UUIDs, numbers, timestamps, line/column numbers are normalized away, so recurrences match |
| Redaction | Private keys, JWTs, bearer/basic tokens, AWS/Stripe/GitHub/Slack/NVIDIA/Google/QA-AgentOS keys, URL passwords, `password=`/`token=`/`api_key:`/`cookie` values, emails, card-like numbers. Applied before parsing, so samples and messages are clean |
| Limits | artifact ≤ 2 MiB parsed (MinIO range read; larger objects are never read whole), inline `text` ≤ 64 KiB, ≤ 25 signatures, lines clipped at 500 chars |
| Isolation | `artifact_id` is resolved only inside the run's project; "not found" and "other project" give the same error |
| Errors | Bad input → `TOOL_INPUT_INVALID` (not retried); storage errors are transient (retried by the dispatcher policy) |

The artifact-capable implementation is injected with the new optional
`RunExecutor(tool_implementations=...)`; the worker and the inline dispatcher pass
`make_tool_implementations(session_factory)`. `runtime/` imports nothing from `modules/`.

## 3. Bug Analyzer agent

`agents/bug-analyzer.yaml`: input `{log_artifact_id, context?, environment?}`; output `title`,
`severity` (`critical|high|medium|low`), `category`, `summary`, `root_cause`, `primary_signature`,
`evidence[{signature, detail}]`, `repro_steps`, `affected_component`, `confidence`. Allowed tools:
`log.parse@1.0.0`. Parameters: `temperature 0`, `max_steps 4`, `max_repairs 2`,
`output_sinks: [bug_store]`. The prompt carries the severity rubric and forbids citing signature ids
that `log.parse` did not return.

Permissions: `artifact:read` and `member`. Registry sync does not set
`ToolDefinition.required_permission` (DB default `member`), so every tool-using agent must hold it
(pre-existing gap, see limitations).

## 4. Bug store and duplicate detection

Tables (migration `0005_m3_bugs`): `bug_clusters` (title, severity, category, `founding_signature`
unique per project, `report_count`, first/last seen) and `bug_reports` (one per run, unique
`run_id`; report fields, grounded signature, exception type, frames, `match` evidence, `embedding`).

Matching (`modules/bugs/matching.py`, shared with the eval harness):

1. **Exact signature**: a grounded primary signature already reported in the project → that cluster.
2. **Embedding**: nearest earlier reports of the *same embedding model and dimension* in the project;
   accepted only if cosine ≥ `DUPLICATE_SIMILARITY_THRESHOLD` **and** structurally compatible (same
   exception type, shared stack frames when both have frames, same category for frameless errors).
   Similar wording alone never merges bugs.
3. Otherwise a new cluster.

`bug_reports.match` records `method`, `similarity`, `threshold`, `matched_bug_id`, `reasons`, the
rejected near-misses and the embedding model/status, so every link can be explained.

Guarantees:

- **Grounding**: the primary signature is kept only if this run's `log.parse` produced it; an
  invented signature is dropped for matching and the report is flagged `grounded: false`.
- **Idempotency**: written in the run's transaction (savepoint) together with the `succeeded`
  state; `run_id` is unique, so retries/redeliveries never create a second report.
- **Concurrency**: two first sightings of a new signature race on the unique founding signature;
  the loser joins the winner's cluster (tested with two real connections).
- **Embeddings off (default)**: exact-signature matching only, `embedding_status: disabled`.
- **Degradation**: if embeddings fail, matching falls back to exact signatures and records
  `embedding_status: failed:<code>` plus a failed `model_calls` row. The run still succeeds.
- **Severity**: a cluster takes the highest severity of its reports.
- Outcome on the run's `succeeded` event: `bug_status`, `bug_id`, `bug_cluster_id`, `bug_match`,
  `bug_grounded` (and `artifact_status`).

## 5. Embeddings (T7) and decision D1

`runtime/gateway/embeddings.py`: `Embedder` protocol; `GatewayEmbedder` (LiteLLM `/v1/embeddings`,
typed gateway errors, timeout, input validation, L2-normalised, dimension check, cost from
`EMBEDDING_COST_PER_1M_TOKENS`, a `model_calls` row per call); `HashingEmbedder` (deterministic,
offline; tests and evals only, never selected unless `EMBEDDING_PROVIDER=fake`).

**Embeddings are disabled by default.** With the shipped configuration no embedding provider is
called and nothing is spent; duplicate detection uses exact error signatures only (the report's
`match.embedding_status` is `disabled`). A deployment opts in by setting `EMBEDDINGS_ENABLED=true`
and a price; the alias exists but is never called while embeddings are disabled.

### Decision D1: Google Gemini through LiteLLM

| Item | Value |
|---|---|
| Model | `gemini-embedding-001` (LiteLLM model `gemini/gemini-embedding-001`, Google AI Studio API) |
| Dimensions | 1536 (Gemini `outputDimensionality`; ANN-indexable in pgvector, limit 2000 for `vector`) |
| Task type | `SEMANTIC_SIMILARITY` (Gemini `taskType`; bug reports are compared symmetrically) |
| Gateway alias | `embedder` in `deploy/litellm/config.yaml`, key from `os.environ/GEMINI_API_KEY` |
| App identity | `EMBEDDING_MODEL_IDENTITY=gemini/gemini-embedding-001:SEMANTIC_SIMILARITY`, `EMBEDDING_DIMENSIONS=1536` |
| Price | 0.15 USD per 1M input tokens, standard paid tier (Gemini API pricing page, checked 2026-11-25); 0.075 batch; a free tier exists. Not defaulted in code |

How the parameters reach Google (verified against the pinned `ghcr.io/berriai/litellm:main-v1.52.0`):

- LiteLLM 1.52 has no Gemini mapping for OpenAI's `dimensions` (it raises `UnsupportedParamsError`
  or, with `drop_params`, silently drops it) and would forward an unknown `input_type` field to
  Google. So the app sends **neither** (`EMBEDDING_REQUEST_DIMENSIONS=false`, `EMBEDDING_INPUT_TYPE`
  unset) and the alias carries the Gemini-native fields, which LiteLLM forwards verbatim.
- Observed request (temporary gateway with this config and a dummy key):
  `POST .../v1beta/models/gemini-embedding-001:batchEmbedContents` with
  `{"requests":[{"model":"models/gemini-embedding-001","content":{"parts":[{"text":"..."}]},"outputDimensionality":1536,"taskType":"SEMANTIC_SIMILARITY"}]}`.
  Google rejected the dummy key; the app received the typed `MODEL_GATEWAY_AUTH_FAILED` and the
  bug store falls back to exact signatures.
- Gemini returns vectors below 3072 dimensions un-normalized; `GatewayEmbedder` L2-normalizes them.
- **Never run LiteLLM with `--detailed_debug` and a real key**: its debug log prints `api_key`.
- `test_gemini_embedding_alias_and_app_defaults_match` pins alias and app defaults together.

| Setting | Default | Meaning |
|---|---|---|
| `EMBEDDINGS_ENABLED` | `false` | `true` = embedding similarity in addition to exact signatures |
| `EMBEDDING_PROVIDER` | `gateway` | `gateway` or `fake` (tests/evals only) |
| `EMBEDDING_GATEWAY_MODEL_KEY` | `embedder` | LiteLLM alias |
| `EMBEDDING_MODEL_IDENTITY` | `gemini/gemini-embedding-001:SEMANTIC_SIMILARITY` | Stored with every vector |
| `EMBEDDING_DIMENSIONS` | `1536` | Must match the alias' `outputDimensionality`; other sizes are rejected |
| `EMBEDDING_REQUEST_DIMENSIONS` | `false` | Keep false for Gemini via LiteLLM 1.52 |
| `EMBEDDING_INPUT_TYPE` | unset | Sent as `input_type` only when set (NVIDIA-style models); keep unset for Gemini |
| `EMBEDDING_TIMEOUT_SECONDS` | `15` | |
| `EMBEDDING_COST_PER_1M_TOKENS` | `0` | **Required > 0** when enabled with the gateway (Gemini: `0.15`); startup fails otherwise |
| `DUPLICATE_SIMILARITY_THRESHOLD` | `0.95` | Initial Gemini calibration (benchmark below) |

Changing the model, dimension or task type changes the vectors: use a new
`EMBEDDING_MODEL_IDENTITY` and re-embed existing reports.

### Enabling locally (after a key is available)

```bash
# 1. key in the git-ignored .env (or the shell environment); never commit it, never paste it in chat
echo "GEMINI_API_KEY=<your key>" >> .env          # run yourself, in your own terminal
# 2. LiteLLM picks it up (dev compose passes GEMINI_API_KEY through)
docker compose -f deploy/docker-compose.yml up -d litellm
# 3. app settings (worker/API environment)
EMBEDDINGS_ENABLED=true EMBEDDING_COST_PER_1M_TOKENS=0.15
```

### Cost accounting (decision B)

- Each embedding call is a `model_calls` row (capability `embedding`) with tokens and cost; run
  reports include it in model usage.
- Gemini's batch embedding response has no token usage: `prompt_tokens` is LiteLLM's local
  estimate (tiktoken-based), so recorded embedding cost is an estimate of Google's bill.
- `runs.cost_usd` and run token totals stay **LLM-only** (reconciled before the output sink runs);
  unified accounting is revisited later.
- No price is assumed in code: enabling gateway embeddings without a positive
  `EMBEDDING_COST_PER_1M_TOKENS` fails configuration validation at startup.
- Costs are stored with 6 decimals (`model_calls.cost_usd NUMERIC(10,6)`), so a single very small
  call (below $0.0000005) is recorded as 0.

### Live verification (2026-11-25, real key, temporary gateway from this config)

| Check | Result |
|---|---|
| Connectivity / dimension | 1536-dim vectors; raw L2 norm 0.69 (Gemini does not normalize below 3072), normalized by the app |
| Latency | ~0.7-1.0 s per single call; 14-text batch ~2.0 s |
| Cost | ~23 estimated tokens per 3 short texts -> 0.000003 USD at 0.15 USD / 1M (LiteLLM token estimate) |
| Benchmark (`evals/embeddings_benchmark.py --embedder gateway`) | duplicate pairs 1.000 / 0.9887; highest non-duplicate 0.9291 (structurally incompatible); highest structurally compatible non-duplicate 0.7825; separable by similarity alone; suggested threshold 0.886 |
| Duplicate gates (`evals/run.py --mode reference --embedder gateway`) | at 0.95 (configured): precision 1.000, recall 1.000; also at 0.886: 1.000 / 1.000. 03/04 linked by embedding, 05/14 and 01/03 kept apart |
| Fallback | gateway stopped -> `MODEL_GATEWAY_UNAVAILABLE` -> exact-signature matching |

**Initial calibration: `DUPLICATE_SIMILARITY_THRESHOLD=0.95`.** Duplicate scores 1.000 / 0.9887,
highest non-duplicate 0.9291: 0.95 separates them even before the structural guard. Gemini
similarities are high even for unrelated text (0.76 in the connectivity probe), so the threshold
must stay high. The dataset has only two duplicate pairs; re-run the benchmark when more labeled
pairs exist or the model, dimension or task type changes.

**ANN index (migration `0006_m3_bug_embedding_index`).** Partial HNSW expression index
`ix_bug_reports_embedding_1536_hnsw` on `(embedding::vector(1536)) vector_cosine_ops` where
`embedding_dimensions = 1536`. Additive: the column stays untyped, rows of other dimensions are not
indexed (never cast), nothing is rebuilt or deleted; down/up verified with a 1536-dim row present
(identical content hash). The neighbour query uses the same cast and a literal `embedding_dimensions
= 1536` predicate so the planner can use the index (EXPLAIN-verified), and sets
`hnsw.iterative_scan = relaxed_order` (pgvector >= 0.8) so the project/model filter cannot starve the
result list. Built in the migration transaction (not `CONCURRENTLY`): fine at today's size; on a
large table build it concurrently first under the same name.

Remaining: enabling embeddings in a deployment (`EMBEDDINGS_ENABLED=true`,
`EMBEDDING_COST_PER_1M_TOKENS=0.15`) is an operator decision; defaults stay disabled.

Alternatives that were considered (for reference): `nvidia/nv-embed-v1` (4096 dims, not
ANN-indexable), `nvidia/nv-embedqa-e5-v5` (1024), `nvidia/llama-3.2-nv-embedqa-1b-v2` (up to 2048).

The schema does not lock a dimension: `embedding` is an untyped `vector`; `embedding_model` and
`embedding_dimensions` are stored per row and similarity is computed only within one model. Until
an ANN index applies (1536 dims, migration 0006) the search is an exact scan of one project's reports of one model (indexed by
`(project_id, embedding_model, embedding_dimensions)`), fine at MVP volume.

## 6. Reports (T1, T2, T9)

| Endpoint | Scope | Notes |
|---|---|---|
| `GET /v1/runs/{id}/report` | `runs:read` | Live JSON, validated against `modules/reports/report.schema.json` before it is returned (`REPORT_SCHEMA_INVALID` 500 otherwise) |
| `POST /v1/runs/{id}/report:export` | `artifacts:write` | Finished runs only (`REPORT_RUN_NOT_FINAL` 409). 201 new version; 200 unchanged evidence (content-hash dedupe) |
| `GET /v1/runs/{id}/reports` · `/reports/{version}` | `runs:read` | Version metadata, artifact references, stable `report_url` |

- Schema `1.1` adds an optional `bug` section (cluster, match, related bugs); `1.0` documents
  remain valid, and reports of other agents are unchanged.
- Export stores `report-<run>-v<N>.json` and `.html` artifacts plus a `reports` row (migration
  `0004_m3_reports`). `content_hash` (canonical JSON without `generated_at`) is unique per run, so
  a retried or repeated export returns the existing version; a storage failure records nothing and
  consumes no version number.
- A report never lists its own exports (they are not run evidence).
- HTML: every value escaped, no scripts, CSP `default-src 'none'`. Small text artifacts (≤ 64 KiB,
  json/text/log/markdown) are embedded with secrets redacted by the same redactor as `log.parse`.
- Console `/runs/{id}/report` (linked from the run page): summary, verdict, attempts, timelines,
  usage and cost, approvals, artifacts, bug analysis with duplicates, exported versions with JSON/HTML
  downloads; `?version=N` renders the stored export of that version.

## 7. Security and isolation

- Every query is scoped by `project_id` (reports, versions, artifacts, bugs, clusters, tool
  artifact reads); other projects' ids return 404.
- Raw logs and secrets never reach the model (asserted in `test_bug_analyzer_agent_end_to_end`).
- Exported HTML cannot execute script; embedded evidence is redacted.
- No new secrets, no provider SDKs in `runtime/` (import-boundary test unchanged and passing).

## 8. Acceptance status

| # | Status | Notes |
|---|---|---|
| AC1 | NOT VERIFIED | Local live command ready (EVALS.md §5); blocked on an available agent model (gemini-2.5-flash is 404 for this account) |
| AC2 | PASS with fake embedder | Exact matching verified with shipped defaults; embedding path verified with the fake embedder; Gemini verified live: precision/recall 1.0 at threshold 0.95 (reference outputs) |
| AC3 | PASS | Including a real MinIO round trip |
| AC4 | PARTIAL | Gate + `ac4` comparison proven with scripted model/judge; real-model run needs agent model + D2 judge decisions |

## 9. Known limitations

- **D1 done (Gemini, verified live, threshold 0.95, 1536-dim HNSW index)**; embeddings stay disabled by
  default until a deployment enables them.
- **Security follow-up (L2, not in M3):** `ToolDefinition.required_permission` is not
  configurable from `tools.yaml` (DB default `member`), so the Bug Analyzer grants `member`
  explicitly. Planned fix: a `required_permission` registry field defaulting to `member`.
- A failed `log.parse` call is fed back to the model as an error message (generic runtime
  behaviour); such reports are stored with `grounded: false` and fail the evals' grounding gate.
- Embedding cost is recorded in `model_calls` (and so in reports) but not in `runs.cost_usd`
  (decision B; unified accounting revisited after D1). Very small per-call costs round to 0
  at 6 decimals.
- Redaction is pattern-based: a bare secret with no recognizable shape (no `key=`/`key:` prefix,
  URL credentials or known token format, e.g. `using secret Abc123xyz`) is not redacted. Treat
  `log.parse` output as reduced-risk, not secret-free, and keep logs from production sanitized at
  the source.
- The output artifact upload (existing M3-R sink step) still runs while the run's row is locked
  by the pending token-total update; the bug store's embedding call does not (it runs before any
  flush, verified by `test_embedding_call_does_not_hold_the_run_row_lock`).
- Bug clusters are not yet exposed through their own API or console page (visible via reports).
- `report_url` is a console path; the console requires sign-in (no anonymous share links).
