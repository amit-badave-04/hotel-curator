"""Typed settings, read from ``CURATOR_*`` environment variables or ``.env``.

Every field here is used somewhere; v1 had ten settings that nothing read.
Paths default to locations under ``CURATOR_HOME`` (the current directory if
unset), so the CLI, the API and the Docker image agree on where data and
models live. See ``.env.example`` for a documented template.
"""

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import parse_qs, urlsplit

from pydantic import (
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_PAGE_SIZE = 100
"""Upper bound for search page sizes, in the API schema and in settings."""
POSTGRES_DRIVER = "postgresql+psycopg://"
TLS_SSLMODES = frozenset({"require", "verify-ca", "verify-full"})


def normalise_database_url(url: str) -> str:
    """Use the psycopg 3 driver for ``postgres://`` and ``postgresql://`` URLs.

    Hosting providers hand out ``postgres://`` URLs, which SQLAlchemy 2
    rejects; ``postgresql://`` would pick psycopg2, which is not installed.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return POSTGRES_DRIVER + url.removeprefix(prefix)
    return url


def uses_tls_or_socket(url: str) -> bool:
    """True for a Postgres URL that requires TLS or connects over a socket.

    A repeated parameter counts by its last value, as libpq reads it.
    """
    query = parse_qs(urlsplit(url).query)
    if query.get("host", [""])[-1].startswith("/"):
        return True
    return query.get("sslmode", [""])[-1] in TLS_SSLMODES


class Settings(BaseSettings):
    """Application settings."""

    # hide_input_in_errors: a failed validation must not print a database
    # URL, which can carry a password, into the start-up log.
    model_config = SettingsConfigDict(
        env_prefix="CURATOR_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    home: Path = Field(default_factory=Path.cwd)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["console", "json"] = "console"

    environment: Literal["development", "production"] = Field(
        default="development",
        description="production refuses to start without Postgres over TLS "
        "(or a Unix socket); the API image sets it.",
    )
    # Credentials: both URLs may carry a password, so they are SecretStr and
    # never appear in a repr or a log; read them through db_url() and
    # migration_db_url().
    database_url: SecretStr = SecretStr("sqlite:///./var/hotel_curator.db")
    migration_database_url: SecretStr | None = Field(
        default=None,
        description="Role that owns the schema, used only by curator db "
        "release; defaults to database_url.",
    )
    migrate_on_start: bool = Field(
        default=True,
        description="The API container runs curator db release before "
        "serving; off on Fly, where the release command does it once.",
    )
    model_package: Path | None = None
    tier_policy: Path | None = None

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]
    rate_limit_per_minute: int = Field(default=120, ge=1, le=100_000)
    analyze_rate_limit_per_minute: int = Field(default=30, ge=1, le=10_000)
    max_request_bytes: int = Field(default=64_000, ge=1_024, le=10_000_000)
    max_part_chars: int = Field(default=2_000, ge=10, le=20_000)
    max_page_size: int = Field(
        default=MAX_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Largest page a search may request (can only be lowered).",
    )
    forwarded_allow_ips: str | None = Field(
        default=None,
        description="Proxies (addresses, CIDR ranges, or * for any peer) "
        "whose X-Real-IP and X-Request-ID are believed; X-Forwarded-For "
        "never is. Unset: the client is the TCP peer.",
    )
    metrics_port: int | None = Field(
        default=None,
        ge=1024,
        le=65535,
        description="Port of the Prometheus metrics listener, separate from "
        "the API's (Fly's [metrics] scrapes it). Unset: no metrics listener.",
    )
    metrics_host: str = Field(
        default="127.0.0.1",
        description="Address the metrics listener binds: an IP address; :: "
        "on Fly, whose scraper comes over the private network.",
    )
    # Credential: a DSN is a write key for the Sentry project.
    sentry_dsn: SecretStr | None = Field(
        default=None,
        description="Send unexpected errors to Sentry (needs the sentry "
        "extra). Unset: no error reports leave the process.",
    )
    seed: int = 20261024

    @field_validator("metrics_host")
    @classmethod
    def _metrics_host_is_an_address(cls, value: str) -> str:
        ipaddress.ip_address(value)  # raises ValueError on a host name
        return value

    @field_validator("forwarded_allow_ips")
    @classmethod
    def _proxies_parse(cls, value: str | None) -> str | None:
        from hotel_curator.api.clientip import TrustedProxies

        TrustedProxies.parse(value)  # raises ValueError on a bad entry
        return value

    @model_validator(mode="after")
    def _production_needs_postgres_over_tls(self) -> Self:
        if self.environment != "production":
            return self
        for name, url in (
            ("database_url", self.db_url()),
            ("migration_database_url", self.migration_db_url()),
        ):
            if not url.startswith(POSTGRES_DRIVER):
                raise ValueError(f"production needs a Postgres {name}")
            if not uses_tls_or_socket(url):
                raise ValueError(
                    f"production needs sslmode=require (or stricter) in "
                    f"{name}, or a Unix-socket host"
                )
        return self

    def db_url(self) -> str:
        """The serving database URL, with the driver normalised."""
        return normalise_database_url(self.database_url.get_secret_value())

    def migration_db_url(self) -> str:
        """The URL ``curator db release`` migrates and seeds with."""
        if self.migration_database_url is None:
            return self.db_url()
        secret = self.migration_database_url.get_secret_value()
        return normalise_database_url(secret)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def data_dir(self) -> Path:
        """Root of all data (raw, processed, labels, demo, manifests)."""
        return self.home / "data"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def artifacts_dir(self) -> Path:
        """Root of versioned derived outputs (models, eval reports, cards)."""
        return self.home / "artifacts"

    @property
    def raw_dir(self) -> Path:
        """Downloaded source files (gitignored)."""
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        """Canonical tables (gitignored, rebuilt by ``make data``)."""
        return self.data_dir / "processed"

    @property
    def labels_dir(self) -> Path:
        """Annotation batches, panel answers and gold sets (committed)."""
        return self.data_dir / "labels"

    @property
    def demo_dir(self) -> Path:
        """Small committed dataset that seeds the demo database."""
        return self.data_dir / "demo"

    @property
    def manifests_dir(self) -> Path:
        """Committed manifests describing every built data file."""
        return self.data_dir / "manifests"

    @property
    def work_dir(self) -> Path:
        """Gitignored scratch space: base models, checkpoints, MLflow store."""
        return self.home / "var"

    @property
    def base_models_dir(self) -> Path:
        """Downloaded pretrained encoders (never committed)."""
        return self.work_dir / "models" / "base"

    @property
    def runs_dir(self) -> Path:
        """Training outputs (checkpoints, predictions); never committed."""
        return self.work_dir / "runs"

    @property
    def models_dir(self) -> Path:
        """Committed model packages and evaluation reports."""
        return self.artifacts_dir / "models"

    @property
    def resolved_tier_policy(self) -> Path:
        """Tier policy file (defaults to the one shipped in ``config/``)."""
        return self.tier_policy or self.home / "config" / "tier_policy.yaml"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings (cached; tests build their own ``Settings``)."""
    return Settings()
