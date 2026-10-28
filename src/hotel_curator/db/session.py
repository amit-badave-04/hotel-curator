"""Engine and session factory built from settings."""

from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

CONNECT_TIMEOUT_SECONDS = 5
"""Fail a connection attempt fast so /readyz answers instead of hanging."""
POOL_SIZE = 5
MAX_OVERFLOW = 5
POOL_RECYCLE_SECONDS = 300
"""Replace pooled connections every five minutes, under any idle cut-off."""


def make_engine(database_url: str) -> Engine:
    """Create an engine; SQLite gets foreign keys and thread sharing."""
    if database_url.startswith("sqlite"):
        engine = create_engine(
            database_url, connect_args={"check_same_thread": False}
        )

        @event.listens_for(engine, "connect")
        def _fk_on(
            dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry
        ) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    query = parse_qs(urlsplit(database_url).query)
    connect_args = (
        {}
        if "connect_timeout" in query
        else {"connect_timeout": CONNECT_TIMEOUT_SECONDS}
    )
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_recycle=POOL_RECYCLE_SECONDS,
        connect_args=connect_args,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Session factory with ``expire_on_commit`` off (read-mostly API)."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transaction scope: commit on success, roll back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
