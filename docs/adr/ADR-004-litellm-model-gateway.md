# ADR-004: Model Gateway via LiteLLM Proxy

## Status
Accepted

## Context
Agents in QA AgentOS require dynamic model routing (e.g. planner slot vs classifier slot), spend tracking, virtual keys, rate-limit fallback chains, and single-endpoint abstraction over multiple LLM providers.

## Decision
Route all LLM interactions through a self-hosted **LiteLLM Proxy** instance. Application code interacts exclusively with LiteLLM using standard OpenAI-compatible endpoints.

## Consequences
- **Pros:** Model providers can be swapped via YAML configuration without modifying code; unified token cost and latency logging.
- **Cons:** Additional proxy container in deployment topology.

## Implementation note (M3)

- Embeddings also go through the gateway (`POST /v1/embeddings`, `LiteLLMClient.embeddings`,
  `runtime/gateway/embeddings.py`) behind the alias `EMBEDDING_GATEWAY_MODEL_KEY` (`embedder`), with
  the same typed errors; each call is a `model_calls` row (capability `embedding`). The model choice
  (D1) is open; see `docs/m3/BUG_ANALYZER.md` §5.
- The G-Eval judge for agent evaluations is also a gateway alias (`EVAL_JUDGE_MODEL_KEY`, D2 open),
  so judge identity is configuration recorded in every eval result.
