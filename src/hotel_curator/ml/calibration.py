"""Probability calibration and calibration metrics.

Why: hotel-level scores in v2 are built from *expected* label counts (the
sum of per-review probabilities), not from thresholded predictions, because
counting thresholded predictions is biased whenever the threshold is not at
the calibrated 50% point. Expected counts are only right if probabilities
are calibrated, so we fit a per-label isotonic map on the gold dev split and
check calibration on gold test with expected calibration error (ECE) and
the Brier score.

The fitted calibrator is stored as plain arrays and applied with
``numpy.interp``, so the serving image does not need scikit-learn.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.floating]
DEFAULT_BINS = 10
MIN_POSITIVES_TO_CALIBRATE = 5


def expected_calibration_error(
    y_true: npt.NDArray[np.bool_], probs: FloatArray, n_bins: int = DEFAULT_BINS
) -> float:
    """Expected calibration error with equal-width probability bins.

    The sum over bins of |observed rate - mean probability|, weighted by each
    bin's share of items. 0 means perfectly calibrated.
    """
    y = np.asarray(y_true, dtype=np.float64).ravel()
    p = np.asarray(probs, dtype=np.float64).ravel()
    if y.shape != p.shape:
        raise ValueError(f"shape mismatch {y.shape} vs {p.shape}")
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    counts = np.bincount(bins, minlength=n_bins)
    sum_p = np.bincount(bins, weights=p, minlength=n_bins)
    sum_y = np.bincount(bins, weights=y, minlength=n_bins)
    used = counts > 0
    gap = np.abs(sum_y[used] - sum_p[used])
    return float(gap.sum() / len(p))


def calibration_in_the_large(
    y_true: npt.NDArray[np.bool_],
    probs: FloatArray,
    weights: FloatArray | None = None,
) -> tuple[float, float]:
    """Expected versus observed share of positives for one label.

    Hotel scores sum probabilities, so this is the check that matters most
    for aggregates. With the inverse-inclusion sampling weights both shares
    estimate the corpus rate, even though the gold sample over-represents
    rare topics.

    Returns:
        ``(weighted mean probability, weighted share of positives)``.

    Raises:
        ValueError: On mismatched shapes or weights that are negative or
            sum to zero.
    """
    p = np.asarray(probs, dtype=np.float64).ravel()
    y = np.asarray(y_true, dtype=bool).ravel()
    w = (
        np.ones_like(p)
        if weights is None
        else np.asarray(weights, dtype=np.float64).ravel()
    )
    if not y.shape == p.shape == w.shape:
        raise ValueError(f"shape mismatch {y.shape}, {p.shape}, {w.shape}")
    if (w < 0).any() or w.sum() <= 0:
        raise ValueError("weights must be non-negative with a positive sum")
    return float(np.average(p, weights=w)), float(np.average(y, weights=w))


def brier_score(y_true: npt.NDArray[np.bool_], probs: FloatArray) -> float:
    """Mean squared error between probabilities and 0/1 outcomes."""
    y = np.asarray(y_true, dtype=np.float64)
    return float(np.mean((np.asarray(probs, dtype=np.float64) - y) ** 2))


@dataclass
class IsotonicCalibrator:
    """Per-label monotone map from raw score to calibrated probability.

    Labels with too few dev positives keep the identity map (recorded in
    ``identity_labels``) instead of an unreliable fit.
    """

    labels: list[str]
    x_points: list[list[float]] = field(default_factory=list)
    y_points: list[list[float]] = field(default_factory=list)
    identity_labels: list[str] = field(default_factory=list)

    @classmethod
    def fit(
        cls,
        y_true: npt.NDArray[np.bool_],
        probs: FloatArray,
        labels: Sequence[str],
        sample_weight: FloatArray | None = None,
    ) -> "IsotonicCalibrator":
        """Fit one isotonic regression per label on dev data.

        Args:
            y_true: Dev gold labels, shape ``(n, n_labels)``.
            probs: Raw model scores, same shape.
            labels: Label names in column order.
            sample_weight: Optional per-row weights. The gold sets
                over-sample rare topics, so passing the inverse-inclusion
                weights makes the calibrated probabilities estimate rates in
                the full corpus rather than in the enriched sample.

        Raises:
            ValueError: On shape mismatch or negative weights.
        """
        if y_true.shape != probs.shape or y_true.shape[1] != len(labels):
            raise ValueError("y_true, probs and labels disagree in shape")
        if sample_weight is not None:
            sample_weight = np.asarray(sample_weight, dtype=np.float64)
            if sample_weight.shape != (len(y_true),):
                raise ValueError("sample_weight must have one value per row")
            if (sample_weight < 0).any():
                raise ValueError("sample_weight must be non-negative")
        from sklearn.isotonic import IsotonicRegression  # training only

        xs, ys, identity = [], [], []
        for k, label in enumerate(labels):
            y = y_true[:, k].astype(np.float64)
            if y.sum() < MIN_POSITIVES_TO_CALIBRATE or y.sum() == len(y):
                xs.append([0.0, 1.0])
                ys.append([0.0, 1.0])
                identity.append(label)
                continue
            iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
            iso.fit(probs[:, k], y, sample_weight=sample_weight)
            xs.append([float(v) for v in iso.X_thresholds_])
            ys.append([float(v) for v in iso.y_thresholds_])
        return cls(list(labels), xs, ys, identity)

    def transform(self, probs: FloatArray) -> npt.NDArray[np.float64]:
        """Map raw scores of shape ``(n, n_labels)`` to calibrated ones."""
        if probs.ndim != 2 or probs.shape[1] != len(self.labels):
            raise ValueError(
                f"expected (n, {len(self.labels)}) scores, got {probs.shape}"
            )
        out = np.empty(probs.shape, dtype=np.float64)
        for k in range(len(self.labels)):
            out[:, k] = np.interp(
                probs[:, k], self.x_points[k], self.y_points[k]
            )
        return out

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-compatible primitives."""
        return {
            "labels": self.labels,
            "x_points": self.x_points,
            "y_points": self.y_points,
            "identity_labels": self.identity_labels,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IsotonicCalibrator":
        """Inverse of :meth:`to_dict`."""
        return cls(
            labels=list(data["labels"]),
            x_points=[list(map(float, x)) for x in data["x_points"]],
            y_points=[list(map(float, y)) for y in data["y_points"]],
            identity_labels=list(data.get("identity_labels", [])),
        )
