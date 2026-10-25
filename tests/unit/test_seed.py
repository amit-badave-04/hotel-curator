"""Seeding the database from a results file."""

from pathlib import Path

import pytest

from hotel_curator.db.migrate import upgrade
from hotel_curator.db.seed import seed, write_results
from hotel_curator.db.session import (
    make_engine,
    make_session_factory,
    session_scope,
)


def test_seed_refuses_an_empty_results_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json.gz"
    write_results(empty, [])
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade(url)
    factory = make_session_factory(make_engine(url))
    with (
        pytest.raises(ValueError, match="no hotels"),
        session_scope(factory) as s,
    ):
        seed(s, empty)
