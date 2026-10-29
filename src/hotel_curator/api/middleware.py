"""ASGI middleware: request ids, access logs, headers, body-size limit."""

import json
import time
import uuid
from collections.abc import Callable

import structlog
from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hotel_curator.api.clientip import (
    REQUEST_ID_HEADER,
    TrustedProxies,
    client_address,
    forwarded_request_id,
)
from hotel_curator.api.errors import STATUS_CODES

logger = structlog.get_logger("hotel_curator.access")

Observer = Callable[[Scope, int, float], None]
"""Receives each answered request: its scope, status and seconds taken."""
ErrorReporter = Callable[[BaseException, str], None]
"""Receives an unexpected error and the id of the request it broke."""

SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cache-control", b"no-store"),
)
TOO_LARGE = "The request body is too large."


async def send_error(
    send: Send,
    status: int,
    message: str,
    request_id: str | None,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    """Send the standard error body from middleware (outside FastAPI)."""
    error: dict[str, str] = {
        "code": STATUS_CODES.get(status, "error"),
        "message": message,
    }
    if request_id:
        error["request_id"] = request_id
    body = json.dumps({"error": error}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestContextMiddleware:
    """Assign a request id, add security headers, log one line per request.

    It is the outermost middleware, so it also answers unexpected errors:
    Starlette's own 500 handler sits outside every user middleware and would
    send a response without the request id and security headers. The same
    timing feeds ``observe`` (metrics), and an unexpected error also goes to
    ``report_error`` (error reports), when they are given.
    """

    def __init__(
        self,
        app: ASGIApp,
        trusted: TrustedProxies | None = None,
        observe: Observer | None = None,
        report_error: ErrorReporter | None = None,
    ) -> None:
        self.app = app
        self.trusted = trusted or TrustedProxies()
        self.observe = observe
        self.report_error = report_error

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Handle one ASGI connection."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = (
            forwarded_request_id(scope, self.trusted) or uuid.uuid4().hex
        )
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        # Read by the rate limiter; never logged (an address is personal).
        state["client"] = client_address(scope, self.trusted)
        started = time.perf_counter()
        status: int | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != REQUEST_ID_HEADER
                ]
                headers.append((REQUEST_ID_HEADER, request_id.encode()))
                headers.extend(SECURITY_HEADERS)
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            # Logged here (no request data, no locals) and answered with the
            # standard body; not re-raised, so nothing else answers twice.
            logger.exception("unhandled error", request_id=request_id)
            if self.report_error is not None:
                self.report_error(exc, request_id)
            if status is None:
                await send_error(
                    send_wrapper, 500, "Internal error.", request_id
                )
        finally:
            seconds = time.perf_counter() - started
            logger.info(
                "request",
                request_id=request_id,
                method=scope.get("method"),
                path=scope.get("path"),
                status=status or 500,
                ms=round(1000 * seconds, 1),
            )
            if self.observe is not None:
                self.observe(scope, status or 500, seconds)


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``max_bytes`` with 413 (SR-2).

    Checks ``Content-Length`` first, then counts streamed bytes, so chunked
    uploads cannot bypass the limit: reading stops at the limit and the
    request gets the same 413.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    def _declared_too_large(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    return int(value) > self.max_bytes
                except ValueError:
                    return True
        return False

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Handle one ASGI connection."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self._declared_too_large(scope):
            request_id = scope.get("state", {}).get("request_id")
            await send_error(send, 413, TOO_LARGE, request_id)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # FastAPI passes HTTPException from the body read through
                    # to the error handler (any other error becomes a 400).
                    raise HTTPException(status_code=413, detail=TOO_LARGE)
            return message

        await self.app(scope, limited_receive, send)
