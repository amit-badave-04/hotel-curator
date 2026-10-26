"""Multi-label classification metrics with honest confidence intervals.

Why this module exists: v1 reported point estimates only, and its gold-set
number was tuned and scored on the same rows. v2 reports every headline
metric with a 95% interval from a *cluster* bootstrap: we resample hotels,
not reviews, because reviews of the same hotel share vocabulary and errors,
so treating them as independent makes intervals too narrow.

All functions take boolean matrices of shape ``(n_items, n_labels)``.
F1 for a label is ``2TP / (2TP + FP + FN)``; a label with no positives and
no predictions has undefined F1 (NaN) and is left out of the macro average,
matching scikit-learn's ``zero_division=np.nan`` behaviour.

Complexity: a bootstrap costs O(B * H * L) after an O(N * L) aggregation,
where B is resamples, H clusters, N items and L labels.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

BoolMatrix = npt.NDArray[np.bool_]
IntArray = npt.NDArray[np.int64]
FloatArray = npt.NDArray[np.float64]

DEFAULT_N_BOOT = 2000
DEFAULT_ALPHA = 0.05
BOOT_CHUNK = 250
"""Resamples drawn per block, bounding memory to BOOT_CHUNK x n_clusters."""


@dataclass(frozen=True)
class LabelScore:
    """Precision, recall and F1 for one label."""

    label: str
    support: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True)
class Interval:
    """A point estimate with a two-sided bootstrap percentile interval."""

    point: float
    low: float
    high: float


@dataclass(frozen=True)
class PairedDiff:
    """Bootstrap distribution summary of ``metric(A) - metric(B)``.

    Attributes:
        p_value: One-sided bootstrap p-value for "A is not better than B",
            the share of resamples where the difference is <= 0.
    """

    point: float
    low: float
    high: float
    p_value: float


def _validate(y_true: BoolMatrix, y_pred: BoolMatrix) -> None:
    if y_true.shape != y_pred.shape or y_true.ndim != 2:
        raise ValueError(
            "y_true and y_pred must be 2-D with the same shape, got "
            f"{y_true.shape} and {y_pred.shape}"
        )


def _item_counts(y_true: BoolMatrix, y_pred: BoolMatrix) -> IntArray:
    """Per-item TP/FP/FN indicators, shape ``(n_items, n_labels, 3)``."""
    t = y_true.astype(bool)
    p = y_pred.astype(bool)
    return np.stack([t & p, ~t & p, t & ~p], axis=-1).astype(np.int64)


def label_counts(y_true: BoolMatrix, y_pred: BoolMatrix) -> IntArray:
    """Return ``[tp, fp, fn]`` per label, shape ``(n_labels, 3)``."""
    _validate(y_true, y_pred)
    return _item_counts(y_true, y_pred).sum(axis=0)


def _f1_from_counts(counts: npt.NDArray[np.number]) -> FloatArray:
    """F1 per label from ``[..., 3]`` counts; NaN where undefined."""
    tp, fp, fn = counts[..., 0], counts[..., 1], counts[..., 2]
    denom = 2 * tp + fp + fn
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(denom > 0, 2 * tp / denom, np.nan).astype(np.float64)


def _macro(f1: FloatArray) -> FloatArray:
    with np.errstate(invalid="ignore"):
        valid = ~np.isnan(f1)
        total = np.where(valid, f1, 0.0).sum(axis=-1)
        n = valid.sum(axis=-1)
        return np.where(n > 0, total / np.maximum(n, 1), np.nan)


def per_label_scores(
    y_true: BoolMatrix, y_pred: BoolMatrix, labels: Sequence[str]
) -> list[LabelScore]:
    """Compute precision, recall and F1 for every label.

    Raises:
        ValueError: If shapes differ or ``labels`` has the wrong length.
    """
    counts = label_counts(y_true, y_pred)
    if len(labels) != counts.shape[0]:
        raise ValueError(
            f"Got {len(labels)} label names for {counts.shape[0]} columns"
        )
    f1 = _f1_from_counts(counts)
    scores = []
    for i, name in enumerate(labels):
        tp, fp, fn = (int(v) for v in counts[i])
        precision = tp / (tp + fp) if tp + fp else float("nan")
        recall = tp / (tp + fn) if tp + fn else float("nan")
        scores.append(
            LabelScore(
                name, tp + fn, tp, fp, fn, precision, recall, float(f1[i])
            )
        )
    return scores


def macro_f1(y_true: BoolMatrix, y_pred: BoolMatrix) -> float:
    """Unweighted mean F1 over labels where F1 is defined."""
    return float(_macro(_f1_from_counts(label_counts(y_true, y_pred))))


def micro_f1(y_true: BoolMatrix, y_pred: BoolMatrix) -> float:
    """F1 over all label decisions pooled together."""
    total = label_counts(y_true, y_pred).sum(axis=0)
    return float(_f1_from_counts(total))


def _cluster_counts(
    y_true: BoolMatrix,
    y_pred: BoolMatrix,
    clusters: npt.ArrayLike | None,
) -> IntArray:
    """Sum per-item counts within clusters, shape ``(n_clusters, L, 3)``."""
    _validate(y_true, y_pred)
    items = _item_counts(y_true, y_pred)
    if clusters is None:
        return items
    ids = np.asarray(clusters)
    if ids.shape[0] != items.shape[0]:
        raise ValueError(
            f"clusters has {ids.shape[0]} entries for {items.shape[0]} items"
        )
    _, inverse = np.unique(ids, return_inverse=True)
    out = np.zeros((inverse.max() + 1, *items.shape[1:]), dtype=np.int64)
    np.add.at(out, inverse, items)
    return out


def _resample_weights(
    n_clusters: int, n_boot: int, rng: np.random.Generator
) -> npt.NDArray[np.float64]:
    """Multinomial cluster weights, shape ``(n_boot, n_clusters)``."""
    probs = np.full(n_clusters, 1.0 / n_clusters)
    return rng.multinomial(n_clusters, probs, size=n_boot).astype(np.float64)


def _boot_macro(cluster_counts: IntArray, weights: FloatArray) -> FloatArray:
    resampled = np.einsum("bh,hlk->blk", weights, cluster_counts)
    return _macro(_f1_from_counts(resampled))


def _boot_stat(
    count_sets: Sequence[IntArray], n_boot: int, seed: int
) -> list[FloatArray]:
    """Bootstrap macro-F1 for several count tensors on shared resamples.

    Resamples are drawn in blocks of :data:`BOOT_CHUNK` so memory stays
    bounded however many items or resamples there are.
    """
    rng = np.random.default_rng(seed)
    n_clusters = count_sets[0].shape[0]
    blocks: list[list[FloatArray]] = [[] for _ in count_sets]
    done = 0
    while done < n_boot:
        size = min(BOOT_CHUNK, n_boot - done)
        weights = _resample_weights(n_clusters, size, rng)
        for i, counts in enumerate(count_sets):
            blocks[i].append(_boot_macro(counts, weights))
        done += size
    return [np.concatenate(b) for b in blocks]


def bootstrap_macro_f1(
    y_true: BoolMatrix,
    y_pred: BoolMatrix,
    clusters: npt.ArrayLike | None,
    *,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
) -> Interval:
    """Macro-F1 with a percentile interval from a cluster bootstrap.

    Args:
        y_true: Gold labels, shape ``(n_items, n_labels)``.
        y_pred: Predicted labels, same shape.
        clusters: Cluster id per item (for example hotel id). ``None``
            treats every item as its own cluster.
        n_boot: Number of bootstrap resamples.
        alpha: Two-sided miss rate; 0.05 gives a 95% interval.
        seed: Random seed for the resampling weights.

    Returns:
        The full-data macro-F1 and its interval.
    """
    counts = _cluster_counts(y_true, y_pred, clusters)
    (boot,) = _boot_stat([counts], n_boot, seed)
    low, high = np.nanquantile(boot, [alpha / 2, 1 - alpha / 2])
    return Interval(macro_f1(y_true, y_pred), float(low), float(high))


def paired_bootstrap_macro_f1_diff(
    y_true: BoolMatrix,
    pred_a: BoolMatrix,
    pred_b: BoolMatrix,
    clusters: npt.ArrayLike | None,
    *,
    n_boot: int = DEFAULT_N_BOOT,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
) -> PairedDiff:
    """Paired cluster bootstrap of ``macroF1(A) - macroF1(B)``.

    Both models are scored on the same resampled clusters in each
    iteration, which is what makes the comparison paired.
    """
    counts_a = _cluster_counts(y_true, pred_a, clusters)
    counts_b = _cluster_counts(y_true, pred_b, clusters)
    boot_a, boot_b = _boot_stat([counts_a, counts_b], n_boot, seed)
    diff = boot_a - boot_b
    low, high = np.nanquantile(diff, [alpha / 2, 1 - alpha / 2])
    point = macro_f1(y_true, pred_a) - macro_f1(y_true, pred_b)
    p_value = float(np.mean(diff <= 0))
    return PairedDiff(float(point), float(low), float(high), p_value)
