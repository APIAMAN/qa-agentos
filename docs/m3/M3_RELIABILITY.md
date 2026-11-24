# M3 — Run Reliability, Recovery & Hardening

Status: **implemented on `feat/m3-reliability` and verified** by the PostgreSQL integration suite
and by live runs through a real Hatchet engine (normal run, worker killed mid-run, approval resume,
task timeout with and without retries left, retry exhaustion, stale-run reaper, full
`live_integration_suite.py`). Uncommitted, pending review; see [Known limitations](#known-limitations).

> Naming note: `docs/research/QA_AGENTOS_ARCHITECTURE.md` plans M3 as *Bug Analyzer + reports +
> evals*. This milestone was re-scoped to reliability hardening; the Bug Analyzer scope moves to
> the next milestone. Update the roadmap table when the plan is re-baselined.

## 1. Guarantees

| Guarantee | Mechanism |
|---|---|
| A run never stays `preparing`/`running` forever | Worker fails the run on permanent errors and on its last retry; the stale-run reaper fails abandoned runs |
| Retries and resumes continue from where execution stopped | PostgreSQL LangGraph checkpointer in the Hatchet worker (thread id = run id) |
| Completed LLM calls and tool side effects are not repeated on recovery | Checkpoint resume + step records committed before the checkpoint advances + cross-attempt tool cache |
| Approval resume is never dropped as a duplicate | Durable resume intent (`runs.resume_requested_at`), claimed exactly once |
| Duplicate deliveries execute once | Row-locked claim protocol with a database fencing token |
| Stale workers cannot corrupt state | `runs.attempt` fencing token checked under row lock for every step write and terminal transition |
| History across attempts is preserved | `run_steps` unique per `(run_id, step_number, attempt)`; one `model_calls` row per real LLM call; `attempt.*` run events |
| Clients never see a state the database does not have | Live events published only after commit; SSE subscribes before snapshotting |

## 2. Run lifecycle and state transitions

```
queued ──claim──▶ preparing ──▶ running ──▶ succeeded | failed | cancelled
   │                 │            │  ▲
   │                 │            ▼  │ approve (resume intent)
   └──────────────── waiting_for_approval ──reject──▶ cancelled
```

Allowed transitions are enforced in `runtime/events.py::ALLOWED_TRANSITIONS`. Terminal states
(`succeeded`, `failed`, `cancelled`) are final. Every transition takes a `FOR UPDATE` row lock and,
when `expected_attempt` is passed, refuses with `RunSupersededError` if a newer attempt owns the run.

Retry/resume claims are recorded as same-status run events whose payload is
`{"event": "attempt.retry" | "attempt.resume", "attempt": N, "previous_attempt": …, "hatchet_attempt": …}`.

## 3. Claim protocol (Hatchet worker)

`workers/main.py::claim_run` runs under a row lock. `runs.attempt` is a **monotonic fencing token**;
`runs.hatchet_run_id` + `runs.hatchet_attempt` identify the owning delivery.

| Run state | Delivery | Result |
|---|---|---|
| terminal | any | skipped ("Already terminal") |
| `waiting_for_approval` | any | skipped |
| `queued` | any | **claim** (`initial`) |
| active, `resume_requested_at` set | any | **claim** (`resume`), token +1, intent consumed |
| active, owned by workflow W at Hatchet attempt k | W at attempt > k | **claim** (`retry`), token +1 |
| active, legacy row (no owner recorded) | Hatchet attempt > token | **claim** (`retry`) |
| anything else | — | "Duplicate delivery ignored" |

Hatchet's `ctx.attempt_number` restarts at 1 for every new task run, which is why the old
`attempt_number <= run.attempt` rule silently dropped approval resumes.

`hatchet_run_id` is only written by the dispatcher/sweeper while the run is still `queued`, so a
late enqueue acknowledgement cannot overwrite the owner recorded by a claim.

## 4. Checkpoint strategy

- The Hatchet worker opens `postgres_checkpointer(DATABASE_URL)` per task (schema `langgraph`,
  one pooled connection). Disable with `WORKER_CHECKPOINTER_ENABLED=false` (not recommended).
- Checkpoint tables are created once per process under a PostgreSQL advisory lock, so concurrent
  worker start-up cannot race on DDL.
- Resume: if the run is `running` and a checkpoint exists, the graph continues from it. If the
  checkpoint is already at `END` (worker died after the graph finished but before the terminal
  commit), the saved state is finalized without re-running any node.
- Ordering: step/model-call rows (and tool step rows) are **committed inside the node, before
  LangGraph persists the superstep checkpoint**. Application history is therefore never behind
  the checkpoint; at worst a node re-runs once after a crash and its re-run is recorded as a new
  attempt row (true spend is still accounted).
- Inline mode keeps the in-memory `MemorySaver` (single process); an inline run interrupted by
  shutdown is failed with `EXECUTION_INTERRUPTED`.
- **Checkpoint fencing.** Attempts of one run can overlap: on a Hatchet task timeout the engine
  starts the retry *before* the timed-out attempt processes its cancellation (observed live: retry
  received at 13:23:23.159, cancel delivered at .161, the old coroutine still running about 1 s after
  the retry had finished the whole run). The executor therefore wraps the checkpointer with
  `runtime/graph/checkpointer.py::fenced_checkpointer`: before every `aput`/`aput_writes` it reads
  `runs.attempt` on a separate pooled connection and raises `RunSupersededError` once a newer attempt
  owns the run; the executor turns that into a `superseded` result. The view subclasses the concrete
  saver and overrides only those two methods, so LangGraph checkpoint semantics are unchanged.
  Applied on PostgreSQL engines (every deployment); see limitations for the residual gap.

## 5. Failure classification

| Class | Examples | Worker behaviour |
|---|---|---|
| Permanent | `MODEL_GATEWAY_AUTH_FAILED`, `MODEL_REQUEST_REJECTED`, `MODEL_STRUCTURED_OUTPUT_UNSUPPORTED`, `NO_MODEL_FOR_CAPABILITY`, `ValueError`/`KeyError`/schema errors, recursion limit | Run → `failed` with the error code; `NonRetryableException` (no Hatchet retry) |
| Retryable | `MODEL_GATEWAY_TIMEOUT`, `MODEL_GATEWAY_UNAVAILABLE`, `MODEL_RATE_LIMITED`, `MODEL_UPSTREAM_ERROR`, `MODEL_RESPONSE_INVALID`, DB/network errors | Run stays active (resumable); exception re-raised so Hatchet retries |
| Retryable on last attempt | any of the above when `attempt_number >= HATCHET_TASK_RETRIES + 1` | Run → `failed` with `RETRIES_EXHAUSTED` (`error.last_error_code` keeps the cause) |
| Abandoned | worker killed with no retry left, task timeout on the last attempt, lost cleanup | Reaper → `failed` with `EXECUTION_STALE` |

Task timeouts: Hatchet cancels the attempt (`execution_timeout = HATCHET_TASK_TIMEOUT_MINUTES`) and,
if retries remain, starts the next attempt, which is claimed as `attempt.retry` and resumes from the
checkpoint. On the last attempt the worker cannot write a terminal state (it is being cancelled), so
the reaper fails the run as `EXECUTION_STALE` within `RUN_STALE_AFTER_SECONDS` plus one sweeper
interval of its last heartbeat (live, 90 s window: heartbeat 13:29:47, timeout about 13:30:47, failed
13:32:00). `error.hatchet_run_id` links the run to the Hatchet task, whose UI shows the timeout.

Gateway errors carry `retryable` (`runtime/gateway/errors.py`).

Graph-level budgets: `parameters.max_steps` (default 10) is enforced by the graph
(`MAX_STEPS_EXCEEDED`); `parameters.max_repairs` (default 2) bounds repair loops; LangGraph's
`recursion_limit` is set to `2*max_steps + 3*max_repairs + 10` as defence in depth.

## 6. Stale-run recovery (operations)

The existing `outbox_sweeper` cron (every `HATCHET_SWEEPER_INTERVAL_SECONDS`) now also runs
`modules/runs/recovery.py::reap_stale_runs`:

1. `preparing`/`running` with heartbeat older than `RUN_STALE_AFTER_SECONDS`
   (default: task timeout + 5 min) and a pending resume → the lost resume is dispatched again.
2. Same, without pending resume → `failed` / `EXECUTION_STALE` (details include last heartbeat,
   attempt, Hatchet id). Zombie workers are fenced by the terminal status.
3. `queued` runs handed to Hatchet but never claimed within the window → `hatchet_run_id` cleared so
   the outbox sweeper re-enqueues them (duplicate deliveries are harmless).

All steps use `FOR UPDATE SKIP LOCKED` and are idempotent. Heartbeats (`runs.heartbeat_at`) are
written at claim time, after every superstep and on every step record.

Manual recovery: a failed/stale run can be re-executed with `POST /v1/runs/{id}/replay`.

## 7. Observability (debugging retries, recovery, timeouts)

No metrics stack was added; the existing signals were sufficient to reconstruct every scenario
verified live:

| Question | Where to look |
|---|---|
| Which attempts ran, and why? | `run_events`: `attempt.retry` / `attempt.resume` payloads (`attempt`, `previous_attempt`, `hatchet_attempt`, `hatchet_run_id`) |
| Why did it fail? | `runs.error_code` + `runs.error` (`RETRIES_EXHAUSTED.last_error_code`, `EXECUTION_STALE.last_heartbeat_at` / `hatchet_run_id`, `MAX_STEPS_EXCEEDED.max_steps`) |
| What did each attempt do and cost? | `run_steps` (per `attempt`), `model_calls` (one row per real LLM call) |
| Worker-side timeline | Structured worker logs with `run_id`, `attempt`, `hatchet_run_id`, `hatchet_attempt` on every line: `Claimed run for execution` (`claim=initial/retry/resume`), `Retryable error … remaining_attempts=N`, `Retries exhausted`, `Refusing checkpoint write from superseded attempt`, `Stale run reconciliation completed` |
| Engine-side timeout/cancel | Hatchet UI / engine logs, by `hatchet_run_id` |

Recommended later (not required for M3): counters for claims by kind, retries exhausted, stale reaps
and fence refusals, once a metrics backend is chosen.

## 8. Events and live updates

- `RunStateManager` queues events on the session; an `after_commit` hook releases them, a rollback
  discards them, and `commit_and_publish()` publishes after the commit. Redis outages never affect
  persisted state (`publish_run_event` never raises).
- SSE (`GET /v1/runs/{id}/events`) releases its DB connection before streaming, subscribes to Redis
  first and only then reads the run snapshot, so a transition in between cannot be lost. Stream
  tickets are consumed atomically with `GETDEL`.
- The run detail page keeps one stream open across non-terminal status changes.

## 9. Artifacts

The run-output artifact is written through an injected `OutputSink`
(`modules/artifacts/service.py::store_run_output_artifact`); the runtime no longer imports
`modules/`. MinIO calls run in a worker thread. The artifact row is committed atomically with the
`succeeded` transition. A storage failure does not fail the run (the output is persisted on the run
row) but is recorded on the terminal event: `payload.artifact_status = "failed"`, `artifact_error`.

## 10. Configuration

| Setting | Default | Purpose |
|---|---|---|
| `RUN_STALE_AFTER_SECONDS` | unset → `HATCHET_TASK_TIMEOUT_MINUTES*60 + 300` | Stale-run threshold (min 60 s) |
| `WORKER_CHECKPOINTER_ENABLED` | `true` | PostgreSQL checkpoints in the Hatchet worker |
| `HATCHET_TASK_RETRIES` | `2` | Total attempts = retries + 1 (drives `RETRIES_EXHAUSTED`) |

Migration `0003_m3_run_reliability` adds `runs.resume_requested_at`, `runs.hatchet_attempt`,
`runs.heartbeat_at`, index `ix_runs_status_heartbeat_at`, and widens the `run_steps` unique key to
`(run_id, step_number, attempt)`. All new columns are nullable (backward compatible). Downgrade
fails if a run already has several attempts for one step; delete older attempt rows deliberately first.

## 11. Test coverage

Live (Hatchet v0.111.0 engine, `p6test_worker` built from this branch, mock LiteLLM, PostgreSQL
checkpoints), executed manually, not in CI:

| Scenario | Result |
|---|---|
| Normal run | `succeeded` |
| Worker container `docker kill`ed mid-run | redelivered at Hatchet attempt 2 → `attempt.retry` → `succeeded` |
| Approval requested mid-run, then approved | new task at Hatchet attempt 1 → `attempt.resume` → resumed from checkpoint → `succeeded` |
| Task timeout (1 min) with 1 retry left | attempt 2 → `attempt.retry` → `succeeded` |
| Task timeout on the last attempt | Hatchet gives up → reaper → `failed` / `EXECUTION_STALE` |
| Gateway unreachable, 2 retries | 3 attempts (`MODEL_GATEWAY_UNAVAILABLE`) → `failed` / `RETRIES_EXHAUSTED`, 2× `attempt.retry` |
| Reaper: vanished-worker run vs healthy run | stale → `EXECUTION_STALE` on the next cron tick; healthy untouched → `succeeded` |
| `live_integration_suite.py` Tests 1–3 | all PASS (Test 3 rewritten for the claim protocol) |


`apps/api/tests/test_m3_reliability.py` (real PostgreSQL, scripted LiteLLM gateway, persisted-state
assertions): worker kill + checkpoint resume without repeating LLM calls, checkpoint already at END,
approval resume in Hatchet mode, single-use resume claim, rejected approval, concurrent conflicting
approvals, concurrent duplicate deliveries, concurrent terminal transitions, stale-worker fencing,
permanent provider error, retry exhaustion, transient-then-success, LLM timeout, DB failure,
max-steps enforcement, cancellation during execution, inline shutdown, stale-run reaper
(idempotent), zombie fencing, tool side-effect idempotency across attempts, failed-tool retry
history, artifact failure recording, atomic artifact write, Redis outage, publish-after-commit,
deterministic model snapshots, checkpoint fence (deterministic unit test with positive and negative
control, plus an end-to-end overlap scenario).

## Known limitations

- **Live Hatchet checks are manual.** They need the Hatchet stack and are not part of CI; how to run
  them is described in `HANDOFF.md`.
- **The checkpoint fence is check-then-write.** The attempt is read immediately before each
  checkpoint write; a claim landing in the few milliseconds between the read and the write can still
  let one stale write through. Closing it completely requires the checkpoint write and a `runs` row
  lock in one transaction (a custom saver), which is not worth the risk to LangGraph semantics today.
- **At-least-once at crash boundaries.** A node interrupted after its external call but before its
  step commit runs again on recovery and is recorded as a separate attempt row (the LLM cost is
  counted twice because it was paid twice). Completed tool steps are never re-executed (cross-attempt
  cache). Tools with external side effects must pass `ToolExecutionContext.idempotency_key` (stable
  across attempts: `qa-agentos:{run_id}:{step_number}`) to downstream APIs that accept idempotency
  keys. LLM providers generally do not, so repeated LLM spend at a crash boundary is unavoidable.
- **A timeout on the last attempt is reported as `EXECUTION_STALE`**, not a dedicated timeout code,
  after up to `RUN_STALE_AFTER_SECONDS` plus one sweeper interval (see §5).
- **No LiteLLM provider fallback: a business decision, not a code gap.** `deploy/litellm/config.yaml`
  has one deployment per alias and no `fallbacks`/`num_retries`. A provider outage therefore surfaces
  as retryable gateway errors → Hatchet retries → `RETRIES_EXHAUSTED`. Adding fallbacks means choosing
  providers and models with cost and data-residency trade-offs, and needs approval first. When it is
  added, make sure LiteLLM retries and Hatchet retries do not multiply.
- **No approval expiry.** Pending approvals wait indefinitely (by design; not reaped).
- **No metrics backend** (see §7).
- Gateway errors still subclass the HTTP `APIError`; separating runtime errors from FastAPI is
  deferred (no behaviour impact).
- Inline mode has no reaper (no cron); inline is intended for development and tests.
