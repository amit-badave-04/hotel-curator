"""Behaviour gates for the promoted model (``pytest -m eval``, plan §3.6).

They read committed artifacts: the promotion decision and the served ONNX
package. They are skipped, not passed, when those artifacts are absent.
The thresholds were fixed before the promoted model was run on the
fixture: each label must be found in at least one of its two clear-cut
examples, at least 80% of all expected labels must be found, and at least
70% of the labels the model gives must be expected ones (so a model that
predicts every label fails).
"""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from hotel_curator.taxonomy import LABELS

pytestmark = pytest.mark.eval

ROOT = Path(__file__).resolve().parents[2]
PROMOTION = ROOT / "artifacts" / "evals" / "promotion.json"
FIXTURE = Path(__file__).with_name("clear_cut_parts.jsonl")
MIN_LABEL_RECALL = 0.5
MIN_OVERALL_RECALL = 0.8
MIN_PRECISION = 0.7
LFS_POINTER = b"version https://git-lfs"


def _promotion() -> dict[str, Any]:
    if not PROMOTION.is_file():
        pytest.skip("no promotion decision committed yet")
    decision: dict[str, Any] = json.loads(PROMOTION.read_text())
    if not decision["winner"]:
        pytest.skip("the promotion rule promoted no model")
    return decision


def test_promoted_model_beats_v1_by_rule_1() -> None:
    decision = _promotion()
    row = next(r for r in decision["rows"] if r["name"] == decision["winner"])
    assert row["vs_v1"]["point"] > 0
    assert row["vs_v1"]["low"] > 0
    assert row["rules"] == {
        "1_beats_v1": True,
        "3_calibrated": True,
        "4_fast_enough": True,
        "package_holds": True,
    }


def _published() -> Path:
    package_dir = ROOT / "artifacts" / "models" / _promotion()["winner"]
    model = package_dir / "model.onnx"
    if not model.is_file() or model.read_bytes()[:40].startswith(LFS_POINTER):
        pytest.skip("promoted package not published (or Git LFS not pulled)")
    return package_dir


def test_published_files_are_the_ones_promotion_judged() -> None:
    from hotel_curator.data.download import sha256_file
    from hotel_curator.inference.package import PACKAGE_FILES

    decision = _promotion()
    package_dir = _published()
    row = next(r for r in decision["rows"] if r["name"] == decision["winner"])
    judged = {f["file"]: f["sha256"] for f in row["package"]["files_sha256"]}
    for name in PACKAGE_FILES:
        assert sha256_file(package_dir / name) == judged[name], name


def test_promoted_package_finds_clear_cut_opinions() -> None:
    from hotel_curator.inference.package import ModelPackage

    package_dir = _published()
    rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    labels = (
        ModelPackage(package_dir)
        .predict([r["text"] for r in rows], [r["part"] for r in rows])
        .labels
    )
    expected = np.array(
        [[label in r["labels"] for label in LABELS] for r in rows]
    )
    found = labels & expected
    per_label = found.sum(axis=0) / expected.sum(axis=0)
    missed = [
        lbl
        for lbl, rec in zip(LABELS, per_label, strict=True)
        if rec < MIN_LABEL_RECALL
    ]
    assert not missed, f"labels below {MIN_LABEL_RECALL} recall: {missed}"
    assert found.sum() / expected.sum() >= MIN_OVERALL_RECALL
    assert found.sum() / max(int(labels.sum()), 1) >= MIN_PRECISION
