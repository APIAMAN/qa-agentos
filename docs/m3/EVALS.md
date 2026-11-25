# M3 — Agent evaluations and CI gates

Covers T5 (golden datasets), T10 (harness) and T11 (`agent-evals` workflow). GitHub Actions runs
deterministic checks only; real-model evaluation is local and opt-in (§5). Design of the agent
and pipeline under test: [BUG_ANALYZER.md](BUG_ANALYZER.md).

## 1. Layout

```
evals/
  pyproject.toml, uv.lock          separate uv project (DeepEval must not change the workspace uv.lock)
  thresholds.yaml                  gates and the modes in which each is mandatory
  run.py                           CLI entry point (qa_evals/runner.py)
  embeddings_benchmark.py          D1 benchmark / threshold calibration
  datasets/bug-analyzer/v1/
    logs/*.log                     14 synthetic, sanitized log bundles
    dataset.yaml                   labels, frozen log.parse signatures, duplicate / distinct pairs
    reference_outputs.json         hand-written reference reports (harness self-test only)
  qa_evals/                        dataset, scoring, judge (DeepEval G-Eval), live runner
  tests/test_harness.py            gate mechanics (keyless)
```

## 2. Golden dataset v1

14 cases written for QA AgentOS (no customer data; the credentials, tokens and emails in fixtures
are fake and exist to exercise redaction): Python, JVM, Node, Go, JSON-lines, PostgreSQL, nginx
access/error logs; severities critical ×2, high ×6, medium ×4, low ×2; an exact duplicate pair
(01/02), a near-duplicate pair with a different signature (03/04), a same-message/different-code
pair that must not merge (05/14), a warnings-only log and an empty log.

Severity and category are labeled by hand with the rubric in `prompts/bug-analyzer/1.0.0.md`.
`expected.signatures` are the reviewed, frozen output of `log.parse` (parser 1.0.0); the backend
suite fails if the parser drifts from them (`test_parser_matches_golden_signatures`). Changing the
parser's output requires bumping `PARSER_VERSION` and the dataset version.

## 3. Metrics and gates (`evals/thresholds.yaml`)

| Metric | Kind | Min | Mandatory in | Criterion |
|---|---|---|---|---|
| `severity_accuracy` | deterministic | 0.90 | all modes | AC1 (≥ 9/10) |
| `category_accuracy` | deterministic | 0.70 | all modes | |
| `signature_accuracy` | deterministic | 0.90 | all modes | primary signature = parser's |
| `grounding_rate` | deterministic | 1.00 | all modes | every cited signature produced by `log.parse` |
| `duplicate_recall` | deterministic* | 1.00 | all modes | AC2 |
| `duplicate_precision` | deterministic* | 1.00 | all modes | AC2 (no false merges) |
| `geval_faithfulness` | LLM judge | 0.70 | live | AC4 |

\* replays the production matching rules (`modules/bugs/matching.py`) with the configured embedder
(the fake one, threshold 0.80, in reference/replay mode).

Embeddings are disabled by default (BUG_ANALYZER.md §5). In live mode the duplicate gates need the
gateway embedder: without `EMBEDDINGS_ENABLED=true` they are reported `not_run` (exit 2), never failed or passed.

Exit codes: **0** every mandatory gate passed · **1** a gate is below its threshold · **2** a
mandatory gate could not be computed (no judge, judge outage, failed runs, spend cap reached).
A gate that did not run is never reported as passed. Results JSON records the mode, dataset and
parser version, agent version and prompt hash, models, embedder, judge, thresholds, git SHA,
per-case problems, duplicate pair decisions, G-Eval scores and cost.

## 4. Modes

