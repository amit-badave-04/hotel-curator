"""Run Alembic migrations programmatically (no alembic.ini needed).

Every migration must be additive (new tables, new nullable columns), so an
older image can still run on a newer schema: a rollback deploys an image
that does not know the newest migration, and its release then leaves the
schema as it is (see ``revisions_ahead``). Removing or renaming anything
takes two releases: stop using it first, drop it later.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

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


def revisions_ahead(engine: Engine, database_url: str) -> list[str]:
    """Revisions the database is at that this code's migrations lack.

    Non-empty after a newer image migrated the database and an older one
    was deployed again (a rollback).
    """
    script = ScriptDirectory.from_config(alembic_config(database_url))
    known = {revision.revision for revision in script.walk_revisions()}
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_heads()
    return sorted(set(current) - known)
