"""Uniform API error type: every error body is ``{"detail", "code"}``.

Author: Xiangzhu Yan
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """An HTTP error with a stable machine-readable ``code``.

    Attributes:
        status_code: HTTP status.
        code: Stable snake_case identifier (e.g. ``invite_code_invalid``).
        detail: Human-readable message.
    """

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def not_found(code: str = "not_found", detail: str = "Not found") -> ApiError:
    """Builds a 404 error (also used to hide resources the caller may not
    see, so existence is never leaked)."""
    return ApiError(404, code, detail)


def forbidden(code: str = "forbidden", detail: str = "Forbidden") -> ApiError:
    """Builds a 403 error."""
    return ApiError(403, code, detail)


def install_error_handlers(app: FastAPI) -> None:
    """Registers the ``ApiError`` → JSON handler on ``app``."""

    @app.exception_handler(ApiError)
    async def _handle(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code},
        )
