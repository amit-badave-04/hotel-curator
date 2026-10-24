"""The only place that loads Hugging Face models and tokenizers.

Every load is from a local directory that was downloaded by
``curator model fetch-base`` (SHA-256 verified) or written by training:

* ``local_files_only=True``: never contacts a model hub, so there is no
  unpinned remote download (bandit B615 does not apply; noted per call);
* ``use_safetensors=True`` for weights: pickled checkpoints are refused, so
  loading a model cannot execute code (SR-8).

A classifier built on a pretrained checkpoint must receive every pretrained
weight; only the new task head may be freshly initialised. Some masked-LM
checkpoints (mmBERT among them) store the input embedding only under the
name of the output layer it is tied to; that copy is restored explicitly,
and any other missing weight is an error rather than a silent random init.
"""

import logging
from pathlib import Path
from typing import Any

import torch
from safetensors import safe_open
from transformers import (
    AutoModel,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    PreTrainedModel,
    PreTrainedTokenizerBase,
)


def load_tokenizer(path: Path | str) -> PreTrainedTokenizerBase:
    """Tokenizer from a local directory."""
    tokenizer: PreTrainedTokenizerBase = AutoTokenizer.from_pretrained(  # nosec B615
        path, local_files_only=True
    )
    return tokenizer


HEAD_PREFIXES = ("classifier.", "score.", "pre_classifier.")
"""Task-head parameters: absent from a pretrained checkpoint by design."""
TIED_OUTPUT_KEYS = (
    "decoder.weight",
    "lm_head.decoder.weight",
    "cls.predictions.decoder.weight",
)
"""Where masked-LM checkpoints keep an output layer tied to the input
embedding."""
EMBEDDING_SUFFIXES = ("tok_embeddings.weight", "word_embeddings.weight")

logger = logging.getLogger(__name__)


def load_classifier(path: Path | str, **kwargs: Any) -> PreTrainedModel:
    """Sequence-classification model from local safetensors weights.

    Raises:
        ValueError: If a pretrained (non-head) weight is missing from the
            checkpoint and cannot be restored from its tied copy.
    """
    model, info = AutoModelForSequenceClassification.from_pretrained(  # nosec B615
        path,
        local_files_only=True,
        use_safetensors=True,
        output_loading_info=True,
        **kwargs,
    )
    missing = [
        k for k in info["missing_keys"] if not k.startswith(HEAD_PREFIXES)
    ]
    if missing:
        restore_tied_embedding(model, Path(path), missing)
    loaded: PreTrainedModel = model
    return loaded


def restore_tied_embedding(
    model: PreTrainedModel, path: Path, missing: list[str]
) -> None:
    """Copy an input embedding stored only as its tied output layer.

    Raises:
        ValueError: If anything else is missing, the model does not tie its
            embeddings, or no same-shaped tied copy is in the checkpoint.
    """
    embedding = [k for k in missing if k.endswith(EMBEDDING_SUFFIXES)]
    if len(embedding) != 1 or len(missing) != 1:
        raise ValueError(f"{path}: pretrained weights missing: {missing}")
    if not getattr(model.config, "tie_word_embeddings", False):
        raise ValueError(f"{path}: {embedding[0]} missing and not tied")
    target = dict(model.named_parameters())[embedding[0]]
    with safe_open(str(path / "model.safetensors"), framework="pt") as f:
        stored = [k for k in TIED_OUTPUT_KEYS if k in set(f.keys())]
        if not stored:
            raise ValueError(f"{path}: no tied copy of {embedding[0]}")
        source = f.get_tensor(stored[0])
    if source.shape != target.shape:
        raise ValueError(f"{path}: {stored[0]} has shape {tuple(source.shape)}")
    with torch.no_grad():
        target.copy_(source)
    # transformers' own load report above lists the embedding as missing.
    logger.info("restored %s from its tied copy %s", embedding[0], stored[0])


def load_encoder(path: Path | str) -> PreTrainedModel:
    """Bare encoder (no head) from local safetensors weights."""
    model: PreTrainedModel = AutoModel.from_pretrained(  # nosec B615
        path, local_files_only=True, use_safetensors=True
    )
    return model
