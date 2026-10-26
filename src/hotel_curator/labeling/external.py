"""External human-label check: TripAdvisor Annotated (Marcheggiani et al.).

Why: the v2 gold set is labeled by an LLM panel. This small public dataset
was labeled by **human** annotators at sentence level with hotel aspects and
polarities, and 73 of its reviews were labeled by three people. Running the
same panel on these reviews measures how the panel compares with people,
next to the human-vs-human agreement on the same reviews.

Caveats, stated wherever results appear:

* Different domain (TripAdvisor, 2000s, whole reviews instead of
  Liked/Disliked parts) and a different guideline; agreement mixes guideline
  mismatch with panel error.
* Only six of our eight attributes have a counterpart (no noise, no wifi:
  the source folds "quiet" into ROOMS).
* Licence unverified: the files are fetched by pinned hash into a gitignored
  directory, the ``author`` field is dropped on load, and only aggregate
  numbers are committed.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pandas as pd

from hotel_curator.data.sources import Source, SourceFile
from hotel_curator.taxonomy import LABELS

_BASE = (
    "https://raw.githubusercontent.com/kashlearning/tripadvisor_hotels/"
    "bce96ac504fb243dac3a915635119bc5043829b2/Dataset"
)
TRIPADVISOR_ANNOTATED = Source(
    key="tripadvisor_annotated",
    title="TripAdvisor Annotated (sentence-level aspect labels by humans)",
    license="unverified; evaluation only, never committed",
    homepage="https://github.com/kashlearning/tripadvisor_hotels",
    files=tuple(
        SourceFile(f"{name}.json", f"{_BASE}/{name}.json", digest, size)
        for name, digest, size in (
            (
                "train.unique",
                "994009c6ab1fef4f6c30e6f221c490ad4bb2b46ccd47635498902cd7530f42cd",
                426778,
            ),
            (
                "test.unique",
                "73349a6dff1bfc65a67370d51f9db1b3709a947804e3d23061d96cf4ffe8538c",
                193683,
            ),
            (
                "test.zero",
                "b2d6182259771a0d6ecead722eb1a62315fa343f6ee31df3c8edd88048979a91",
                128645,
            ),
            (
                "test.one",
                "99bd20691401c363c3e26dc70ad5015fa690efd2529fc2b256256903a1a90bca",
                126626,
            ),
            (
                "test.two",
                "51096b65b043fd0c4ef7443ea349c4832181acd498114eda1d923924de08c3d2",
                131502,
            ),
        )
    ),
)
TRIPLE_FILES = ("test.zero.json", "test.one.json", "test.two.json")
"""One file per human annotator of the same test reviews."""
HUMAN_RATERS = len(TRIPLE_FILES)

ASPECT_TO_ATTRIBUTE: dict[str, str] = {
    "CLEANLINESS": "cleanliness",
    "ROOMS": "room",
    "SERVICE": "staff",
    "CHECKIN": "staff",
    "LOCATION": "location",
    "VALUE": "value",
    "FOOD": "food",
}
_EXPLICIT = {"p": "positive", "n": "negative"}
_IMPLICIT = {"ip": "positive", "in": "negative"}
EXTERNAL_LABELS: tuple[str, ...] = tuple(
    label
    for label in LABELS
    if label.rsplit("_", 1)[0] in set(ASPECT_TO_ATTRIBUTE.values())
)
"""The 12 of our 16 labels that the external set can check."""


def review_labels(
    segment_labels: Iterable[Mapping[str, str]], *, implicit: bool
) -> set[str]:
    """Union of a review's sentence labels, mapped to our label names.

    ``x`` (mixed or neutral) and unknown codes give no label; implicit
    codes (``ip``/``in``) count only when ``implicit`` is true.
    """
    codes = {**_EXPLICIT, **(_IMPLICIT if implicit else {})}
    out = set()
    for segment in segment_labels:
        for aspect, code in segment.items():
            attribute = ASPECT_TO_ATTRIBUTE.get(aspect)
            polarity = codes.get(code)
            if attribute and polarity:
                out.add(f"{attribute}_{polarity}")
    return out


def _read_rows(paths: Sequence[Path]) -> list[dict[str, object]]:
    rows = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                row.pop("author", None)
                rows.append(row)
    return rows


def load_reviews(paths: Sequence[Path]) -> pd.DataFrame:
    """One row per unique review: id, hotel id, joined text, segment labels."""
    rows = _read_rows(paths)
    frame = pd.DataFrame(
        {
            "review_id": [str(r["reviewId"]) for r in rows],
            "hotel_id": [str(r["hotelId"]) for r in rows],
            "text": [" ".join(map(str, r["segments"])) for r in rows],  # type: ignore[call-overload]
            "segment_labels": [r["segmentLabels"] for r in rows],
            "annotator": [int(str(r["annotatorId"])) for r in rows],
        }
    )
    return frame.drop_duplicates("review_id").reset_index(drop=True)


def human_labels(
    triple_paths: Sequence[Path], *, implicit: bool
) -> npt.NDArray[np.bool_]:
    """Human ratings ``(n_reviews, n_annotators, 12)`` for the triple set."""
    per_file = []
    for path in triple_paths:
        rows = sorted(_read_rows([path]), key=lambda r: str(r["reviewId"]))
        matrix = np.zeros((len(rows), len(EXTERNAL_LABELS)), dtype=bool)
        for i, row in enumerate(rows):
            got = review_labels(row["segmentLabels"], implicit=implicit)  # type: ignore[arg-type]
            for k, label in enumerate(EXTERNAL_LABELS):
                matrix[i, k] = label in got
        per_file.append(matrix)
    return np.stack(per_file, axis=1)


def compare_label_sets(
    panel: Mapping[str, set[str]],
    reference: Mapping[str, set[str]],
    labels: Sequence[str],
) -> list[dict[str, float | int | str]]:
    """Per-label agreement of panel labels with a reference (human) labeling.

    Reviews are the keys present in both mappings. Precision and recall treat
    the reference as truth; Cohen's kappa is symmetric.
    """
    from sklearn.metrics import cohen_kappa_score

    ids = sorted(set(panel) & set(reference))
    rows: list[dict[str, float | int | str]] = []
    for label in labels:
        y_ref = np.array([label in reference[i] for i in ids])
        y_pan = np.array([label in panel[i] for i in ids])
        tp = int((y_ref & y_pan).sum())
        fp = int((~y_ref & y_pan).sum())
        fn = int((y_ref & ~y_pan).sum())
        kappa = (
            float(cohen_kappa_score(y_ref, y_pan))
            if len({*y_ref.tolist(), *y_pan.tolist()}) > 1
            else float("nan")
        )
        rows.append(
            {
                "label": label,
                "n_reviews": len(ids),
                "support": tp + fn,
                "precision": tp / (tp + fp) if tp + fp else float("nan"),
                "recall": tp / (tp + fn) if tp + fn else float("nan"),
                "f1": 2 * tp / (2 * tp + fp + fn)
                if tp + fp + fn
                else float("nan"),
                "cohen_kappa": kappa,
            }
        )
    return rows


def _kappa(a: npt.NDArray[np.bool_], b: npt.NDArray[np.bool_]) -> float:
    """Cohen's kappa; NaN when both raters gave the same single value."""
    from sklearn.metrics import cohen_kappa_score

    if len({*a.tolist(), *b.tolist()}) < 2:
        return float("nan")
    return float(cohen_kappa_score(a, b))


