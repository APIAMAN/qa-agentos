"""Async HTTP client for the LiteLLM proxy's OpenAI-compatible API.

This is the only path by which QA AgentOS talks to models. It uses ``httpx`` against
LiteLLM (``POST /v1/chat/completions``); provider SDKs and direct provider APIs are
intentionally not used. LiteLLM owns provider routing and fallback, so this client
performs no application-level retries.

Secrets: the master key is held as ``SecretStr``-derived private state, sent only in
the ``Authorization`` header, and never included in errors, logs, or ``repr``.
"""
import json
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Dict, Mapping, Optional, Sequence, Type

import httpx

from qa_agentos.core.config import Settings
from qa_agentos.core.logging import logger
from qa_agentos.runtime.gateway.errors import (
    ModelGatewayAuthError,
    ModelGatewayTimeoutError,
    ModelGatewayUnavailableError,
    ModelRateLimitedError,
    ModelRequestRejectedError,
    ModelResponseInvalidError,
    ModelStructuredOutputUnsupportedError,
    ModelUpstreamError,
)
from qa_agentos.runtime.gateway.resolution import ResolvedModel

CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
_ALLOWED_ROLES = frozenset({"system", "user", "assistant"})


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str

    def to_payload(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class StructuredOutputSpec:
    """Request JSON output conforming to ``schema`` (JSON Schema)."""

    name: str
    schema: Mapping[str, Any]
    strict: bool = True


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]


@dataclass(frozen=True)
class CompletionResult:
    content: str
    gateway_model_key: str
    returned_model: Optional[str]
    finish_reason: Optional[str]
    response_id: Optional[str]
    usage: Optional[TokenUsage]
    response_format_mode: str  # "text" | "json_schema" | "json_object"


def _normalize_base_url(endpoint: str) -> str:
    base = endpoint.strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


def _optional_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _parse_usage(raw: Any) -> Optional[TokenUsage]:
    if not isinstance(raw, dict):
        return None
    usage = TokenUsage(
        prompt_tokens=_optional_int(raw.get("prompt_tokens")),
        completion_tokens=_optional_int(raw.get("completion_tokens")),
        total_tokens=_optional_int(raw.get("total_tokens")),
    )
    if usage.prompt_tokens is None and usage.completion_tokens is None and usage.total_tokens is None:
        return None
    return usage