| Mode | Model calls | Purpose |
|---|---|---|
| `reference` | none | Hand-written reference outputs: harness self-test, catches parser/dataset/scoring drift (CI) |
| `replay` | none | Re-score recorded outputs; `--outputs` repeatable to merge subset runs from different days |
| `preflight` | 2 | One minimal call to the agent alias and to the judge alias: access and quota check |
| `live` | ~3 per case | Real agent + G-Eval judge on the dataset (**AC1**) |
| `ac4` | ~5 per case | `live` with the baseline prompt and with `evals/prompts/bug-analyzer-weakened.md` (**AC4**) |

**Reference/replay results are not a model evaluation** (`meta.outputs_are_model_generated`).

## 5. Local real-model evaluation (the only place models are called)

Real model calls happen only in `preflight`/`live`/`ac4`, only with `--allow-live`, never under CI
(`GITHUB_ACTIONS`/`CI` refused), and always inside one shared budget:

| Control | Flag / env | Default |
|---|---|---|
| Hard call limit (agent + judge + embedding) | `--max-calls` / `EVAL_MAX_CALLS` | 60 |
| Spend cap (estimated at configured paid prices) | `--max-cost-usd` / `EVAL_MAX_COST_USD` | 0.50 USD |
| Pacing between calls (free-tier RPM) | `--min-interval-seconds` / `EVAL_MIN_INTERVAL_SECONDS` | 6.5 s |
| Stop on quota | 429 or `RESOURCE_EXHAUSTED` stops the run; nothing is retried | always |
| Prices (required, never assumed) | `EVAL_AGENT_INPUT_COST_PER_1M`, `EVAL_AGENT_OUTPUT_COST_PER_1M`, `EVAL_JUDGE_INPUT_COST_PER_1M`, `EVAL_JUDGE_OUTPUT_COST_PER_1M` | none: refused without them |
| Judge alias | `EVAL_JUDGE_MODEL_KEY` (LiteLLM alias `judge`) | none |

A request that fails or times out still counts against the call limit (it may still be billed).
Results report calls, tokens and estimated cost **per role** (`agent`, `judge`, `embedding`); the
duplicate gates use the fake embedder by default, so live runs make no embedding calls.
`--dry-run` validates configuration and prints the plan (cases, planned calls vs limit) with no
network access. Evaluation data is the synthetic dataset only; DeepEval telemetry is disabled.

Commands (the gateway reads `GEMINI_API_KEY` from `.env`; keys are never printed):

```bash
# 0. gateway with the repo config (dev compose passes GEMINI_API_KEY through)
docker compose -f deploy/docker-compose.yml up -d litellm
export LITELLM_ENDPOINT=http://localhost:4000 LITELLM_MASTER_KEY=<local master key> \
       MODEL_PROVIDER_ALLOWLIST=gemini EVAL_JUDGE_MODEL_KEY=judge \
       EVAL_AGENT_INPUT_COST_PER_1M=<agent price> EVAL_AGENT_OUTPUT_COST_PER_1M=<agent price> \
       EVAL_JUDGE_INPUT_COST_PER_1M=<judge price> EVAL_JUDGE_OUTPUT_COST_PER_1M=<judge price>

uv run --project evals python evals/run.py --mode ac4 --dry-run                      # plan, no calls
uv run --project evals python evals/run.py --mode preflight --allow-live            # 2 calls
uv run --project evals python evals/run.py --mode live --allow-live --max-calls 50  # AC1 (14 cases)
uv run --project evals python evals/run.py --mode ac4 --allow-live --max-calls 100  # AC4 baseline vs weakened

# free tier with a small daily quota: run subsets on different days, then merge
uv run --project evals python evals/run.py --mode live --allow-live --cases 01-checkout-keyerror,02-checkout-keyerror-repeat,... \
       --out evals/results/day1.json
uv run --project evals python evals/run.py --mode replay --outputs evals/results/day1-baseline-outputs.json \
       --outputs evals/results/day2-baseline-outputs.json
```

Exit codes: 0 pass · 1 a gate below threshold (AC4: baseline failed or weakened prompt not caught) ·
2 not run (refused, missing config, quota/limit reached, failed runs). `ac1.verdict` is `PASS` only
for >= 10 model-generated cases with >= 90 % correct severity; fewer cases give `NOT VERIFIED`.

