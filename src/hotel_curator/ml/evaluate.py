"""The evaluation harness: one way to score every model on the gold set.

Every model rung (TF-IDF, embeddings, fine-tuned encoder) and the v1
checkpoint is scored here, on the same rows, with the same metrics, so the
comparison table is apples to apples.

Unit of prediction: a review *part* (the Liked or the Disliked text). A
review's labels are the union of its parts' labels. The headline metric is
**review-level macro-F1 over the 16 labels**, with a 95% interval from a
bootstrap that resamples hotels. Review level is the level v1 predicted at,
so it is the level at which v1 and v2 can be compared fairly.

What the report contains: part- and review-level macro/micro F1, per-label
precision/recall/F1 with support, expected calibration error per label, and
slices (part type, sampling stratum, city, review length) so weaknesses are
visible rather than averaged away.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
import pandas as pd

from hotel_curator.ml.calibration import (
    brier_score,
    calibration_in_the_large,
    expected_calibration_error,
)
from hotel_curator.ml.metrics import (
    DEFAULT_N_BOOT,
    Interval,
    LabelScore,
    bootstrap_macro_f1,
    micro_f1,
    per_label_scores,
)
from hotel_curator.ml.thresholds import apply_thresholds
from hotel_curator.taxonomy import LABELS, Polarity, split_label

FloatMatrix = npt.NDArray[np.floating]
BoolMatrix = npt.NDArray[np.bool_]

LENGTH_BANDS = (
    (0, 10, "words<10"),
    (10, 40, "words10-39"),
    (40, 10**6, "words40+"),
)


class Predictor(Protocol):
    """Anything that maps review parts to 16 label probabilities."""

    name: str

    def predict_proba(
        self, texts: Sequence[str], parts: Sequence[str]
    ) -> FloatMatrix:
        """Return probabilities of shape ``(len(texts), 16)``."""
        ...


@dataclass(frozen=True)
class EvalConfig:
    """Evaluation settings.

    Attributes:
        thresholds: Per-label decision thresholds (0.5 when ``None``).
        n_boot: Bootstrap resamples for the headline intervals.
        n_boot_slices: Resamples for slice intervals (cheaper).
        min_slice_reviews: Slices with fewer reviews are skipped.
        seed: Bootstrap seed.
    """

    thresholds: Mapping[str, float] | None = None
    n_boot: int = DEFAULT_N_BOOT
    n_boot_slices: int = 500
    min_slice_reviews: int = 30
    seed: int = 0


@dataclass(frozen=True)
class SliceResult:
    """Macro-F1 on one subset of the evaluation data."""

    name: str
    level: str
    n_items: int
    macro_f1: Interval


@dataclass
class EvalReport:
    """Everything the harness measured for one model on one gold split."""

    model: str
    n_parts: int
    n_reviews: int
    n_hotels: int
    review_macro_f1: Interval
    review_micro_f1: float
    part_macro_f1: Interval | None
    part_micro_f1: float | None
    per_label: list[LabelScore]
    ece: dict[str, float]
    review_ece: dict[str, float] = field(default_factory=dict)
    review_brier: float = float("nan")
    calibration_in_the_large: dict[str, tuple[float, float]] = field(
        default_factory=dict
    )
    cross_polarity: dict[str, float] = field(default_factory=dict)
    slices: list[SliceResult] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)

    @property
    def mean_review_ece(self) -> float:
        """Mean over labels of the review-level ECE (promotion rule 3)."""
        return float(np.mean(list(self.review_ece.values())))

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible representation (NaN kept as float NaN)."""
        return asdict(self)


def review_level(
    review_ids: npt.ArrayLike, part_labels: BoolMatrix
) -> tuple[npt.NDArray[np.str_], BoolMatrix]:
    """Union part-level labels into review-level labels.

    Returns:
        Sorted unique review ids and their merged label matrix.
    """
    ids = np.asarray(review_ids)
    unique, inverse = np.unique(ids, return_inverse=True)
    merged = np.zeros((len(unique), part_labels.shape[1]), dtype=bool)
    np.logical_or.at(merged, inverse, part_labels.astype(bool))
    return unique, merged


def noisy_or(
    review_ids: npt.ArrayLike, part_probs: FloatMatrix
) -> tuple[npt.NDArray[np.str_], npt.NDArray[np.float64]]:
    """Review-level probabilities: ``1 - prod(1 - p_part)`` per label.

    This is the per-review judgement the hotel scores use (ADR 006): a
    review counts at most once per label however many parts mention it.

    Returns:
        Sorted unique review ids and their ``(n_reviews, n_labels)``
        probabilities.
    """
    ids = np.asarray(review_ids)
    unique, inverse = np.unique(ids, return_inverse=True)
    log_miss = np.zeros((len(unique), part_probs.shape[1]), dtype=np.float64)
    clipped = np.clip(np.asarray(part_probs, dtype=np.float64), 0.0, 1.0)
    np.add.at(log_miss, inverse, np.log1p(-np.minimum(clipped, 1 - 1e-12)))
    return unique, 1.0 - np.exp(log_miss)


