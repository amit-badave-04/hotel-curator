"""CLI integration tests on synthetic data in a temporary home directory."""

import gzip
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from hotel_curator.cli.main import app
from hotel_curator.ml.data import LabeledParts
from hotel_curator.ml.evaluate import EvalConfig
from hotel_curator.ml.experiment import (
    assess_parts,
    part_predictions,
    rung_rules_sha256,
    save_outcome,
)
from hotel_curator.taxonomy import LABELS
from tests.helpers import invoke_cli, synthetic_results, toy_label_set


def test_db_upgrade_and_seed_are_idempotent(home: Path) -> None:
    demo = home / "data" / "demo"
    demo.mkdir(parents=True)
    payload = [r.model_dump(mode="json") for r in synthetic_results()]
    with gzip.open(demo / "hotel_results.json.gz", "wt") as handle:
        json.dump(payload, handle)
    invoke_cli("db", "upgrade")
    assert "loaded 5 hotels" in invoke_cli("db", "seed")
    assert "up to date" in invoke_cli("db", "seed")


def _split(prefix: str, seed: int) -> LabeledParts:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(120):
        for part in ("liked", "disliked"):
            rows.append(
                {
                    "review_id": f"r_{prefix}{i:04d}",
                    "hotel_id": f"h_{prefix}{i % 24:03d}",
                    "part": part,
                    "text": f"text {i}",
                    "stratum": "uniform",
                    "sampling_weight": 1.0,
                    **dict(
                        zip(LABELS, rng.random(len(LABELS)) < 0.2, strict=True)
                    ),
                }
            )
    return LabeledParts(prefix, pd.DataFrame(rows))


def _package_like_the_rung(rung_dir: Path) -> None:
    """Package outputs whose scores equal the rung's (an exact export)."""
    report = json.loads((rung_dir / "report.json").read_text())
    package_report = {
        "weights": "fp32",
        "dev_review_macro_f1": {"fp32": 0.9, "int8": 0.8},
        "max_quantization_loss": 0.005,
        "gold_test": report["test"],
        "rules_sha256": rung_rules_sha256(rung_dir),
        "files_sha256": [],
    }
    (rung_dir / "package_report.json").write_text(json.dumps(package_report))
    shutil.copy(
        rung_dir / "scores_gold_test.parquet",
        rung_dir / "package_scores_gold_test.parquet",
    )


def test_model_compare_writes_decision_and_doc(home: Path) -> None:
    dev, test = _split("d", 0), _split("t", 1)
    gold_dir = home / "data" / "labels" / "gold_test"
    gold_dir.mkdir(parents=True)
    test.frame.to_parquet(gold_dir / "labels.parquet")
    evals = home / "artifacts" / "evals"
    config = EvalConfig(n_boot=50, n_boot_slices=20, seed=0)
    for name, family, noise, hours in (
        ("v1-x", "v1", 0.3, None),
        ("enc-ft", "encoder", 0.02, 0.5),
    ):
        rng = np.random.default_rng(len(name))

        dev_raw, test_raw = (
            np.where(split.y ^ (rng.random(split.y.shape) < noise), 0.8, 0.2)
            for split in (dev, test)
        )
        outcome = assess_parts(
            name, family, dev, test, dev_raw, test_raw, config
        )
        save_outcome(
            outcome,
            evals / name,
            part_predictions(dev, test, dev_raw, test_raw),
        )
        if hours is not None:
            (evals / name / "throughput.json").write_text(
                json.dumps({"corpus_hours": hours})
            )
            _package_like_the_rung(evals / name)
    output = invoke_cli("model", "compare")
    assert "winner: enc-ft" in output
    decision = json.loads((evals / "promotion.json").read_text())
    assert decision["v1_config"] == "v1-x"
    winner = next(r for r in decision["rows"] if r["name"] == "enc-ft")
    assert winner["rules"]["package_holds"] is True
    doc = (home / "docs" / "model" / "COMPARISON.md").read_text()
    assert "promote `enc-ft`" in doc


def test_label_audit_score_requires_labels(home: Path) -> None:
    labels = home / "data" / "labels"
    (labels / "gold_test").mkdir(parents=True)
    _split("t", 1).frame.to_parquet(labels / "gold_test" / "labels.parquet")
    sheet = pd.DataFrame(
        {
            "review_id": ["r_t0000", "r_t0001"],
            "liked_text": ["a", "b"],
            "disliked_text": ["", ""],
            "liked_codes": ["", ""],
            "disliked_codes": ["", ""],
        }
    )
    sheet.to_csv(labels / "human_audit_template.csv", index=False)
    assert CliRunner().invoke(app, ["label", "audit-score"]).exit_code == 1
    sheet.loc[0, "liked_codes"] = "C+ S-"
    sheet.loc[1, "liked_codes"] = "none"
    sheet.to_csv(labels / "human_audit_template.csv", index=False)
    assert "2 labelled parts" in invoke_cli("label", "audit-score")
    result = json.loads((labels / "human_audit_result.json").read_text())
    assert result["parts"] == 2
    assert len(result["labels"]) == len(LABELS)


