"""Tests for encoder fine-tuning, ONNX export and ONNX inference.

Uses a tiny randomly initialised BERT and a toy WordPiece vocabulary built
in the test, so nothing is downloaded.
"""

from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from hotel_curator.inference.onnx_predictor import OnnxPredictor  # noqa: E402
from hotel_curator.ml.encoder import (  # noqa: E402
    EncoderConfig,
    EncoderPredictor,
    export_onnx,
    train_encoder,
)
from tests.helpers import tiny_bert  # noqa: E402


@pytest.fixture(scope="module")
def tiny_base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tiny_bert(tmp_path_factory.mktemp("tiny_bert"))


def _toy(n: int, seed: int) -> tuple[list[str], list[str], np.ndarray]:
    rng = np.random.default_rng(seed)
    y = np.zeros((n, 2), dtype=bool)
    texts = []
    for i in range(n):
        clean = rng.random() < 0.5
        y[i, 0] = clean
        y[i, 1] = not clean
        texts.append("the room was very " + ("clean" if clean else "dirty"))
    parts = ["liked"] * n
    return texts, parts, y


def test_train_predict_export_and_onnx_parity(
    tiny_base: Path, tmp_path: Path
) -> None:
    texts, parts, y = _toy(64, seed=0)
    dev_texts, dev_parts, dev_y = _toy(32, seed=1)
    config = EncoderConfig(
        base_model=str(tiny_base),
        max_length=16,
        batch_size=16,
        learning_rate=5e-3,
        epochs=4,
        seed=0,
        num_threads=1,
    )
    result = train_encoder(
        (texts, parts, y),
        (dev_texts, dev_parts, dev_y),
        ["c+", "c-"],
        config,
        tmp_path / "model",
    )
    assert result.best_dev_macro_f1 > 0.9
    assert (tmp_path / "model" / "model.safetensors").exists()
    assert len(result.history) == 4

    predictor = EncoderPredictor(tmp_path / "model", max_length=16)
    probs = predictor.predict_proba(dev_texts, dev_parts)
    assert probs.shape == (32, 2)

    onnx_path = export_onnx(
        tmp_path / "model", tmp_path / "onnx", max_length=16
    )
    onnx_predictor = OnnxPredictor(onnx_path.parent)
    onnx_probs = onnx_predictor.predict_proba(dev_texts, dev_parts)
    np.testing.assert_allclose(onnx_probs, probs, atol=1e-4)


def test_a_modernbert_classifier_exports_with_parity(
    tiny_base: Path, tmp_path: Path
) -> None:
    """ModernBERT (mmBERT's architecture) breaks with tuple outputs."""
    from transformers import (
        ModernBertConfig,
        ModernBertForSequenceClassification,
    )

    from tests.helpers import TINY_VOCAB

    model_dir = tmp_path / "modernbert"
    config = ModernBertConfig(
        vocab_size=len(TINY_VOCAB),
        hidden_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=64,
        max_position_embeddings=64,
        local_attention=16,
        pad_token_id=0,
        num_labels=2,
        id2label={0: "c+", 1: "c-"},
        label2id={"c+": 0, "c-": 1},
    )
    torch.manual_seed(0)
    ModernBertForSequenceClassification(config).save_pretrained(model_dir)
    for name in ("tokenizer.json", "tokenizer_config.json"):
        (model_dir / name).write_bytes((tiny_base / name).read_bytes())
    texts, parts, _ = _toy(8, seed=2)
    probs = EncoderPredictor(model_dir, max_length=16).predict_proba(
        texts, parts
    )
    onnx_path = export_onnx(model_dir, tmp_path / "onnx", max_length=16)
    onnx_probs = OnnxPredictor(onnx_path.parent).predict_proba(texts, parts)
    np.testing.assert_allclose(onnx_probs, probs, atol=1e-4)


