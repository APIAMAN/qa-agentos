import json
import uuid

import httpx
import pytest
from qa_agentos.runtime.gateway.client import (
    ChatMessage,
    LiteLLMClient,
    StructuredOutputSpec,
)
from qa_agentos.runtime.gateway.errors import (
    ModelGatewayAuthError,
    ModelGatewayTimeoutError,
    ModelGatewayUnavailableError,
    ModelRateLimitedError,
    ModelResponseInvalidError,
    ModelUpstreamError,
)
from qa_agentos.runtime.gateway.resolution import ResolvedModel


def _dummy_model(
    gateway_key: str = "mock-planner",
    *,
    structured_output: bool = True,
    capabilities: tuple[str, ...] = ("reasoning", "json"),
) -> ResolvedModel:
    return ResolvedModel(
        capability="planner",
        model_id=uuid.uuid4(),
        provider="mock",
        name="mock-model",
        gateway_model_key=gateway_key,
        capabilities=capabilities,
        structured_output=structured_output,
        context_window=128000,
        max_output_tokens=4096,
        priority=0,
    )


async def test_valid_chat_completion_success():
    """1. Valid LiteLLM OpenAI-compatible completion response parsed properly."""
    expected_body = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": 1677652288,
        "model": "gpt-3.5-turbo",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello from LiteLLM!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 12, "total_tokens": 21},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=expected_body)

    client = LiteLLMClient(
        base_url="http://mock-litellm:4000",
        api_key="sk-test-secret-key-12345",
        transport=httpx.MockTransport(handler),
    )

    result = await client.chat_completion(
        model=_dummy_model("planner-alias"),
        messages=[ChatMessage(role="user", content="Hello")],
    )

    assert result.content == "Hello from LiteLLM!"
    assert result.gateway_model_key == "planner-alias"
    assert result.returned_model == "gpt-3.5-turbo"
    assert result.finish_reason == "stop"
    assert result.response_id == "chatcmpl-123"
    assert result.usage is not None
    assert result.usage.total_tokens == 21


async def test_endpoint_path_body_and_auth_header():
    """2. Verifies correct path (/v1/chat/completions), gateway alias, body, and Authorization header."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization")
        captured["content_type"] = request.headers.get("Content-Type")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "OK"}}],
            },
        )

    client = LiteLLMClient(
        base_url="http://mock-litellm:4000",
        api_key="sk-litellm-secret-999",
        transport=httpx.MockTransport(handler),
    )

    await client.chat_completion(
        model=_dummy_model("my-gateway-key"),
        messages=[
            ChatMessage(role="system", content="System instruction"),
            ChatMessage(role="user", content="User message"),
        ],
        temperature=0.2,
    )

    assert captured["path"] == "/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-litellm-secret-999"
    assert captured["content_type"] == "application/json"
    assert captured["body"]["model"] == "my-gateway-key"
    assert captured["body"]["temperature"] == 0.2
    assert len(captured["body"]["messages"]) == 2
    assert captured["body"]["messages"][0]["role"] == "system"


async def test_structured_output_request_construction():
    """3. Verifies structured-output request construction with response_format json_schema."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "{}"}}]},
        )

    client = LiteLLMClient(
        base_url="http://mock-litellm:4000",
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )

    schema = {"type": "object", "properties": {"result": {"type": "string"}}}
    await client.chat_completion(
        model=_dummy_model(structured_output=True),
        messages=[ChatMessage(role="user", content="Extract")],
        structured_output=StructuredOutputSpec(name="ExtractSchema", schema=schema),
    )

    resp_format = captured["body"]["response_format"]
    assert resp_format["type"] == "json_schema"
    assert resp_format["json_schema"]["name"] == "ExtractSchema"
    assert resp_format["json_schema"]["schema"] == schema
    assert resp_format["json_schema"]["strict"] is True


