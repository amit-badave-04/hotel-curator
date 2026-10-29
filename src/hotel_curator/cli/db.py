"""``curator db`` and ``curator serve``: migrate, seed, run the API."""

import logging
from pathlib import Path
from typing import Annotated

import typer

from hotel_curator.config import get_settings

app = typer.Typer(help="Database migrations and demo data.")
logger = logging.getLogger(__name__)

GRACEFUL_SHUTDOWN_SECONDS = 20
"""How long uvicorn lets in-flight requests finish after SIGTERM.

Fly's ``kill_timeout`` for the API must be longer (fly.toml).
"""


def _say(message: str) -> None:
    """Print a result line, or log it where every line must be JSON.

    The API image logs JSON: there the release command's lines (Fly's
    release command, the entrypoint's start-up check) join the same stream.
    """
    if get_settings().log_format == "json":
        logger.info(message)
    else:
        typer.echo(message)


@app.command("upgrade")
def upgrade_cmd(
    revision: Annotated[str, typer.Argument(help="Target revision.")] = "head",
) -> None:
    """Apply Alembic migrations up to ``revision``."""
    from hotel_curator.db.migrate import upgrade
    from hotel_curator.db.release import ensure_sqlite_dir

    url = get_settings().migration_db_url()
    ensure_sqlite_dir(url)
    upgrade(url, revision)
    _say(f"database at {revision}")


@app.command("seed")
def seed_cmd(
    path: Annotated[
        Path | None,
        typer.Option(help="Results file; defaults to the committed demo data."),
    ] = None,
) -> None:
    """Load hotel results into the database (no-op if already loaded)."""
    from hotel_curator.cli.score import DEMO_RESULTS
    from hotel_curator.db.seed import seed
    from hotel_curator.db.session import (
        make_engine,
        make_session_factory,
        session_scope,
    )

    settings = get_settings()
    source = path or settings.demo_dir / DEMO_RESULTS
    engine = make_engine(settings.migration_db_url())
    with session_scope(make_session_factory(engine)) as session:
        loaded = seed(session, source)
    engine.dispose()
    _say(f"loaded {loaded} hotels from {source}" if loaded else "up to date")


@app.command("release")
def release_cmd(
    if_enabled: Annotated[
        bool,
        typer.Option(
            "--if-enabled",
            help="Do nothing unless CURATOR_MIGRATE_ON_START is true "
            "(used by the container entrypoint).",
        ),
    ] = False,
) -> None:
    """Migrate, then load the committed results, under a database lock.

    Idempotent and safe to run on every deploy (Fly's release command).
    Uses CURATOR_MIGRATION_DATABASE_URL when set.
    """
    from hotel_curator.cli.score import DEMO_RESULTS
    from hotel_curator.db.release import release

    settings = get_settings()
    if if_enabled and not settings.migrate_on_start:
        _say("release skipped: CURATOR_MIGRATE_ON_START is false")
        return
    source = settings.demo_dir / DEMO_RESULTS
    loaded = release(settings.migration_db_url(), source)
    _say(f"loaded {loaded} hotels from {source}" if loaded else "up to date")


def serve(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
    workers: Annotated[int, typer.Option(help="Worker processes.")] = 1,
) -> None:
    """Run the API with uvicorn (binds to localhost unless told otherwise)."""
    import uvicorn

    if workers > 1 and get_settings().metrics_port is not None:
        # Each worker would keep its own counts, and only one could bind
        # the port: the metrics would cover part of the traffic.
        raise typer.BadParameter(
            "CURATOR_METRICS_PORT needs a single worker", param_hint="--workers"
        )

    uvicorn.run(
        "hotel_curator.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
        # Client identity comes from X-Real-IP, read by the app itself
        # (api/clientip.py); uvicorn must not rewrite the peer from
        # X-Forwarded-For, which Fly's proxy appends to.
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        # Logging is already set up (cli.main); uvicorn's own config would
        # add plain-text handlers beside it. The app logs each request.
        log_config=None,
        access_log=False,
    )
