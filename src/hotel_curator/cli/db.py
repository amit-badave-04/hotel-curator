"""``curator db`` and ``curator serve``: migrate, seed, run the API."""

from pathlib import Path
from typing import Annotated

import typer

from hotel_curator.config import get_settings

app = typer.Typer(help="Database migrations and demo data.")

GRACEFUL_SHUTDOWN_SECONDS = 20
"""How long uvicorn lets in-flight requests finish after SIGTERM.

Fly's ``kill_timeout`` for the API must be longer (fly.toml).
"""


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
    typer.echo(f"database at {revision}")


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
    typer.echo(
        f"loaded {loaded} hotels from {source}" if loaded else "up to date"
    )


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
        typer.echo("release skipped: CURATOR_MIGRATE_ON_START is false")
        return
    source = settings.demo_dir / DEMO_RESULTS
    loaded = release(settings.migration_db_url(), source)
    typer.echo(
        f"loaded {loaded} hotels from {source}" if loaded else "up to date"
    )


def serve(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
    workers: Annotated[int, typer.Option(help="Worker processes.")] = 1,
) -> None:
    """Run the API with uvicorn (binds to localhost unless told otherwise)."""
    import uvicorn

    trusted = get_settings().forwarded_allow_ips
    uvicorn.run(
        "hotel_curator.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
        proxy_headers=trusted is not None,
        forwarded_allow_ips=trusted or "127.0.0.1",
        server_header=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