def triple_set_kappas(
    humans: npt.NDArray[np.bool_],
    panel: Sequence[set[str]],
    labels: Sequence[str],
) -> list[dict[str, float | str]]:
    """Like-for-like agreement on the reviews three people labeled.

    For each label: the mean Cohen's kappa over the three human pairs, and
    the mean Cohen's kappa between the panel and each human. If the second
    is close to the first, the panel agrees with people about as well as
    people agree with each other.

    Args:
        humans: Ratings ``(n_reviews, HUMAN_RATERS, n_labels)``.
        panel: The panel's labels for the same reviews, same order.
        labels: Label names in column order.
    """
    from itertools import combinations

    expected = (len(panel), HUMAN_RATERS, len(labels))
    if humans.shape != expected:
        raise ValueError(
            f"humans has shape {humans.shape}; panel and labels need {expected}"
        )

    rows: list[dict[str, float | str]] = []
    for k, label in enumerate(labels):
        pan = np.array([label in labels_ for labels_ in panel])
        h = humans[:, :, k]
        human_pairs = [
            _kappa(h[:, i], h[:, j])
            for i, j in combinations(range(HUMAN_RATERS), 2)
        ]
        panel_human = [_kappa(pan, h[:, i]) for i in range(HUMAN_RATERS)]
        rows.append(
            {
                "label": label,
                "n_reviews": float(len(panel)),
                "human_positives_mean": float(h.sum(axis=0).mean()),
                "panel_positives": float(pan.sum()),
                "human_human_kappa": float(np.nanmean(human_pairs)),
                "panel_human_kappa": float(np.nanmean(panel_human)),
            }
        )
    return rows
