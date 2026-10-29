"""Release and serving roles against a real Postgres over TLS.

Runs when both URLs are set (CI job ``postgres-tls`` starts the database
from ``deploy/postgres``); skipped otherwise. Kept out of
``tests/integration`` so it needs only the API's dependencies.

    CURATOR_TEST_POSTGRES_OWNER_URL  curator_owner: migrations and seed
    CURATOR_TEST_POSTGRES_APP_URL    curator_app: read-only serving role

Both URLs must use ``sslmode=require``. The tests drop and recreate the
tables, so never point them at a database that matters. With
``CURATOR_TEST_POSTGRES_REQUIRED=1`` (set in CI) missing URLs are an error,
not a skip, so the job cannot pass without testing anything.

These tests see only the pg_hba.conf rules that match their own source
address; tests/unit/test_postgres_init.py checks the whole file.
"""

import os
import ssl
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from hotel_curator.config import Settings, normalise_database_url
from hotel_curator.db.release import RELEASE_LOCK_ID, release_lock
from hotel_curator.db.seed import read_results
from hotel_curator.db.session import make_engine

OWNER = os.environ.get("CURATOR_TEST_POSTGRES_OWNER_URL", "")
APP = os.environ.get("CURATOR_TEST_POSTGRES_APP_URL", "")
ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "data" / "demo" / "hotel_results.json.gz"
CURATOR = Path(sys.executable).with_name("curator")
"""The console script Fly's release_command runs."""

if os.environ.get("CURATOR_TEST_POSTGRES_REQUIRED") and not (OWNER and APP):
    raise RuntimeError("CURATOR_TEST_POSTGRES_*_URL are required here")
pytestmark = pytest.mark.skipif(
    not (OWNER and APP), reason="no test Postgres (see module docstring)"
)
OTHER_DATABASES = ("postgres", "template1")


def _engine(url: str) -> Engine:
    return make_engine(normalise_database_url(url))


def _reset() -> None:
    """Drop every table the owner created, so the next release starts empty."""
    engine = _engine(OWNER)
    with engine.begin() as conn:
        for table in inspect(conn).get_table_names():
            conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
    engine.dispose()


def _release() -> subprocess.Popen[str]:
    env = {
        **os.environ,
        "CURATOR_HOME": str(ROOT),
        "CURATOR_ENVIRONMENT": "production",
        "CURATOR_DATABASE_URL": APP,
        "CURATOR_MIGRATION_DATABASE_URL": OWNER,
    }
    return subprocess.Popen(
        [str(CURATOR), "db", "release"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _hotel_count() -> int:
    results, _ = read_results(RESULTS)
    return len(results)


def _with(url: str, *, user: str | None = None, **query: str) -> str:
    """``url`` with another user (and a wrong password) or query values."""
    parts = urlsplit(url)
    netloc = parts.netloc
    if user is not None:
        netloc = f"{user}:not-the-password@{netloc.rpartition('@')[2]}"
    params = dict(parse_qsl(parts.query)) | query
    return urlunsplit(parts._replace(netloc=netloc, query=urlencode(params)))


def _refused(url: str) -> None:
    """The server refuses ``url`` in pg_hba.conf, before any password.

    A connection that reached authentication would fail with "password
    authentication failed" instead, so a wrong password is no shortcut.
    """
    engine = _engine(url)
    with (
        pytest.raises(OperationalError, match=r"no pg_hba\.conf entry"),
        engine.connect(),
    ):
        pass
    engine.dispose()


def test_connections_use_tls_and_plain_ones_are_refused() -> None:
    engine = _engine(APP)
    with engine.connect() as conn:
        ssl = conn.execute(
            text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
        ).scalar_one()
    engine.dispose()
    assert ssl is True
    for url in (OWNER, APP):
        _refused(_with(url, sslmode="disable"))


def _on(url: str, database: str) -> str:
    return str(urlsplit(url)._replace(path=f"/{database}").geturl())


@pytest.mark.parametrize("database", ["curator", *OTHER_DATABASES])
@pytest.mark.parametrize("sslmode", ["require", "disable"])
def test_the_superuser_cannot_connect_over_the_network(
    database: str, sslmode: str
) -> None:
    _refused(_on(_with(OWNER, user="postgres", sslmode=sslmode), database))


@pytest.mark.parametrize("user", ["postgres", "curator_owner", "curator_app"])
def test_nobody_gets_a_replication_connection(user: str) -> None:
    """A physical replication connection matches hba's "replication"."""
    _refused(_with(OWNER, user=user, replication="true"))


@pytest.mark.parametrize("database", OTHER_DATABASES)
@pytest.mark.parametrize("sslmode", ["require", "disable"])
def test_the_roles_reach_only_their_database(
    database: str, sslmode: str
) -> None:
    for url in (OWNER, APP):
        _refused(_on(_with(url, sslmode=sslmode), database))


def test_verify_full_refuses_a_server_no_trusted_ca_signed() -> None:
    """``require`` encrypts; ``verify-full`` also needs the server's CA."""
    paths = ssl.get_default_verify_paths()
    cafile = paths.cafile or paths.openssl_cafile
    strict = APP.replace("sslmode=require", "sslmode=verify-full")
    engine = _engine(f"{strict}&sslrootcert={cafile}")
    with (
        pytest.raises(OperationalError, match="certificate verify failed"),
        engine.connect(),
    ):
        pass
    engine.dispose()


def test_neither_role_is_a_superuser() -> None:
    for url in (OWNER, APP):
        engine = _engine(url)
        with engine.connect() as conn:
            superuser = conn.execute(
                text(
                    "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
                )
            ).scalar_one()
        engine.dispose()
        assert superuser is False


def test_concurrent_releases_load_once_then_stay_up_to_date() -> None:
    _reset()
    first, second = _release(), _release()
    outputs = sorted(p.communicate(timeout=300)[0] for p in (first, second))
    assert first.returncode == 0 and second.returncode == 0, outputs
    n = _hotel_count()
    assert sum(f"loaded {n} hotels" in out for out in outputs) == 1, outputs
    assert sum("up to date" in out for out in outputs) == 1, outputs
    again = _release()
    assert "up to date" in again.communicate(timeout=300)[0]


def _lock_is_free() -> bool:
    """Whether no session holds the release lock right now.

    Read from pg_locks: only pg_advisory_lock is granted to the owner role.
    A bigint key is stored as its high and low 32 bits (objsubid 1).
    """
    engine = _engine(OWNER)
    with engine.connect() as conn:
        held = conn.execute(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                " AND objsubid = 1 AND classid::bigint = :high"
                " AND objid::bigint = :low AND granted"
            ),
            {
                "high": RELEASE_LOCK_ID >> 32,
                "low": RELEASE_LOCK_ID & 0xFFFFFFFF,
            },
        ).scalar_one()
    engine.dispose()
    return int(held) == 0


def test_the_release_lock_is_held_until_the_release_ends() -> None:
    engine = _engine(OWNER)
    with release_lock(engine):
        assert not _lock_is_free()
    assert _lock_is_free()
    # A failed release must not leave the next deploy waiting forever.
    with (
        pytest.raises(RuntimeError, match="migration failed"),
        release_lock(engine),
    ):
        raise RuntimeError("migration failed")
    assert _lock_is_free()
    engine.dispose()


def test_serving_role_reads_but_cannot_write() -> None:
    if _hotel_count() != _count_as_app():
        _reset()
        assert _release().wait(timeout=300) == 0
    engine = _engine(APP)
    for statement in (
        "DELETE FROM hotels",
        "UPDATE dataset_info SET hotels = 0",
        "CREATE TABLE intruder (id int)",
    ):
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            engine.begin() as conn,
        ):
            conn.execute(text(statement))
    engine.dispose()
    assert _count_as_app() == _hotel_count()


