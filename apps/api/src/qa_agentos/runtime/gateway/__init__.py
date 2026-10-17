"""Model gateway: capability resolution and the LiteLLM HTTP client."""

from qa_agentos.runtime.gateway.client import (
    ChatMessage,
    CompletionResult,
    LiteLLMClient,
    StructuredOutputSpec,
    TokenUsage,
)
from qa_agentos.runtime.gateway.errors import (
    ModelGatewayAuthError,
    ModelGatewayError,
    ModelGatewayTimeoutError,
    ModelGatewayUnavailableError,
    ModelRateLimitedError,
    ModelRequestRejectedError,
    ModelResponseInvalidError,
    ModelStructuredOutputUnsupportedError,
    ModelUpstreamError,
    NoModelForCapabilityError,
)
from qa_agentos.runtime.gateway.resolution import (
    MOCK_PROVIDERS,
    CapabilityRequirement,
    ModelResolver,
    ProviderPolicy,
    ResolvedModel,
    select_model,
)

__all__ = [
    "ChatMessage",
    "CompletionResult",
    "LiteLLMClient",
    "StructuredOutputSpec",
    "TokenUsage",
    "ModelGatewayError",
    "ModelGatewayAuthError",
    "ModelGatewayTimeoutError",
    "ModelGatewayUnavailableError",
    "ModelRateLimitedError",
    "ModelRequestRejectedError",
    "ModelResponseInvalidError",
    "ModelStructuredOutputUnsupportedError",
    "ModelUpstreamError",
    "NoModelForCapabilityError",
    "MOCK_PROVIDERS",
    "CapabilityRequirement",
    "ModelResolver",
    "ProviderPolicy",
    "ResolvedModel",
    "select_model",
]