def test_package_with_thresholds_calibration_and_int8(
    tiny_base: Path, tmp_path: Path
) -> None:
    from hotel_curator.inference.package import ModelPackage
    from hotel_curator.ml.calibration import IsotonicCalibrator
    from hotel_curator.ml.package import measure_throughput, write_package

    texts, parts, y = _toy(48, seed=2)
    config = EncoderConfig(
        base_model=str(tiny_base),
        max_length=16,
        batch_size=16,
        learning_rate=5e-3,
        epochs=2,
        seed=0,
        num_threads=1,
    )
    train_encoder(
        (texts, parts, y),
        (texts, parts, y),
        ["c+", "c-"],
        config,
        tmp_path / "model",
    )
    identity = IsotonicCalibrator(
        ["c+", "c-"], [[0.0, 1.0]] * 2, [[0.0, 1.0]] * 2, ["c+", "c-"]
    )
    extra = {
        "name": "tiny",
        "thresholds": {"c+": 0.5, "c-": 0.5},
        "calibration": identity.to_dict(),
    }
    reference = EncoderPredictor(tmp_path / "model", max_length=16)
    raw = reference.predict_proba(texts, parts)
    for use_int8, tolerance in ((False, 1e-4), (True, 0.1)):
        out = write_package(
            tmp_path / "model",
            tmp_path / f"pkg-{use_int8}",
            max_length=16,
            extra_metadata=extra,
            use_int8=use_int8,
        )
        package = ModelPackage(out)
        prediction = package.predict(texts, parts)
        np.testing.assert_allclose(
            prediction.probabilities, raw, atol=tolerance
        )
        assert package.metadata["weights"] == ("int8" if use_int8 else "fp32")
    fp32 = ModelPackage(tmp_path / "pkg-False")
    assert (fp32.predict(texts, parts).labels == (raw >= 0.5)).all()
    timing = measure_throughput(fp32.predictor, texts, parts, corpus_parts=480)
    assert timing["corpus_hours"] > 0
    assert timing["sample_parts"] == len(texts)


def test_length_bucketed_batches_cover_every_item_once() -> None:
    from hotel_curator.ml.encoder import length_bucketed_batches

    rng = np.random.default_rng(0)
    lengths = rng.integers(1, 300, size=1001).tolist()
    batches = length_bucketed_batches(lengths, 16, rng)
    flat = np.concatenate(batches)
    assert sorted(flat.tolist()) == list(range(1001))
    assert max(len(b) for b in batches) == 16
    spread = np.mean([np.ptp([lengths[i] for i in b]) for b in batches])
    random_spread = np.mean(
        [np.ptp(rng.choice(lengths, 16)) for _ in range(len(batches))]
    )
    assert spread < random_spread / 3


def test_build_and_score_a_16_label_package(
    tiny_base: Path, tmp_path: Path
) -> None:
    import pandas as pd

    from hotel_curator.ml.calibration import IsotonicCalibrator
    from hotel_curator.ml.data import LabeledParts
    from hotel_curator.ml.package import (
        MAX_QUANTIZATION_LOSS,
        build_package,
        score_package,
    )
    from hotel_curator.taxonomy import LABELS

    texts, parts, y2 = _toy(64, seed=3)
    y = np.zeros((64, len(LABELS)), dtype=bool)
    y[:, LABELS.index("cleanliness_positive")] = y2[:, 0]
    y[:, LABELS.index("cleanliness_negative")] = y2[:, 1]
    frame = pd.DataFrame(
        {
            "review_id": [f"r{i}" for i in range(64)],
            "hotel_id": [f"h{i % 8}" for i in range(64)],
            "part": parts,
            "text": texts,
            "stratum": "uniform",
            "sampling_weight": 1.0,
            **{label: y[:, k] for k, label in enumerate(LABELS)},
        }
    )
    split = LabeledParts("gold_dev", frame)
    config = EncoderConfig(
        base_model=str(tiny_base),
        max_length=16,
        batch_size=16,
        learning_rate=5e-3,
        epochs=2,
        seed=0,
        num_threads=1,
    )
    train_encoder(
        (texts, parts, y),
        (texts, parts, y),
        list(LABELS),
        config,
        tmp_path / "model",
    )
    identity = IsotonicCalibrator(
        list(LABELS), [[0.0, 1.0]] * 16, [[0.0, 1.0]] * 16, list(LABELS)
    )
    out = tmp_path / "package"
    report = build_package(
        tmp_path / "model",
        out,
        metadata={
            "name": "tiny16",
            "thresholds": dict.fromkeys(LABELS, 0.5),
            "calibration": identity.to_dict(),
        },
        max_length=16,
        dev=split,
    )
    assert report["weights"] in {"fp32", "int8"}
    assert (out / "model.onnx").is_file()
    scored, raw = score_package(out, {"gold_dev": split}, seed=0)
    assert scored["gold_dev"]["n_reviews"] == 64
    assert raw["gold_dev"].shape == (len(split.y), len(LABELS))
    assert report["max_quantization_loss"] == MAX_QUANTIZATION_LOSS
