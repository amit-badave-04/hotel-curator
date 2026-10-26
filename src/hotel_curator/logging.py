"""Logging setup: human-readable in a terminal, JSON lines in production.

Uses structlog on top of the standard library so that third-party loggers
(uvicorn, SQLAlchemy, alembic) end up in the same format. Call
:func:`configure_logging` once at process start (CLI entry point or API
lifespan).
"""

import logging
import sys

import structlog


def _rendering(fmt: str) -> list[structlog.types.Processor]:
    """Final processors: JSON lines or console lines.

    Tracebacks never include local variables: they would hold submitted
    review text and request headers.
    """
    if fmt == "json":
        return [
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            ),
            structlog.processors.JSONRenderer(),
        ]
    return [
        structlog.dev.ConsoleRenderer(
            colors=False, exception_formatter=structlog.dev.plain_traceback
        )
    ]


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    """Configure stdlib logging and structlog.

    Args:
        level: Minimum level name, e.g. ``"INFO"``.
        fmt: ``"console"`` for colour-free key=value lines, ``"json"`` for
            one JSON object per line.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *_rendering(fmt),
        ],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
