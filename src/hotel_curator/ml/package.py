"""Turn a promoted checkpoint into a self-contained serving package.

A package directory holds everything the API needs and nothing it does not:

* ``model.onnx``: the encoder and its 16-way head (fp32, or int8 weights
  when dynamic quantisation keeps gold-dev macro-F1 within
  :data:`MAX_QUANTIZATION_LOSS`),
* ``tokenizer.json``: the matching fast tokenizer,
* ``metadata.json``: label order, max length, pad token, thresholds,
  isotonic calibration, provenance (base model, label sets, evaluation).

Packages are produced by ``curator model package`` and read by
:class:`hotel_curator.inference.onnx_predictor.OnnxPredictor`.
"""

import json
import logging
import shutil
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from hotel_curator.ml.encoder import export_onnx

logger = logging.getLogger(__name__)

MAX_QUANTIZATION_LOSS = 0.005
"""Largest gold-dev macro-F1 drop accepted for int8 weights."""


def quantize(fp32: Path, int8: Path) -> Path:
    """Dynamic int8 quantisation of weights (activations stay fp32).

    The dynamo exporter records intermediate shapes traced from its sample
    batch in ``value_info``; they contradict the dynamic batch axis and
    break the quantiser's shape inference, so they are dropped first (the
    runtime re-infers shapes on load).
    """
    import onnx
    from onnxruntime.quantization import QuantType, quantize_dynamic

    model = onnx.load(str(fp32))
    del model.graph.value_info[:]
    cleaned = int8.with_name(int8.stem + ".fp32-clean.onnx")
    onnx.save(model, str(cleaned))
    try:
        quantize_dynamic(str(cleaned), str(int8), weight_type=QuantType.QInt8)
    finally:
        cleaned.unlink(missing_ok=True)
    return int8


def write_package(
    checkpoint: Path,
    out_dir: Path,
    *,
    max_length: int,
    extra_metadata: dict[str, Any],
    use_int8: bool,
) -> Path:
    """Export ``checkpoint`` to ``out_dir`` and merge ``extra_metadata``.

    Returns:
        ``out_dir``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        onnx_path = export_onnx(checkpoint, staging, max_length=max_length)
        out_dir.mkdir(parents=True, exist_ok=True)
        if use_int8:
            quantize(onnx_path, out_dir / "model.onnx")
        else:
            shutil.copyfile(onnx_path, out_dir / "model.onnx")
        shutil.copyfile(staging / "tokenizer.json", out_dir / "tokenizer.json")
        metadata = json.loads((staging / "metadata.json").read_text())
    metadata.update(extra_metadata)
    metadata["weights"] = "int8" if use_int8 else "fp32"
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=1))
    return out_dir


THROUGHPUT_THREADS = 4
"""ONNX Runtime threads for the rule-4 timing (the 4-core build machine)."""
TIMING_REPEATS = 3
WARMUP_PARTS = 256


def measure_throughput(
    predictor: Any,
    texts: Sequence[str],
    parts: Sequence[str],
    corpus_parts: int,
    *,
    threads: int = THROUGHPUT_THREADS,
    repeats: int = TIMING_REPEATS,
) -> dict[str, float]:
    """Time ``predictor`` on a sample and extrapolate to the corpus.

    One warm-up batch first, then the median of ``repeats`` timed passes,
    so a one-off stall does not decide promotion rule 4.

    Returns:
        Sample size, threads, median seconds, parts per second and the
        projected corpus hours.
    """
    predictor.predict_proba(texts[:WARMUP_PARTS], parts[:WARMUP_PARTS])
    runs = []
    for _ in range(repeats):
        started = time.perf_counter()
        predictor.predict_proba(texts, parts)
        runs.append(time.perf_counter() - started)
    seconds = float(np.median(runs))
    rate = len(texts) / seconds
    return {
        "sample_parts": float(len(texts)),
        "threads": float(threads),
        "repeats": float(repeats),
        "seconds": seconds,
        "parts_per_second": rate,
        "corpus_parts": float(corpus_parts),
        "corpus_hours": corpus_parts / rate / 3600.0,
    }


def _dev_review_f1(
    package_dir: Path, dev: Any, thresholds: dict[str, float]
) -> float:
    """Gold-dev review-level macro-F1 of a package (the selection metric)."""
    from hotel_curator.inference.onnx_predictor import OnnxPredictor
    from hotel_curator.ml.evaluate import review_level
    from hotel_curator.ml.metrics import macro_f1
    from hotel_curator.ml.thresholds import apply_thresholds
    from hotel_curator.taxonomy import LABELS

    probs = OnnxPredictor(package_dir).predict_proba(dev.texts, dev.parts)
    predicted = apply_thresholds(probs, thresholds, LABELS)
    ids = dev.frame["review_id"]
    _, y_true = review_level(ids, dev.y)
    _, y_pred = review_level(ids, predicted)
    return macro_f1(y_true, y_pred)


def build_package(
    checkpoint: Path,
    out_dir: Path,
    *,
    metadata: dict[str, Any],
    max_length: int,
    dev: Any,
) -> dict[str, Any]:
    """Export fp32 and int8 packages, keep int8 if it is good enough.

    Args:
        checkpoint: Fine-tuned model directory.
        out_dir: Final package directory (replaced).
        metadata: Thresholds, calibration and provenance to embed.
        max_length: Tokenizer truncation length.
        dev: Gold-dev label set (``LabeledParts``) for the quantisation check.

    Returns:
        ``{"weights": "int8" | "fp32", "dev_review_macro_f1": {"fp32": ...,
        "int8": ...}, "max_quantization_loss": ...}``.
    """
    thresholds = metadata["thresholds"]
    with tempfile.TemporaryDirectory() as tmp:
        f1 = {}
        for weights in ("fp32", "int8"):
            staged = write_package(
                checkpoint,
                Path(tmp) / weights,
                max_length=max_length,
                extra_metadata=metadata,
                use_int8=weights == "int8",
            )
            f1[weights] = _dev_review_f1(staged, dev, thresholds)
        chosen = (
            "int8"
            if f1["fp32"] - f1["int8"] <= MAX_QUANTIZATION_LOSS
            else "fp32"
        )
        if out_dir.exists():
            shutil.rmtree(out_dir)
        shutil.copytree(Path(tmp) / chosen, out_dir)
    logger.info("dev review macro-F1 %s; shipping %s", f1, chosen)
    return {
        "weights": chosen,
        "dev_review_macro_f1": f1,
        "max_quantization_loss": MAX_QUANTIZATION_LOSS,
    }


def score_package(
    package_dir: Path, splits: dict[str, Any], seed: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-score a package on gold splits with the shared harness.

    Returns:
        Per split, the harness report and the raw part scores (promotion
        re-checks rules 1 and 3 on them: they are what is served).
    """
    from hotel_curator.inference.package import ModelPackage
    from hotel_curator.ml.evaluate import EvalConfig, score_predictions

    package = ModelPackage(package_dir)
    config = EvalConfig(thresholds=package.metadata["thresholds"], seed=seed)
    reports: dict[str, Any] = {}
    raw_scores: dict[str, Any] = {}
    for name, split in splits.items():
        raw = package.predictor.predict_proba(split.texts, split.parts)
        raw_scores[name] = raw
        reports[name] = score_predictions(
            f"{package.name}-package",
            split.frame,
            raw,
            config,
            calibrated=package.calibrate(raw),
        ).to_dict()
    return reports, raw_scores
