"""Tests for the stratified annotation sampler."""

import numpy as np
import pandas as pd
import pytest

from hotel_curator.labeling.sampling import SampleSpec, draw_sample


@pytest.fixture
def reviews() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n = 5000
    liked = rng.choice(
        ["Great location", "Friendly staff", "Fast wifi everywhere", None],
        size=n,
        p=[0.45, 0.45, 0.05, 0.05],
    )
    disliked = rng.choice(
        ["Small room", "Far from the metro", None], size=n, p=[0.5, 0.05, 0.45]
    )
    return pd.DataFrame(
        {
            "review_id": [f"r_{i:012x}" for i in range(n)],
            "hotel_id": rng.choice([f"h_{i:010x}" for i in range(80)], size=n),
            "liked_text": liked,
            "disliked_text": disliked,
            "split": rng.choice(["train", "val", "test"], size=n),
        }
    )


def test_sample_respects_split_and_size(reviews: pd.DataFrame) -> None:
    spec = SampleSpec(split="test", n_total=300, enriched_share=0.3, seed=1)
    sample = draw_sample(reviews, spec)
    assert len(sample) == 300
    assert sample["review_id"].is_unique
    test_ids = set(reviews.loc[reviews["split"] == "test", "review_id"])
    assert set(sample["review_id"]) <= test_ids


def test_sample_excludes_reviews_without_text(reviews: pd.DataFrame) -> None:
    spec = SampleSpec(split="test", n_total=300, enriched_share=0.3, seed=1)
    sample = draw_sample(reviews, spec)
    assert (
        sample["liked_text"].notna() | sample["disliked_text"].notna()
    ).all()


def test_enriched_stratum_share_and_weights(reviews: pd.DataFrame) -> None:
    spec = SampleSpec(split="test", n_total=300, enriched_share=0.3, seed=1)
    sample = draw_sample(reviews, spec)
    shares = sample["stratum"].value_counts(normalize=True)
    assert shares["enriched"] == pytest.approx(0.3, abs=0.01)
    # Inverse-inclusion weights must add up to the eligible population.
    eligible = reviews[
        (reviews["split"] == "test")
        & (reviews["liked_text"].notna() | reviews["disliked_text"].notna())
    ]
    assert sample["sampling_weight"].sum() == pytest.approx(len(eligible))


def test_sample_is_deterministic(reviews: pd.DataFrame) -> None:
    spec = SampleSpec(split="val", n_total=100, enriched_share=0.2, seed=5)
    first = draw_sample(reviews, spec)
    second = draw_sample(reviews.sample(frac=1, random_state=9), spec)
    assert list(first["review_id"]) == list(second["review_id"])


def test_exclude_ids(reviews: pd.DataFrame) -> None:
    spec = SampleSpec(split="test", n_total=100, enriched_share=0.2, seed=1)
    first = draw_sample(reviews, spec)
    second = draw_sample(reviews, spec, exclude=set(first["review_id"]))
    assert not set(first["review_id"]) & set(second["review_id"])


def test_too_large_sample_raises(reviews: pd.DataFrame) -> None:
    spec = SampleSpec(split="test", n_total=10_000, enriched_share=0.2, seed=1)
    with pytest.raises(ValueError, match="eligible"):
        draw_sample(reviews, spec)
