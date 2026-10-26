"""Rung (b) of the modeling ladder: frozen sentence embeddings + linear heads.

Question it answers: is a general-purpose sentence embedding enough, or does
the task need fine-tuning? Each part is embedded once with bge-small-en-v1.5
(CLS pooling, L2-normalised, as its authors specify), the Liked/Disliked
flag is appended as one extra feature, and one logistic regression per label
is fitted on top. Training the heads takes seconds; embedding the corpus
costs about as much as one forward pass of a small encoder.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Self

import numpy as np
import numpy.typing as npt
from sklearn.linear_model import LogisticRegression

from hotel_curator.text import format_input

FloatMatrix = npt.NDArray[np.float64]
EMBED_MAX_LENGTH = 128
EMBED_BATCH = 64


class SentenceEmbedder:
    """CLS-pooled, L2-normalised embeddings from a BERT-style encoder."""

    def __init__(self, model_dir: Path, *, num_threads: int = 4) -> None:
        import torch

        from hotel_curator.ml.hf import load_encoder, load_tokenizer

        torch.set_num_threads(num_threads)
        self._tokenizer = load_tokenizer(model_dir)
        self._model = load_encoder(model_dir)
        self._model.eval()  # type: ignore[no-untyped-call]

    def embed(self, inputs: Sequence[str]) -> FloatMatrix:
        """Return ``(len(inputs), hidden)`` unit-length vectors."""
        import torch

        order = np.argsort([len(s) for s in inputs])
        out: FloatMatrix | None = None
        with torch.no_grad():
            for start in range(0, len(order), EMBED_BATCH):
                idx = order[start : start + EMBED_BATCH]
                enc = self._tokenizer(
                    [inputs[i] for i in idx],
                    truncation=True,
                    max_length=EMBED_MAX_LENGTH,
                    padding=True,
                    return_tensors="pt",
                )
                cls = self._model(**enc).last_hidden_state[:, 0]
                vectors = torch.nn.functional.normalize(cls, dim=-1)
                if out is None:
                    out = np.zeros((len(inputs), vectors.shape[1]))
                out[idx] = vectors.double().numpy()
        return out if out is not None else np.zeros((0, 0))


def part_features(
    embedder: SentenceEmbedder, texts: Sequence[str], parts: Sequence[str]
) -> FloatMatrix:
    """Embedding of the formatted part plus a Liked (1) / Disliked (0) flag."""
    inputs = [format_input(t, p) for t, p in zip(texts, parts, strict=True)]
    flag = np.array([[1.0 if p == "liked" else 0.0] for p in parts])
    return np.hstack([embedder.embed(inputs), flag])


class EmbeddingLogReg:
    """One logistic-regression head per label on precomputed features."""

    name = "bge-small-logreg"

    def __init__(
        self, labels: Sequence[str], *, c: float = 4.0, seed: int = 0
    ) -> None:
        self.labels = list(labels)
        self.c = c
        self.seed = seed
        self._heads: list[LogisticRegression | float] = []

    def fit(self, features: FloatMatrix, y: npt.ArrayLike) -> Self:
        """Fit one balanced logistic regression per label.

        Raises:
            ValueError: If ``y`` does not have one column per label.
        """
        y_arr = np.asarray(y, dtype=bool)
        if y_arr.shape != (len(features), len(self.labels)):
            raise ValueError(f"y has shape {y_arr.shape}")
        self._heads = []
        for k in range(len(self.labels)):
            target = y_arr[:, k]
            if target.all() or not target.any():
                self._heads.append(float(target.all()))
                continue
            head = LogisticRegression(
                C=self.c,
                class_weight="balanced",
                max_iter=3000,
                random_state=self.seed,
            )
            head.fit(features, target.astype(int))
            self._heads.append(head)
        return self

    def predict_proba(self, features: FloatMatrix) -> FloatMatrix:
        """Return ``(n, n_labels)`` probabilities.

        Raises:
            RuntimeError: If the model has not been fitted.
        """
        if not self._heads:
            raise RuntimeError("model is not fitted")
        out = np.zeros((len(features), len(self.labels)))
        for k, head in enumerate(self._heads):
            if isinstance(head, float):
                out[:, k] = head
            else:
                out[:, k] = head.predict_proba(features)[:, 1]
        return out
