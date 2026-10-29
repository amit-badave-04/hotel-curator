"""Seeding the database from a results file."""

from pathlib import Path

import pytest
from sqlalchemy import select

from hotel_curator.db.migrate import upgrade
from hotel_curator.db.models import DatasetInfo
from hotel_curator.db.seed import review_period, seed, write_results
from hotel_curator.db.session import (
    make_engine,
    make_session_factory,
    session_scope,
)
from tests.helpers import synthetic_results


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


def test_review_period_spans_every_hotel() -> None:
    first, second, *_ = synthetic_results()
    early = first.model_copy(update={"first_month": "2015-08"})
    late = second.model_copy(
        update={"first_month": "2016-01", "last_month": "2017-11"}
    )
    assert review_period([late, early]) == ("2015-08", "2017-11")
    assert review_period([early, late]) == ("2015-08", "2017-11")


def test_seed_records_the_review_period_and_fills_it_in_later(
    tmp_path: Path,
) -> None:
    results = synthetic_results()
    path = tmp_path / "results.json.gz"
    write_results(path, results)
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade(url)
    factory = make_session_factory(make_engine(url))
    expected = ("2015-09", "2017-08")  # the synthetic hotels' period
    with session_scope(factory) as s:
        assert seed(s, path) == len(results)
    with session_scope(factory) as s:
        info = s.scalars(select(DatasetInfo)).one()
        assert (info.first_month, info.last_month) == expected
        # A row from before migration 0002: same data, no period yet.
        info.first_month = info.last_month = None
    with session_scope(factory) as s:
        assert seed(s, path) == 0
    with session_scope(factory) as s:
        info = s.scalars(select(DatasetInfo)).one()
        assert (info.first_month, info.last_month) == expected
