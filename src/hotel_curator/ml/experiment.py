"""Run one rung of the model ladder end to end and record the outcome.

Every rung goes through the same steps, so differences in the comparison
table come from the models and nothing else:

1. the model produces raw scores for gold-dev and gold-test parts;
2. per-label thresholds are tuned on gold-dev (``ml.thresholds``);
3. per-label isotonic calibration is fitted on gold-dev with the
   inverse-inclusion sampling weights (``ml.calibration``);
4. both splits are scored by the harness (``ml.evaluate``);
5. raw scores, thresholds, calibrator and reports are written to
   ``artifacts/evals/<rung>/`` (committed, so the comparison can be rebuilt
   without retraining) and logged to MLflow.

gold-test is touched only in step 4.
"""

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from hotel_curator.ml.calibration import IsotonicCalibrator
from hotel_curator.ml.data import LabeledParts
from hotel_curator.ml.evaluate import (
    EvalConfig,
    EvalReport,
    noisy_or,
    review_level,
    score_predictions,
    score_review_predictions,
)
from hotel_curator.ml.thresholds import tune_thresholds
from hotel_curator.taxonomy import LABELS

logger = logging.getLogger(__name__)

FloatMatrix = npt.NDArray[np.float64]
PREDICTION_KEYS = ("review_id", "part")


@dataclass
class RungOutcome:
    """Everything one rung produced, ready to be saved and compared."""

    name: str
    family: str
    level: str
    dev: EvalReport
    test: EvalReport
    thresholds: dict[str, float]
    calibrator: IsotonicCalibrator
    params: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        """Headline numbers for the comparison table and MLflow."""
        return {
            "name": self.name,
            "family": self.family,
            "level": self.level,
            "dev_review_macro_f1": self.dev.review_macro_f1.point,
            "test_review_macro_f1": self.test.review_macro_f1.point,
            "test_review_macro_f1_low": self.test.review_macro_f1.low,
            "test_review_macro_f1_high": self.test.review_macro_f1.high,
            "test_review_micro_f1": self.test.review_micro_f1,
            "test_part_macro_f1": (
                self.test.part_macro_f1.point
                if self.test.part_macro_f1
                else math.nan
            ),
            "test_mean_review_ece": self.test.mean_review_ece,
            "test_review_brier": self.test.review_brier,
            "test_cross_polarity_f1": self.test.cross_polarity.get(
                "f1", math.nan
            ),
            **{f"seconds_{k}": v for k, v in self.timings.items()},
        }


def assess_parts(
    name: str,
    family: str,
    dev: LabeledParts,
    test: LabeledParts,
    dev_raw: FloatMatrix,
    test_raw: FloatMatrix,
    config: EvalConfig,
) -> RungOutcome:
    """Tune, calibrate and score a part-level model on both gold splits."""
    thresholds = tune_thresholds(dev.y, dev_raw, LABELS)
    calibrator = IsotonicCalibrator.fit(
        dev.y, dev_raw, LABELS, sample_weight=dev.weights
    )
    scored = EvalConfig(
        thresholds=thresholds,
        n_boot=config.n_boot,
        n_boot_slices=config.n_boot_slices,
        min_slice_reviews=config.min_slice_reviews,
        seed=config.seed,
    )
    reports = [
        score_predictions(
            name, split.frame, raw, scored, calibrated=calibrator.transform(raw)
        )
        for split, raw in ((dev, dev_raw), (test, test_raw))
    ]
    return RungOutcome(
        name, family, "part", reports[0], reports[1], thresholds, calibrator
    )


def _review_weights(split: LabeledParts, ids: list[str]) -> FloatMatrix:
    weights = split.frame.groupby("review_id")["sampling_weight"].first()
    return weights.reindex(ids).to_numpy(dtype=np.float64)


def assess_reviews(
    name: str,
    family: str,
    dev: LabeledParts,
    test: LabeledParts,
    dev_scores: tuple[list[str], FloatMatrix],
    test_scores: tuple[list[str], FloatMatrix],
    config: EvalConfig,
    *,
    thresholds: dict[str, float] | None = None,
) -> RungOutcome:
    """Like :func:`assess_parts` for a model that scores whole reviews.

    Args:
        name: Rung name.
        family: Rung family (``v1`` for the v1 checkpoint).
        dev: Gold-dev parts (labels are merged to review level).
        test: Gold-test parts.
        dev_scores: Review ids and raw scores for gold-dev reviews.
        test_scores: Review ids and raw scores for gold-test reviews.
        config: Bootstrap settings.
        thresholds: Fixed thresholds (e.g. v1's shipped ones); tuned on
            gold-dev at review level when ``None``.
    """
    unique, dev_true = review_level(dev.frame["review_id"], dev.y)
    dev_aligned = _align_reviews(unique.tolist(), *dev_scores)
    tuned = thresholds or tune_thresholds(dev_true, dev_aligned, LABELS)
    calibrator = IsotonicCalibrator.fit(
        dev_true,
        dev_aligned,
        LABELS,
        sample_weight=_review_weights(dev, unique.tolist()),
    )
    scored = replace(config, thresholds=tuned)
    reports = []
    for split, (ids, raw) in ((dev, dev_scores), (test, test_scores)):
        report = score_review_predictions(name, split.frame, ids, raw, scored)
        calibrated = score_review_predictions(
            name, split.frame, ids, calibrator.transform(raw), scored
        )
        report.review_ece = calibrated.review_ece
        report.review_brier = calibrated.review_brier
        report.calibration_in_the_large = calibrated.calibration_in_the_large
        reports.append(report)
    return RungOutcome(
        name, family, "review", reports[0], reports[1], tuned, calibrator
    )


