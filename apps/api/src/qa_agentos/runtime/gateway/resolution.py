"""Capability-based model resolution against the database-backed model catalog.

Selection rules (applied in order):

1. The agent version's ``model_requirements`` must declare the requested capability slot.
2. Only catalog rows with ``status == "available"`` and ``is_active`` are considered.
3. The provider must be permitted by the :class:`ProviderPolicy` (explicit allowlist;
   mock providers are additionally restricted to ``ENVIRONMENT == "test"``).
4. The model's ``capabilities`` must be a superset of the slot's required capabilities.
5. If the slot requires ``structured_output``, the model must support it.
6. If the slot declares ``min_context_window``, the model's context window must meet it.
7. Among eligible models, the lowest ``priority`` wins; ties are broken
   deterministically by ``(provider, name, id)``.

Nothing here hard-codes a provider or model name: changing ``priority`` or ``status``
in the registry changes the outcome without any code change.
"""
import uuid
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from qa_agentos.core.config import Settings
from qa_agentos.models.domain import ModelCatalog
from qa_agentos.runtime.gateway.errors import NoModelForCapabilityError

#: Providers that never call a real model and must not serve normal traffic.
MOCK_PROVIDERS: FrozenSet[str] = frozenset({"mock"})

#: The only environment in which mock providers may be selected.
TEST_ENVIRONMENT = "test"

AVAILABLE_STATUS = "available"


@dataclass(frozen=True)
class ProviderPolicy:
    """Which providers may be selected by the resolver."""

    allowed_providers: FrozenSet[str]
    environment: str

    def permits(self, provider: str) -> bool:
        if provider in MOCK_PROVIDERS and self.environment != TEST_ENVIRONMENT:
            return False
        return provider in self.allowed_providers

    def rejection_reason(self, provider: str) -> Optional[str]:
        if provider in MOCK_PROVIDERS and self.environment != TEST_ENVIRONMENT:
            return "mock_provider_forbidden_outside_test"
        if provider not in self.allowed_providers:
            return "provider_not_allowlisted"
        return None

    @classmethod
    def from_settings(cls, settings: Settings) -> "ProviderPolicy":
        return cls(
            allowed_providers=settings.model_provider_allowlist,
            environment=settings.ENVIRONMENT.strip().lower(),
        )


@dataclass(frozen=True)
class CapabilityRequirement:
    """Normalized requirement for one capability slot of an agent version."""

    capability: str
    required_capabilities: Tuple[str, ...]
    structured_output: bool = False
    min_context_window: Optional[int] = None

    @classmethod
    def from_model_requirements(
        cls, model_requirements: Mapping[str, Any], capability: str
    ) -> "CapabilityRequirement":
        slot = model_requirements.get(capability) if model_requirements else None
        if not isinstance(slot, Mapping):
            raise NoModelForCapabilityError(
                f"Agent version does not declare model requirements for capability '{capability}'.",
                details={
                    "capability": capability,
                    "declared_capabilities": sorted(model_requirements.keys()) if model_requirements else [],
                },
            )
        raw_caps = slot.get("capabilities", [])
        if not isinstance(raw_caps, list) or not all(isinstance(c, str) for c in raw_caps):
            raise NoModelForCapabilityError(
                f"Model requirements for capability '{capability}' have an invalid 'capabilities' list.",
                details={"capability": capability},
            )
        min_ctx = slot.get("min_context_window")
        return cls(
            capability=capability,
            required_capabilities=tuple(sorted(set(raw_caps))),
            structured_output=bool(slot.get("structured_output", False)),
            min_context_window=int(min_ctx) if min_ctx is not None else None,
        )


@dataclass(frozen=True)
class ResolvedModel:
    """Immutable snapshot of the model chosen for a capability slot."""

    capability: str
    model_id: uuid.UUID
    provider: str
    name: str
    gateway_model_key: str
    capabilities: Tuple[str, ...]
    structured_output: bool
    context_window: int
    max_output_tokens: Optional[int]
    priority: int

    @property
    def supports_json_mode(self) -> bool:
        return "json" in self.capabilities

    def snapshot(self) -> Dict[str, Any]:
        """JSON-serializable form, suitable for a run's ``resolved_models`` snapshot."""
        return {
            "capability": self.capability,
            "model_id": str(self.model_id),
            "provider": self.provider,
            "name": self.name,
            "gateway_model_key": self.gateway_model_key,
            "capabilities": list(self.capabilities),
            "structured_output": self.structured_output,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "priority": self.priority,
        }


