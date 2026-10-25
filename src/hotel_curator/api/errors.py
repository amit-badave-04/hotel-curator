"""One error shape for every failure, without stack traces or echoed input.

FastAPI's default 422 body includes the submitted value of each invalid
field; for review text that would echo user input back (SR-4), so the
handler below keeps only the location and a message.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

STATUS_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    422: "invalid_request",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}


class FieldError(BaseModel):
    """One invalid input field."""

    location: list[str | int]
    message: str


class ErrorBody(BaseModel):
    """Machine-readable code, human message, request id for support."""

    code: str
    message: str
    request_id: str | None = None
    fields: list[FieldError] | None = None


class ErrorResponse(BaseModel):
    """Body of every 4xx/5xx response."""

    error: ErrorBody


def error_response(
    request: Request,
    status: int,
    message: str,
    *,
    fields: list[FieldError] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the standard error response."""
    body = ErrorResponse(
        error=ErrorBody(
            code=STATUS_CODES.get(status, "error"),
            message=message,
            request_id=getattr(request.state, "request_id", None),
            fields=fields,
        )
    )
    return JSONResponse(
        body.model_dump(exclude_none=True), status_code=status, headers=headers
    )


def install_handlers(app: FastAPI) -> None:
    """Register handlers for HTTP and validation errors."""

    @app.exception_handler(StarletteHTTPException)
    async def _http(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        message = (
            exc.detail if isinstance(exc.detail, str) else "Request failed."
        )
        return error_response(
            request,
            exc.status_code,
            message,
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            FieldError(location=list(e.get("loc", ())), message=str(e["msg"]))
            for e in exc.errors()
        ]
        return error_response(
            request, 422, "The request is invalid.", fields=fields
        )

    # Unexpected errors are answered by RequestContextMiddleware, which is
    # outside FastAPI's handlers but inside the security-header wrapper.
