"""Tests for typed settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from hotel_curator.config import (
    Settings,
    normalise_database_url,
    uses_tls_or_socket,
)


def test_paths_derive_from_home(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path)
    assert settings.data_dir == tmp_path / "data"
    assert settings.processed_dir == tmp_path / "data" / "processed"
    assert settings.raw_dir == tmp_path / "data" / "raw"
    assert settings.labels_dir == tmp_path / "data" / "labels"
    assert settings.manifests_dir == tmp_path / "data" / "manifests"
    assert settings.demo_dir == tmp_path / "data" / "demo"
    assert settings.artifacts_dir == tmp_path / "artifacts"
    assert settings.resolved_tier_policy == (
        tmp_path / "config" / "tier_policy.yaml"
    )


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
    monkeypatch.setenv("CURATOR_LOG_FORMAT", "json")
    monkeypatch.setenv("CURATOR_RATE_LIMIT_PER_MINUTE", "7")
    settings = Settings()
    assert settings.home == tmp_path
    assert settings.log_format == "json"
    assert settings.rate_limit_per_minute == 7


def test_invalid_enum_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURATOR_LOG_FORMAT", "xml")
    with pytest.raises(ValidationError):
        Settings()


def test_env_example_lists_only_real_settings() -> None:
    example = Path(__file__).resolve().parents[2] / ".env.example"
    names = {
        line.split("=", 1)[0].lstrip("# ").strip()
        for line in example.read_text().splitlines()
        if line.lstrip("# ").startswith("CURATOR_")
    }
    fields = {f"CURATOR_{name.upper()}" for name in Settings.model_fields}
    assert names <= fields, names - fields


TLS_URL = "postgresql+psycopg://app:pw@db.internal:5432/curator?sslmode=require"
SOCKET_URL = "postgresql+psycopg://curator@/curator?host=/var/run/postgresql"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        (TLS_URL, TLS_URL),
        ("sqlite:///./var/x.db", "sqlite:///./var/x.db"),
    ],
)
def test_normalise_database_url(url: str, expected: str) -> None:
    assert normalise_database_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (TLS_URL, True),
        (SOCKET_URL, True),
        (TLS_URL.replace("require", "verify-full"), True),
        (TLS_URL.replace("require", "prefer"), False),
        (TLS_URL.removesuffix("?sslmode=require"), False),
        (f"{TLS_URL}&sslmode=disable", False),
        ("postgresql+psycopg://u:p@h/db?host=relative", False),
    ],
)
def test_uses_tls_or_socket(url: str, expected: bool) -> None:
    assert uses_tls_or_socket(url) is expected


def test_production_accepts_tls_or_socket_postgres() -> None:
    settings = Settings(environment="production", database_url=TLS_URL)
    assert settings.migration_db_url() == TLS_URL
    socket = Settings(
        environment="production",
        database_url=SOCKET_URL,
        migration_database_url=TLS_URL.replace("app:", "owner:"),
    )
    assert socket.db_url() == SOCKET_URL
    assert socket.migration_db_url().startswith("postgresql+psycopg://owner:")


@pytest.mark.parametrize(
    ("database_url", "migration_url", "message"),
    [
        ("sqlite:///./var/x.db", None, "a Postgres database_url"),
        (TLS_URL.replace("require", "disable"), None, "sslmode=require"),
        (TLS_URL, "postgres://o:secret-pw@h/db", "migration_database_url"),
    ],
)
def test_production_refuses_plain_or_sqlite_urls(
    database_url: str, migration_url: str | None, message: str
) -> None:
    with pytest.raises(ValidationError, match=message) as refused:
        Settings(
            environment="production",
            database_url=database_url,
            migration_database_url=migration_url,
        )
    assert "secret-pw" not in str(refused.value)
    assert "pw@" not in str(refused.value)


def test_database_urls_and_the_dsn_never_appear_in_repr() -> None:
    dsn = "https://0123456789abcdef0123456789abcdef@sentry.example/1"
    settings = Settings(
        database_url=TLS_URL,
        migration_database_url="postgres://o:hunter2@h/d",
        sentry_dsn=dsn,
    )
    shown = f"{settings!r} {settings} {settings.model_dump()}"
    assert "pw@" not in shown
    assert "hunter2" not in shown
    assert "0123456789abcdef" not in shown
    assert settings.migration_db_url() == "postgresql+psycopg://o:hunter2@h/d"