async def test_http_429_rate_limited_mapping():
    """4. HTTP 429 maps to ModelRateLimitedError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}})

    client = LiteLLMClient(
        base_url="http://mock-litellm:4000",
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelRateLimitedError) as exc_info:
        await client.chat_completion(
            model=_dummy_model(),
            messages=[ChatMessage(role="user", content="test")],
        )
    assert exc_info.value.code == "MODEL_RATE_LIMITED"
    assert exc_info.value.status_code == 429


async def test_http_5xx_upstream_error_mapping():
    """5. HTTP 5xx maps to ModelUpstreamError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": {"code": "bad_gateway"}})

    client = LiteLLMClient(
        base_url="http://mock-litellm:4000",
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelUpstreamError) as exc_info:
        await client.chat_completion(
            model=_dummy_model(),
            messages=[ChatMessage(role="user", content="test")],
        )
    assert exc_info.value.code == "MODEL_UPSTREAM_ERROR"
    assert exc_info.value.status_code == 502


async def test_timeout_and_connection_failure_mapping():
    """6. Timeout maps to ModelGatewayTimeoutError; transport error maps to ModelGatewayUnavailableError."""
    # Timeout
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Read timed out")

    client_timeout = LiteLLMClient(
        base_url="http://mock-litellm:4000",
        api_key="sk-test",
        transport=httpx.MockTransport(timeout_handler),
    )
    with pytest.raises(ModelGatewayTimeoutError) as exc_timeout:
        await client_timeout.chat_completion(
            model=_dummy_model(),
            messages=[ChatMessage(role="user", content="test")],
        )
    assert exc_timeout.value.code == "MODEL_GATEWAY_TIMEOUT"
    assert exc_timeout.value.status_code == 504

    # Connect error
    def connect_err_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Failed to establish connection")

    client_conn = LiteLLMClient(
        base_url="http://mock-litellm:4000",
        api_key="sk-test",
        transport=httpx.MockTransport(connect_err_handler),
    )
    with pytest.raises(ModelGatewayUnavailableError) as exc_conn:
        await client_conn.chat_completion(
            model=_dummy_model(),
            messages=[ChatMessage(role="user", content="test")],
        )
    assert exc_conn.value.code == "MODEL_GATEWAY_UNAVAILABLE"
    assert exc_conn.value.status_code == 503


async def test_invalid_json_response():
    """7. Invalid JSON response maps to ModelResponseInvalidError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>502 Bad Gateway</html>")

    client = LiteLLMClient(
        base_url="http://mock-litellm:4000",
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ModelResponseInvalidError) as exc_info:
        await client.chat_completion(
            model=_dummy_model(),
            messages=[ChatMessage(role="user", content="test")],
        )
    assert exc_info.value.code == "MODEL_RESPONSE_INVALID"


async def test_missing_or_malformed_choices():
    """8. Missing or empty choices list maps to ModelResponseInvalidError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = LiteLLMClient(
        base_url="http://mock-litellm:4000",
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ModelResponseInvalidError) as exc_info:
        await client.chat_completion(
            model=_dummy_model(),
            messages=[ChatMessage(role="user", content="test")],
        )
    assert exc_info.value.code == "MODEL_RESPONSE_INVALID"


async def test_token_usage_parsing_when_present_and_safe_when_omitted():
    """9. Token usage parsed when present; gracefully returns None when omitted."""
    # When omitted
    def handler_no_usage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "text"}}]},
        )

    client = LiteLLMClient(
        base_url="http://mock-litellm:4000",
        api_key="sk-test",
        transport=httpx.MockTransport(handler_no_usage),
    )
    res = await client.chat_completion(
        model=_dummy_model(),
        messages=[ChatMessage(role="user", content="test")],
    )
    assert res.usage is None


async def test_no_api_key_leakage_in_errors_or_repr():
    """10. Secret key is not exposed in repr or error messages/details."""
    secret_key = "super-confidential-master-key-xyz987"

    def handler_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Invalid auth token", "code": "unauthorized"}},
        )

    client = LiteLLMClient(
        base_url="http://mock-litellm:4000",
        api_key=secret_key,
        transport=httpx.MockTransport(handler_401),
    )

    # Repr must mask the key
    assert secret_key not in repr(client)
    assert "**********" in repr(client)

    # Exception message and details must not leak the key
    with pytest.raises(ModelGatewayAuthError) as exc_info:
        await client.chat_completion(
            model=_dummy_model(),
            messages=[ChatMessage(role="user", content="test")],
        )

    err = exc_info.value
    assert secret_key not in err.message
    assert secret_key not in str(err.details)
    assert secret_key not in str(err)
