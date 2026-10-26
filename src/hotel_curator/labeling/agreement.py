"""Inter-annotator agreement and majority-vote aggregation.

Why this module exists: v1 had a single LLM annotator and therefore no
agreement statistic at all. v2's gold set is labeled by a panel of three
independent annotators; agreement between them is reported per label so a
reader can see which labels are well defined and which are not.

Agreement among LLM annotators is a *consistency proxy*, not human
inter-annotator agreement: models can share the same blind spots. The docs
say so wherever these numbers appear.

Fleiss' kappa comes from statsmodels and Cohen's kappa from scikit-learn;
this module only reshapes data and handles the undefined cases.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import numpy.typing as npt
from sklearn.metrics import cohen_kappa_score
from statsmodels.stats.inter_rater import fleiss_kappa

Ratings = npt.NDArray[np.integer] | npt.NDArray[np.bool_]


@dataclass(frozen=True)
class LabelAgreement:
    """Agreement statistics for one label across the annotator panel."""

    label: str
    fleiss_kappa: float
    mean_pairwise_cohen_kappa: float
    unanimous_rate: float
    positives_majority: int


def _check_binary_2d(ratings: Ratings) -> npt.NDArray[np.int64]:
    arr = np.asarray(ratings).astype(np.int64)
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise ValueError(
            f"ratings must be (n_items, n_raters>=2), got {arr.shape}"
        )
    if not np.isin(arr, (0, 1)).all():
        raise ValueError("ratings must be binary (0/1)")
    return arr


def fleiss_kappa_binary(ratings: Ratings) -> float:
    """Fleiss' kappa for binary ratings of shape ``(n_items, n_raters)``.

    Returns NaN when every rating is identical, because expected agreement
    is then 1 and kappa is undefined.
    """
    arr = _check_binary_2d(ratings)
    positives = arr.sum(axis=1)
    table = np.column_stack([arr.shape[1] - positives, positives])
    if (table.sum(axis=0) == 0).any():
        return float("nan")
    return float(fleiss_kappa(table, method="fleiss"))


def mean_pairwise_cohen_kappa(ratings: Ratings) -> float:
    """Average Cohen's kappa over all annotator pairs (NaN if undefined)."""
    arr = _check_binary_2d(ratings)
    kappas = []
    for i, j in combinations(range(arr.shape[1]), 2):
        a, b = arr[:, i], arr[:, j]
        if len(np.unique(np.concatenate([a, b]))) < 2:
            continue
        kappas.append(cohen_kappa_score(a, b))
    return float(np.mean(kappas)) if kappas else float("nan")


def majority_vote(ratings: npt.NDArray[np.bool_]) -> npt.NDArray[np.bool_]:
    """Aggregate ``(n_items, n_raters, n_labels)`` ratings by majority.

    Raises:
        ValueError: If the number of raters is even (ties are possible).
    """
    arr = np.asarray(ratings, dtype=bool)
    if arr.ndim != 3:
        raise ValueError(f"ratings must be 3-D, got shape {arr.shape}")
    n_raters = arr.shape[1]
    if n_raters % 2 == 0:
        raise ValueError(f"need an odd number of raters, got {n_raters}")
    return np.asarray(arr.sum(axis=1) * 2 > n_raters)


def agreement_report(
    ratings: npt.NDArray[np.bool_], labels: Sequence[str]
) -> list[LabelAgreement]:
    """Per-label agreement for ``(n_items, n_raters, n_labels)`` ratings."""
    arr = np.asarray(ratings, dtype=bool)
    if arr.ndim != 3 or arr.shape[2] != len(labels):
        raise ValueError(
            f"ratings shape {arr.shape} does not match {len(labels)} labels"
        )
    majority = majority_vote(arr)
    report = []
    for k, label in enumerate(labels):
        per_label = arr[:, :, k]
        unanimous = (per_label.all(axis=1) | ~per_label.any(axis=1)).mean()
        report.append(
            LabelAgreement(
                label=label,
                fleiss_kappa=fleiss_kappa_binary(per_label),
                mean_pairwise_cohen_kappa=mean_pairwise_cohen_kappa(per_label),
                unanimous_rate=float(unanimous),
                positives_majority=int(majority[:, k].sum()),
            )
        )
    return report