def cross_polarity_scores(
    gold: pd.DataFrame, y_true: BoolMatrix, y_pred: BoolMatrix
) -> dict[str, float]:
    """Micro P/R/F1 on cross-polarity cells.

    Cross-polarity cells are negative labels in Liked parts and positive
    labels in Disliked parts ("Liked: nothing, the room was dirty"). A model
    that learned "Liked means positive" scores badly here even when its
    overall F1 is high.
    """
    liked = (gold["part"] == "liked").to_numpy()
    negative = np.array(
        [split_label(label)[1] is Polarity.NEGATIVE for label in LABELS]
    )
    cells = np.where(liked[:, None], negative[None, :], ~negative[None, :])
    t, p = y_true & cells, y_pred & cells
    tp, fp, fn = int((t & p).sum()), int((~t & p).sum()), int((t & ~p).sum())
    return {
        "support": float(tp + fn),
        "precision": tp / (tp + fp) if tp + fp else float("nan"),
        "recall": tp / (tp + fn) if tp + fn else float("nan"),
        "f1": 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else float("nan"),
    }


def _calibration(
    rev_true: BoolMatrix,
    rev_probs: FloatMatrix,
    weights: npt.NDArray[np.float64],
) -> tuple[dict[str, float], float, dict[str, tuple[float, float]]]:
    ece = {
        label: expected_calibration_error(rev_true[:, k], rev_probs[:, k])
        for k, label in enumerate(LABELS)
    }
    in_large = {
        label: calibration_in_the_large(
            rev_true[:, k], rev_probs[:, k], weights
        )
        for k, label in enumerate(LABELS)
    }
    return ece, brier_score(rev_true, rev_probs), in_large


def _gold_matrix(gold: pd.DataFrame) -> BoolMatrix:
    missing = [label for label in LABELS if label not in gold.columns]
    if missing:
        raise KeyError(f"gold is missing label columns: {missing}")
    return gold[list(LABELS)].to_numpy(dtype=bool)


def _review_frame(gold: pd.DataFrame) -> pd.DataFrame:
    words = gold["text"].str.count(r"\S+").groupby(gold["review_id"]).sum()
    first = gold.groupby("review_id").first()
    frame = pd.DataFrame({"hotel_id": first["hotel_id"], "words": words})
    for column in ("stratum", "city", "sampling_weight"):
        if column in gold.columns:
            frame[column] = first[column]
    return frame.sort_index()


def _slices(
    masks: Mapping[str, npt.NDArray[np.bool_]],
    level: str,
    y_true: BoolMatrix,
    y_pred: BoolMatrix,
    clusters: npt.ArrayLike,
    config: EvalConfig,
) -> list[SliceResult]:
    out = []
    cluster_arr = np.asarray(clusters)
    for name, mask in masks.items():
        if mask.sum() < config.min_slice_reviews:
            continue
        ci = bootstrap_macro_f1(
            y_true[mask],
            y_pred[mask],
            cluster_arr[mask],
            n_boot=config.n_boot_slices,
            seed=config.seed,
        )
        out.append(SliceResult(name, level, int(mask.sum()), ci))
    return out


def _review_masks(frame: pd.DataFrame) -> dict[str, npt.NDArray[np.bool_]]:
    masks: dict[str, npt.NDArray[np.bool_]] = {}
    for column in ("stratum", "city"):
        if column in frame.columns:
            for value in sorted(frame[column].dropna().unique()):
                masks[f"{column}={value}"] = (frame[column] == value).to_numpy()
    for low, high, name in LENGTH_BANDS:
        words = frame["words"]
        masks[name] = ((words >= low) & (words < high)).to_numpy()
    return masks


def _review_weights(frame: pd.DataFrame) -> npt.NDArray[np.float64]:
    """Inverse-inclusion weight per review (1 when not recorded)."""
    if "sampling_weight" not in frame.columns:
        return np.ones(len(frame), dtype=np.float64)
    return frame["sampling_weight"].to_numpy(dtype=np.float64)


def score_predictions(
    model: str,
    gold: pd.DataFrame,
    part_probs: FloatMatrix,
    config: EvalConfig,
    calibrated: FloatMatrix | None = None,
) -> EvalReport:
    """Score part-level probabilities against the gold parts.

    Args:
        model: Name recorded in the report.
        gold: One row per (review, part) with ``review_id``, ``hotel_id``,
            ``part``, ``text`` and the 16 label columns.
        part_probs: Raw scores aligned with ``gold`` rows; thresholds apply
            to these.
        config: Thresholds and bootstrap settings.
        calibrated: Calibrated probabilities for the same rows, used for
            ECE, Brier and review-level probabilities. Defaults to
            ``part_probs``.
    """
    y_true = _gold_matrix(gold)
    if part_probs.shape != y_true.shape:
        raise ValueError(f"probs {part_probs.shape} vs gold {y_true.shape}")
    probs = part_probs if calibrated is None else calibrated
    if probs.shape != y_true.shape:
        raise ValueError(f"calibrated {probs.shape} vs gold {y_true.shape}")
    thresholds = dict(config.thresholds or dict.fromkeys(LABELS, 0.5))
    y_pred = apply_thresholds(part_probs, thresholds, LABELS)
    review_ids, rev_true = review_level(gold["review_id"], y_true)
    _, rev_pred = review_level(gold["review_id"], y_pred)
    _, rev_probs = noisy_or(gold["review_id"], probs)
    report = _review_report(
        model, gold, review_ids, rev_true, rev_pred, rev_probs, config
    )
    _add_part_metrics(report, gold, (y_true, y_pred, probs), config)
    report.thresholds = thresholds
    return report