def test_mlflow_run_is_logged(tmp_path: Path) -> None:
    pytest.importorskip("mlflow")
    from hotel_curator.ml.tracking import log_run

    artifact = tmp_path / "report.json"
    artifact.write_text("{}")
    run_id = log_run(
        tmp_path / "work",
        "test-run",
        {"seed": 1},
        {"f1": 0.5, "nan": float("nan")},
        [artifact],
    )
    assert len(run_id) == 32
    assert (tmp_path / "work" / "mlflow.db").is_file()


def test_run_encoder_end_to_end_on_a_tiny_model(home: Path) -> None:
    pytest.importorskip("torch")
    from hotel_curator.ml.hf import load_classifier
    from tests.helpers import tiny_bert

    base = tiny_bert(home / "tiny")
    for name, seed in (("train", 0), ("gold_dev", 1), ("gold_test", 2)):
        directory = home / "data" / "labels" / name
        directory.mkdir(parents=True)
        toy_label_set(name[:2] + str(seed), 60, seed).to_parquet(
            directory / "labels.parquet"
        )
    output = invoke_cli(
        "model",
        "run-encoder",
        "tiny",
        "--base-dir",
        str(base),
        "--learning-rate",
        "0.005",
        "--epochs",
        "2",
        "--train-fraction",
        "0.5",
    )
    # Half the data and other hyperparameters: a rung of its own, so the
    # full run's results, checkpoint and package are never overwritten.
    rung = "tiny-ft-frac0.5-lr0.005-ep2"
    assert f"{rung}: dev macro-F1" in output
    evals = home / "artifacts" / "evals"
    assert not (evals / "tiny-ft").exists()
    report = json.loads((evals / rung / "report.json").read_text())
    assert report["params"]["n_train_reviews"] == 30
    assert report["params"]["epochs"] == 2
    assert report["params"]["train_sets"] == "train"
    checkpoint = load_classifier(home / "var" / "runs" / rung)
    assert report["params"]["n_parameters"] == sum(
        p.numel() for p in checkpoint.parameters()
    )


def test_package_of_an_unevaluated_rung_lists_the_evaluated_ones(
    home: Path,
) -> None:
    pytest.importorskip("torch")
    # A run with non-default settings is evaluated under its tagged name, so
    # `package tiny-ft` must say where its results are, not crash.
    probe = home / "artifacts" / "evals" / "tiny-ft-lr0.005-ep1"
    probe.mkdir(parents=True)
    (probe / "report.json").write_text("{}")
    result = CliRunner().invoke(app, ["model", "package", "tiny-ft"])
    assert result.exit_code == 2
    assert result.exc_info is None or result.exc_info[0] is SystemExit
    assert "no evaluation of rung 'tiny-ft'" in result.output
    assert "tiny-ft-lr0.005-ep1" in result.output


def test_runs_on_more_data_get_their_own_rung_name() -> None:
    from hotel_curator.cli.model import rung_name

    assert rung_name("tfidf-logreg", extra=False) == "tfidf-logreg"
    assert rung_name("roberta-base-ft", extra=True) == "roberta-base-ft-train2"


def test_encoder_rung_name_tags_every_non_default_setting() -> None:
    from hotel_curator.cli.model import encoder_rung_name

    defaults: dict[str, float | int] = {
        "train_fraction": 1.0,
        "seed": 20261024,
        "learning_rate": 3e-5,
        "epochs": 4,
    }

    def name(extra: bool = False, **run: float | int) -> str:
        return encoder_rung_name(
            "roberta-base", defaults | run, defaults, extra=extra
        )

    assert name() == "roberta-base-ft"
    assert name(extra=True) == "roberta-base-ft-train2"
    assert name(seed=20261024, learning_rate=3e-5) == "roberta-base-ft"
    assert name(train_fraction=0.5) == "roberta-base-ft-frac0.5"
    assert name(seed=7) == "roberta-base-ft-seed7"
    assert name(learning_rate=2e-5) == "roberta-base-ft-lr2e-05"
    assert name(epochs=3) == "roberta-base-ft-ep3"
    assert (
        name(extra=True, train_fraction=0.5, seed=7, learning_rate=2e-5)
        == "roberta-base-ft-frac0.5-seed7-lr2e-05-train2"
    )
