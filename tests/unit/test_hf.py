"""Pretrained weights must all load; only the task head may be new."""

from pathlib import Path

import pytest
import torch
from safetensors.torch import load_file, save_file

from hotel_curator.ml.hf import load_classifier
from tests.helpers import tiny_bert

EMBEDDING = "embeddings.word_embeddings.weight"


def _rewrite(base: Path, change: dict[str, str | None]) -> None:
    """Rename (or drop, for ``None``) tensors in the checkpoint."""
    path = base / "model.safetensors"
    tensors = load_file(path)
    for old, new in change.items():
        tensor = tensors.pop(old)
        if new is not None:
            tensors[new] = tensor
    save_file(tensors, path, metadata={"format": "pt"})


def test_complete_checkpoint_loads(tmp_path: Path) -> None:
    model = load_classifier(tiny_bert(tmp_path / "base"), num_labels=3)
    assert model.config.num_labels == 3


def test_embedding_is_restored_from_its_tied_copy(tmp_path: Path) -> None:
    base = tiny_bert(tmp_path / "base")
    original = load_file(base / "model.safetensors")[EMBEDDING]
    _rewrite(base, {EMBEDDING: "cls.predictions.decoder.weight"})
    model = load_classifier(base, num_labels=3)
    restored = model.bert.embeddings.word_embeddings.weight
    assert torch.equal(restored, original)


def test_missing_pretrained_weight_is_an_error(tmp_path: Path) -> None:
    base = tiny_bert(tmp_path / "base")
    _rewrite(base, {"encoder.layer.0.attention.self.query.weight": None})
    with pytest.raises(ValueError, match="pretrained weights missing"):
        load_classifier(base, num_labels=3)


def test_embedding_without_tied_copy_is_an_error(tmp_path: Path) -> None:
    base = tiny_bert(tmp_path / "base")
    _rewrite(base, {EMBEDDING: None})
    with pytest.raises(ValueError, match="no tied copy"):
        load_classifier(base, num_labels=3)
