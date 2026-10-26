"""Tests for the evaluation harness (with a fake predictor, no model)."""

from collections.abc import Sequence

import numpy as np
import pandas as pd
import pytest

from hotel_curator.ml.evaluate import (
    EvalConfig,
    evaluate,
    review_level,
)
from hotel_curator.taxonomy import LABELS


class OraclePredictor:
    """Predicts the gold labels with a little noise."""

    name = "oracle"

    def __init__(self, gold: pd.DataFrame, flip: float, seed: int = 0) -> None:
        rng = np.random.default_rng(seed)
        y = gold[list(LABELS)].to_numpy(dtype=bool)
        noisy = np.logical_xor(y, rng.random(y.shape) < flip)
        self._lookup = {
            (r, p): row
            for r, p, row in zip(
                gold["review_id"], gold["part"], noisy, strict=True
            )
        }
        self._order = list(zip(gold["review_id"], gold["part"], strict=True))

    def predict_proba(
        self, texts: Sequence[str], parts: Sequence[str]
    ) -> np.ndarray:
        rows = [self._lookup[key] for key in self._order[: len(texts)]]
        return np.where(np.array(rows), 0.9, 0.1)


@pytest.fixture
def gold() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    n_reviews = 300
    rows = []
    for i in range(n_reviews):
        for part in ("liked", "disliked"):
            labels = rng.random(len(LABELS)) < 0.15
            rows.append(
                {
                    "review_id": f"r_{i:012x}",
                    "hotel_id": f"h_{i % 40:010x}",
                    "part": part,
                    "text": f"text {i} {part}",
                    "stratum": "uniform" if i % 4 else "enriched",
                    "city": "Paris" if i % 2 else "London",
                    **dict(zip(LABELS, labels, strict=True)),
                }
            )
    return pd.DataFrame(rows)


def test_review_level_is_union_of_parts() -> None:
    parts = np.array([[1, 0], [0, 1], [0, 0], [0, 0]], dtype=bool)
    ids = np.array(["a", "a", "b", "b"])
    review_ids, merged = review_level(ids, parts)
    assert list(review_ids) == ["a", "b"]
    assert merged.tolist() == [[True, True], [False, False]]


def test_perfect_predictor_scores_one(gold: pd.DataFrame) -> None:
    report = evaluate(
        OraclePredictor(gold, flip=0.0), gold, EvalConfig(n_boot=200)
    )
    assert report.part_macro_f1.point == pytest.approx(1.0)
    assert report.review_macro_f1.point == pytest.approx(1.0)


def test_noisy_predictor_has_interval_and_slices(gold: pd.DataFrame) -> None:
    report = evaluate(
        OraclePredictor(gold, flip=0.1), gold, EvalConfig(n_boot=200)
    )
    ci = report.review_macro_f1
    assert 0 < ci.low < ci.point < ci.high < 1
    assert len(report.per_label) == len(LABELS)
    assert {s.name for s in report.slices} >= {
        "part=liked",
        "part=disliked",
        "stratum=uniform",
        "city=Paris",
    }
    assert report.n_reviews == 300
    assert report.n_hotels == 40


def test_report_serialises(gold: pd.DataFrame) -> None:
    report = evaluate(
        OraclePredictor(gold, flip=0.1), gold, EvalConfig(n_boot=100)
    )
    data = report.to_dict()
    assert data["model"] == "oracle"
    assert "review_macro_f1" in data
    assert len(data["per_label"]) == len(LABELS)


def test_missing_label_columns_raise(gold: pd.DataFrame) -> None:
    with pytest.raises(KeyError):
        evaluate(
            OraclePredictor(gold, flip=0.0),
            gold.drop(columns=["wifi_negative"]),
            EvalConfig(n_boot=10),
        )


def test_noisy_or_combines_parts() -> None:
    from hotel_curator.ml.evaluate import noisy_or

    ids = np.array(["b", "a", "a"])
    probs = np.array([[0.2, 1.0], [0.5, 0.0], [0.5, 0.0]])
    unique, merged = noisy_or(ids, probs)
    assert unique.tolist() == ["a", "b"]
    np.testing.assert_allclose(merged, [[0.75, 0.0], [0.2, 1.0]], atol=1e-9)


def test_cross_polarity_counts_only_cross_cells() -> None:
    from hotel_curator.ml.evaluate import cross_polarity_scores

    gold = pd.DataFrame({"part": ["liked", "disliked"]})
    y_true = np.zeros((2, len(LABELS)), dtype=bool)
    y_pred = np.zeros_like(y_true)
    neg = LABELS.index("room_negative")
    pos = LABELS.index("room_positive")
    y_true[0, neg] = True  # cross: negative label in a Liked part
    y_pred[0, neg] = True
    y_true[1, neg] = True  # same-polarity cell: ignored
    y_pred[1, pos] = True  # cross false positive
    scores = cross_polarity_scores(gold, y_true, y_pred)
    assert scores["support"] == 1
    assert scores["precision"] == 0.5
    assert scores["recall"] == 1.0


def test_review_predictions_match_part_path_for_union(
    gold: pd.DataFrame,
) -> None:
    from hotel_curator.ml.evaluate import (
        score_predictions,
        score_review_predictions,
    )

    probs = np.where(gold[list(LABELS)].to_numpy(dtype=bool), 0.9, 0.1)
    part_report = score_predictions(
        "parts", gold, probs, EvalConfig(n_boot=100)
    )
    ids, rev = review_level(gold["review_id"], probs >= 0.5)
    rev_probs = np.where(rev, 0.9, 0.1)
    shuffled = np.random.default_rng(0).permutation(len(ids))
    review_report = score_review_predictions(
        "reviews",
        gold,
        ids[shuffled].tolist(),
        rev_probs[shuffled],
        EvalConfig(n_boot=100),
    )
    assert review_report.review_macro_f1.point == pytest.approx(
        part_report.review_macro_f1.point
    )
    assert review_report.part_macro_f1 is None
    assert review_report.ece == {}
    assert set(review_report.review_ece) == set(LABELS)


def test_review_predictions_reject_unknown_reviews(
    gold: pd.DataFrame,
) -> None:
    from hotel_curator.ml.evaluate import score_review_predictions

    probs = np.zeros((1, len(LABELS)))
    with pytest.raises(ValueError, match="gold reviews"):
        score_review_predictions(
            "x", gold, ["r_unknown"], probs, EvalConfig(n_boot=10)
        )


def test_calibrated_probs_drive_ece_not_thresholds(gold: pd.DataFrame) -> None:
    from hotel_curator.ml.evaluate import score_predictions

    y = gold[list(LABELS)].to_numpy(dtype=bool)
    raw = np.where(y, 0.9, 0.1)
    report = score_predictions(
        "m", gold, raw, EvalConfig(n_boot=50), calibrated=y.astype(float)
    )
    assert report.review_macro_f1.point == pytest.approx(1.0)
    assert max(report.ece.values()) == pytest.approx(0.0)
    assert report.mean_review_ece == pytest.approx(0.0)