def _align_reviews(
    wanted: list[str], ids: list[str], raw: FloatMatrix
) -> FloatMatrix:
    """Rows of ``raw`` (keyed by ``ids``) in the order of ``wanted``.

    Raises:
        ValueError: If a wanted review has no score.
    """
    order = pd.Index(ids).get_indexer(pd.Index(wanted))
    if (order < 0).any():
        raise ValueError(f"{int((order < 0).sum())} reviews have no score")
    return raw[order]


def _prediction_frame(split: LabeledParts, raw: FloatMatrix) -> pd.DataFrame:
    frame = split.frame[list(PREDICTION_KEYS)].reset_index(drop=True)
    return pd.concat(
        [frame, pd.DataFrame(raw.astype(np.float64), columns=list(LABELS))],
        axis=1,
    )


def save_outcome(
    outcome: RungOutcome,
    out_dir: Path,
    predictions: dict[str, pd.DataFrame] | None = None,
) -> Path:
    """Write reports, thresholds, calibrator and raw scores to ``out_dir``.

    Returns:
        Path of ``report.json``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": outcome.summary(),
        "params": outcome.params,
        "dev": outcome.dev.to_dict(),
        "test": outcome.test.to_dict(),
    }
    path = out_dir / "report.json"
    path.write_text(json.dumps(payload, indent=1, default=_json_default))
    (out_dir / "thresholds.json").write_text(
        json.dumps(outcome.thresholds, indent=1)
    )
    (out_dir / "calibration.json").write_text(
        json.dumps(outcome.calibrator.to_dict())
    )
    for split, frame in (predictions or {}).items():
        frame.to_parquet(out_dir / f"scores_{split}.parquet", index=False)
    return path


def part_predictions(
    dev: LabeledParts,
    test: LabeledParts,
    dev_raw: FloatMatrix,
    test_raw: FloatMatrix,
) -> dict[str, pd.DataFrame]:
    """Raw part scores keyed by split, in the committed layout."""
    return {
        "gold_dev": _prediction_frame(dev, dev_raw),
        "gold_test": _prediction_frame(test, test_raw),
    }


def review_predictions(
    scores: dict[str, tuple[list[str], FloatMatrix]],
) -> dict[str, pd.DataFrame]:
    """Raw review scores keyed by split."""
    return {
        split: pd.concat(
            [
                pd.DataFrame({"review_id": ids}),
                pd.DataFrame(raw.astype(np.float64), columns=list(LABELS)),
            ],
            axis=1,
        )
        for split, (ids, raw) in scores.items()
    }


def load_review_level_predictions(
    out_dir: Path,
    split: str,
    threshold_file: str = "thresholds.json",
    scores: str = "scores",
) -> tuple[npt.NDArray[np.str_], npt.NDArray[np.bool_], FloatMatrix]:
    """Review ids, thresholded labels and calibrated noisy-OR probabilities.

    Works for both part-level and review-level rungs, so any two rungs can
    be compared review by review (paired bootstrap). ``scores`` is the file
    prefix: the rung's own scores, or :data:`PACKAGE_SCORES` for its
    packaged model (which carries the same thresholds and calibration).
    """
    frame = pd.read_parquet(out_dir / f"{scores}_{split}.parquet")
    thresholds = json.loads((out_dir / threshold_file).read_text())
    calibrator = IsotonicCalibrator.from_dict(
        json.loads((out_dir / "calibration.json").read_text())
    )
    raw = frame[list(LABELS)].to_numpy(dtype=np.float64)
    cut = np.array([thresholds[label] for label in LABELS])
    ids, labels = review_level(frame["review_id"], raw >= cut)
    _, probs = noisy_or(frame["review_id"], calibrator.transform(raw))
    return ids, labels, probs


def _json_default(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


FIXED_THRESHOLDS = "v1-thresholds"
"""``params["thresholds"]`` of a rung scored at given, not tuned, thresholds."""
PACKAGE_SCORES = "package_scores"
"""File prefix of the packaged model's raw gold scores."""
PACKAGE_OUTPUTS = (
    "throughput.json",
    "package_report.json",
    f"{PACKAGE_SCORES}_gold_dev.parquet",
    f"{PACKAGE_SCORES}_gold_test.parquet",
)
"""Files ``curator model package`` writes next to a rung's report."""


