"""Typed errors raised by tool resolution, validation, and dispatch.

All errors inherit from APIError (status_code, code, message, details)
to integrate cleanly with FastAPI error envelopes while keeping internal
traces and credentials protected.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import status

from qa_agentos.core.errors import APIError


class ToolError(APIError):
    """Base class for all tool dispatcher errors."""


class ToolNotFoundError(ToolError):
    """Raised when the requested tool does not exist in the catalog."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code="TOOL_NOT_FOUND",
            message=message,
            details=details,
        )


class ToolVersionUnavailableError(ToolError):
    """Raised when the requested tool version does not exist or is inactive."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code="TOOL_VERSION_UNAVAILABLE",
            message=message,
            details=details,
        )


class ToolNotAllowedError(ToolError):
    """Raised when an agent version is not permitted to invoke the requested tool."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            code="TOOL_NOT_ALLOWED",
            message=message,
            details=details,
        )


class ToolInputInvalidError(ToolError):
    """Raised when tool input fails JSON Schema validation."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="TOOL_INPUT_INVALID",
            message=message,
            details=details,
        )


class ToolPermissionDeniedError(ToolError):
    """Raised when caller/agent lacks required tool execution permissions."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            code="TOOL_PERMISSION_DENIED",
            message=message,
            details=details,
        )


class ToolTimeoutError(ToolError):
    """Raised when tool execution exhausts configured invocation timeout."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            code="TOOL_TIMEOUT",
            message=message,
            details=details,
        )


class ToolOutputInvalidError(ToolError):
    """Raised when tool execution output fails schema validation or serialization."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="TOOL_OUTPUT_INVALID",
            message=message,
            details=details,
        )


class ToolImplementationUnsupportedError(ToolError):
    """Raised when a tool's kind or implementation identifier is unsupported."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            code="TOOL_IMPLEMENTATION_UNSUPPORTED",
            message=message,
            details=details,
        )


class ToolExecutionFailedError(ToolError):
    """Raised when a tool execution fails due to runtime or upstream error."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="TOOL_EXECUTION_FAILED",
            message=message,
            details=details,
        )


class ToolStepConflictError(ToolError):
    """Raised when attempting to reuse a completed run step with conflicting input data."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            code="TOOL_STEP_CONFLICT",
            message=message,
            details=details,
        )
