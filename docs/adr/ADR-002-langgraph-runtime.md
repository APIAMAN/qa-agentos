# ADR-002: LangGraph Orchestration & Checkpointing

## Status
Accepted

## Context
Agentic QA workflows require determinism, state machine persistence across step interruptions, human-in-the-loop approvals, and full execution replayability.

## Decision
Adopt **LangGraph (OSS python library)** as the generic agent orchestration graph engine, paired with PostgreSQL for checkpointing. Human oversight is implemented natively via LangGraph's `interrupt()` primitive, allowing runs to pause, persist state in Postgres, and resume upon API approval.

## Consequences
- **Pros:** Native support for graphs, deterministic transitions, built-in checkpointing to Postgres, robust human approval pauses.
- **Cons:** Learning curve for complex graphs (mitigated by standardizing on a `generic_react` graph structure).
