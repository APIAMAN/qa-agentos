"""Typed errors raised by model resolution and the LiteLLM gateway client.

All errors follow the project's ``APIError`` convention (stable ``code``, human
``message``, structured ``details``). Messages and details never include API keys,
authorization headers, or raw upstream response bodies (which may echo provider
credentials).
"""

from typing import Any, Optional

from fastapi import status

from qa_agentos.core.errors import APIError


class ModelGatewayError(APIError):
    """Base class for all model resolution / gateway failures."""


class NoModelForCapabilityError(ModelGatewayError):
    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="NO_MODEL_FOR_CAPABILITY",
            message=message,
            details=details,
        )


class ModelStructuredOutputUnsupportedError(ModelGatewayError):
    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="MODEL_STRUCTURED_OUTPUT_UNSUPPORTED",
            message=message,
            details=details,
        )


class ModelGatewayTimeoutError(ModelGatewayError):
    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            code="MODEL_GATEWAY_TIMEOUT",
            message=message,
            details=details,
        )


class ModelGatewayUnavailableError(ModelGatewayError):
    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="MODEL_GATEWAY_UNAVAILABLE",
            message=message,
            details=details,
        )


class ModelRateLimitedError(ModelGatewayError):
    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="MODEL_RATE_LIMITED",
            message=message,
            details=details,
        )


class ModelUpstreamError(ModelGatewayError):
    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="MODEL_UPSTREAM_ERROR",
            message=message,
            details=details,
        )


class ModelGatewayAuthError(ModelGatewayError):
    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="MODEL_GATEWAY_AUTH_FAILED",
            message=message,
            details=details,
        )


class ModelRequestRejectedError(ModelGatewayError):
    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="MODEL_REQUEST_REJECTED",
            message=message,
            details=details,
        )


class ModelResponseInvalidError(ModelGatewayError):
    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="MODEL_RESPONSE_INVALID",
            message=message,
            details=details,
        )
