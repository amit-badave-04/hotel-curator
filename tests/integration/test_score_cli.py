"""Train, package and serve-score a tiny model end to end through the CLI.

Covers the glue the unit tests cannot: ``model package`` on a trained run,
``score parts`` with the ONNX package, ``score hotels`` writing the demo data
and manifest, and ``score validate`` writing TIERS.md.
"""

import gzip
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from hotel_curator.cli.main import app as cli_app
from hotel_curator.config import get_settings
from tests.helpers import (
    invoke_cli,
    synthetic_inputs,
    tiny_bert,
    toy_label_set,
)

TEST_HOTELS = {"h_00000000a1", "h_00000000c3"}
RUNG = "tiny-ft-lr0.005-ep1"
"""The run's default name: its learning rate and epochs are not the
defaults, so the name carries them (ADR 005)."""


def _write_processed(home: Path) -> int:
    """The synthetic corpus as canonical tables; returns its part count."""
    inputs = synthetic_inputs()
    processed = home / "data" / "processed"
    processed.mkdir(parents=True)
    hotels = inputs.hotels.assign(
        split=lambda h: h["hotel_id"].map(
            lambda i: "test" if i in TEST_HOTELS else "train"
        )
    )
    hotels.to_parquet(processed / "hotels.parquet")
    # The site's own score: only the external-agreement check reads it.
    reviews = inputs.reviews.assign(
        reviewer_score=lambda r: r["hotel_id"].map(
            lambda i: 4.0 if i == "h_00000000b2" else 9.0
        )
    )
    reviews.to_parquet(processed / "reviews.parquet")
    texts = inputs.reviews[["liked_text", "disliked_text"]]
    return int(texts.notna().sum().sum())


def _write_labels(home: Path) -> None:
    for name, seed in (("train", 0), ("gold_dev", 1), ("gold_test", 2)):
        directory = home / "data" / "labels" / name
        directory.mkdir(parents=True)
        frame = toy_label_set(name[:2] + str(seed), 60, seed)
        frame.to_parquet(directory / "labels.parquet")


def _check_package_outputs_and_publish(home: Path, package: Path) -> None:
    """Fingerprints are recorded, reassess drops them, publish checks them."""
    from hotel_curator.ml.experiment import PACKAGE_OUTPUTS, rung_rules_sha256

    evals = home / "artifacts" / "evals" / RUNG
    report = json.loads((evals / "package_report.json").read_text())
    assert report["rules_sha256"] == rung_rules_sha256(evals)
    assert all((evals / name).is_file() for name in PACKAGE_OUTPUTS)

    invoke_cli("model", "reassess", RUNG)
    assert not any((evals / name).exists() for name in PACKAGE_OUTPUTS)
    invoke_cli("model", "package", RUNG)
    report = json.loads((evals / "package_report.json").read_text())

    promotion = {
        "winner": RUNG,
        "rows": [{"name": RUNG, "package": report}],
    }
    (home / "artifacts" / "evals" / "promotion.json").write_text(
        json.dumps(promotion)
    )
    invoke_cli("model", "publish")
    published = home / "artifacts" / "models" / RUNG
    assert (published / "model.onnx").read_bytes() == (
        package / "model.onnx"
    ).read_bytes()
    metadata = package / "metadata.json"
    original = metadata.read_text()
    metadata.write_text(original.replace(RUNG, "tampered"))
    refused = CliRunner().invoke(cli_app, ["model", "publish"])
    assert refused.exit_code != 0
    assert "metadata.json differ" in refused.output
    metadata.write_text(original)


def test_package_score_and_validate_a_tiny_model(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("onnxruntime")
    n_parts = _write_processed(home)
    _write_labels(home)
    base = tiny_bert(home / "tiny")
    invoke_cli(
        "model", "run-encoder", "tiny", "--base-dir", str(base),
        "--learning-rate", "0.005", "--epochs", "1",
    )  # fmt: skip
    assert f"{RUNG}:" in invoke_cli("model", "package", RUNG)
    package = home / "var" / "packages" / RUNG
    assert (package / "model.onnx").is_file()
    _check_package_outputs_and_publish(home, package)

    # Served by path from outside the project, under another directory
    # name: results must still name the rung and find its evaluation.
    served = tmp_path_factory.mktemp("elsewhere") / "served-model"
    shutil.copytree(package, served)
    monkeypatch.setenv("CURATOR_MODEL_PACKAGE", str(served))
    get_settings.cache_clear()
    invoke_cli("score", "parts", "--threads", "1")
    scores = pd.read_parquet(
        home / "data" / "processed" / "part_scores.parquet"
    )
    assert len(scores) == n_parts
    assert (
        scores.drop(columns=["review_id", "part"]).stack().between(0, 1).all()
    )

    invoke_cli("score", "hotels")
    demo = home / "data" / "demo" / "hotel_results.json.gz"
    hotels = json.loads(gzip.decompress(demo.read_bytes()))
    assert {h["hotel_id"] for h in hotels} == set(
        synthetic_inputs().hotels["hotel_id"]
    )
    manifest = json.loads(
        (home / "data" / "manifests" / "hotel_results.json").read_text()
    )
    assert manifest["inputs"]["model_package"] == RUNG
    assert manifest["stats"]["hotels"] == len(hotels)

    invoke_cli("score", "validate")
    tiers = (home / "docs" / "model" / "TIERS.md").read_text()
    assert tiers.startswith("# Tiers: outcome and validation")
