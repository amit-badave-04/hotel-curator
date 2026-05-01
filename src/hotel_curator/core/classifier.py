"""Model loading and batched inference.

Extracted from NB03/NB04. Zero framework deps beyond torch +
transformers + numpy.
"""

import json
import logging
from pathlib import Path

import numpy as np
import torch
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
)

logger = logging.getLogger(__name__)

# Type alias for the model + tokenizer pair
ModelBundle = tuple[
    AutoModelForSequenceClassification,
    AutoTokenizer,
    list[str],           # label_names
    dict[str, float],    # thresholds
]


def load_model_bundle(
    ckpt_dir: Path,
    *,
    device: str = "cpu",
    threshold_set: str = "val",
) -> ModelBundle:
    """Load model, tokenizer, labels, and thresholds.

    Parameters
    ----------
    ckpt_dir : Path
        Directory containing HF checkpoint + JSON artifacts.
    device : str
        "cuda" or "cpu".
    threshold_set : str
        "val" for val-tuned, "gold" for gold-set re-tuned.

    Returns
    -------
    ModelBundle
        (model, tokenizer, label_names, thresholds)
    """
    if not (ckpt_dir / "config.json").exists():
        raise FileNotFoundError(
            f"No checkpoint at {ckpt_dir}. "
            f"Run the training pipeline first."
        )

    tokenizer = AutoTokenizer.from_pretrained(str(ckpt_dir))
    model = AutoModelForSequenceClassification.from_pretrained(
        str(ckpt_dir),
    )
    model.to(device).eval()

    with (ckpt_dir / "label_names.json").open() as f:
        label_names = json.load(f)

    thresh_file = (
        "thresholds_gold_tuned.json"
        if threshold_set == "gold"
        else "thresholds.json"
    )
    thresh_path = ckpt_dir / thresh_file
    if not thresh_path.exists():
        thresh_path = ckpt_dir / "thresholds.json"
    with thresh_path.open() as f:
        thresholds = json.load(f)

    logger.info(
        "Model loaded: %d labels, device=%s, thresholds=%s",
        len(label_names), device, thresh_file,
    )
    return model, tokenizer, label_names, thresholds


@torch.no_grad()
def infer_probs(
    model: AutoModelForSequenceClassification,
    tokenizer: AutoTokenizer,
    texts: list[str],
    *,
    max_length: int = 256,
    batch_size: int = 64,
    device: str = "cpu",
    use_bf16: bool = False,
) -> np.ndarray:
    """Run batched sigmoid-prob inference.

    Parameters
    ----------
    model : AutoModelForSequenceClassification
        Loaded model in eval mode.
    tokenizer : AutoTokenizer
        Matching tokenizer.
    texts : list[str]
        Review texts to classify.
    max_length : int
        Tokenizer truncation length (must match training).
    batch_size : int
        Inference batch size.
    device : str
        "cuda" or "cpu".
    use_bf16 : bool
        Enable bfloat16 autocast (CUDA only).

    Returns
    -------
    np.ndarray
        Shape (N, num_labels) of sigmoid probabilities.
    """
    model.eval()
    all_probs: list[np.ndarray] = []

    use_amp = use_bf16 and device == "cuda"
    autocast_ctx = (
        torch.amp.autocast(
            device_type="cuda", dtype=torch.bfloat16,
        )
        if use_amp
        else torch.amp.autocast(
            device_type="cpu", enabled=False,
        )
    )

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        enc = tokenizer(
            batch,
            truncation=True,
            max_length=max_length,
            padding=True,
            return_tensors="pt",
        ).to(device)
        with autocast_ctx:
            logits = model(**enc).logits
        probs = torch.sigmoid(logits.float()).cpu().numpy()
        all_probs.append(probs)

    return np.concatenate(all_probs, axis=0)


def apply_thresholds(
    probs: np.ndarray,
    thresholds: dict[str, float],
    label_names: list[str],
) -> np.ndarray:
    """Threshold per-label probs into binary predictions.

    Parameters
    ----------
    probs : np.ndarray
        Shape (N, num_labels) of sigmoid probabilities.
    thresholds : dict[str, float]
        Per-label decision thresholds.
    label_names : list[str]
        Ordered label names matching probs columns.

    Returns
    -------
    np.ndarray
        Shape (N, num_labels) of int predictions (0 or 1).
    """
    out = np.zeros_like(probs, dtype=int)
    for i, lbl in enumerate(label_names):
        out[:, i] = (probs[:, i] >= thresholds[lbl]).astype(int)
    return out
