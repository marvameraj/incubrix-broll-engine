from typing import Any, Optional
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        retryable: bool = False,
        details: Optional[dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}
        super().__init__(message)


def build_error_response(
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    correlation_id: str,
    details: Optional[dict[str, Any]] = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "correlation_id": correlation_id,
                "details": details or {},
            }
        },
        headers={"X-Correlation-Id": correlation_id},
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    cid = getattr(request.state, "correlation_id", "unknown")
    return build_error_response(exc.status_code, exc.code, exc.message, exc.retryable, cid, exc.details)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    cid = getattr(request.state, "correlation_id", "unknown")
    msg = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors())
    return build_error_response(422, "VALIDATION_ERROR", msg, False, cid, {"errors": exc.errors()})


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    cid = getattr(request.state, "correlation_id", "unknown")
    return build_error_response(exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail), exc.status_code >= 500, cid)