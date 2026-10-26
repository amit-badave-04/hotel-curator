"""Tests for the TF-IDF baselines on a tiny synthetic task."""

import numpy as np
import pytest

from hotel_curator.ml.baselines import TfidfLightGBM, TfidfLogReg
from hotel_curator.ml.metrics import macro_f1
from hotel_curator.text import format_input

WORDS = {
    0: ["clean", "spotless", "tidy"],
    1: ["dirty", "stained", "smelly"],
    2: ["friendly", "helpful", "kind"],
}
FILLER = ["the", "hotel", "was", "and", "our", "stay", "very", "really"]


def _data(n: int, seed: int) -> tuple[list[str], list[str], np.ndarray]:
    rng = np.random.default_rng(seed)
    y = rng.random((n, 3)) < 0.3
    texts = []
    for row in y:
        words = list(rng.choice(FILLER, size=5))
        for k in np.flatnonzero(row):
            words.append(str(rng.choice(WORDS[int(k)])))
        rng.shuffle(words)
        texts.append(" ".join(words))
    parts = list(rng.choice(["liked", "disliked"], size=n))
    return texts, parts, y


def test_format_input_adds_part_marker() -> None:
    assert format_input("Great location", "liked") == "Liked: Great location"
    assert format_input("Tiny", "disliked") == "Disliked: Tiny"
    with pytest.raises(ValueError):
        format_input("x", "other")


@pytest.mark.parametrize("model_cls", [TfidfLogReg, TfidfLightGBM])
def test_baseline_learns_keyword_labels(model_cls: type) -> None:
    texts, parts, y = _data(600, seed=0)
    test_texts, test_parts, y_test = _data(200, seed=1)
    model = model_cls(labels=["a", "b", "c"], seed=0).fit(texts, parts, y)
    probs = model.predict_proba(test_texts, test_parts)
    assert probs.shape == (200, 3)
    assert ((probs >= 0) & (probs <= 1)).all()
    assert macro_f1(y_test, probs >= 0.5) > 0.9


def test_label_without_positives_predicts_zero() -> None:
    texts, parts, y = _data(200, seed=0)
    y[:, 2] = False
    model = TfidfLogReg(labels=["a", "b", "c"], seed=0).fit(texts, parts, y)
    probs = model.predict_proba(texts[:10], parts[:10])
    assert (probs[:, 2] == 0).all()
