import uuid
from typing import List

import pytest
from qa_agentos.models.domain import ModelCatalog
from qa_agentos.runtime.gateway.errors import NoModelForCapabilityError
from qa_agentos.runtime.gateway.resolution import (
    CapabilityRequirement,
    ModelResolver,
    ProviderPolicy,
    select_model,
)
from sqlalchemy.ext.asyncio import AsyncSession


def _make_model(
    name: str,
    provider: str,
    gateway_key: str,
    *,
    priority: int = 0,
    capabilities: List[str] | None = None,
    structured_output: bool = True,
    context_window: int = 128000,
    status: str = "available",
    is_active: bool = True,
) -> ModelCatalog:
    return ModelCatalog(
        id=uuid.uuid4(),
        name=name,
        provider=provider,
        gateway_model_key=gateway_key,
        priority=priority,
        capabilities=capabilities or ["reasoning", "json", "planner"],
        structured_output=structured_output,
        context_window=context_window,
        status=status,
        is_active=is_active,
    )


def test_select_lowest_numerical_priority():
    """Rule 1: Chooses the eligible model with the lowest numerical priority."""
    policy = ProviderPolicy(allowed_providers=frozenset({"nvidia_nim", "gemini"}), environment="test")
    req = CapabilityRequirement(
        capability="planner", required_capabilities=("reasoning", "json"), structured_output=True
    )

    m_low = _make_model("model-a", "nvidia_nim", "planner-a", priority=10)
    m_high = _make_model("model-b", "gemini", "planner-b", priority=0)

    chosen = select_model([m_low, m_high], req, policy)
    assert chosen.provider == "gemini"
    assert chosen.gateway_model_key == "planner-b"


def test_changing_priority_changes_selection_without_code_changes():
    """Rule 2: Changing model priorities flips the selected model dynamically."""
    policy = ProviderPolicy(allowed_providers=frozenset({"nvidia_nim", "gemini"}), environment="test")
    req = CapabilityRequirement(capability="planner", required_capabilities=("reasoning", "json"))

    m1 = _make_model("model-1", "nvidia_nim", "key-1", priority=5)
    m2 = _make_model("model-2", "gemini", "key-2", priority=10)

    assert select_model([m1, m2], req, policy).gateway_model_key == "key-1"

    # Flip priority on m2 in data
    m2.priority = 1
    assert select_model([m1, m2], req, policy).gateway_model_key == "key-2"


def test_ignores_non_available_status():
    """Rule 3: Ignores models whose status is degraded or unavailable."""
    policy = ProviderPolicy(allowed_providers=frozenset({"nvidia_nim", "gemini"}), environment="test")
    req = CapabilityRequirement(capability="planner", required_capabilities=("reasoning",))

    m_degraded = _make_model("model-deg", "nvidia_nim", "key-deg", priority=0, status="degraded")
    m_unavail = _make_model("model-un", "gemini", "key-un", priority=1, status="unavailable")
    m_avail = _make_model("model-ok", "gemini", "key-ok", priority=10, status="available")

    chosen = select_model([m_degraded, m_unavail, m_avail], req, policy)
    assert chosen.gateway_model_key == "key-ok"


def test_rejects_missing_capabilities():
    """Rule 4: Rejects models that do not satisfy all required capabilities."""
    policy = ProviderPolicy(allowed_providers=frozenset({"nvidia_nim"}), environment="test")
    req = CapabilityRequirement(capability="planner", required_capabilities=("reasoning", "vision"))

    m_no_vision = _make_model("model-1", "nvidia_nim", "key-1", capabilities=["reasoning", "json"])

    with pytest.raises(NoModelForCapabilityError) as exc_info:
        select_model([m_no_vision], req, policy)
    assert exc_info.value.code == "NO_MODEL_FOR_CAPABILITY"
    assert "vision" in str(exc_info.value.details)


