"""Langfuse observability and tracing interface for QA AgentOS.

Provides unified tracing for agent runs, graph node spans, and model generation
observations with strict fallback to no-op when credentials are unconfigured or
in test/CI environments.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Protocol, cast, runtime_checkable

from qa_agentos.core.config import Settings
from qa_agentos.core.config import settings as default_settings
from structlog import get_logger

logger = get_logger(__name__)

# Keys that must never be leaked into observability metadata or payloads
REDACTED_KEYS = frozenset(
    {
        "api_key",
        "secret",
        "token",
        "password",
        "litellm_master_key",
        "secret_key",
        "authorization",
        "cookie",
        "credential",
        "private_key",
    }
)

# Regex patterns for detecting and scrubbing free-text / embedded secrets
_PATTERN_BEARER_AUTH = re.compile(r"(?i)\b(bearer|basic)\s+([a-zA-Z0-9_\-\.\+/=]+)")
_PATTERN_SK_KEY = re.compile(r"\bsk-[a-zA-Z0-9_\-]{16,}\b")
_PATTERN_LITELLM_KEY = re.compile(r"\bsk-litellm-[a-zA-Z0-9_\-]{16,}\b")
_PATTERN_GH_TOKEN = re.compile(r"\bgh[pousr]_[a-zA-Z0-9]{20,}\b")
_PATTERN_SLACK_TOKEN = re.compile(r"\bxox[baprs]-[0-9a-zA-Z]{10,}\b")
_PATTERN_JWT = re.compile(r"\beyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\b")
_PATTERN_URL_CREDS = re.compile(r"://([^:\s]+):([^@\s]+)@")
_PATTERN_KEY_ASSIGN = re.compile(
    r"(?i)\b(api[_-]?key|secret[_-]?key|secret|password|auth[_-]?token|access[_-]?token)\b\s*[:=]\s*(['\"][^'\"]+['\"]|[^\s,;]+)"
)


def _sanitize_string(text: str) -> str:
    """Redact known secret patterns from arbitrary free-text strings."""
    if not text:
        return text
    text = _PATTERN_BEARER_AUTH.sub(r"\1 [REDACTED]", text)
    text = _PATTERN_SK_KEY.sub("[REDACTED_API_KEY]", text)
    text = _PATTERN_LITELLM_KEY.sub("[REDACTED_API_KEY]", text)
    text = _PATTERN_GH_TOKEN.sub("[REDACTED_API_KEY]", text)
    text = _PATTERN_SLACK_TOKEN.sub("[REDACTED_API_KEY]", text)
    text = _PATTERN_JWT.sub("[REDACTED_JWT]", text)
    text = _PATTERN_URL_CREDS.sub("://[REDACTED]:[REDACTED]@", text)
    text = _PATTERN_KEY_ASSIGN.sub(r"\1=[REDACTED]", text)
    return text


def sanitize_payload(data: Any) -> Any:
    """Recursively sanitize arbitrary data structures (dicts, lists, strings) to redact secrets."""
    if data is None:
        return None
    if isinstance(data, str):
        return _sanitize_string(data)
    if isinstance(data, dict):
        clean_dict: Dict[str, Any] = {}
        for k, v in data.items():
            k_str = str(k)
            if any(red in k_str.lower() for red in REDACTED_KEYS):
                clean_dict[k_str] = "[REDACTED]"
            else:
                clean_dict[k_str] = sanitize_payload(v)
        return clean_dict
    if isinstance(data, list):
        return [sanitize_payload(item) for item in data]
    if isinstance(data, tuple):
        return tuple(sanitize_payload(item) for item in data)
    if isinstance(data, set):
        return {sanitize_payload(item) for item in data}
    return data


def sanitize_metadata(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Strip sensitive fields from metadata before sending to telemetry."""
    if not data:
        return {}
    sanitized = sanitize_payload(data)
    return sanitized if isinstance(sanitized, dict) else {}


class SpanContext:
    """Represents an active span observation."""

    def __init__(self, observation_id: Optional[str] = None, client_span: Any = None) -> None:
        self.id = observation_id
        self._client_span = client_span

    def end(self, output_data: Optional[Any] = None, error: Optional[str] = None) -> None:
        if self._client_span is not None:
            try:
                updates: Dict[str, Any] = {}
                if output_data is not None:
                    updates["output"] = sanitize_payload(output_data)
                if error is not None:
                    updates["level"] = "ERROR"
                    updates["status_message"] = _sanitize_string(str(error))
                self._client_span.end(**updates)
            except Exception as exc:
                logger.warning("Failed to end Langfuse span", error=str(exc))


class GenerationContext:
    """Represents an active generation observation."""

    def __init__(self, generation_id: Optional[str] = None, client_gen: Any = None) -> None:
        self.id = generation_id
        self._client_gen = client_gen

    def end(
        self,
        output_data: Optional[Any] = None,
        usage: Optional[Dict[str, Any]] = None,
        cost: Optional[Dict[str, float]] = None,
        error: Optional[str] = None,
    ) -> None:
        if self._client_gen is not None:
            try:
                updates: Dict[str, Any] = {}
                if output_data is not None:
                    updates["output"] = sanitize_payload(output_data)
                if usage is not None:
                    updates["usage_details"] = usage
                if cost is not None:
                    updates["cost_details"] = cost
                if error is not None:
                    updates["level"] = "ERROR"
                    updates["status_message"] = _sanitize_string(str(error))
                self._client_gen.end(**updates)
            except Exception as exc:
                logger.warning("Failed to end Langfuse generation", error=str(exc))


