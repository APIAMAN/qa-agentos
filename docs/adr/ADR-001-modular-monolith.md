# ADR-001: Modular Monolith Architecture

## Status
Accepted

## Context
QA AgentOS is designed for personal MVP deployment moving toward a scalable production SaaS, operated initially by a solo developer. Distributed microservices introduce significant operational tax, network overhead, complex deployment topologies, and cross-service coordination overhead that are unnecessary and harmful at this stage.

## Decision
Build the QA AgentOS control plane as a **single Python/FastAPI modular monolith**. 

All core platform domains (Projects, Agent Registries, Tool Registries, Model Routing Config, Run State Machines, Approvals, Artifacts, and Reporting) live in one deployable FastAPI application. The only execution components that scale independently are stateless execution worker processes pulling durable jobs from PostgreSQL via Hatchet.

## Consequences
- **Pros:** High velocity, trivial local setup (`docker compose up`), zero microservice network latency within the control plane, single database transaction boundary.
- **Cons:** Shared memory space for API domain modules (mitigated by strict directory module boundaries in `apps/api/src/qa_agentos/modules/`).