def rescore_with_thresholds(
    outcome: RungOutcome,
    thresholds: dict[str, float],
    splits: tuple[LabeledParts, LabeledParts],
    raw: tuple[FloatMatrix, FloatMatrix],
    config: EvalConfig,
) -> RungOutcome:
    """Re-score a part-level outcome at fixed thresholds (e.g. v1's own)."""
    fixed = replace(config, thresholds=thresholds)
    outcome.dev, outcome.test = (
        score_predictions(
            outcome.name,
            split.frame,
            split_raw,
            fixed,
            calibrated=outcome.calibrator.transform(split_raw),
        )
        for split, split_raw in zip(splits, raw, strict=True)
    )
    outcome.thresholds = thresholds
    return outcome


def saved_part_scores(out_dir: Path, split: LabeledParts) -> FloatMatrix:
    """A rung's saved raw part scores, in the row order of ``split``.

    Raises:
        ValueError: If a part of ``split`` has no saved score.
    """
    scores = pd.read_parquet(out_dir / f"scores_{split.name}.parquet")
    merged = split.frame[["review_id", "part"]].merge(
        scores, on=["review_id", "part"], how="left", validate="one_to_one"
    )
    raw = merged[list(LABELS)].to_numpy(dtype=np.float64)
    if np.isnan(raw).any():
        raise ValueError(f"{out_dir.name}: {split.name} parts without scores")
    return raw


def saved_review_scores(
    out_dir: Path, split_name: str
) -> tuple[list[str], FloatMatrix]:
    """A review-level rung's saved review ids and raw scores."""
    scores = pd.read_parquet(out_dir / f"scores_{split_name}.parquet")
    ids = scores["review_id"].astype(str).tolist()
    return ids, scores[list(LABELS)].to_numpy(dtype=np.float64)


def reassess(
    out_dir: Path, dev: LabeledParts, test: LabeledParts, config: EvalConfig
) -> RungOutcome:
    """Re-score a saved rung with the current harness, without retraining.

    Thresholds and calibration are re-fitted on gold-dev from the saved raw
    scores, as at training time; a rung scored at fixed thresholds keeps
    them. Parameters and timings are carried over.
    """
    report = json.loads((out_dir / "report.json").read_text())
    summary, params = report["summary"], report["params"]
    name, family = summary["name"], summary["family"]
    fixed = None
    if params.get("thresholds") == FIXED_THRESHOLDS:
        fixed = json.loads((out_dir / "thresholds.json").read_text())
    if summary["level"] == "review":
        outcome = assess_reviews(
            name,
            family,
            dev,
            test,
            saved_review_scores(out_dir, dev.name),
            saved_review_scores(out_dir, test.name),
            config,
            thresholds=fixed,
        )
    else:
        raw = (
            saved_part_scores(out_dir, dev),
            saved_part_scores(out_dir, test),
        )
        outcome = assess_parts(name, family, dev, test, *raw, config)
        if fixed is not None:
            outcome = rescore_with_thresholds(
                outcome, fixed, (dev, test), raw, config
            )
    outcome.params = params
    outcome.timings = {
        key.removeprefix("seconds_"): value
        for key, value in summary.items()
        if key.startswith("seconds_")
    }
    return outcome


def clear_package_outputs(out_dir: Path) -> None:
    """Drop package measurements that no longer describe the rung.

    Called after retraining (another checkpoint) and after re-scoring with
    the harness (other thresholds, calibration or metrics): the package
    must then be built and judged again.
    """
    for name in PACKAGE_OUTPUTS:
        (out_dir / name).unlink(missing_ok=True)


def scoring_rules_sha256(
    thresholds: dict[str, float], calibration: dict[str, Any]
) -> str:
    """Fingerprint of the thresholds and calibration a model is judged with.

    A package embeds both; promotion judges a package only while they still
    equal the rung's current ``thresholds.json`` and ``calibration.json``.
    """
    canonical = json.dumps(
        {"thresholds": thresholds, "calibration": calibration},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def rung_rules_sha256(out_dir: Path) -> str:
    """:func:`scoring_rules_sha256` of a rung's saved thresholds, calibrator."""
    return scoring_rules_sha256(
        json.loads((out_dir / "thresholds.json").read_text()),
        json.loads((out_dir / "calibration.json").read_text()),
    )
