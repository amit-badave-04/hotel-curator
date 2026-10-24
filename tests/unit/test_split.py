"""Tests for the hotel-disjoint, stratified train/val/test split."""

import numpy as np
import pandas as pd
import pytest

from hotel_curator.data.split import (
    SplitConfig,
    assign_splits,
    unify_address_groups,
)


@pytest.fixture
def hotels() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n = 600
    return pd.DataFrame(
        {
            "hotel_id": [f"h_{i:010x}" for i in range(n)],
            "city": rng.choice(["Paris", "London", "Vienna"], size=n),
            "n_reviews": rng.integers(8, 3000, size=n),
        }
    )


def test_every_hotel_gets_exactly_one_split(hotels: pd.DataFrame) -> None:
    out = assign_splits(hotels, SplitConfig(seed=1))
    assert out["split"].isin(["train", "val", "test"]).all()
    assert len(out) == len(hotels)
    assert out["hotel_id"].is_unique


def test_proportions_close_to_config(hotels: pd.DataFrame) -> None:
    config = SplitConfig(val_fraction=0.15, test_fraction=0.15, seed=1)
    shares = assign_splits(hotels, config)["split"].value_counts(normalize=True)
    assert shares["test"] == pytest.approx(0.15, abs=0.03)
    assert shares["val"] == pytest.approx(0.15, abs=0.03)


def test_each_city_present_in_each_split(hotels: pd.DataFrame) -> None:
    out = assign_splits(hotels, SplitConfig(seed=1))
    table = pd.crosstab(out["city"], out["split"])
    assert (table > 0).all().all()


def test_deterministic_given_seed(hotels: pd.DataFrame) -> None:
    a = assign_splits(hotels, SplitConfig(seed=7))
    b = assign_splits(
        hotels.sample(frac=1, random_state=3), SplitConfig(seed=7)
    )
    merged = a.merge(b, on="hotel_id", suffixes=("_a", "_b"))
    assert (merged["split_a"] == merged["split_b"]).all()


def test_invalid_fractions_rejected() -> None:
    with pytest.raises(ValueError):
        SplitConfig(val_fraction=0.6, test_fraction=0.5)


def test_same_address_hotels_share_the_largest_members_split() -> None:
    hotels = pd.DataFrame(
        {
            "hotel_id": ["h_a", "h_b", "h_c"],
            "address": ["8 Main St London", "8  main st LONDON", "1 Other St"],
            "n_reviews": [733, 945, 10],
            "split": ["val", "train", "test"],
        }
    )
    out = unify_address_groups(hotels)
    assert out.set_index("hotel_id")["split"].to_dict() == {
        "h_a": "train",
        "h_b": "train",
        "h_c": "test",
    }
