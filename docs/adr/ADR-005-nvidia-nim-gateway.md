# ADR-005: NVIDIA NIM & Fallback Providers

## Status
Accepted

## Context
Development economics benefit from leveraging NVIDIA NIM hosted endpoints (`integrate.api.nvidia.com`) for primary reasoning/planning models while maintaining fallback capabilities for high-availability production requirements.

## Decision
Configure NVIDIA NIM as the default provider for the `planner` capability slot in LiteLLM config, with Google Gemini (`gemini-2.5-flash`) as the secondary fallback provider for speed and context window operations.

## Consequences
- **Pros:** Significantly reduced LLM development costs during bootstrapping phase.
- **Cons:** Free tier model catalogs undergo deprecation cycles; gateway resilience fallbacks are mandatory.
