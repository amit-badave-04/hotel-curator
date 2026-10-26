"""Programmatic Alembic configuration."""

from pathlib import Path

from hotel_curator.db.migrate import alembic_config, downgrade, upgrade


def test_percent_encoded_password_reaches_sqlalchemy_unchanged() -> None:
    url = "postgresql+psycopg://curator:p%40ss%2Fword@db:5432/curator"
    assert alembic_config(url).get_main_option("sqlalchemy.url") == url


def test_upgrade_and_downgrade_round_trip(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade(url)
    downgrade(url, "base")
    upgrade(url)
