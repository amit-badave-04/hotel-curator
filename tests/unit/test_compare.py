"""Tests for the promotion rule applied to saved evaluation directories."""

import json
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hotel_curator.ml.compare import (
    NO_HUMAN_CHECK,
    NO_HUMAN_CHECK_NOTE,
    Decision,
    RungRow,
    _pick_winner,
    _size,
    comparison_markdown,
    decide,
    load_rows,
    mean_part_ece,
)
from hotel_curator.ml.data import LabeledParts
from hotel_curator.ml.evaluate import EvalConfig
from hotel_curator.ml.experiment import (
    assess_parts,
    part_predictions,
    rung_rules_sha256,
    save_outcome,
)
from hotel_curator.taxonomy import LABELS

FAST = EvalConfig(n_boot=50, n_boot_slices=20, seed=0)


def _split(prefix: str, seed: int) -> LabeledParts:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(150):
        for part in ("liked", "disliked"):
            rows.append(
                {
                    "review_id": f"r_{prefix}{i:04d}",
                    "hotel_id": f"h_{prefix}{i % 30:03d}",
                    "part": part,
                    "text": f"t{i}",
                    "stratum": "uniform",
                    "sampling_weight": 1.0,
                    **dict(
                        zip(LABELS, rng.random(len(LABELS)) < 0.25, strict=True)
                    ),
                }
            )
    return LabeledParts(prefix, pd.DataFrame(rows))


def _rung(
    root: Path,
    name: str,
    family: str,
    noise: float,
    dev: LabeledParts,
    test: LabeledParts,
    hours: float | None,
) -> None:
    rng = np.random.default_rng(len(name))

    def scores(split: LabeledParts) -> np.ndarray:
        flip = rng.random(split.y.shape) < noise
        return np.where(split.y ^ flip, 0.8, 0.2)

    dev_raw, test_raw = scores(dev), scores(test)
    outcome = assess_parts(name, family, dev, test, dev_raw, test_raw, FAST)
    save_outcome(
        outcome, root / name, part_predictions(dev, test, dev_raw, test_raw)
    )
    if hours is not None:
        (root / name / "throughput.json").write_text(
            json.dumps({"corpus_hours": hours})
        )
        _package(root / name, root / name / "scores_gold_test.parquet")


def _package(rung_dir: Path, scores: Path, ece: float | None = None) -> None:
    """Package outputs whose gold-test scores are read from ``scores``.

    The report copies the rung's own test report, with every review ECE set
    to ``ece`` when given.
    """
    test = json.loads((rung_dir / "report.json").read_text())["test"]
    if ece is not None:
        test["review_ece"] = dict.fromkeys(test["review_ece"], ece)
    dev_f1 = {"fp32": 0.9, "int8": 0.9}
    report = {
        "weights": "int8",
        "dev_review_macro_f1": dev_f1,
        "max_quantization_loss": 0.005,
        "gold_test": test,
        "rules_sha256": rung_rules_sha256(rung_dir),
        "files_sha256": [{"file": "model.onnx", "sha256": "0" * 64}],
    }
    (rung_dir / "package_report.json").write_text(json.dumps(report))
    shutil.copy(scores, rung_dir / "package_scores_gold_test.parquet")


@pytest.fixture
def evals(tmp_path: Path) -> tuple[Path, LabeledParts]:
    dev, test = _split("d", 0), _split("t", 1)
    _rung(tmp_path, "v1-a", "v1", 0.30, dev, test, None)
    _rung(tmp_path, "v1-b", "v1", 0.20, dev, test, None)
    _rung(tmp_path, "good", "encoder", 0.02, dev, test, 1.0)
    _rung(tmp_path, "slow", "encoder", 0.01, dev, test, 9.0)
    _rung(tmp_path, "bad", "tfidf", 0.40, dev, test, 0.1)
    return tmp_path, test


def test_decide_picks_v1_on_dev_and_applies_rules(
    evals: tuple[Path, LabeledParts],
) -> None:
    root, test = evals
    decision = decide(root, test.frame, n_boot=200, seed=0)
    assert decision.v1_config == "v1-b"
    rows = {r.name: r for r in decision.rows}
    assert rows["good"].rules["1_beats_v1"] is True
    assert rows["good"].rules["4_fast_enough"] is True
    assert rows["slow"].rules["4_fast_enough"] is False
    assert rows["bad"].rules["1_beats_v1"] is False
    assert decision.eligible == ["good", "slow"]  # the rule-2 pool
    # "slow" has the best gold-dev score but fails rule 4: the plan's rules
    # are conjunctive, so nothing ships rather than the runner-up.
    assert decision.winner is None
    assert any(
        "slow, which does not pass 4_fast_enough" in n for n in decision.notes
    )
    text = comparison_markdown(decision, root)
    assert "no rung is promoted" in text


