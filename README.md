# QA AgentOS

**QA AgentOS** is an open-source, single-modular-monolith QA Agent platform built on FastAPI, LangGraph, PostgreSQL (with `pgvector`), Hatchet, and Next.js.

## Status

- **M0 Foundation Completed** (Monorepo, Database Schemas, AuthN/AuthZ, Projects API, Docker Stack, Next.js Console Shell).
- For complete architecture details, read [`docs/research/QA_AGENTOS_ARCHITECTURE.md`](docs/research/QA_AGENTOS_ARCHITECTURE.md).

## Quickstart (Local Development)

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Start the local infrastructure stack:
   ```bash
   make up
   ```

3. Run migrations and seed demo data:
   ```bash
   make migrate
   make bootstrap
   ```

4. Run local backend & frontend:
   - Backend API: `make dev-api` (http://localhost:8000)
   - Frontend Console: `make dev-web` (http://localhost:3000)

## Architecture Decisions

Architecture Decision Records (ADRs) are located under [`docs/adr/`](docs/adr/):
- [ADR-001: Modular Monolith Architecture](docs/adr/ADR-001-modular-monolith.md)
- [ADR-002: LangGraph Orchestration & Checkpoints](docs/adr/ADR-002-langgraph-runtime.md)
- [ADR-003: Registries as Versioned Data](docs/adr/ADR-003-data-driven-registries.md)
- [ADR-004: Model Gateway via LiteLLM](docs/adr/ADR-004-litellm-model-gateway.md)
- [ADR-005: NVIDIA NIM & Fallback Providers](docs/adr/ADR-005-nvidia-nim-gateway.md)
