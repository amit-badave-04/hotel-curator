"""Migrate and seed in one step, serialised by a database lock.

Fly runs ``curator db release`` once per deploy (``release_command``); the
API container runs it at start when ``CURATOR_MIGRATE_ON_START`` is true
(docker compose). Both steps are idempotent: Alembic skips applied
migrations and the seed skips a dataset whose SHA-256 is already loaded.
A database migrated by a newer image (before a rollback) is left as it is,
because migrations are additive (``hotel_curator.db.migrate``).
On Postgres a session-level advisory lock makes concurrent releases (two
deploys, or a deploy and a restart) run one after the other.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from hotel_curator.db.migrate import revisions_ahead, upgrade
from hotel_curator.db.seed import seed
from hotel_curator.db.session import (
    make_engine,
    make_session_factory,
    session_scope,
)

logger = logging.getLogger(__name__)

RELEASE_LOCK_ID = 2026_10_28_011
"""Advisory-lock key; any constant works as long as every release uses it."""
RELEASE_LOCK_WAIT_SECONDS = 120
"""How long a release waits for another one. A release takes seconds, so a
lock held longer is stuck, and the deploy fails with a message that says
so instead of waiting for Fly to give up on the release command."""
SQLITE_PREFIX = "sqlite:///"
LOCK_NOT_AVAILABLE = "55P03"
"""SQLSTATE of a lock wait that ran into lock_timeout."""


@contextmanager
def release_lock(
    engine: Engine, wait_seconds: int = RELEASE_LOCK_WAIT_SECONDS
) -> Iterator[None]:
    """Hold the release lock on Postgres; a no-op on SQLite (one process).

    Raises:
        RuntimeError: another session held the lock for ``wait_seconds``.
    """
    if engine.dialect.name != "postgresql":
        yield
        return
    key = {"key": RELEASE_LOCK_ID}
    # Autocommit, so the lock holder is not left "idle in transaction"
    # while Alembic and the seed work on their own connections.
    with engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as conn:
        conn.execute(
            text("SELECT set_config('lock_timeout', :wait, false)"),
            {"wait": f"{wait_seconds}s"},
        )
        try:
            conn.execute(text("SELECT pg_advisory_lock(:key)"), key)
        except OperationalError as exc:
            # Only a timed-out wait means a held lock; anything else (a
            # dropped connection, say) is passed on as it is.
            if getattr(exc.orig, "sqlstate", None) != LOCK_NOT_AVAILABLE:
                raise
            raise RuntimeError(
                f"Another session has held the release lock "
                f"({RELEASE_LOCK_ID}) for {wait_seconds} s; find it in "
                f"pg_locks (locktype advisory) and end it, then deploy again"
            ) from exc
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), key)


def ensure_sqlite_dir(database_url: str) -> None:
    """Create the directory of a SQLite file URL (Alembic will not)."""
    if database_url.startswith(SQLITE_PREFIX):
        db_file = Path(database_url.removeprefix(SQLITE_PREFIX))
        db_file.parent.mkdir(parents=True, exist_ok=True)


def _migrate(engine: Engine, database_url: str) -> None:
    """Migrate to head, unless a newer image already went further."""
    ahead = revisions_ahead(engine, database_url)
    if ahead:
        logger.warning(
            "Database is at revision %s, which this image does not know "
            "(an older image after a newer release): schema left as it is",
            ", ".join(ahead),
        )
        return
    upgrade(database_url)


def release(database_url: str, results: Path) -> int:
    """Migrate to head, then load ``results`` unless already loaded.

    Returns:
        Number of hotels loaded (0 when the dataset was already loaded).
    """
    ensure_sqlite_dir(database_url)
    engine = make_engine(database_url)
    try:
        with release_lock(engine):
            _migrate(engine, database_url)
            with session_scope(make_session_factory(engine)) as session:
                loaded = seed(session, results)
    finally:
        engine.dispose()
    logger.info("Release done: %d hotels loaded", loaded)
    return loaded
