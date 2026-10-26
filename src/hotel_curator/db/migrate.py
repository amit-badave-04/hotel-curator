"""Run Alembic migrations programmatically (no alembic.ini needed)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(database_url: str) -> Config:
    """Alembic config pointing at the packaged migrations."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    # Alembic's config interpolates "%"; a percent-encoded password in the
    # URL (e.g. "%40" for "@") must reach SQLAlchemy unchanged.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    """Migrate the database to ``revision``."""
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    """Migrate the database down to ``revision``."""
    command.downgrade(alembic_config(database_url), revision)
