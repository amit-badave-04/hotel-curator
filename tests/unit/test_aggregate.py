"""Tests for eligible reviews, noisy-OR review signals and hotel evidence."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hotel_curator.scoring.aggregate import (
    eligible_reviews,
    hotel_evidence,
    review_signals,
)
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.taxonomy import LABELS, Attribute

POLICY = TierPolicy.load(
    Path(__file__).parents[2] / "config" / "tier_policy.yaml"
)
LONG = "the room was clean and the staff were friendly and helpful"


def _reviews() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "review_id": ["r1", "r2", "r3", "r4", "r5"],
            "hotel_id": ["h1", "h1", "h1", "h2", "h1"],
            "review_date": pd.to_datetime(
                [
                    "2016-01-05",
                    "2016-02-01",
                    "2016-02-03",
                    "2016-01-01",
                    "2016-03-01",
                ]
            ),
            "liked_text": [LONG, LONG, "Nice", LONG, None],
            "disliked_text": [None, None, None, None, None],
        }
    )


def test_eligible_drops_training_and_later_near_duplicates() -> None:
    kept = eligible_reviews(_reviews(), {"r3"}, POLICY)
    # r2 duplicates r1 in the same hotel; r4 has the same text elsewhere.
    assert kept["review_id"].tolist() == ["r1", "r5", "r4"]


def test_short_identical_texts_are_not_duplicates() -> None:
    reviews = _reviews()
    reviews["liked_text"] = ["Nice", "Nice", "Nice", "Nice", None]
    kept = eligible_reviews(reviews, set(), POLICY)
    assert len(kept) == 5


def _parts(rows: list[tuple[str, str, dict[str, float]]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {"review_id": r, "part": p, **dict.fromkeys(LABELS, 0.0), **probs}
            for r, p, probs in rows
        ]
    )
    return frame


def test_review_signals_noisy_or_and_hard_labels() -> None:
    reviews = _reviews().iloc[[0, 4]]
    parts = _parts(
        [
            ("r1", "liked", {"room_positive": 0.6}),
            ("r1", "disliked", {"room_positive": 0.5, "noise_negative": 0.2}),
        ]
    )
    signals = review_signals(reviews, parts, POLICY)
    first = signals.set_index("review_id").loc["r1"]
    assert first["pos_room"] == pytest.approx(0.8)
    assert first["neg_noise"] == pytest.approx(0.2)
    assert bool(first["pos_hard_room"]) is True
    assert bool(first["neg_hard_noise"]) is False
    empty = signals.set_index("review_id").loc["r5"]
    assert empty["pos_room"] == 0.0
    assert str(empty["month"]) == "2016-03"


def test_hotel_evidence_sums_and_spread() -> None:
    reviews = _reviews().iloc[[0, 1, 2, 4]]
    parts = _parts(
        [
            ("r1", "liked", {"staff_positive": 0.9}),
            ("r2", "disliked", {"staff_negative": 0.7}),
            ("r3", "disliked", {"staff_negative": 0.4}),
        ]
    )
    evidence = hotel_evidence(review_signals(reviews, parts, POLICY), POLICY)
    h1 = evidence["h1"]
    staff = h1.counts[Attribute.STAFF]
    assert h1.reviews == 4
    assert staff.positive == pytest.approx(0.9)
    assert staff.negative == pytest.approx(1.1)
    assert staff.complaining_reviews == 1
    assert (staff.positive_hard, staff.negative_hard) == (1, 1)
    assert h1.spread.distinct_months == 3
    assert h1.spread.max_month_share == pytest.approx(0.5)
    assert (h1.first_month, h1.last_month) == ("2016-01", "2016-03")


def test_signals_are_bounded() -> None:
    rng = np.random.default_rng(0)
    reviews = _reviews()
    rows = [
        (r, p, dict(zip(LABELS, rng.random(len(LABELS)), strict=True)))
        for r in reviews["review_id"]
        for p in ("liked", "disliked")
    ]
    signals = review_signals(reviews, _parts(rows), POLICY)
    values = signals.filter(regex="^(pos|neg)_[a-z]+$").to_numpy()
    assert ((values >= 0) & (values <= 1)).all()