### Model access and free tier (verified 2026-11-25 with the local key)

- `gemini-2.5-flash` (the `classifier` alias the Bug Analyzer resolves to with a Gemini-only
  allowlist) and `gemini-2.5-flash-lite` (proposed judge) return **404 "no longer available to new
  users"** for this account. Google points to `gemini-3.8-flash` and `gemini-3.5-flash-lite`.
- Preflight with a temporary config: `gemini-3.5-flash-lite` **200 OK** (8 + 1 tokens);
  `gemini-3.8-flash` **503 "high demand"** at the time of the check.
- The repo `judge` alias now points to `gemini/gemini-3.5-flash-lite` (D2 candidate). Preflight
  through the gateway with the repo config (2026-11-25): `judge` **200**, returned model
  `gemini-3.5-flash-lite`, usage 8 + 1 tokens reported; `classifier` (`gemini-2.5-flash`) **404**.
- Agent candidate `gemini-3.5-flash-lite` (temporary eval-only gateway config; the repo `classifier`
  alias is unchanged): **200**, 8 + 1 tokens. `gemini-3.8-flash`: **503** again (single check, no retry).
- Prices (aistudio.google.com/docs/pricing, read 2026-11-25, Standard, per 1M tokens): 3.5 Flash-Lite
  $0.30 in / $2.50 out; 3.8 Flash $0.75 / $3.75 until 2026-12-31, then $1.50 / $7.50. Both list a
  Free Tier "Free of charge", where content **is used to improve Google's products** (synthetic data only).
  `models.yaml` still lists 0.075 / 0.30 for `gemini-2.5-flash` (stale, model unavailable).
- Free-tier status cannot be read through the API. Check the project's tier at
  `aistudio.google.com/projects` (Free = no linked billing; Tier 1 = billing linked, all usage
  charged) and limits at `aistudio.google.com/rate-limit`. The rate-limits page publishes no RPM/RPD for
  these models. **AC1/AC4 runs wait for that confirmation.**
- Same model as agent and judge (if both are 3.5 Flash-Lite) risks self-preference bias in G-Eval;
  the deterministic gates (severity, signature, grounding) are unaffected.

## 6. CI: `.github/workflows/agent-evals.yml` (deterministic only)

No model is called from GitHub Actions: no secrets, no gateway, no live job. One job runs on PRs
and pushes to `main` touching `agents/`, `prompts/`, `tools.yaml`, `models.yaml`, `evals/`,
`deploy/litellm/`, the runtime tools/gateway/graph or `modules/bugs/`:

1. `uv sync --project evals --locked`
2. harness self-tests (gate mechanics incl. weakened-output gates, budget controls; scripted model and judge)
3. reference gate (`--mode reference`)
4. canary: `--mode live --allow-live` must be refused (exit 2) under CI

The job sets `EMBEDDINGS_ENABLED=false`, empty `EVAL_JUDGE_MODEL_KEY` and `LITELLM_MASTER_KEY`.

## 7. Negative gate proof (AC4)

| Check | Kind | Result |
|---|---|---|
| Degraded outputs fail the gate (`--mode replay`) | deterministic | exit 1: `severity_accuracy` 0.857, `grounding_rate` 0.929 |
| Low G-Eval score fails the gate | scripted judge | exit 1 |
| `ac4` comparison: baseline passes, weakened prompt caught | scripted model + judge | exit 0 (`test_ac4_passes_only_when_weakened_prompt_is_caught`) |
| No judge / judge outage / quota | scripted | exit 2, never a pass |
| **Real model: baseline vs weakened prompt** | local `--mode ac4 --allow-live` | **NOT RUN**: the agent and judge models need D2 / registry decisions (see §5) |

### First real-model run (2026-11-25, 2 cases, `gemini-3.5-flash-lite` as agent and judge)