def test_decide_promotes_the_rule_2_candidate_when_it_passes(
    evals: tuple[Path, LabeledParts],
) -> None:
    root, test = evals
    shutil.rmtree(root / "slow")
    decision = decide(root, test.frame, n_boot=200, seed=0)
    assert decision.winner == "good"
    assert decision.rows[0].rules  # every non-v1 rung is judged
    good = next(r for r in decision.rows if r.name == "good")
    assert good.rules["package_holds"] is True
    text = comparison_markdown(decision, root)
    assert "promote `good`" in text
    assert "| **good** |" in text
    assert "## Run notes" not in text
    assert "on gold-test (150 reviews from 30 hotels" in text
    assert "## Packaged models" in text
    assert "| good | int8 | 0.900 / 0.900 |" in text


def test_part_ece_is_reported_next_to_review_ece(
    evals: tuple[Path, LabeledParts],
) -> None:
    root, test = evals
    shutil.rmtree(root / "slow")
    decision = decide(root, test.frame, n_boot=50, seed=0)
    text = comparison_markdown(decision, root)
    report = json.loads((root / "good" / "report.json").read_text())
    review_ece = report["summary"]["test_mean_review_ece"]
    part_ece = mean_part_ece(report["test"])
    assert "| Review ECE | Part ECE |" in text
    (line,) = [x for x in text.splitlines() if x.startswith("| **good** |")]
    assert f"| {review_ece:.3f} | {part_ece:.3f} |" in line
    assert "| Label | Review ECE | Part ECE |" in text  # calibration table
    assert mean_part_ece({"ece": {"a": 0.1, "b": 0.3}}) == pytest.approx(0.2)
    assert math.isnan(mean_part_ece({"ece": {}}))  # a whole-review rung


def test_labels_without_a_human_check_are_flagged(
    evals: tuple[Path, LabeledParts],
) -> None:
    root, test = evals
    shutil.rmtree(root / "slow")
    assert NO_HUMAN_CHECK == (
        "noise_positive",
        "noise_negative",
        "wifi_positive",
        "wifi_negative",
    )
    text = comparison_markdown(
        decide(root, test.frame, n_boot=50, seed=0), root
    )
    for label in NO_HUMAN_CHECK:
        # Once in the per-label table, once in the calibration table.
        assert text.count(f"| {label} † |") == 2
    assert "| cleanliness_positive |" in text
    assert text.count(NO_HUMAN_CHECK_NOTE) == 2


def test_a_package_that_loses_to_v1_is_not_promoted(
    evals: tuple[Path, LabeledParts],
) -> None:
    root, test = evals
    shutil.rmtree(root / "slow")
    # The package of "good" scores like the weakest rung.
    _package(root / "good", root / "bad" / "scores_gold_test.parquet")
    decision = decide(root, test.frame, n_boot=200, seed=0)
    good = next(r for r in decision.rows if r.name == "good")
    assert good.rules["1_beats_v1"] is True
    assert good.rules["package_holds"] is False
    assert decision.winner is None
    assert any("package_holds (no)" in n for n in decision.notes)


def test_a_badly_calibrated_package_is_not_promoted(
    evals: tuple[Path, LabeledParts],
) -> None:
    root, test = evals
    shutil.rmtree(root / "slow")
    _package(root / "good", root / "good" / "scores_gold_test.parquet", 0.2)
    decision = decide(root, test.frame, n_boot=50, seed=0)
    good = next(r for r in decision.rows if r.name == "good")
    assert good.rules["3_calibrated"] is True  # the checkpoint is fine
    assert good.rules["package_holds"] is False
    assert decision.winner is None


def test_a_package_with_other_thresholds_is_not_judged(
    evals: tuple[Path, LabeledParts],
) -> None:
    root, test = evals
    shutil.rmtree(root / "slow")
    thresholds = root / "good" / "thresholds.json"
    cuts = json.loads(thresholds.read_text())
    thresholds.write_text(json.dumps(dict.fromkeys(cuts, 0.5)))  # re-tuned
    decision = decide(root, test.frame, n_boot=50, seed=0)
    good = next(r for r in decision.rows if r.name == "good")
    assert good.package is not None and not good.package["current"]
    assert good.package_vs_v1 is None
    assert good.rules["package_holds"] is None
    assert decision.winner is None
    assert any("model package good" in n for n in decision.notes)
    assert "| good | int8 (stale) |" in comparison_markdown(decision, root)


def test_a_package_without_scores_is_not_measured(
    evals: tuple[Path, LabeledParts],
) -> None:
    root, test = evals
    shutil.rmtree(root / "slow")
    (root / "good" / "package_scores_gold_test.parquet").unlink()
    decision = decide(root, test.frame, n_boot=50, seed=0)
    assert decision.winner is None
    assert any("package_holds (not measured)" in n for n in decision.notes)


def test_rule_4_is_not_measured_before_packaging(
    evals: tuple[Path, LabeledParts],
) -> None:
    root, test = evals
    shutil.rmtree(root / "slow")
    (root / "good" / "throughput.json").unlink()
    decision = decide(root, test.frame, n_boot=50, seed=0)
    rows = {r.name: r for r in decision.rows}
    assert rows["good"].rules["4_fast_enough"] is None
    assert decision.winner is None
    assert any("4_fast_enough (not measured)" in n for n in decision.notes)


