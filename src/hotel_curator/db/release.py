"""Migrate and seed in one step, serialised by a database lock.

Fly runs ``curator db release`` once per deploy (``release_command``); the
API container runs it at start when ``CURATOR_MIGRATE_ON_START`` is true
(docker compose). Both steps are idempotent: Alembic skips applied
migrations and the seed skips a dataset whose SHA-256 is already loaded.
On Postgres a session-level advisory lock makes concurrent releases (two
deploys, or a deploy and a restart) run one after the other.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, text

from hotel_curator.db.migrate import upgrade
from hotel_curator.db.seed import seed
from hotel_curator.db.session import (
    make_engine,
    make_session_factory,
    session_scope,
)

logger = logging.getLogger(__name__)

RELEASE_LOCK_ID = 2026_10_28_011
"""Advisory-lock key; any constant works as long as every release uses it."""
SQLITE_PREFIX = "sqlite:///"


@contextmanager
def release_lock(engine: Engine) -> Iterator[None]:
    """Hold the release lock on Postgres; a no-op on SQLite (one process)."""
    if engine.dialect.name != "postgresql":
        yield
        return
    key = {"key": RELEASE_LOCK_ID}
    # Autocommit, so the lock holder is not left "idle in transaction"
    # while Alembic and the seed work on their own connections.
    with engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), key)
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), key)


def ensure_sqlite_dir(database_url: str) -> None:
    """Create the directory of a SQLite file URL (Alembic will not)."""
    if database_url.startswith(SQLITE_PREFIX):
        db_file = Path(database_url.removeprefix(SQLITE_PREFIX))
        db_file.parent.mkdir(parents=True, exist_ok=True)


def release(database_url: str, results: Path) -> int:
    """Migrate to head, then load ``results`` unless already loaded.

    Returns:
        Number of hotels loaded (0 when the dataset was already loaded).
    """
    ensure_sqlite_dir(database_url)
    engine = make_engine(database_url)
    try:
        with release_lock(engine):
            upgrade(database_url)
            with session_scope(make_session_factory(engine)) as session:
                loaded = seed(session, results)
    finally:
        engine.dispose()
    logger.info("Release done: %d hotels loaded", loaded)
    return loaded