@runtime_checkable
class Tracer(Protocol):
    """Tracer protocol for agent run observability."""

    @property
    def is_enabled(self) -> bool: ...

    def create_trace_id(self) -> Optional[str]: ...

    def start_span(
        self,
        trace_id: Optional[str],
        name: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        input_data: Optional[Any] = None,
    ) -> SpanContext: ...

    def start_generation(
        self,
        trace_id: Optional[str],
        name: str,
        model: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        input_data: Optional[Any] = None,
        model_parameters: Optional[Dict[str, Any]] = None,
        usage: Optional[Dict[str, int]] = None,
        cost: Optional[Dict[str, float]] = None,
    ) -> GenerationContext: ...

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


class NoOpTracer:
    """Safe no-op tracer used when Langfuse credentials are not configured."""

    @property
    def is_enabled(self) -> bool:
        return False

    def create_trace_id(self) -> Optional[str]:
        return None

    def start_span(
        self,
        trace_id: Optional[str],
        name: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        input_data: Optional[Any] = None,
    ) -> SpanContext:
        return SpanContext(observation_id=None, client_span=None)

    def start_generation(
        self,
        trace_id: Optional[str],
        name: str,
        model: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        input_data: Optional[Any] = None,
        model_parameters: Optional[Dict[str, Any]] = None,
        usage: Optional[Dict[str, int]] = None,
        cost: Optional[Dict[str, float]] = None,
    ) -> GenerationContext:
        return GenerationContext(generation_id=None, client_gen=None)

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


class LangfuseTracer:
    """Live Langfuse tracer backed by the official Langfuse SDK."""

    def __init__(self, public_key: str, secret_key: str, host: str) -> None:
        from langfuse import Langfuse

        self._client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )

    @property
    def is_enabled(self) -> bool:
        return True

    def create_trace_id(self) -> Optional[str]:
        try:
            return self._client.create_trace_id()
        except Exception as exc:
            logger.warning("Failed to generate trace ID via Langfuse", error=str(exc))
            return None

    def start_span(
        self,
        trace_id: Optional[str],
        name: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        input_data: Optional[Any] = None,
    ) -> SpanContext:
        if not trace_id:
            return SpanContext(observation_id=None, client_span=None)
        try:
            trace_ctx: Dict[str, Any] = {"trace_id": trace_id}
            if parent_id:
                trace_ctx["parent_span_id"] = parent_id

            client = cast(Any, self._client)
            span = client.start_observation(
                trace_context=trace_ctx,
                name=name,
                as_type="span",
                metadata=sanitize_metadata(metadata),
                input=sanitize_payload(input_data),
            )
            return SpanContext(observation_id=str(span.id), client_span=span)
        except Exception as exc:
            logger.warning("Failed to start Langfuse span observation", span_name=name, error=str(exc))
            return SpanContext(observation_id=None, client_span=None)

    def start_generation(
        self,
        trace_id: Optional[str],
        name: str,
        model: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        input_data: Optional[Any] = None,
        model_parameters: Optional[Dict[str, Any]] = None,
        usage: Optional[Dict[str, int]] = None,
        cost: Optional[Dict[str, float]] = None,
    ) -> GenerationContext:
        if not trace_id:
            return GenerationContext(generation_id=None, client_gen=None)
        try:
            trace_ctx: Dict[str, Any] = {"trace_id": trace_id}
            if parent_id:
                trace_ctx["parent_span_id"] = parent_id

            client = cast(Any, self._client)
            gen = client.start_observation(
                trace_context=trace_ctx,
                name=name,
                as_type="generation",
                model=model,
                metadata=sanitize_metadata(metadata),
                input=sanitize_payload(input_data),
                model_parameters=sanitize_payload(model_parameters),
                usage_details=usage,
                cost_details=cost,
            )
            return GenerationContext(generation_id=str(gen.id), client_gen=gen)
        except Exception as exc:
            logger.warning("Failed to start Langfuse generation observation", gen_name=name, error=str(exc))
            return GenerationContext(generation_id=None, client_gen=None)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception as exc:
            logger.warning("Failed to flush Langfuse events", error=str(exc))

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception as exc:
            logger.warning("Failed to shutdown Langfuse client", error=str(exc))


_global_tracer: Optional[Tracer] = None


def get_tracer(settings: Optional[Settings] = None) -> Tracer:
    """Factory to retrieve or construct the active Tracer singleton."""
    global _global_tracer
    if _global_tracer is not None:
        return _global_tracer

    s = settings or default_settings
    if s.langfuse_enabled and s.LANGFUSE_PUBLIC_KEY and s.LANGFUSE_SECRET_KEY:
        try:
            _global_tracer = LangfuseTracer(
                public_key=s.LANGFUSE_PUBLIC_KEY,
                secret_key=s.LANGFUSE_SECRET_KEY.get_secret_value(),
                host=s.LANGFUSE_HOST,
            )
            return _global_tracer
        except Exception as exc:
            logger.warning("Failed to initialize LangfuseTracer; falling back to NoOpTracer", error=str(exc))
            _global_tracer = NoOpTracer()
            return _global_tracer

    _global_tracer = NoOpTracer()
    return _global_tracer


def reset_tracer() -> None:
    """Reset singleton tracer (primarily for testing)."""
    global _global_tracer
    _global_tracer = None
