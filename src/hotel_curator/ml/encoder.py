"""Rung (c) of the modeling ladder: fine-tune a pretrained transformer encoder.

What it does: takes a pretrained encoder directory (for example
``roberta-base``), adds a 16-way multi-label head, fine-tunes it on
silver-labeled review parts with class-weighted binary cross-entropy, and
keeps the epoch with the best *deployment* metric: dev macro-F1 with
per-label thresholds tuned on dev. v1 selected checkpoints on F1 at a fixed
0.5 threshold that it never used in production.

Why a small hand-written loop instead of ``transformers.Trainer``: the loop
is ~60 lines, has no hidden defaults, and makes the selection metric and
the class weighting explicit. Everything numeric (optimizer, scheduler,
model) still comes from PyTorch and transformers.

CPU cost: batches group parts of similar length (review parts range from
one word to several hundred), so little compute is spent on padding.
Measured training times are recorded with every run
(``artifacts/evals/<rung>/report.json``) and in the model card.
"""

import json
import logging
import random
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt
import torch
from transformers import (
    PreTrainedModel,
    PreTrainedTokenizerBase,
    get_linear_schedule_with_warmup,
)

from hotel_curator.ml.hf import load_classifier, load_tokenizer
from hotel_curator.ml.metrics import macro_f1
from hotel_curator.ml.thresholds import apply_thresholds, tune_thresholds
from hotel_curator.text import format_input

logger = logging.getLogger(__name__)

FloatMatrix = npt.NDArray[np.float64]
Split = tuple[Sequence[str], Sequence[str], npt.NDArray[np.bool_]]
"""(texts, parts, labels) for one data split."""


@dataclass(frozen=True)
class EncoderConfig:
    """Hyperparameters for fine-tuning; recorded verbatim in the model card."""

    base_model: str
    max_length: int = 128
    batch_size: int = 32
    learning_rate: float = 3e-5
    epochs: int = 4
    warmup_ratio: float = 0.06
    weight_decay: float = 0.01
    pos_weight_cap: float = 10.0
    grad_clip: float = 1.0
    seed: int = 13
    num_threads: int = 4


@dataclass
class TrainResult:
    """Outcome of :func:`train_encoder`."""

    best_epoch: int
    best_dev_macro_f1: float
    thresholds: dict[str, float]
    history: list[dict[str, float]] = field(default_factory=list)
    seconds: float = 0.0
    n_parameters: int = 0
    """Parameters of the fine-tuned model, encoder and head (the rule-2 size
    of a base not in ``ml.compare.SIZE_MILLIONS``)."""


def count_parameters(model: torch.nn.Module) -> int:
    """Number of parameters (weights and biases) in ``model``."""
    return sum(p.numel() for p in model.parameters())