Free Tier confirmed in AI Studio (no billing; 3.5 Flash-Lite 15 RPM / 500 RPD). Result: **not run**
(exit 2). Gemini rejects the Bug Analyzer's structured-output request with HTTP 400
`INVALID_ARGUMENT`: the union types `type: ["string", "null"]` in `agents/bug-analyzer.yaml`
(`root_cause`, `primary_signature`, `evidence[].signature`, `affected_component`) are not converted by
LiteLLM 1.52 into Gemini's `nullable: true`. This is model-independent (it also affects
`gemini-2.5-flash`) and was never exercised before because AC1 had not run. 3 calls used, $0, no
retries. Severity, grounding, faithfulness and JSON validity are therefore **unmeasured**.

**Second run (after the nullable fix in `runtime/gateway/client.py`)**, case `01-checkout-keyerror` only:
Gemini accepted the request (no 400). Result exit 1: severity `low` (expected `high`), category `other`
(expected `functional`), signature `null`, G-Eval faithfulness 0.10 (**provisional**: same model as agent
and judge). 2 calls (agent 1, judge 1), estimated $0.000986 at paid prices, $0 on the Free Tier.
Cause: `generate_node` sends the strict output schema on every turn, including the turn where the agent
must request `log.parse` via `{"tool_calls": [...]}`. Gemini enforces the schema, so the tool request
cannot be expressed and the model returned a placeholder report (`tool_calls: 0`). The scripted model
ignores schemas, so offline tests never showed this. Case 2 was not run (same cause). This is a
runtime design issue (tool-using agent + strict structured output) that needs a decision; it is not a
model-quality result.

**Third run (after Option 1 in `runtime/graph/nodes.py`)**: tool-request turns are sent with
`response_format: {"type": "json_object"}`; once a tool result is in (or no tool call fits the step
budget, or the agent has no tools) the strict `json_schema` is sent. Cases `01-checkout-keyerror` and
`07-payment-go-panic`, run one at a time (5-call / $0.025 cap each):

| Case | log.parse | Severity | Category | Signature | Grounding | Faithfulness (provisional) | Calls | Est. USD |
|---|---|---|---|---|---|---|---|---|
| 01-checkout-keyerror | invoked (1) | high = high | functional = functional | match | 1.0 | 1.0 | 3 | 0.002416 |
| 07-payment-go-panic | invoked (1) | critical = critical | crash = crash | match | 1.0 | 1.0 | 3 | 0.002110 |

JSON validity: both final reports passed the app's schema validation (no repairs). Total 6 calls,
estimated $0.004526 at paid prices, $0 on the Free Tier. Faithfulness is **provisional**: the judge is the
same model as the agent. Run metadata names `gemini-2.5-flash` (registry entry); the calls went through a
temporary gateway config mapping `classifier` to `gemini-3.5-flash-lite`. **Two cases are not AC1/AC4
evidence** (`ac1.verdict` NOT VERIFIED).

## 8. Open decisions

- **Agent model**: `gemini-2.5-flash` is unavailable to this account; choose a replacement (e.g.
  `gemini-3.8-flash`) and update `models.yaml` + the `classifier` alias with verified prices.
- **D2 judge**: `gemini-3.5-flash-lite` configured on the `judge` alias and verified reachable; price verified.
- Free-tier/billing status of the key's project (AI Studio).
- ~~Gemini-compatible nullable fields~~: fixed in `runtime/gateway/client.py` (2026-11-25).
- ~~Strict output schema on tool-request turns~~: fixed in `runtime/graph/nodes.py` (Option 1, 2026-11-25).
- Agent model: `classifier` alias / `models.yaml` still name the unavailable `gemini-2.5-flash`; replacing it with `gemini-3.5-flash-lite` (price $0.30 / $2.50) needs approval.
- The 0.70 faithfulness minimum is a starting point; set it from the first real runs.