def test_enforces_structured_output_requirement():
    """Rule 5: Enforces structured_output requirement when requested by slot."""
    policy = ProviderPolicy(allowed_providers=frozenset({"nvidia_nim"}), environment="test")
    req = CapabilityRequirement(capability="planner", required_capabilities=("json",), structured_output=True)

    m_no_struct = _make_model("model-no-struct", "nvidia_nim", "key-1", structured_output=False, priority=0)
    m_struct = _make_model("model-struct", "nvidia_nim", "key-2", structured_output=True, priority=5)

    chosen = select_model([m_no_struct, m_struct], req, policy)
    assert chosen.gateway_model_key == "key-2"


def test_rejects_providers_excluded_by_allowlist():
    """Rule 6: Rejects models belonging to providers outside the allowlist."""
    policy = ProviderPolicy(allowed_providers=frozenset({"nvidia_nim"}), environment="test")
    req = CapabilityRequirement(capability="planner", required_capabilities=("json",))

    m_gemini = _make_model("model-gemini", "gemini", "key-gemini", priority=0)

    with pytest.raises(NoModelForCapabilityError) as exc_info:
        select_model([m_gemini], req, policy)
    assert exc_info.value.code == "NO_MODEL_FOR_CAPABILITY"
    assert "provider_not_allowlisted" in str(exc_info.value.details)


def test_mock_provider_forbidden_outside_test_environment():
    """Rule 7: Mock provider cannot be selected in production or development environments."""
    for env in ("production", "development", "staging"):
        policy = ProviderPolicy(allowed_providers=frozenset({"mock", "nvidia_nim"}), environment=env)
        req = CapabilityRequirement(capability="planner", required_capabilities=("json",))
        m_mock = _make_model("mock-json", "mock", "mock-json", priority=0)

        with pytest.raises(NoModelForCapabilityError) as exc_info:
            select_model([m_mock], req, policy)
        assert exc_info.value.code == "NO_MODEL_FOR_CAPABILITY"
        assert "mock_provider_forbidden_outside_test" in str(exc_info.value.details)

    # Allowed in test environment
    test_policy = ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test")
    m_mock = _make_model("mock-json", "mock", "mock-json", priority=0)
    chosen = select_model([m_mock], req, test_policy)
    assert chosen.provider == "mock"


def test_returns_typed_error_when_no_eligible_model_exists():
    """Rule 8: Returns typed NoModelForCapabilityError with clear details when no candidates qualify."""
    policy = ProviderPolicy(allowed_providers=frozenset({"nvidia_nim"}), environment="test")
    req = CapabilityRequirement(capability="classifier", required_capabilities=("reasoning",))

    with pytest.raises(NoModelForCapabilityError) as exc_info:
        select_model([], req, policy)
    assert exc_info.value.code == "NO_MODEL_FOR_CAPABILITY"
    assert exc_info.value.status_code == 503


async def test_resolver_uses_database_catalog(pg_session: AsyncSession):
    """Rule 9: Uses real database-backed catalog query rather than hard-coded models."""
    policy = ProviderPolicy(allowed_providers=frozenset({"nvidia_nim", "gemini"}), environment="test")
    resolver = ModelResolver(policy)

    # Insert two dynamic models in PostgreSQL session
    m1 = _make_model("db-dynamic-nim", "nvidia_nim", "gateway-nim", priority=10, capabilities=["reasoning", "json"])
    m2 = _make_model("db-dynamic-gemini", "gemini", "gateway-gemini", priority=2, capabilities=["reasoning", "json"])
    pg_session.add(m1)
    pg_session.add(m2)
    await pg_session.flush()

    reqs = {
        "planner": {
            "capabilities": ["reasoning", "json"],
            "structured_output": True,
            "min_context_window": 32000,
        }
    }

    resolved = await resolver.resolve(pg_session, reqs, "planner")
    assert resolved.provider == "gemini"
    assert resolved.gateway_model_key == "gateway-gemini"
    assert resolved.model_id == m2.id
