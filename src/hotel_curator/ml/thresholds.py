"""Per-label decision thresholds, tuned on a development set only.

Why: a single 0.5 threshold is wrong when label prevalence ranges from
under 1% to over 40%. v1 tuned thresholds on its gold set and then
reported F1 on the same rows; those gold-tuned thresholds went as low as
0.037 and, applied to the corpus, flagged ``location_negative`` on 50.8% of
reviews (v1 audit, D11).
v2 tunes on the gold *dev* split, never on gold *test*, searches only
thresholds inside a sane range, and falls back to a fixed value when a
label has too few positives to tune reliably.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from sklearn.metrics import precision_recall_curve

FloatMatrix = npt.NDArray[np.floating]
BoolMatrix = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class ThresholdPolicy:
    """Guard rails for threshold tuning.

    Attributes:
        min_threshold: Lowest allowed threshold.
        max_threshold: Highest allowed threshold.
        min_positives: Labels with fewer dev positives use ``fallback``.
        fallback: Threshold for labels that cannot be tuned.
    """

    min_threshold: float = 0.1
    max_threshold: float = 0.9
    min_positives: int = 10
    fallback: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 < self.min_threshold <= self.max_threshold < 1.0:
            raise ValueError("need 0 < min_threshold <= max_threshold < 1")
        if not self.min_threshold <= self.fallback <= self.max_threshold:
            raise ValueError("fallback must lie within the threshold bounds")


def _best_f1_threshold(
    y: npt.NDArray[np.bool_],
    p: npt.NDArray[np.floating],
    policy: ThresholdPolicy,
) -> float:
    precision, recall, thresholds = precision_recall_curve(y, p)
    # precision/recall have one more entry than thresholds; drop the last.
    precision, recall = precision[:-1], recall[:-1]
    in_bounds = (thresholds >= policy.min_threshold) & (
        thresholds <= policy.max_threshold
    )
    if not in_bounds.any():
        return policy.fallback
    with np.errstate(invalid="ignore", divide="ignore"):
        f1 = np.where(
            precision + recall > 0,
            2 * precision * recall / (precision + recall),
            0.0,
        )
    f1 = np.where(in_bounds, f1, -1.0)
    return float(thresholds[int(np.argmax(f1))])


def tune_thresholds(
    y_true: BoolMatrix,
    probs: FloatMatrix,
    labels: Sequence[str],
    policy: ThresholdPolicy | None = None,
) -> dict[str, float]:
    """Pick the F1-maximising threshold for each label within bounds.

    Args:
        y_true: Dev-set gold labels, shape ``(n_items, n_labels)``.
        probs: Model scores for the same items, same shape.
        labels: Label names in column order.
        policy: Guard rails; defaults to :class:`ThresholdPolicy`.

    Returns:
        Mapping from label name to threshold.

    Raises:
        ValueError: On shape mismatch.
    """
    policy = policy or ThresholdPolicy()
    if y_true.shape != probs.shape or y_true.shape[1] != len(labels):
        raise ValueError(
            f"shape mismatch: y {y_true.shape}, probs {probs.shape}, "
            f"{len(labels)} labels"
        )
    out: dict[str, float] = {}
    for k, label in enumerate(labels):
        y = y_true[:, k].astype(bool)
        if y.sum() < policy.min_positives or y.all():
            out[label] = policy.fallback
            continue
        out[label] = _best_f1_threshold(y, probs[:, k], policy)
    return out


def apply_thresholds(
    probs: FloatMatrix, thresholds: Mapping[str, float], labels: Sequence[str]
) -> BoolMatrix:
    """Binarise scores with per-label thresholds (``score >= threshold``).

    Raises:
        KeyError: If a label has no threshold.
    """
    cut = np.array([thresholds[label] for label in labels], dtype=np.float64)
    return np.asarray(probs >= cut[None, :])
