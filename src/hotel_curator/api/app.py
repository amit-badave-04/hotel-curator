"""Application factory: ``uvicorn --factory hotel_curator.api.app:create_app``.

Everything configurable comes from :class:`~hotel_curator.config.Settings`
(``CURATOR_*`` environment variables). The lifespan opens the database
engine and loads the model package once; if the package is missing or
broken, the reason is logged, ``/v1/analyze`` answers 503 and every other
route keeps working.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from limits.strategies import MovingWindowRateLimiter

from hotel_curator import __version__
from hotel_curator.api.clientip import TrustedProxies
from hotel_curator.api.errorreports import (
    flush_error_reports,
    init_error_reports,
)
from hotel_curator.api.errors import ErrorResponse, install_handlers
from hotel_curator.api.metrics import ApiMetrics, MetricsServer
from hotel_curator.api.middleware import (
    BodySizeLimitMiddleware,
    ErrorReporter,
    RequestContextMiddleware,
)
from hotel_curator.api.ratelimit import (
    RateLimitMiddleware,
    RateRule,
    new_limiter,
)
from hotel_curator.api.routes import analyze, health, hotels, meta
from hotel_curator.api.routes.docs import mount_docs
from hotel_curator.api.state import AppState
from hotel_curator.config import Settings, get_settings
from hotel_curator.db.session import make_engine, make_session_factory
from hotel_curator.inference.locate import promoted_package
from hotel_curator.logging import configure_logging
from hotel_curator.scoring.policy import TierPolicy

logger = logging.getLogger(__name__)

DESCRIPTION = """
Hotel quality tiers built from guest reviews, with the evidence behind them.

* Tiers and grades follow a pre-registered policy (`/v1/meta` shows every
  value); every number can be recomputed from the `positive`, `negative`
  and `reviews` fields.
* Rate limits are per client and per process; exceeding them returns 429
  with `Retry-After`.
* Errors share one shape: `{"error": {"code", "message", "request_id"}}`.
"""


def _model_dir(settings: Settings) -> Path | None:
    # A missing, truncated or malformed promotion decision must not stop the
    # API: only /v1/analyze depends on the model.
    try:
        return promoted_package(settings)
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("Model unavailable: %s", exc)
        return None


def _start_metrics(
    settings: Settings, metrics: ApiMetrics
) -> MetricsServer | None:
    """Start the metrics listener when a port is configured.

    A port that cannot be bound is logged as an error, not fatal: the API
    serves without metrics rather than not at all, and the missing series
    trip the "no metrics" alert (docs/RUNBOOK.md).
    """
    if settings.metrics_port is None:
        return None
    try:
        listener = metrics.serve(settings.metrics_port, settings.metrics_host)
    except OSError as exc:
        logger.error("Metrics listener not started: %s", exc)
        return None
    logger.info("Metrics on port %d", listener.port)
    return listener


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    limiter = new_limiter()
    metrics = ApiMetrics()
    report_error = init_error_reports(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.db_url())
        app.state.curator = AppState(
            settings=settings,
            engine=engine,
            sessions=make_session_factory(engine),
            limiter=limiter,
            policy=TierPolicy.load(settings.resolved_tier_policy),
            model_dir=_model_dir(settings),
        )
        # Load at startup; a failure is recorded, never retried.
        metrics.set_model_loaded(app.state.curator.model() is not None)
        listener = _start_metrics(settings, metrics)
        yield
        if listener is not None:
            listener.close()
        if report_error is not None:
            flush_error_reports()
        engine.dispose()

    app = FastAPI(
        title="hotel-curator API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url=None,  # served by routes.docs without a CDN
        redoc_url=None,
        # No 307 to a Host-derived URL that would also drop a proxy prefix.
        redirect_slashes=False,
        responses={
            422: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
        },
    )
    app.state.metrics = metrics
    _add_middleware(app, settings, limiter, metrics, report_error)
    install_handlers(app)
    _include_routers(app)
    return app


def _add_middleware(
    app: FastAPI,
    settings: Settings,
    limiter: MovingWindowRateLimiter,
    metrics: ApiMetrics,
    report_error: ErrorReporter | None,
) -> None:
    # Starlette runs the last-added middleware first: request context (ids,
    # headers, access log, unexpected errors) wraps CORS (so 429s and 413s
    # carry CORS headers), which wraps the rate limits, which run before the
    # body-size limit and before any body is read.
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes
    )
    app.add_middleware(
        RateLimitMiddleware,
        limiter=limiter,
        rules=[
            RateRule.per_minute("/v1", "api", settings.rate_limit_per_minute),
            RateRule.per_minute(
                "/v1/analyze", "analyze", settings.analyze_rate_limit_per_minute
            ),
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )
    app.add_middleware(
        RequestContextMiddleware,
        trusted=TrustedProxies.parse(settings.forwarded_allow_ips),
        observe=metrics.observe,
        report_error=report_error,
    )


def _include_routers(app: FastAPI) -> None:
    app.include_router(health.router)
    mount_docs(app)
    for module in (hotels, meta, analyze):
        app.include_router(module.router)
