"""Tests for label-set loading, the rung runner and the v1 adapter helpers."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hotel_curator.ml.data import LabeledParts, load_all, load_label_set
from hotel_curator.ml.embeddings import EmbeddingLogReg
from hotel_curator.ml.evaluate import EvalConfig
from hotel_curator.ml.experiment import (
    FIXED_THRESHOLDS,
    PACKAGE_OUTPUTS,
    assess_parts,
    assess_reviews,
    clear_package_outputs,
    load_review_level_predictions,
    part_predictions,
    reassess,
    rescore_with_thresholds,
    review_predictions,
    save_outcome,
)
from hotel_curator.ml.v1 import v1_review_texts, v1_thresholds
from hotel_curator.taxonomy import LABELS, V1_TO_V2_LABEL

FAST = EvalConfig(n_boot=50, n_boot_slices=20, seed=0)


def _parts(prefix: str, n_reviews: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_reviews):
        for part in ("liked", "disliked"):
            rows.append(
                {
                    "review_id": f"r_{prefix}{i:04d}",
                    "hotel_id": f"h_{prefix}{i % 25:03d}",
                    "part": part,
                    "text": f"{part} text {i}",
                    "stratum": "enriched" if i % 3 == 0 else "uniform",
                    "sampling_weight": 2.0 if i % 3 == 0 else 1.0,
                    "city": "Paris",
                    **dict(
                        zip(LABELS, rng.random(len(LABELS)) < 0.2, strict=True)
                    ),
                }
            )
    return pd.DataFrame(rows)


def _write_sets(root: Path, shared_hotel: bool = False) -> None:
    for k, name in enumerate(("train", "gold_dev", "gold_test")):
        frame = _parts(f"s{k}" if not shared_hotel else "x", 60, k)
        (root / name).mkdir(parents=True)
        frame.to_parquet(root / name / "labels.parquet")


def test_load_all_sorts_and_checks_disjointness(tmp_path: Path) -> None:
    _write_sets(tmp_path)
    sets = load_all(tmp_path)
    dev = sets["gold_dev"]
    assert dev.y.shape == (120, len(LABELS))
    assert dev.frame["review_id"].is_monotonic_increasing
    assert dev.weights.max() == 2.0


def test_load_all_rejects_overlap(tmp_path: Path) -> None:
    _write_sets(tmp_path, shared_hotel=True)
    with pytest.raises(ValueError, match="share"):
        load_all(tmp_path)


def test_load_label_set_rejects_duplicates_and_empty_text(
    tmp_path: Path,
) -> None:
    frame = _parts("a", 5, 0)
    (tmp_path / "train").mkdir()
    pd.concat([frame, frame.head(1)]).to_parquet(
        tmp_path / "train" / "labels.parquet"
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_label_set(tmp_path, "train")
    frame.loc[0, "text"] = " "
    frame.to_parquet(tmp_path / "train" / "labels.parquet")
    with pytest.raises(ValueError, match="without text"):
        load_label_set(tmp_path, "train")
    with pytest.raises(FileNotFoundError):
        load_label_set(tmp_path, "gold_dev")


@pytest.fixture
def splits() -> tuple[LabeledParts, LabeledParts]:
    return (
        LabeledParts("gold_dev", _parts("d", 80, 1)),
        LabeledParts("gold_test", _parts("t", 80, 2)),
    )


def test_assess_parts_and_round_trip(
    tmp_path: Path, splits: tuple[LabeledParts, LabeledParts]
) -> None:
    dev, test = splits
    rng = np.random.default_rng(0)
    dev_raw = np.clip(dev.y * 0.7 + rng.random(dev.y.shape) * 0.3, 0, 1)
    test_raw = np.clip(test.y * 0.7 + rng.random(test.y.shape) * 0.3, 0, 1)
    outcome = assess_parts("m", "toy", dev, test, dev_raw, test_raw, FAST)
    assert outcome.test.review_macro_f1.point > 0.9
    assert set(outcome.thresholds) == set(LABELS)
    out = save_outcome(
        outcome, tmp_path, part_predictions(dev, test, dev_raw, test_raw)
    )
    summary = json.loads(out.read_text())["summary"]
    assert summary["test_review_macro_f1"] == pytest.approx(
        outcome.test.review_macro_f1.point
    )
    ids, labels, probs = load_review_level_predictions(tmp_path, "gold_test")
    assert len(ids) == 80
    assert labels.shape == probs.shape == (80, len(LABELS))
    assert ((probs >= 0) & (probs <= 1)).all()


def test_assess_reviews_with_fixed_and_tuned_thresholds(
    tmp_path: Path, splits: tuple[LabeledParts, LabeledParts]
) -> None:
    dev, test = splits
    scores = {}
    for name, split in (("gold_dev", dev), ("gold_test", test)):
        ids = sorted(split.frame["review_id"].unique())
        y = split.frame.groupby("review_id")[list(LABELS)].any().loc[ids]
        scores[name] = (ids, y.to_numpy(dtype=float) * 0.8 + 0.1)
    fixed = dict.fromkeys(LABELS, 0.95)
    never = assess_reviews(
        "v1",
        "v1",
        dev,
        test,
        scores["gold_dev"],
        scores["gold_test"],
        FAST,
        thresholds=fixed,
    )
    tuned = assess_reviews(
        "v1", "v1", dev, test, scores["gold_dev"], scores["gold_test"], FAST
    )
    assert never.test.review_macro_f1.point == 0.0
    assert tuned.test.review_macro_f1.point == pytest.approx(1.0)
    assert tuned.level == "review"
    save_outcome(tuned, tmp_path, review_predictions(scores))
    ids, labels, _ = load_review_level_predictions(tmp_path, "gold_test")
    assert len(ids) == 80
    assert labels.any()


def test_embedding_logreg_handles_constant_labels() -> None:
    rng = np.random.default_rng(0)
    features = rng.normal(size=(40, 5))
    y = np.zeros((40, 2), dtype=bool)
    y[:, 0] = features[:, 0] > 0
    model = EmbeddingLogReg(["a", "b"]).fit(features, y)
    probs = model.predict_proba(features)
    assert probs.shape == (40, 2)
    assert (probs[:, 1] == 0).all()
    assert ((probs[:, 0] > 0.5) == y[:, 0]).mean() > 0.9
    with pytest.raises(RuntimeError):
        EmbeddingLogReg(["a"]).predict_proba(features)


def test_v1_review_texts_join_liked_then_disliked() -> None:
    gold = pd.DataFrame(
        {
            "review_id": ["r_b", "r_a", "r_a"],
            "part": ["liked", "disliked", "liked"],
            "text": ["Only", "Small Room", "Great Staff"],
        }
    )
    ids, texts = v1_review_texts(gold)
    assert ids == ["r_a", "r_b"]
    assert texts == ["great staff small room", "only"]


def test_v1_thresholds_are_renamed(tmp_path: Path) -> None:
    raw = {name: 0.5 for name in V1_TO_V2_LABEL}
    (tmp_path / "thresholds.json").write_text(json.dumps(raw))
    assert set(v1_thresholds(tmp_path)) == set(LABELS)


def _toy_raw(split: LabeledParts, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.clip(split.y * 0.7 + rng.random(split.y.shape) * 0.3, 0, 1)


def test_reassess_reproduces_a_saved_rung(
    tmp_path: Path, splits: tuple[LabeledParts, LabeledParts]
) -> None:
    dev, test = splits
    raw = (_toy_raw(dev, 0), _toy_raw(test, 1))
    outcome = assess_parts("m", "toy", dev, test, *raw, FAST)
    outcome.params, outcome.timings = {"seed": 1}, {"train": 2.0}
    save_outcome(outcome, tmp_path, part_predictions(dev, test, *raw))
    again = reassess(tmp_path, dev, test, FAST)
    assert again.summary() == outcome.summary()
    assert again.thresholds == outcome.thresholds
    assert again.params == {"seed": 1}


def test_reassess_keeps_fixed_thresholds(
    tmp_path: Path, splits: tuple[LabeledParts, LabeledParts]
) -> None:
    dev, test = splits
    raw = (_toy_raw(dev, 0), _toy_raw(test, 1))
    fixed = dict.fromkeys(LABELS, 0.95)
    outcome = rescore_with_thresholds(
        assess_parts("v1", "v1", dev, test, *raw, FAST),
        fixed,
        (dev, test),
        raw,
        FAST,
    )
    outcome.params = {"thresholds": FIXED_THRESHOLDS}
    save_outcome(outcome, tmp_path, part_predictions(dev, test, *raw))
    again = reassess(tmp_path, dev, test, FAST)
    assert again.thresholds == fixed
    assert again.summary() == outcome.summary()


def test_clear_package_outputs_after_retraining(tmp_path: Path) -> None:
    for name in PACKAGE_OUTPUTS:
        (tmp_path / name).write_text("{}")
    (tmp_path / "NOTES.md").write_text("kept")
    clear_package_outputs(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["NOTES.md"]