def test_serving_role_cannot_take_the_release_lock_or_store_objects() -> None:
    # Ways to change state that every role has by default: a held release
    # lock stops every deploy, and large objects can fill the volume.
    engine = _engine(APP)
    try:
        for statement in (
            f"SELECT pg_advisory_lock({RELEASE_LOCK_ID})",
            f"SELECT pg_try_advisory_lock({RELEASE_LOCK_ID})",
            f"SELECT pg_advisory_lock_shared({RELEASE_LOCK_ID})",
            f"SELECT pg_advisory_xact_lock({RELEASE_LOCK_ID})",
            "SELECT pg_advisory_lock(1, 2)",
            "SELECT lo_from_bytea(0, 'x')",
            "SELECT lo_creat(-1)",
        ):
            with (
                pytest.raises(ProgrammingError, match="permission denied"),
                engine.begin() as conn,
            ):
                conn.execute(text(statement))
    finally:
        # Closing the connections releases any lock a failure left held.
        engine.dispose()
    owner = _engine(OWNER)
    with owner.connect() as conn:
        limit, config = conn.execute(
            text(
                "SELECT rolconnlimit, rolconfig FROM pg_roles"
                " WHERE rolname = :r"
            ),
            {"r": "curator_app"},
        ).one()
    owner.dispose()
    assert limit == 20
    assert config == ["temp_file_limit=64MB"]


def test_a_stuck_release_lock_fails_the_release_with_a_clear_message() -> None:
    key = {"key": RELEASE_LOCK_ID}
    holder, engine = _engine(OWNER), _engine(OWNER)
    with holder.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), key)
        try:
            with (
                pytest.raises(RuntimeError, match="held the release lock"),
                release_lock(engine, wait_seconds=1),
            ):
                pass
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), key)
    holder.dispose()
    engine.dispose()
    assert _lock_is_free()


def _count_as_app() -> int:
    engine = _engine(APP)
    try:
        with engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM hotels"))
            return int(count.scalar_one())
    except ProgrammingError:
        return -1
    finally:
        engine.dispose()


def test_production_settings_accept_these_urls() -> None:
    settings = Settings(
        environment="production",
        database_url=APP,
        migration_database_url=OWNER,
    )
    assert settings.db_url().startswith("postgresql+psycopg://")
