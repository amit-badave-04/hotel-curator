"""Tests for per-label threshold tuning and probability calibration."""

import numpy as np
import pytest

from hotel_curator.ml.calibration import (
    IsotonicCalibrator,
    brier_score,
    expected_calibration_error,
)
from hotel_curator.ml.thresholds import (
    ThresholdPolicy,
    apply_thresholds,
    tune_thresholds,
)


def _synthetic(n: int = 3000, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    y = rng.random((n, 3)) < np.array([0.4, 0.1, 0.02])
    # Scores are informative but miscalibrated (squashed towards 0.5).
    logits = np.where(y, 1.5, -1.5) + rng.normal(0, 1.2, size=y.shape)
    probs = 1 / (1 + np.exp(-0.4 * logits))
    return y, probs


def test_tuned_thresholds_beat_default_on_f1() -> None:
    y, p = _synthetic()
    policy = ThresholdPolicy(min_threshold=0.05, max_threshold=0.95)
    tuned = tune_thresholds(y, p, ["a", "b", "c"], policy)
    default = {k: 0.5 for k in ("a", "b", "c")}
    from hotel_curator.ml.metrics import macro_f1

    f1_tuned = macro_f1(y, apply_thresholds(p, tuned, ["a", "b", "c"]))
    f1_default = macro_f1(y, apply_thresholds(p, default, ["a", "b", "c"]))
    assert f1_tuned >= f1_default


def test_thresholds_respect_bounds() -> None:
    y, p = _synthetic()
    policy = ThresholdPolicy(min_threshold=0.2, max_threshold=0.8)
    tuned = tune_thresholds(y, p, ["a", "b", "c"], policy)
    assert all(0.2 <= t <= 0.8 for t in tuned.values())


def test_label_with_too_few_positives_gets_fallback() -> None:
    y = np.zeros((100, 1), dtype=bool)
    y[:3, 0] = True
    p = np.random.default_rng(0).random((100, 1))
    policy = ThresholdPolicy(min_positives=10, fallback=0.5)
    assert tune_thresholds(y, p, ["rare"], policy) == {"rare": 0.5}


def test_apply_thresholds_missing_label_raises() -> None:
    with pytest.raises(KeyError):
        apply_thresholds(np.zeros((2, 2)), {"a": 0.5}, ["a", "b"])


def test_ece_zero_for_perfectly_calibrated_bins() -> None:
    p = np.array([0.25] * 4 + [0.75] * 4)
    y = np.array([1, 0, 0, 0, 1, 1, 1, 0], dtype=bool)
    assert expected_calibration_error(y, p, n_bins=2) == pytest.approx(0.0)


def test_ece_large_for_overconfident_scores() -> None:
    p = np.full(100, 0.99)
    y = np.zeros(100, dtype=bool)
    assert expected_calibration_error(y, p) > 0.9


def test_brier_score() -> None:
    assert brier_score(np.array([1, 0], bool), np.array([1.0, 0.0])) == 0.0
    assert brier_score(np.array([1, 0], bool), np.array([0.0, 1.0])) == 1.0


def test_isotonic_calibration_reduces_ece() -> None:
    y, p = _synthetic(seed=1)
    y_test, p_test = _synthetic(seed=2)
    calibrator = IsotonicCalibrator.fit(y, p, ["a", "b", "c"])
    calibrated = calibrator.transform(p_test)
    for k in range(3):
        before = expected_calibration_error(y_test[:, k], p_test[:, k])
        after = expected_calibration_error(y_test[:, k], calibrated[:, k])
        assert after < before
    assert calibrated.min() >= 0.0 and calibrated.max() <= 1.0


def test_isotonic_round_trips_through_dict() -> None:
    y, p = _synthetic(seed=1)
    calibrator = IsotonicCalibrator.fit(y, p, ["a", "b", "c"])
    restored = IsotonicCalibrator.from_dict(calibrator.to_dict())
    np.testing.assert_allclose(restored.transform(p), calibrator.transform(p))


def test_weighted_isotonic_matches_replicated_rows() -> None:
    rng = np.random.default_rng(3)
    p = rng.random((400, 1))
    y = rng.random((400, 1)) < p
    weights = rng.integers(1, 4, size=400).astype(float)
    weighted = IsotonicCalibrator.fit(y, p, ["a"], sample_weight=weights)
    rep = np.repeat(np.arange(400), weights.astype(int))
    replicated = IsotonicCalibrator.fit(y[rep], p[rep], ["a"])
    grid = np.linspace(0, 1, 50)[:, None]
    np.testing.assert_allclose(
        weighted.transform(grid), replicated.transform(grid), atol=1e-9
    )


@pytest.mark.parametrize("bad", [np.ones(3), -np.ones(400)])
def test_weighted_isotonic_rejects_bad_weights(bad: np.ndarray) -> None:
    y = np.zeros((400, 1), dtype=bool)
    y[:50] = True
    with pytest.raises(ValueError, match="sample_weight"):
        IsotonicCalibrator.fit(y, np.zeros((400, 1)), ["a"], sample_weight=bad)


def test_calibration_in_the_large() -> None:
    from hotel_curator.ml.calibration import calibration_in_the_large

    y = np.array([True, False, True])
    p = np.array([0.5, 0.25, 0.75])
    assert calibration_in_the_large(y, p) == (0.5, pytest.approx(2 / 3))
    # A review drawn with weight 3 stands for three corpus reviews.
    expected, observed = calibration_in_the_large(y, p, np.array([1, 3, 1]))
    assert expected == pytest.approx((0.5 + 0.75 + 0.75) / 5)
    assert observed == pytest.approx(2 / 5)
    with pytest.raises(ValueError, match="weights"):
        calibration_in_the_large(y, p, np.array([0.0, 0.0, 0.0]))
