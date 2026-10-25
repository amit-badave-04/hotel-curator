"""From part probabilities to per-hotel evidence (ADR 006, section 1).

Steps, each a pure function on DataFrames:

1. :func:`eligible_reviews`: drop reviews used as classifier training data
   (their probabilities are in-sample) and near-duplicates (within a hotel,
   the same normalised text of at least ``near_duplicate_min_words`` words
   as an earlier review).
2. :func:`review_signals`: per review and attribute, the noisy-OR of the
   Liked and Disliked parts' calibrated probabilities, so a review counts at
   most once per polarity; plus hard-label versions (any part >= 0.5).
3. :func:`hotel_evidence`: per hotel, the sums ``P`` and ``N``, the review
   count ``n``, complaining reviews, hard counts and the month spread.

Reviews without any text part stay eligible and contribute ``n`` but no
opinion: a guest with nothing to say about the noise did not complain
about it.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from hotel_curator.scoring.grades import AttributeCounts
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.tiers import ReviewSpread
from hotel_curator.taxonomy import LABELS, Attribute, Polarity

POS = "pos"
NEG = "neg"


def signal_columns(prefix: str) -> list[str]:
    """``<prefix>_<attribute>`` column names in attribute order."""
    return [f"{prefix}_{a.value}" for a in Attribute]


def eligible_reviews(
    reviews: pd.DataFrame,
    training_review_ids: Collection[str],
    policy: TierPolicy,
) -> pd.DataFrame:
    """Reviews that may count towards hotel scores.

    Args:
        reviews: Canonical reviews (``review_id``, ``hotel_id``,
            ``review_date``, ``liked_text``, ``disliked_text``).
        training_review_ids: Reviews used to train the classifier.
        policy: Supplies ``near_duplicate_min_words``.

    Returns:
        The eligible subset, sorted by hotel, date and id.
    """
    kept = reviews[~reviews["review_id"].isin(set(training_review_ids))]
    kept = kept.sort_values(["hotel_id", "review_date", "review_id"])
    text = (
        (kept["liked_text"].fillna("") + " " + kept["disliked_text"].fillna(""))
        .str.strip()
        .str.lower()
    )
    long_enough = text.str.count(r"\S+") >= policy.near_duplicate_min_words
    duplicate = long_enough & pd.DataFrame(
        {"hotel_id": kept["hotel_id"], "text": text}
    ).duplicated(keep="first")
    return kept[~duplicate].reset_index(drop=True)


def _label_index(attribute: Attribute, polarity: Polarity) -> int:
    return LABELS.index(f"{attribute.value}_{polarity.value}")


def review_signals(
    reviews: pd.DataFrame,
    part_probs: pd.DataFrame,
    policy: TierPolicy,
) -> pd.DataFrame:
    """Per-review noisy-OR signals for every attribute and polarity.

    Args:
        reviews: Eligible reviews (``review_id``, ``hotel_id``,
            ``review_date``).
        part_probs: Calibrated part probabilities: ``review_id``, ``part``
            and the 16 label columns. Parts of other reviews are ignored.
        policy: Supplies the hard-label and complaining-review thresholds.

    Returns:
        One row per review with ``review_id``, ``hotel_id``, ``month`` and
        ``pos_*``, ``neg_*``, ``pos_hard_*``, ``neg_hard_*`` columns.
    """
    parts = part_probs[part_probs["review_id"].isin(reviews["review_id"])]
    probs = parts[list(LABELS)].to_numpy(dtype=np.float64)
    log_miss = pd.DataFrame(
        np.log1p(-np.clip(probs, 0.0, 1.0 - 1e-12)), columns=list(LABELS)
    )
    log_miss["review_id"] = parts["review_id"].to_numpy()
    hard = pd.DataFrame(
        probs >= policy.hard_label_probability, columns=list(LABELS)
    )
    hard["review_id"] = parts["review_id"].to_numpy()
    summed = log_miss.groupby("review_id").sum()
    soft = pd.DataFrame(
        1.0 - np.exp(summed.to_numpy()),
        index=summed.index,
        columns=summed.columns,
    )
    any_hard = hard.groupby("review_id").any()
    out = reviews[["review_id", "hotel_id"]].copy()
    out["month"] = pd.to_datetime(reviews["review_date"]).dt.to_period("M")
    soft = soft.reindex(out["review_id"]).fillna(0.0)
    any_hard = any_hard.reindex(out["review_id"]).fillna(False).astype(bool)
    for attribute in Attribute:
        for polarity, prefix in (
            (Polarity.POSITIVE, POS),
            (Polarity.NEGATIVE, NEG),
        ):
            label = LABELS[_label_index(attribute, polarity)]
            out[f"{prefix}_{attribute.value}"] = soft[label].to_numpy()
            out[f"{prefix}_hard_{attribute.value}"] = any_hard[label].to_numpy()
    return out.reset_index(drop=True)


@dataclass(frozen=True)
class HotelEvidence:
    """Everything the tier rules need about one hotel."""

    hotel_id: str
    reviews: int
    counts: dict[Attribute, AttributeCounts]
    spread: ReviewSpread
    first_month: str
    last_month: str
    monthly_reviews: dict[str, int]
    """Eligible reviews per month (``YYYY-MM``), months with reviews only."""


def attribute_counts(
    sums: Mapping[Any, float], reviews: int, complaining: Mapping[Any, float]
) -> dict[Attribute, AttributeCounts]:
    """Per-attribute counts from one group's summed review signals.

    Args:
        sums: Summed ``pos_*``/``neg_*`` signals; ``*_hard_*`` sums are
            optional (hard counts are 0 without them).
        reviews: Reviews in the group.
        complaining: Per ``neg_*`` column, reviews at or above the
            complaining-review probability.
    """
    return {
        a: AttributeCounts(
            attribute=a,
            positive=float(sums[f"{POS}_{a.value}"]),
            # Float sums of probabilities can overshoot n by rounding.
            negative=min(float(sums[f"{NEG}_{a.value}"]), float(reviews)),
            reviews=reviews,
            complaining_reviews=int(complaining[f"{NEG}_{a.value}"]),
            positive_hard=int(sums.get(f"{POS}_hard_{a.value}", 0)),
            negative_hard=int(sums.get(f"{NEG}_hard_{a.value}", 0)),
        )
        for a in Attribute
    }


def hotel_evidence(
    signals: pd.DataFrame, policy: TierPolicy
) -> dict[str, HotelEvidence]:
    """Aggregate review signals into per-hotel evidence. O(rows)."""
    columns = (
        signal_columns(POS)
        + signal_columns(NEG)
        + signal_columns(f"{POS}_hard")
        + signal_columns(f"{NEG}_hard")
    )
    sums = signals.groupby("hotel_id")[columns].sum().to_dict("index")
    complaining = (
        (signals[signal_columns(NEG)] >= policy.complaining_review_probability)
        .groupby(signals["hotel_id"])
        .sum()
        .to_dict("index")
    )
    months = {
        str(hotel): group.value_counts().sort_index()
        for hotel, group in signals.groupby("hotel_id")["month"]
    }
    out: dict[str, HotelEvidence] = {}
    for hotel_key, row in sums.items():
        hotel_id = str(hotel_key)
        per_month = months[hotel_id]
        n = int(per_month.sum())
        out[hotel_id] = HotelEvidence(
            hotel_id=hotel_id,
            reviews=n,
            counts=attribute_counts(row, n, complaining[hotel_key]),
            spread=ReviewSpread(
                distinct_months=len(per_month),
                max_month_share=float(per_month.max() / n),
            ),
            first_month=str(per_month.index.min()),
            last_month=str(per_month.index.max()),
            monthly_reviews={str(m): int(c) for m, c in per_month.items()},
        )
    return out
