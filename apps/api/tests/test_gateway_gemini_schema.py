"""Gemini request-schema compatibility and redacted upstream error messages (offline, no model calls)."""

import copy
import json
import uuid
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
import yaml
from qa_agentos.runtime.gateway.client import (
    ChatMessage,
    LiteLLMClient,
    StructuredOutputSpec,
    gemini_compatible_schema,
)
from qa_agentos.runtime.gateway.errors import ModelRequestRejectedError
from qa_agentos.runtime.gateway.resolution import ResolvedModel

REPO = Path(__file__).resolve().parents[3]
BUG_SCHEMA: Dict[str, Any] = yaml.safe_load((REPO / "agents" / "bug-analyzer.yaml").read_text(encoding="utf-8"))[
    "output_schema"
]
NULLABLE_TOP = ("root_cause", "primary_signature", "affected_component")


def _model(provider: str) -> ResolvedModel:
    return ResolvedModel(
        capability="planner",
        model_id=uuid.uuid4(),
        provider=provider,
        name="m",
        gateway_model_key="classifier",
        capabilities=("reasoning", "json"),
        structured_output=True,
        context_window=128000,
        max_output_tokens=4096,
        priority=0,
    )


def _sent_schema(provider: str) -> Dict[str, Any]:
    client = LiteLLMClient(base_url="http://gw", api_key="sk-test")
    payload = client.build_payload(
        _model(provider),
        [ChatMessage(role="user", content="x")],
        structured_output=StructuredOutputSpec(name="bug_analyzer_output", schema=BUG_SCHEMA),
    )
    return payload["response_format"]["json_schema"]["schema"]


def test_gemini_request_uses_nullable_for_all_four_fields() -> None:
    sent = _sent_schema("gemini")
    props = sent["properties"]
    for name in NULLABLE_TOP:
        assert props[name]["type"] == "string" and props[name]["nullable"] is True, name
    sig = props["evidence"]["items"]["properties"]["signature"]
    assert sig == {"type": "string", "nullable": True, "pattern": "^sig_[0-9a-f]{16}$"}
    # No type list may remain anywhere in the outgoing schema.
    assert '"type": [' not in json.dumps(sent)


def test_non_nullable_fields_unchanged() -> None:
    props = _sent_schema("gemini")["properties"]
    for name in ("title", "severity", "category", "summary", "repro_steps", "confidence"):
        assert props[name] == BUG_SCHEMA["properties"][name], name
        assert "nullable" not in props[name]
    assert props["evidence"]["items"]["properties"]["detail"] == {"type": "string", "maxLength": 1000}


@pytest.mark.parametrize("provider", ["nvidia_nim", "mock", "openai"])
def test_non_gemini_providers_unchanged(provider: str) -> None:
    assert _sent_schema(provider) == BUG_SCHEMA


def test_original_schema_not_mutated_and_idempotent() -> None:
    before = copy.deepcopy(BUG_SCHEMA)
    once = gemini_compatible_schema(BUG_SCHEMA)
    assert BUG_SCHEMA == before
    assert gemini_compatible_schema(once) == once
    assert gemini_compatible_schema({"type": ["string", "number", "null"]}) == {"type": ["string", "number", "null"]}


async def test_upstream_message_kept_and_redacted() -> None:
    master = "sk-local-master-secret-123456"
    google_msg = (
        "Invalid JSON payload received. Unknown name \"type\" at 'generation_config.response_schema.properties[4].value'. "
        "key=AIzaSyA1234567890abcdefghijklmnopqrstuv AQ.Ab8RN6Lfake_new_format_key_0123456789 "
        f"Authorization: Bearer {master}"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": google_msg + " " + "x" * 2000, "code": "400"}})

    client = LiteLLMClient(base_url="http://gw", api_key=master, transport=httpx.MockTransport(handler))
    with pytest.raises(ModelRequestRejectedError) as exc:
        await client.chat_completion(model=_model("gemini"), messages=[ChatMessage(role="user", content="x")])
    await client.aclose()
    msg = exc.value.details["upstream_message"]
    assert "generation_config.response_schema.properties[4].value" in msg
    for secret in (master, "AIzaSyA1234567890abcdefghijklmnopqrstuv", "AQ.Ab8RN6Lfake_new_format_key_0123456789"):
        assert secret not in msg and secret not in str(exc.value.details) and secret not in str(exc.value)
    assert len(msg) <= 500
    assert "headers" not in exc.value.details and "messages" not in exc.value.details
