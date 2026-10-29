"""Optional error reports to Sentry (ADR 011, D17).

Off unless ``CURATOR_SENTRY_DSN`` is set. A report is sent only for an
unexpected error (the request middleware's 500) and carries the exception,
its stack without local variables, and the request id that finds the
matching log lines. Nothing from the request goes with it: no body, query
string, header, cookie or client address, and no breadcrumbs (they would
copy log lines). :func:`scrub_event` enforces that on every event, whatever
produced it.
"""

from typing import TYPE_CHECKING, Any

from hotel_curator import __version__
from hotel_curator.api.middleware import ErrorReporter
from hotel_curator.config import Settings

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

FLUSH_SECONDS = 2.0
"""How long shutdown waits for queued reports (Fly's kill_timeout is 30 s)."""


def scrub_event(event: "Event", _hint: "Hint") -> "Event":
    """Drop everything an event could carry from a request or a log line."""
    event.pop("request", None)
    event.pop("user", None)
    event.pop("breadcrumbs", None)
    event.pop("extra", None)
    return event


def _report(exc: BaseException, request_id: str) -> None:
    import sentry_sdk

    with sentry_sdk.new_scope() as scope:
        scope.set_tag("request_id", request_id)
        sentry_sdk.capture_exception(exc)


def flush_error_reports(timeout: float = FLUSH_SECONDS) -> None:
    """Send reports still queued, before the process exits.

    Needed because uvicorn ends by re-raising the signal it caught, so
    Sentry's own exit handler never runs.
    """
    import sentry_sdk

    sentry_sdk.flush(timeout)


def init_error_reports(
    settings: Settings, transport: Any = None
) -> ErrorReporter | None:
    """Start Sentry when a DSN is configured; return the reporter.

    Args:
        settings: Supplies the DSN and the environment name.
        transport: Replaces Sentry's HTTP transport (tests).

    Returns:
        A reporter, or ``None`` when no DSN is set.

    Raises:
        RuntimeError: If a DSN is set but sentry-sdk is not installed.
    """
    if settings.sentry_dsn is None:
        return None
    try:
        import sentry_sdk
    except ImportError as exc:
        raise RuntimeError(
            "CURATOR_SENTRY_DSN is set but sentry-sdk is not installed "
            "(install the sentry extra)"
        ) from exc
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.environment,
        release=f"hotel-curator@{__version__}",
        # Sentry's default sends no personal data; frames carry no locals.
        include_local_variables=False,
        max_request_body_size="never",
        max_breadcrumbs=0,
        # Only the reporter sends events: no integrations (framework,
        # logging, thread or exit hooks), so nothing else is captured.
        default_integrations=False,
        auto_enabling_integrations=False,
        traces_sample_rate=None,
        before_send=scrub_event,
        transport=transport,
    )
    return _report