class LiteLLMClient:
    """Thin, typed client for LiteLLM chat completions."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 60.0,
        http_client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._base_url = _normalize_base_url(base_url)
        self._api_key = api_key
        self._timeout = timeout_seconds
        if http_client is not None:
            self._http = http_client
            self._owns_http = False
        else:
            self._http = httpx.AsyncClient(transport=transport, timeout=timeout_seconds)
            self._owns_http = True

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> "LiteLLMClient":
        return cls(
            base_url=settings.LITELLM_ENDPOINT,
            api_key=settings.LITELLM_MASTER_KEY.get_secret_value(),
            timeout_seconds=settings.LITELLM_TIMEOUT_SECONDS,
            http_client=http_client,
            transport=transport,
        )

    def __repr__(self) -> str:  # never expose the key
        return f"LiteLLMClient(base_url={self._base_url!r}, api_key='**********')"

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "LiteLLMClient":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ request

    def build_payload(
        self,
        model: ResolvedModel,
        messages: Sequence[ChatMessage],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        structured_output: Optional[StructuredOutputSpec] = None,
        json_mode: bool = False,
    ) -> Dict[str, Any]:
        if not messages:
            raise ValueError("At least one chat message is required")
        for m in messages:
            if m.role not in _ALLOWED_ROLES:
                raise ValueError(f"Unsupported chat message role: {m.role!r}")

        payload: Dict[str, Any] = {
            "model": model.gateway_model_key,
            "messages": [m.to_payload() for m in messages],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        if structured_output is not None:
            if model.structured_output:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": structured_output.name,
                        "schema": dict(structured_output.schema),
                        "strict": structured_output.strict,
                    },
                }
            elif model.supports_json_mode:
                # Fall back to JSON mode; schema conformance is validated by the caller.
                payload["response_format"] = {"type": "json_object"}
            else:
                raise ModelStructuredOutputUnsupportedError(
                    "Selected model supports neither JSON-schema structured output nor JSON mode.",
                    details={
                        "capability": model.capability,
                        "provider": model.provider,
                        "gateway_model_key": model.gateway_model_key,
                    },
                )
        elif json_mode:
            if not model.supports_json_mode and not model.structured_output:
                raise ModelStructuredOutputUnsupportedError(
                    "Selected model does not support JSON mode.",
                    details={
                        "capability": model.capability,
                        "provider": model.provider,
                        "gateway_model_key": model.gateway_model_key,
                    },
                )
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def chat_completion(
        self,
        model: ResolvedModel,
        messages: Sequence[ChatMessage],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        structured_output: Optional[StructuredOutputSpec] = None,
        json_mode: bool = False,
        timeout_seconds: Optional[float] = None,
    ) -> CompletionResult:
        payload = self.build_payload(
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            structured_output=structured_output,
            json_mode=json_mode,
        )
        mode = payload.get("response_format", {}).get("type", "text")
        log_ctx = {
            "gateway_model_key": model.gateway_model_key,
            "provider": model.provider,
            "capability": model.capability,
        }
        url = f"{self._base_url}{CHAT_COMPLETIONS_PATH}"
        try:
            response = await self._http.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=timeout_seconds if timeout_seconds is not None else self._timeout,
            )
        except httpx.TimeoutException:
            logger.warning("model_gateway_timeout", **log_ctx)
            raise ModelGatewayTimeoutError(
                "Timed out waiting for the model gateway.", details=dict(log_ctx)
            ) from None
        except httpx.TransportError as exc:
            logger.warning("model_gateway_unreachable", error_type=type(exc).__name__, **log_ctx)
            raise ModelGatewayUnavailableError(
                "Could not connect to the model gateway.",
                details={**log_ctx, "error_type": type(exc).__name__},
            ) from None

        self._raise_for_status(response, log_ctx)
        return self._parse_completion(response, model, mode, log_ctx)

    # ----------------------------------------------------------------- response

    @staticmethod
    def _upstream_error_code(response: httpx.Response) -> Optional[str]:
        """Extract only a short error code/type token; never the free-text message."""
        try:
            body = response.json()
        except ValueError:
            return None
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            for key in ("code", "type"):
                val = err.get(key)
                if isinstance(val, (str, int)) and len(str(val)) <= 64:
                    return str(val)
        return None

    def _raise_for_status(self, response: httpx.Response, log_ctx: Dict[str, Any]) -> None:
        status_code = response.status_code
        if 200 <= status_code < 300:
            return
        details: Dict[str, Any] = {**log_ctx, "status_code": status_code}
        upstream_code = self._upstream_error_code(response)
        if upstream_code is not None:
            details["upstream_code"] = upstream_code
        logger.warning("model_gateway_http_error", **details)

        if status_code == 429:
            raise ModelRateLimitedError("Model gateway rate limit exceeded.", details=details)
        if status_code in (408, 504):
            raise ModelGatewayTimeoutError("Model gateway reported an upstream timeout.", details=details)
        if status_code >= 500:
            raise ModelUpstreamError("Model gateway or upstream provider failed.", details=details)
        if status_code in (401, 403):
            raise ModelGatewayAuthError("Model gateway rejected the client credentials.", details=details)
        raise ModelRequestRejectedError("Model gateway rejected the request.", details=details)

    def _parse_completion(
        self,
        response: httpx.Response,
        model: ResolvedModel,
        mode: str,
        log_ctx: Dict[str, Any],
    ) -> CompletionResult:
        def invalid(reason: str) -> ModelResponseInvalidError:
            logger.warning("model_gateway_invalid_response", reason=reason, **log_ctx)
            return ModelResponseInvalidError(
                "Model gateway returned an invalid completion response.",
                details={**log_ctx, "reason": reason},
            )

        try:
            body = json.loads(response.content)
        except (ValueError, UnicodeDecodeError):
            raise invalid("malformed_json") from None
        if not isinstance(body, dict):
            raise invalid("body_not_object")

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise invalid("missing_choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise invalid("choice_not_object")
        message = first.get("message")
        if not isinstance(message, dict):
            raise invalid("missing_message")
        content = message.get("content")
        if not isinstance(content, str):
            raise invalid("missing_content")

        def opt_str(value: Any) -> Optional[str]:
            return value if isinstance(value, str) else None

        return CompletionResult(
            content=content,
            gateway_model_key=model.gateway_model_key,
            returned_model=opt_str(body.get("model")),
            finish_reason=opt_str(first.get("finish_reason")),
            response_id=opt_str(body.get("id")),
            usage=_parse_usage(body.get("usage")),
            response_format_mode=mode,
        )
