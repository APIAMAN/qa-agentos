# ADR-002: LangGraph Orchestration & Checkpointing

## Status
Accepted

## Context
Agentic QA workflows require determinism, state machine persistence across step interruptions, human-in-the-loop approvals, and full execution replayability.

## Decision
Adopt **LangGraph (OSS python library)** as the generic agent orchestration graph engine, paired with PostgreSQL for checkpointing. Human oversight is implemented natively via LangGraph's `interrupt()` primitive, allowing runs to pause, persist state in Postgres, and resume upon API approval.

## Implementation note (M3)
PostgreSQL checkpointing is now wired into the durable Hatchet worker (previously only tests used it;
the worker ran without a checkpointer, so retries restarted from START). Approval pauses are
implemented by the `waiting_for_approval` run status plus a durable resume intent rather than
`interrupt()`. Recovery, fencing and ordering semantics are documented in
[docs/m3/M3_RELIABILITY.md](../m3/M3_RELIABILITY.md).

## Consequences
- **Pros:** Native support for graphs, deterministic transitions, built-in checkpointing to Postgres, robust human approval pauses.
- **Cons:** Learning curve for complex graphs (mitigated by standardizing on a `generic_react` graph structure).
