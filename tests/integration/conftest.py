"""Fixtures for tests that run the ``curator`` command line."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from hotel_curator.config import get_settings


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh project home for the CLI, with its own SQLite database."""
    monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
    monkeypatch.setenv(
        "CURATOR_DATABASE_URL", f"sqlite:///{tmp_path / 'var' / 'cli.db'}"
    )
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()
