"""Rung (a) of the modeling ladder: TF-IDF features + linear / boosted heads.

Why these exist: a strong, cheap, well-understood baseline tells us how much
a neural model actually adds. If a fine-tuned encoder cannot clearly beat
TF-IDF + logistic regression, the extra cost is not justified.

Features: word 1-2 grams plus character 3-5 grams (robust to the source's
missing punctuation and to typos), sublinear TF. Each of the 16 labels gets
its own binary head (one-vs-rest). The part marker ("Liked:" /
"Disliked:", see ``hotel_curator.text``) is part of the input text, so the
model can use it.

Baselines are retrained in seconds and are never pickled; only their
evaluation reports are kept.
"""

from collections.abc import Sequence
from typing import Self

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion

from hotel_curator.text import format_input

FloatMatrix = npt.NDArray[np.float64]
MAX_WORD_FEATURES = 200_000
MAX_CHAR_FEATURES = 300_000


def _vectoriser() -> FeatureUnion:
    return FeatureUnion(
        [
            (
                "word",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    min_df=2,
                    max_features=MAX_WORD_FEATURES,
                    sublinear_tf=True,
                    lowercase=True,
                ),
            ),
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    min_df=3,
                    max_features=MAX_CHAR_FEATURES,
                    sublinear_tf=True,
                    lowercase=True,
                ),
            ),
        ]
    )


class _TfidfBase:
    """Shared fit/predict plumbing for one-vs-rest TF-IDF models."""

    name = "tfidf-base"

    def __init__(self, labels: Sequence[str], seed: int = 0) -> None:
        self.labels = list(labels)
        self.seed = seed
        self._vectoriser = _vectoriser()
        self._heads: list[object | None] = []
        self._constant: dict[int, float] = {}

    def _features(
        self, texts: Sequence[str], parts: Sequence[str], *, fit: bool
    ) -> sparse.csr_matrix:
        inputs = [format_input(t, p) for t, p in zip(texts, parts, strict=True)]
        if fit:
            return self._vectoriser.fit_transform(inputs).tocsr()
        return self._vectoriser.transform(inputs).tocsr()

    def _make_head(self) -> object:
        raise NotImplementedError

    def fit(
        self, texts: Sequence[str], parts: Sequence[str], y: npt.ArrayLike
    ) -> Self:
        """Fit the vectoriser and one head per label.

        A label with no positive (or no negative) training example gets no
        head and always predicts probability 0 (or 1).
        """
        y_arr = np.asarray(y, dtype=bool)
        if y_arr.shape != (len(texts), len(self.labels)):
            raise ValueError(f"y has shape {y_arr.shape}")
        features = self._features(texts, parts, fit=True)
        self._heads, self._constant = [], {}
        for k in range(len(self.labels)):
            target = y_arr[:, k]
            if target.all() or not target.any():
                self._heads.append(None)
                self._constant[k] = float(target.all())
                continue
            head = self._make_head()
            head.fit(features, target.astype(int))  # type: ignore[attr-defined]
            self._heads.append(head)
        return self

    def predict_proba(
        self, texts: Sequence[str], parts: Sequence[str]
    ) -> FloatMatrix:
        """Return ``(n, n_labels)`` probabilities."""
        if not self._heads:
            raise RuntimeError("model is not fitted")
        features = self._features(texts, parts, fit=False)
        out = np.zeros((len(texts), len(self.labels)), dtype=np.float64)
        for k, head in enumerate(self._heads):
            if head is None:
                out[:, k] = self._constant[k]
            else:
                out[:, k] = head.predict_proba(features)[:, 1]  # type: ignore[attr-defined]
        return out


class TfidfLogReg(_TfidfBase):
    """TF-IDF + L2 logistic regression with balanced class weights."""

    name = "tfidf-logreg"

    def _make_head(self) -> object:
        return LogisticRegression(
            C=4.0,
            class_weight="balanced",
            max_iter=2000,
            solver="liblinear",
            random_state=self.seed,
        )


class TfidfLightGBM(_TfidfBase):
    """TF-IDF + gradient-boosted trees (LightGBM) per label."""

    name = "tfidf-lightgbm"

    def _make_head(self) -> object:
        return lgb.LGBMClassifier(
            n_estimators=300,
            learning_rate=0.05,
            num_leaves=31,
            min_child_samples=5,
            colsample_bytree=0.3,
            is_unbalance=True,
            random_state=self.seed,
            n_jobs=4,
            verbose=-1,
        )
