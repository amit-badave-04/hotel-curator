"""Tests for inter-annotator agreement and majority-vote aggregation."""

import numpy as np
import pytest

from hotel_curator.labeling.agreement import (
    agreement_report,
    fleiss_kappa_binary,
    majority_vote,
    mean_pairwise_cohen_kappa,
)


def test_perfect_agreement_gives_kappa_one() -> None:
    ratings = np.array([[1, 1, 1], [0, 0, 0], [1, 1, 1], [0, 0, 0]])
    assert fleiss_kappa_binary(ratings) == pytest.approx(1.0)
    assert mean_pairwise_cohen_kappa(ratings) == pytest.approx(1.0)


def test_fleiss_kappa_known_value() -> None:
    # 4 items x 3 raters; worked by hand:
    # P_i = [1, 1/3, 1, 1/3] -> P_bar = 2/3; p1 = 6/12 -> P_e = 0.5
    # kappa = (2/3 - 1/2) / (1 - 1/2) = 1/3
    ratings = np.array([[1, 1, 1], [1, 1, 0], [0, 0, 0], [0, 0, 1]])
    assert fleiss_kappa_binary(ratings) == pytest.approx(1 / 3)


def test_kappa_undefined_when_no_variation() -> None:
    ratings = np.zeros((5, 3), dtype=int)
    assert np.isnan(fleiss_kappa_binary(ratings))


def test_majority_vote() -> None:
    ratings = np.array(
        [
            [[1, 0], [1, 1], [0, 0]],
            [[0, 1], [0, 1], [1, 1]],
        ],
        dtype=bool,
    )  # shape (items=2, raters=3, labels=2)
    assert majority_vote(ratings).tolist() == [[True, False], [False, True]]


def test_majority_vote_requires_odd_rater_count() -> None:
    with pytest.raises(ValueError, match="odd"):
        majority_vote(np.zeros((2, 2, 3), dtype=bool))


def test_agreement_report_per_label() -> None:
    rng = np.random.default_rng(0)
    truth = rng.random((200, 2)) < 0.3
    raters = np.stack(
        [truth ^ (rng.random((200, 2)) < 0.05) for _ in range(3)], axis=1
    )
    report = agreement_report(raters, ["a", "b"])
    assert [row.label for row in report] == ["a", "b"]
    for row in report:
        assert 0.6 < row.fleiss_kappa <= 1.0
        assert 0.0 <= row.unanimous_rate <= 1.0
        assert row.positives_majority >= 0