def set_seed(seed: int) -> None:
    """Seed Python, NumPy and PyTorch RNGs."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _pos_weight(y: npt.NDArray[np.bool_], cap: float) -> torch.Tensor:
    """sqrt(neg/pos) per label, capped; keeps rare labels from vanishing."""
    pos = y.sum(axis=0).astype(np.float64)
    neg = len(y) - pos
    weight = np.sqrt(np.where(pos > 0, neg / np.maximum(pos, 1), 1.0))
    return torch.tensor(np.clip(weight, 1.0, cap), dtype=torch.float32)


def _encode(
    tokenizer: PreTrainedTokenizerBase,
    texts: Sequence[str],
    parts: Sequence[str],
    max_length: int,
) -> dict[str, torch.Tensor]:
    inputs = [format_input(t, p) for t, p in zip(texts, parts, strict=True)]
    return tokenizer(  # type: ignore[no-any-return]
        inputs,
        truncation=True,
        max_length=max_length,
        padding=True,
        return_tensors="pt",
    )


@torch.no_grad()
def predict_logits(
    model: PreTrainedModel,
    tokenizer: PreTrainedTokenizerBase,
    texts: Sequence[str],
    parts: Sequence[str],
    *,
    max_length: int,
    batch_size: int = 64,
) -> FloatMatrix:
    """Sigmoid probabilities in input order, batching by length for speed."""
    model.eval()  # type: ignore[no-untyped-call]
    order = np.argsort([len(t) for t in texts])
    n_labels = int(model.config.num_labels or 0)
    out = np.zeros((len(texts), n_labels), dtype=np.float64)
    for start in range(0, len(order), batch_size):
        idx = order[start : start + batch_size]
        enc = _encode(
            tokenizer,
            [texts[i] for i in idx],
            [parts[i] for i in idx],
            max_length,
        )
        logits = model(**enc).logits
        out[idx] = torch.sigmoid(logits).double().numpy()
    return out


BUCKET_BATCHES = 50
"""Batches per sorting chunk in :func:`length_bucketed_batches`."""


def length_bucketed_batches(
    lengths: Sequence[int], batch_size: int, rng: np.random.Generator
) -> list[npt.NDArray[np.intp]]:
    """Shuffled batches of similar-length items ("smart batching").

    Items are shuffled, cut into chunks of ``BUCKET_BATCHES`` batches, sorted
    by length inside each chunk and split into batches; the batch order is
    then shuffled. Every item appears exactly once per epoch, and padding to
    the longest item in a batch wastes less compute than with random
    batches, because review parts vary widely in length.
    """
    perm = rng.permutation(len(lengths))
    chunk = batch_size * BUCKET_BATCHES
    batches: list[npt.NDArray[np.intp]] = []
    for start in range(0, len(perm), chunk):
        block = perm[start : start + chunk]
        block = block[np.argsort([lengths[i] for i in block], kind="stable")]
        batches.extend(
            block[i : i + batch_size] for i in range(0, len(block), batch_size)
        )
    order = rng.permutation(len(batches))
    return [batches[i] for i in order]


def _train_epoch(
    model: PreTrainedModel,
    tokenizer: PreTrainedTokenizerBase,
    train: Split,
    config: EncoderConfig,
    optim: torch.optim.Optimizer,
    sched: torch.optim.lr_scheduler.LRScheduler,
    loss_fn: torch.nn.Module,
    rng: np.random.Generator,
) -> float:
    texts, parts, y = train
    model.train()
    losses = []
    for idx in length_bucketed_batches(
        [len(t) for t in texts], config.batch_size, rng
    ):
        enc = _encode(
            tokenizer,
            [texts[i] for i in idx],
            [parts[i] for i in idx],
            config.max_length,
        )
        target = torch.tensor(y[idx], dtype=torch.float32)
        loss = loss_fn(model(**enc).logits, target)
        optim.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)
        optim.step()
        sched.step()
        losses.append(loss.item())
    return float(np.mean(losses))


def _load_for_training(
    config: EncoderConfig, labels: Sequence[str]
) -> tuple[PreTrainedModel, PreTrainedTokenizerBase]:
    tokenizer = load_tokenizer(config.base_model)
    model = load_classifier(
        config.base_model,
        num_labels=len(labels),
        problem_type="multi_label_classification",
        id2label=dict(enumerate(labels)),
        label2id={label: i for i, label in enumerate(labels)},
    )
    return model, tokenizer


def _optimizer(
    model: PreTrainedModel, config: EncoderConfig, n_train: int
) -> tuple[torch.optim.Optimizer, torch.optim.lr_scheduler.LRScheduler]:
    """AdamW without weight decay on biases and norms; linear warmup/decay."""
    no_decay = ("bias", "LayerNorm.weight", "layer_norm.weight")
    named = list(model.named_parameters())
    groups = [
        {
            "params": [p for n, p in named if not n.endswith(no_decay)],
            "weight_decay": config.weight_decay,
        },
        {
            "params": [p for n, p in named if n.endswith(no_decay)],
            "weight_decay": 0.0,
        },
    ]
    optim = torch.optim.AdamW(groups, lr=config.learning_rate)
    steps = config.epochs * -(-n_train // config.batch_size)
    sched = get_linear_schedule_with_warmup(  # type: ignore[no-untyped-call]
        optim, int(config.warmup_ratio * steps), steps
    )
    return optim, sched


def _dev_epoch_score(
    model: PreTrainedModel,
    tokenizer: PreTrainedTokenizerBase,
    dev: Split,
    labels: Sequence[str],
    max_length: int,
) -> tuple[float, dict[str, float]]:
    """Dev macro-F1 at thresholds tuned on dev, and those thresholds."""
    probs = predict_logits(
        model, tokenizer, dev[0], dev[1], max_length=max_length
    )
    thresholds = tune_thresholds(dev[2], probs, labels)
    return macro_f1(
        dev[2], apply_thresholds(probs, thresholds, labels)
    ), thresholds


def _loss(y: npt.ArrayLike, cap: float) -> torch.nn.Module:
    """Multi-label BCE with capped positive weights (rare labels count more)."""
    return torch.nn.BCEWithLogitsLoss(
        pos_weight=_pos_weight(np.asarray(y, dtype=bool), cap)
    )


def _write_run_files(
    out_dir: Path, result: TrainResult, config: EncoderConfig
) -> None:
    (out_dir / "thresholds.json").write_text(
        json.dumps(result.thresholds, indent=2)
    )
    (out_dir / "train_config.json").write_text(
        json.dumps(
            {"config": asdict(config), "history": result.history}, indent=2
        )
    )


def train_encoder(
    train: Split,
    dev: Split,
    labels: Sequence[str],
    config: EncoderConfig,
    out_dir: Path,
) -> TrainResult:
    """Fine-tune ``config.base_model`` and save the best epoch to ``out_dir``.

    Args:
        train: Training texts, parts and ``(n, n_labels)`` bool labels.
        dev: Development split used for threshold tuning and selection.
        labels: Label names in column order.
        config: Hyperparameters.
        out_dir: Destination for the best model (safetensors), tokenizer,
            ``thresholds.json`` and ``train_config.json``.

    Returns:
        Best epoch, its dev macro-F1, thresholds, history, parameter count.
    """
    started = time.perf_counter()
    set_seed(config.seed)
    torch.set_num_threads(config.num_threads)
    model, tokenizer = _load_for_training(config, labels)
    optim, sched = _optimizer(model, config, len(train[0]))
    loss_fn = _loss(train[2], config.pos_weight_cap)
    rng = np.random.default_rng(config.seed)
    result = TrainResult(best_epoch=-1, best_dev_macro_f1=-1.0, thresholds={})
    out_dir.mkdir(parents=True, exist_ok=True)
    for epoch in range(1, config.epochs + 1):
        loss = _train_epoch(
            model, tokenizer, train, config, optim, sched, loss_fn, rng
        )
        f1, thresholds = _dev_epoch_score(
            model, tokenizer, dev, labels, config.max_length
        )
        result.history.append(
            {"epoch": epoch, "train_loss": loss, "dev_macro_f1": f1}
        )
        logger.info("epoch %d loss %.4f dev macro-F1 %.4f", epoch, loss, f1)
        if f1 > result.best_dev_macro_f1:
            result.best_epoch, result.best_dev_macro_f1 = epoch, f1
            result.thresholds = thresholds
            model.save_pretrained(out_dir, safe_serialization=True)
            tokenizer.save_pretrained(out_dir)
    result.seconds = time.perf_counter() - started
    result.n_parameters = count_parameters(model)
    _write_run_files(out_dir, result, config)
    return result


class EncoderPredictor:
    """PyTorch predictor for a fine-tuned encoder directory."""

    def __init__(self, model_dir: Path, *, max_length: int = 128) -> None:
        self.name = f"encoder:{model_dir.name}"
        self.max_length = max_length
        self.tokenizer = load_tokenizer(model_dir)
        self.model = load_classifier(model_dir)
        self.model.eval()  # type: ignore[no-untyped-call]

    def predict_proba(
        self, texts: Sequence[str], parts: Sequence[str]
    ) -> FloatMatrix:
        """Return ``(n, n_labels)`` probabilities."""
        return predict_logits(
            self.model, self.tokenizer, texts, parts, max_length=self.max_length
        )


class _LogitsOnly(torch.nn.Module):
    """A classifier whose forward returns only its logits, for export.

    Asking the model for tuple outputs instead (``return_dict=False``)
    breaks ModernBERT, whose head reads attributes of its encoder's output.
    """

    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(
        self, input_ids: torch.Tensor, attention_mask: torch.Tensor
    ) -> torch.Tensor:
        """Logits for one padded batch."""
        logits: torch.Tensor = self.model(
            input_ids=input_ids, attention_mask=attention_mask
        ).logits
        return logits


def export_onnx(model_dir: Path, out_dir: Path, *, max_length: int) -> Path:
    """Export a fine-tuned encoder to ONNX plus a tokenizer for serving.

    Writes ``model.onnx``, ``tokenizer.json`` and ``metadata.json`` (labels,
    max length, input format) to ``out_dir``.

    Returns:
        Path to ``model.onnx``.
    """
    tokenizer = load_tokenizer(model_dir)
    model = load_classifier(model_dir)
    model.eval()  # type: ignore[no-untyped-call]
    out_dir.mkdir(parents=True, exist_ok=True)
    sample = _encode(tokenizer, ["example text"] * 2, ["liked"] * 2, max_length)
    onnx_path = out_dir / "model.onnx"
    batch, seq = torch.export.Dim("batch"), torch.export.Dim("sequence")
    torch.onnx.export(
        _LogitsOnly(model),
        (),
        str(onnx_path),
        kwargs={
            "input_ids": sample["input_ids"],
            "attention_mask": sample["attention_mask"],
        },
        input_names=["input_ids", "attention_mask"],
        output_names=["logits"],
        dynamic_shapes={
            "input_ids": {0: batch, 1: seq},
            "attention_mask": {0: batch, 1: seq},
        },
        dynamo=True,
        external_data=False,
    )
    tokenizer.backend_tokenizer.save(str(out_dir / "tokenizer.json"))
    id2label = {
        str(k): str(v) for k, v in (model.config.id2label or {}).items()
    }
    num_labels = int(model.config.num_labels or 0)
    labels = [id2label[str(i)] for i in range(num_labels)]
    metadata = {
        "labels": labels,
        "max_length": max_length,
        "pad_token": tokenizer.pad_token,
        "pad_id": tokenizer.pad_token_id,
        "input_format": "<Liked|Disliked>: <text>",
    }
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
    return onnx_path
