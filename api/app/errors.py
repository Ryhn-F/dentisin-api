"""Consistent error model: every non-2xx body is

    {"error": {"code": "<stable code>", "message": "<short, no data>", "request_id": "..."}}

Messages are fixed strings chosen by the server; they never echo client input,
tokens, storage paths or exception text, and never contain stack traces.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .repositories.base import RepositoryError

log = logging.getLogger("dentisai.api.errors")


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


# Shorthands used across routers/services ------------------------------------------
def unauthorized(code: str = "unauthorized", message: str = "Authentication required.") -> ApiError:
    return ApiError(401, code, message, headers={"WWW-Authenticate": 'Bearer realm="dentisai"'})


def forbidden(message: str = "This role may not use this endpoint.") -> ApiError:
    return ApiError(403, "forbidden", message)


def not_found(code: str = "not_found", message: str = "Not found.") -> ApiError:
    return ApiError(404, code, message)


def conflict(code: str, message: str) -> ApiError:
    return ApiError(409, code, message)


def unavailable(code: str, message: str) -> ApiError:
    return ApiError(503, code, message)


_STATUS_CODES: dict[int, tuple[str, str]] = {
    400: ("bad_request", "Bad request."),
    401: ("unauthorized", "Authentication required."),
    403: ("forbidden", "Forbidden."),
    404: ("not_found", "Not found."),
    405: ("method_not_allowed", "Method not allowed."),
    406: ("not_acceptable", "Not acceptable."),
    413: ("payload_too_large", "Request body too large."),
    415: ("unsupported_media_type", "Unsupported media type."),
    429: ("rate_limited", "Too many requests."),
}


def request_id_of(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid if isinstance(rid, str) else "-"


def error_body(code: str, message: str, request_id: str, **extra: Any) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    err.update(extra)
    return {"error": err}


def error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(code, message, request_id, **extra),
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(
            exc.status_code, exc.code, exc.message, request_id_of(request), headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Only location + error type + pydantic's generic message. The offending
        # input value and ctx are dropped so client data is never reflected.
        details = [
            {
                "loc": [str(p) for p in err.get("loc", ())],
                "type": str(err.get("type", "value_error")),
                "msg": str(err.get("msg", "invalid"))[:200],
            }
            for err in exc.errors()[:20]
        ]
        return error_response(
            422,
            "validation_error",
            "Request failed validation.",
            request_id_of(request),
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _STATUS_CODES.get(exc.status_code, ("http_error", "Request failed."))
        headers = dict(exc.headers) if exc.headers else None
        return error_response(exc.status_code, code, message, request_id_of(request), headers)

    @app.exception_handler(RepositoryError)
    async def _upstream(request: Request, exc: RepositoryError) -> JSONResponse:
        log.warning(
            "upstream_error",
            extra={"upstream_status": exc.upstream_status, "request_id": request_id_of(request)},
        )
        return error_response(
            503, "upstream_unavailable", "Upstream service unavailable.", request_id_of(request)
        )

    # Unhandled exceptions are converted to a generic 500 by
    # app.middleware.RequestContextMiddleware (which, unlike Starlette's
    # ServerErrorMiddleware, does not re-raise and therefore never lets a
    # traceback reach the server log or the client).
