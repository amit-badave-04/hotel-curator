"""Typed settings, read from ``CURATOR_*`` environment variables or ``.env``.

Every field here is used somewhere; v1 had ten settings that nothing read.
Paths default to locations under ``CURATOR_HOME`` (the current directory if
unset), so the CLI, the API and the Docker image agree on where data and
models live. See ``.env.example`` for a documented template.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_PAGE_SIZE = 100
"""Upper bound for search page sizes, in the API schema and in settings."""


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_prefix="CURATOR_", env_file=".env", extra="ignore"
    )

    home: Path = Field(default_factory=Path.cwd)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["console", "json"] = "console"

    database_url: str = "sqlite:///./var/hotel_curator.db"
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
        description="Proxy addresses whose X-Forwarded-For is trusted for "
        "client identity (rate limits). Unset: use the TCP peer.",
    )
    seed: int = 20261024

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