def test_decide_without_v1_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="v1"):
        decide(tmp_path, pd.DataFrame(), n_boot=10, seed=0)


def test_tie_note_shows_when_speed_would_pick_another_rung() -> None:
    from hotel_curator.ml.compare import _speed_note

    def row(name: str, size: float, hours: float) -> RungRow:
        return RungRow(name, "encoder", size, {}, corpus_hours=hours)

    small_slow, big_fast = row("small", 68.4, 2.0), row("big", 140, 1.0)
    (note,) = _speed_note([small_slow, big_fast], small_slow)
    assert "big would win the tie" in note
    assert "(68M vs 140M)" in note
    assert _speed_note([small_slow, big_fast], big_fast) == []


def test_size_uses_the_recorded_count_for_a_base_the_table_lacks() -> None:
    encoder = {"family": "encoder"}
    counted = {"base_model": "ettin-small", "n_parameters": 68_000_000}
    assert _size(encoder, counted) == pytest.approx(68.0)
    assert _size(encoder, {"base_model": "roberta-base"}) == 125  # older run
    assert _size({"family": "tfidf"}, {}) == 1
    # A new base without a recorded count has no size, not a made-up one.
    assert _size(encoder, {"base_model": "ettin-small"}) is None


def test_runs_of_one_listed_base_have_one_size() -> None:
    # An old roberta-base run (no count) and a new one (its exact count) are
    # the same model: the tie falls to gold-dev F1, not to the rounding of
    # the table's 125M against 124,657,936 parameters.
    encoder = {"family": "encoder"}
    old = _size(encoder, {"base_model": "roberta-base"})
    new = _size(
        encoder, {"base_model": "roberta-base", "n_parameters": 124_657_936}
    )
    assert old == new == 125
    decision = _decision(
        [
            _row("roberta-base-ft-train2", old, 0.936),
            _row("roberta-base-ft-seed7", new, 0.930),
        ]
    )
    assert decision.winner == "roberta-base-ft-train2"


def test_load_rows_reads_the_recorded_parameter_count(
    evals: tuple[Path, LabeledParts],
) -> None:
    root, _ = evals
    path = root / "good" / "report.json"
    report = json.loads(path.read_text())
    report["params"] = {"base_model": "ettin", "n_parameters": 68_000_000}
    path.write_text(json.dumps(report))
    sizes = {r.name: r.size_millions for r in load_rows(root)}
    assert sizes["good"] == pytest.approx(68.0)
    assert sizes["slow"] is None  # family "encoder", nothing recorded
    assert sizes["v1-a"] == 125


def test_run_notes_are_shown(evals: tuple[Path, LabeledParts]) -> None:
    root, test = evals
    (root / "slow" / "NOTES.md").write_text("Trained with random batches.\n")
    decision = decide(root, test.frame, n_boot=50, seed=0)
    text = comparison_markdown(decision, root)
    assert "## Run notes\n\n- `slow`: Trained with random batches." in text


def _row(
    name: str, size: float | None, dev_f1: float, **rules: bool | None
) -> RungRow:
    passes = {
        "1_beats_v1": True,
        "3_calibrated": True,
        "4_fast_enough": True,
        "package_holds": True,
    }
    return RungRow(
        name,
        "encoder",
        size,
        {"dev_review_macro_f1": dev_f1},
        rules=passes | rules,
    )


def _decision(rows: list[RungRow]) -> Decision:
    decision = Decision("v1", None, [r.name for r in rows], rows)
    _pick_winner(rows, decision)
    return decision


def test_rule_2_tie_goes_to_the_smaller_model() -> None:
    decision = _decision([_row("big", 125, 0.920), _row("small", 14, 0.912)])
    assert decision.winner == "small"
    assert any("tie within 0.01" in n for n in decision.notes)
    clear = _decision([_row("big", 125, 0.930), _row("small", 14, 0.912)])
    assert clear.winner == "big"  # 0.018 apart: no tie


def test_rule_2_tie_uses_recorded_sizes_and_refuses_unknown_ones() -> None:
    # A new base (e.g. a 68M Ettin-small) wins a tie on its real size.
    decision = _decision([_row("roberta", 125, 0.92), _row("new", 68.0, 0.915)])
    assert decision.winner == "new"
    with pytest.raises(ValueError, match="unknown parameter count"):
        _decision([_row("roberta", 125, 0.92), _row("new", None, 0.915)])
    # Without a tie the size is not needed.
    assert _decision([_row("new", None, 0.92)]).winner == "new"


def test_rule_3_failure_of_the_candidate_blocks_promotion() -> None:
    rows = [
        _row("best", 125, 0.95, **{"3_calibrated": False}),
        _row("calibrated", 14, 0.90),
    ]
    decision = _decision(rows)
    assert decision.winner is None
    assert any(
        "best, which does not pass 3_calibrated (no)" in n
        for n in decision.notes
    )
