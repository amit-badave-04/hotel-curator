"""Stratified sampling of reviews for annotation.

Why: a uniform sample of a few thousand reviews contains only a handful of
wifi complaints, so rare labels cannot be measured. We therefore draw from
two disjoint strata of the eligible reviews in one split:

* ``enriched``: reviews whose text matches :data:`RARE_TOPIC_PATTERN`
  (wifi/internet words, or location words in the Disliked part), and
* ``uniform``: everything else,

with a fixed share of the sample from ``enriched``. Each sampled review
carries ``sampling_weight`` = (stratum population) / (stratum sample size),
the inverse of its inclusion probability, so prevalence estimates over the
sample can be re-weighted to the population. Model metrics (F1) are reported
both on the whole sample and on the uniform stratum alone.

Sampling is deterministic: reviews are ordered by a seeded hash of their id,
so row order never changes the sample.
"""

import hashlib
import re
from dataclasses import dataclass

import pandas as pd

RARE_TOPIC_PATTERN = re.compile(
    r"\b(?:wi ?fi|wifi|internet|wireless|connection)\b", re.IGNORECASE
)
DISLIKED_LOCATION_PATTERN = re.compile(
    r"\b(?:location|far|walk|metro|station|tube|area|neighbou?rhood|distance)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SampleSpec:
    """What to sample.

    Attributes:
        split: Which hotel split to sample from (train / val / test).
        n_total: Number of reviews to draw.
        enriched_share: Share of the sample drawn from the enriched stratum.
        seed: Seed for the deterministic ordering.
    """

    split: str
    n_total: int
    enriched_share: float
    seed: int

    def __post_init__(self) -> None:
        if self.n_total <= 0:
            raise ValueError("n_total must be positive")
        if not 0.0 <= self.enriched_share <= 1.0:
            raise ValueError("enriched_share must be in [0, 1]")


def _matches_rare_topic(liked: pd.Series, disliked: pd.Series) -> pd.Series:
    liked_text = liked.fillna("")
    disliked_text = disliked.fillna("")
    wifi = liked_text.str.contains(
        RARE_TOPIC_PATTERN
    ) | disliked_text.str.contains(RARE_TOPIC_PATTERN)
    location = disliked_text.str.contains(DISLIKED_LOCATION_PATTERN)
    return wifi | location


def _seeded_order(ids: pd.Series, seed: int) -> pd.Series:
    return ids.map(lambda i: hashlib.sha256(f"{seed}:{i}".encode()).hexdigest())


def _eligible_pool(
    reviews: pd.DataFrame, spec: SampleSpec, exclude: set[str]
) -> pd.DataFrame:
    """Reviews of the split with some text, minus ``exclude``.

    Raises:
        ValueError: If fewer than ``spec.n_total`` remain.
    """
    pool = reviews[reviews["split"] == spec.split]
    pool = pool[pool["liked_text"].notna() | pool["disliked_text"].notna()]
    if exclude:
        pool = pool[~pool["review_id"].isin(exclude)]
    if len(pool) < spec.n_total:
        raise ValueError(
            f"only {len(pool)} eligible reviews in split {spec.split!r}, "
            f"{spec.n_total} requested"
        )
    return pool


def draw_sample(
    reviews: pd.DataFrame,
    spec: SampleSpec,
    *,
    exclude: set[str] | None = None,
) -> pd.DataFrame:
    """Draw a stratified, weighted, deterministic annotation sample.

    Args:
        reviews: Canonical reviews with a ``split`` column.
        spec: Split, size, enrichment share and seed.
        exclude: Review ids that must not be drawn (e.g. already labeled).

    Returns:
        The sampled rows plus ``stratum`` and ``sampling_weight`` columns,
        ordered by the seeded hash.

    Raises:
        ValueError: If there are fewer eligible reviews than requested.
    """
    pool = _eligible_pool(reviews, spec, exclude or set())
    pool = pool.assign(
        stratum=_matches_rare_topic(
            pool["liked_text"], pool["disliked_text"]
        ).map({True: "enriched", False: "uniform"}),
        _order=_seeded_order(pool["review_id"], spec.seed),
    ).sort_values("_order")
    enriched = pool[pool["stratum"] == "enriched"]
    uniform = pool[pool["stratum"] == "uniform"]
    n_enriched = min(round(spec.n_total * spec.enriched_share), len(enriched))
    n_uniform = spec.n_total - n_enriched
    if n_uniform > len(uniform):
        raise ValueError(
            f"only {len(uniform)} eligible uniform-stratum reviews, "
            f"{n_uniform} requested"
        )
    parts = []
    for stratum, n in ((enriched, n_enriched), (uniform, n_uniform)):
        if n:
            drawn = stratum.head(n).assign(sampling_weight=len(stratum) / n)
            parts.append(drawn)
    sample = pd.concat(parts).sort_values("_order").drop(columns="_order")
    return sample.reset_index(drop=True)
