"""The frozen v1 checkpoint, adapted to the v2 evaluation harness.

v1 is a roberta-base classifier fine-tuned on whole TripAdvisor reviews,
with its own label order (``label_names.json``) and per-label thresholds
tuned on its own validation data. Its training text came from the source
already lowercased, stopword-stripped and with split contractions ("did
n't"; v1 audit, D3). It never saw a Liked/Disliked split, so two ways of
feeding it a Booking review are scored:

* ``review``: the review's parts joined into one lowercased text, a whole
  review as v1 was trained on; one prediction per review.
* ``parts``: each part on its own, lowercased; the review's labels are the
  union of its parts' labels, as for v2.

Both inputs are only lowercased: the stopword removal and contraction
splitting of v1's training text were never documented and are not
reproduced, so v1 sees stopwords it rarely saw in training. Which input is
used, and whether v1 keeps its shipped thresholds or gets thresholds tuned
on gold-dev, is chosen on gold-dev by the same rule as for v2 models, so the
comparison gives v1 the best of these four configurations.
"""

import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pandas as pd

from hotel_curator.taxonomy import LABELS, V1_TO_V2_LABEL

V1_MAX_LENGTH = 256
"""v1's training sequence length (NB03 ``MAX_SEQ_LENGTH``)."""

FloatMatrix = npt.NDArray[np.float64]


def v1_review_texts(gold: pd.DataFrame) -> tuple[list[str], list[str]]:
    """One lowercased text per review: Liked part, then Disliked part.

    Returns:
        Sorted review ids and their texts.
    """
    ordered = gold.assign(
        _order=gold["part"].map({"liked": 0, "disliked": 1})
    ).sort_values(["review_id", "_order"], kind="stable")
    joined = ordered.groupby("review_id", sort=True)["text"].agg(" ".join)
    return joined.index.tolist(), [t.lower() for t in joined]


def v1_thresholds(model_dir: Path) -> dict[str, float]:
    """v1's shipped thresholds, renamed to v2 labels."""
    raw = json.loads((model_dir / "thresholds.json").read_text())
    return {V1_TO_V2_LABEL[name]: float(value) for name, value in raw.items()}


class V1Predictor:
    """Run the v1 checkpoint and return probabilities in v2 label order."""

    name = "v1-roberta-base"

    def __init__(self, model_dir: Path, *, num_threads: int = 4) -> None:
        """Load the checkpoint.

        Raises:
            ValueError: If v1's labels do not map one-to-one onto v2's.
        """
        import torch

        from hotel_curator.ml.hf import load_classifier, load_tokenizer

        torch.set_num_threads(num_threads)
        names = json.loads((model_dir / "label_names.json").read_text())
        mapped = [V1_TO_V2_LABEL.get(name) for name in names]
        if sorted(m or "" for m in mapped) != sorted(LABELS):
            raise ValueError(f"v1 labels {names} do not map onto v2 labels")
        self._columns = [mapped.index(label) for label in LABELS]
        self._tokenizer = load_tokenizer(model_dir)
        self._model = load_classifier(model_dir)
        self._model.eval()  # type: ignore[no-untyped-call]

    def predict_texts(
        self, texts: Sequence[str], *, batch_size: int = 32
    ) -> FloatMatrix:
        """Probabilities ``(n, 16)`` for already-lowercased texts."""
        import torch

        order = np.argsort([len(t) for t in texts])
        out = np.zeros((len(texts), len(LABELS)), dtype=np.float64)
        with torch.no_grad():
            for start in range(0, len(order), batch_size):
                idx = order[start : start + batch_size]
                enc = self._tokenizer(
                    [texts[i] for i in idx],
                    truncation=True,
                    max_length=V1_MAX_LENGTH,
                    padding=True,
                    return_tensors="pt",
                )
                probs = torch.sigmoid(self._model(**enc).logits).double()
                out[idx] = probs.numpy()[:, self._columns]
        return out

    def predict_proba(
        self, texts: Sequence[str], parts: Sequence[str]
    ) -> FloatMatrix:
        """``parts`` mode: each part on its own (the marker is not used)."""
        del parts
        return self.predict_texts([t.lower() for t in texts])
