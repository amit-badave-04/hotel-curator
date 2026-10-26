"""Load the committed label tables in the shape every model rung consumes.

A label table (``data/labels/<set>/labels.parquet``, written by
``curator label merge``) has one row per review part with text, the 16
label columns, the hotel, the sampling stratum and the inverse-inclusion
sampling weight. This module is the single place that turns those tables
into model inputs, so every rung trains and is scored on identical rows.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt
import pandas as pd

from hotel_curator.taxonomy import LABELS

LabelSet = Literal["train", "train_2", "gold_dev", "gold_test"]
LABEL_SETS: tuple[LabelSet, ...] = ("train", "gold_dev", "gold_test")
EXTRA_TRAIN: LabelSet = "train_2"
"""Optional second training sample (``curator label sample-extra``)."""
REQUIRED_COLUMNS = (
    "review_id",
    "hotel_id",
    "part",
    "text",
    "stratum",
    "sampling_weight",
    *LABELS,
)


@dataclass(frozen=True)
class LabeledParts:
    """One label set: the table plus convenient array views."""

    name: str
    frame: pd.DataFrame

    @property
    def texts(self) -> list[str]:
        """Normalised part texts (without the Liked/Disliked marker)."""
        return self.frame["text"].astype(str).tolist()

    @property
    def parts(self) -> list[str]:
        """``"liked"`` or ``"disliked"`` per row."""
        return self.frame["part"].astype(str).tolist()

    @property
    def y(self) -> npt.NDArray[np.bool_]:
        """Gold labels, shape ``(n_parts, 16)`` in :data:`LABELS` order."""
        return self.frame[list(LABELS)].to_numpy(dtype=bool)

    @property
    def weights(self) -> npt.NDArray[np.float64]:
        """Inverse-inclusion sampling weights (population estimates)."""
        return self.frame["sampling_weight"].to_numpy(dtype=np.float64)

    def subset(self, mask: npt.NDArray[np.bool_]) -> "LabeledParts":
        """Rows where ``mask`` is true, as a new label set."""
        return LabeledParts(self.name, self.frame[mask].reset_index(drop=True))


def load_label_set(labels_dir: Path, name: LabelSet) -> LabeledParts:
    """Read and check one label table.

    Rows are sorted by (review id, part) so every consumer sees the same
    order regardless of how the file was written.

    Raises:
        FileNotFoundError: If the table does not exist.
        KeyError: If required columns are missing.
        ValueError: If a (review, part) pair occurs twice or text is empty.
    """
    path = labels_dir / name / "labels.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found; run `curator label merge`")
    frame = pd.read_parquet(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise KeyError(f"{path} lacks columns {missing}")
    if frame.duplicated(["review_id", "part"]).any():
        raise ValueError(f"{path} has duplicate (review_id, part) rows")
    if (frame["text"].fillna("").str.strip() == "").any():
        raise ValueError(f"{path} has parts without text")
    frame = frame.sort_values(["review_id", "part"], kind="stable")
    return LabeledParts(name, frame.reset_index(drop=True))


def load_all(labels_dir: Path) -> dict[LabelSet, LabeledParts]:
    """Label sets, checked to be disjoint.

    Train, gold-dev and gold-test always; ``train_2`` too when it has been
    merged. No review may appear in two sets, and no hotel in two splits
    (the two training sets share hotels by design).

    Raises:
        ValueError: If the sets overlap.
    """
    names: list[LabelSet] = list(LABEL_SETS)
    if (labels_dir / EXTRA_TRAIN / "labels.parquet").is_file():
        names.append(EXTRA_TRAIN)
    sets = {name: load_label_set(labels_dir, name) for name in names}
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            keys = ["review_id"]
            if {a, b} != {"train", EXTRA_TRAIN}:
                keys.append("hotel_id")
            for key in keys:
                shared = set(sets[a].frame[key]) & set(sets[b].frame[key])
                if shared:
                    raise ValueError(
                        f"{a} and {b} share {len(shared)} {key} values"
                    )
    return sets


def training_set(
    sets: dict[LabelSet, LabeledParts], *, extra: bool
) -> LabeledParts:
    """The training labels, with ``train_2`` appended when ``extra``.

    Raises:
        KeyError: If ``extra`` is requested but ``train_2`` is not merged.
    """
    if not extra:
        return sets["train"]
    if EXTRA_TRAIN not in sets:
        raise KeyError("train_2 is not merged; run curator label merge")
    frame = pd.concat([sets["train"].frame, sets[EXTRA_TRAIN].frame])
    frame = frame.sort_values(["review_id", "part"], kind="stable")
    return LabeledParts("train+train_2", frame.reset_index(drop=True))