def _add_part_metrics(
    report: EvalReport,
    gold: pd.DataFrame,
    arrays: tuple[BoolMatrix, BoolMatrix, FloatMatrix],
    config: EvalConfig,
) -> None:
    """Part-level F1, per-part slices, ECE and cross-polarity errors."""
    y_true, y_pred, probs = arrays
    hotels = gold["hotel_id"].to_numpy()
    part_masks = {
        f"part={p}": (gold["part"] == p).to_numpy()
        for p in ("liked", "disliked")
    }
    report.slices = (
        _slices(part_masks, "part", y_true, y_pred, hotels, config)
        + report.slices
    )
    report.n_parts = len(gold)
    report.part_macro_f1 = bootstrap_macro_f1(
        y_true, y_pred, hotels, n_boot=config.n_boot, seed=config.seed
    )
    report.part_micro_f1 = micro_f1(y_true, y_pred)
    report.ece = {
        label: expected_calibration_error(y_true[:, k], probs[:, k])
        for k, label in enumerate(LABELS)
    }
    report.cross_polarity = cross_polarity_scores(gold, y_true, y_pred)


def _review_report(
    model: str,
    gold: pd.DataFrame,
    review_ids: npt.NDArray[np.str_],
    rev_true: BoolMatrix,
    rev_pred: BoolMatrix,
    rev_probs: FloatMatrix,
    config: EvalConfig,
) -> EvalReport:
    frame = _review_frame(gold).reindex(review_ids.tolist())
    rev_hotels = frame["hotel_id"].to_numpy()
    review_ece, brier, in_large = _calibration(
        rev_true, rev_probs, _review_weights(frame)
    )
    return EvalReport(
        model=model,
        n_parts=0,
        n_reviews=len(review_ids),
        n_hotels=int(gold["hotel_id"].nunique()),
        review_macro_f1=bootstrap_macro_f1(
            rev_true,
            rev_pred,
            rev_hotels,
            n_boot=config.n_boot,
            seed=config.seed,
        ),
        review_micro_f1=micro_f1(rev_true, rev_pred),
        part_macro_f1=None,
        part_micro_f1=None,
        per_label=per_label_scores(rev_true, rev_pred, LABELS),
        ece={},
        review_ece=review_ece,
        review_brier=brier,
        calibration_in_the_large=in_large,
        slices=_slices(
            _review_masks(frame),
            "review",
            rev_true,
            rev_pred,
            rev_hotels,
            config,
        ),
        thresholds=dict(config.thresholds or dict.fromkeys(LABELS, 0.5)),
    )


def score_review_predictions(
    model: str,
    gold: pd.DataFrame,
    review_ids: Sequence[str],
    review_probs: FloatMatrix,
    config: EvalConfig,
) -> EvalReport:
    """Score a model that predicts whole reviews (v1) on the gold reviews.

    Args:
        model: Name recorded in the report.
        gold: Gold parts, as for :func:`score_predictions`.
        review_ids: Review ids, aligned with ``review_probs`` rows.
        review_probs: Review-level probabilities ``(n_reviews, 16)``.
        config: Thresholds and bootstrap settings.

    Raises:
        ValueError: If the reviews differ from the gold reviews.
    """
    unique, rev_true = review_level(gold["review_id"], _gold_matrix(gold))
    order = pd.Index(list(review_ids))
    if set(order) != set(unique.tolist()) or len(order) != len(unique):
        raise ValueError("review_ids must match the gold reviews exactly")
    probs = np.asarray(review_probs, dtype=np.float64)[
        order.get_indexer(unique.tolist())
    ]
    thresholds = dict(config.thresholds or dict.fromkeys(LABELS, 0.5))
    rev_pred = apply_thresholds(probs, thresholds, LABELS)
    return _review_report(
        model, gold, unique, rev_true, rev_pred, probs, config
    )


def evaluate(
    predictor: Predictor, gold: pd.DataFrame, config: EvalConfig
) -> EvalReport:
    """Run a part-level predictor on the gold parts and score it."""
    probs = predictor.predict_proba(
        gold["text"].tolist(), gold["part"].tolist()
    )
    return score_predictions(predictor.name, gold, np.asarray(probs), config)
