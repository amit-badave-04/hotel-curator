"""``curator db`` and ``curator serve``: migrate, seed, run the API."""

from pathlib import Path
from typing import Annotated

import typer

from hotel_curator.config import get_settings

app = typer.Typer(help="Database migrations and demo data.")


@app.command("upgrade")
def upgrade_cmd(
    revision: Annotated[str, typer.Argument(help="Target revision.")] = "head",
) -> None:
    """Apply Alembic migrations up to ``revision``."""
    from hotel_curator.db.migrate import upgrade

    settings = get_settings()
    if settings.database_url.startswith("sqlite:///"):
        db_file = Path(settings.database_url.removeprefix("sqlite:///"))
        db_file.parent.mkdir(parents=True, exist_ok=True)
    upgrade(settings.database_url, revision)
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
    engine = make_engine(settings.database_url)
    with session_scope(make_session_factory(engine)) as session:
        loaded = seed(session, source)
    engine.dispose()
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
    )
