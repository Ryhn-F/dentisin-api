"""Pure-ASGI middleware: request context + access log + catch-all 500,
security headers, request-size limit, per-IP rate limit.

Order (outermost first, see main.create_app):
RequestContext -> SecurityHeaders -> (CORS, only if configured) -> BodySizeLimit -> RateLimit -> app
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import error_body
from .ratelimit import TokenBucketLimiter

access_log = logging.getLogger("dentisai.api.access")
error_log = logging.getLogger("dentisai.api.errors")

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"cache-control", b"no-store"),
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)
UNTHROTTLED_PATHS = frozenset({"/healthz", "/readyz"})


def _json_bytes(status: int, code: str, message: str, request_id: str) -> bytes:
    return json.dumps(error_body(code, message, request_id), separators=(",", ":")).encode()


async def _send_error(
    send: Send,
    status: int,
    code: str,
    message: str,
    request_id: str,
    extra_headers: Iterable[tuple[bytes, bytes]] = (),
) -> None:
    body = _json_bytes(status, code, message, request_id)
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *extra_headers,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _request_id(scope: Scope) -> str:
    state = scope.get("state") or {}
    rid = state.get("request_id")
    return rid if isinstance(rid, str) else "-"


class RequestContextMiddleware:
    """Assigns a server-generated request id (client ids are ignored so they
    cannot be used for log injection), logs one access line using the ROUTE
    TEMPLATE (never the raw path or query), and turns any unhandled exception
    into a generic 500 without re-raising (no traceback in logs or response)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = secrets.token_hex(8)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status_holder = {"status": 500, "started": False}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                status_holder["started"] = True
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 - last-resort handler, never re-raised
            error_log.error(
                "unhandled_exception",
                extra={"exc_type": type(exc).__name__, "request_id": request_id},
            )
            if not status_holder["started"]:
                status_holder["status"] = 500
                await _send_error(
                    send_wrapper, 500, "internal_error", "Internal server error.", request_id
                )
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "<unmatched>"
            access_log.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": scope.get("method", "-"),
                    "route": template,
                    "status": status_holder["status"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in {b"server", b"x-powered-by"}
                ]
                present = {k.lower() for k, _ in headers}
                headers.extend(h for h in SECURITY_HEADERS if h[0] not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodySizeLimitMiddleware:
    """Rejects bodies above ``max_bytes`` with 413: early via Content-Length,
    and while streaming for chunked/lying clients."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = _request_id(scope)
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await _send_error(send, 400, "bad_request", "Invalid Content-Length.", rid)
                    return
                if declared > self.max_bytes:
                    await _send_error(
                        send, 413, "payload_too_large", "Request body too large.", rid
                    )
                    return
        received = 0
        started = False
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Answer 413 ourselves (FastAPI would turn an exception raised
                    # here into a generic 400) and tell the app the client left.
                    rejected = True
                    if not started:
                        await _send_error(
                            send, 413, "payload_too_large", "Request body too large.", rid
                        )
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if rejected:
                return  # the 413 has already been sent
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, limited_receive, tracking_send)


def client_ip(scope: Scope, trust_forwarded_for: bool) -> str:
    if trust_forwarded_for:
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                # Left-most entry = original client as seen by the trusted proxy.
                first = str(value.decode("latin-1")).split(",")[0].strip()
                if first:
                    return first[:64]
    client = scope.get("client")
    return str(client[0]) if client else "unknown"


class RateLimitMiddleware:
    def __init__(
        self, app: ASGIApp, limiter: TokenBucketLimiter, trust_forwarded_for: bool
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.trust_forwarded_for = trust_forwarded_for

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in UNTHROTTLED_PATHS:
            await self.app(scope, receive, send)
            return
        allowed, retry_after = self.limiter.acquire(
            "ip:" + client_ip(scope, self.trust_forwarded_for)
        )
        if not allowed:
            await _send_error(
                send,
                429,
                "rate_limited",
                "Too many requests.",
                _request_id(scope),
                extra_headers=[(b"retry-after", str(retry_after).encode())],
            )
            return
        await self.app(scope, receive, send)
