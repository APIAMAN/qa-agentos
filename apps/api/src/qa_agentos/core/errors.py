from typing import Any, Optional

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse


class APIError(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.details = details


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


class UnauthorizedError(APIError):
    def __init__(self, message: str = "Invalid or missing credentials", details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message=message,
            details=details,
        )


class ForbiddenError(APIError):
    def __init__(self, message: str = "Permission denied", details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN",
            message=message,
            details=details,
        )


class NotFoundError(APIError):
    def __init__(self, message: str = "Resource not found", details: Optional[Any] = None) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code="NOT_FOUND",
            message=message,
            details=details,
        )