def _rejection_reason(
    model: ModelCatalog, requirement: CapabilityRequirement, policy: ProviderPolicy
) -> Optional[str]:
    if model.status != AVAILABLE_STATUS or not model.is_active:
        return f"status_{model.status}" if model.status != AVAILABLE_STATUS else "inactive"
    provider_reason = policy.rejection_reason(model.provider)
    if provider_reason:
        return provider_reason
    model_caps = set(model.capabilities or [])
    missing = [c for c in requirement.required_capabilities if c not in model_caps]
    if missing:
        return "missing_capabilities:" + ",".join(missing)
    if requirement.structured_output and not model.structured_output:
        return "structured_output_unsupported"
    if requirement.min_context_window is not None and model.context_window < requirement.min_context_window:
        return "context_window_too_small"
    return None


def select_model(
    candidates: Iterable[ModelCatalog],
    requirement: CapabilityRequirement,
    policy: ProviderPolicy,
) -> ResolvedModel:
    """Pure selection over catalog rows. Raises ``NoModelForCapabilityError`` if none qualify."""
    eligible: List[ModelCatalog] = []
    rejections: Dict[str, str] = {}
    for model in candidates:
        reason = _rejection_reason(model, requirement, policy)
        if reason is None:
            eligible.append(model)
        else:
            rejections[f"{model.provider}/{model.name}"] = reason

    if not eligible:
        raise NoModelForCapabilityError(
            f"No eligible model satisfies capability '{requirement.capability}'.",
            details={
                "capability": requirement.capability,
                "required_capabilities": list(requirement.required_capabilities),
                "structured_output": requirement.structured_output,
                "min_context_window": requirement.min_context_window,
                "allowed_providers": sorted(policy.allowed_providers),
                "rejections": rejections,
            },
        )

    chosen = min(eligible, key=lambda m: (m.priority, m.provider, m.name, str(m.id)))
    return ResolvedModel(
        capability=requirement.capability,
        model_id=chosen.id,
        provider=chosen.provider,
        name=chosen.name,
        gateway_model_key=chosen.gateway_model_key,
        capabilities=tuple(chosen.capabilities or []),
        structured_output=chosen.structured_output,
        context_window=chosen.context_window,
        max_output_tokens=chosen.max_output_tokens,
        priority=chosen.priority,
    )


class ModelResolver:
    """Resolves capability slots to catalog models using current database records."""

    def __init__(self, policy: ProviderPolicy) -> None:
        self._policy = policy

    @classmethod
    def from_settings(cls, settings: Settings) -> "ModelResolver":
        return cls(ProviderPolicy.from_settings(settings))

    @property
    def policy(self) -> ProviderPolicy:
        return self._policy

    async def _load_candidates(self, db: AsyncSession) -> Sequence[ModelCatalog]:
        stmt = select(ModelCatalog).where(
            ModelCatalog.status == AVAILABLE_STATUS,
            ModelCatalog.is_active.is_(True),
        )
        return (await db.execute(stmt)).scalars().all()

    async def resolve(
        self,
        db: AsyncSession,
        model_requirements: Mapping[str, Any],
        capability: str,
    ) -> ResolvedModel:
        requirement = CapabilityRequirement.from_model_requirements(model_requirements, capability)
        candidates = await self._load_candidates(db)
        return select_model(candidates, requirement, self._policy)

    async def resolve_all(
        self,
        db: AsyncSession,
        model_requirements: Mapping[str, Any],
    ) -> Dict[str, ResolvedModel]:
        """Resolve every declared capability slot (single catalog query)."""
        candidates = await self._load_candidates(db)
        return {
            slot: select_model(
                candidates,
                CapabilityRequirement.from_model_requirements(model_requirements, slot),
                self._policy,
            )
            for slot in sorted(model_requirements.keys())
        }
