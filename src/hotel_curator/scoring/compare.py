"""Credible differences between hotels and over time (ADR 006, §3 and §5).

Both questions are answered with seeded Monte Carlo draws from the two Beta
posteriors, so the answers are exactly reproducible:

* :func:`prob_better`: how sure are we that hotel A is better than hotel B
  on an attribute? A difference is highlighted only at >= 0.95.
* :func:`trend`: did an attribute change between the baseline window (18
  months) and the recent window (6 months)? Flagged when a shift of at least
  10 points of praise share (5 points of complaint rate for noise and wifi)
  has posterior probability >= 0.90 and both windows have enough opinions.
"""

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

from hotel_curator.scoring.aggregate import (
    NEG,
    POS,
    attribute_counts,
    signal_columns,
)
from hotel_curator.scoring.grades import (
    AttributeCounts,
    family_of,
    posterior_params,
)
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.taxonomy import Attribute


def _draws(
    counts: AttributeCounts,
    policy: TierPolicy,
    n: int,
    rng: np.random.Generator,
) -> np.ndarray:
    family = family_of(counts.attribute, policy)
    a, b = posterior_params(counts, family, policy)
    return rng.beta(a, b, size=n)


def prob_better(
    a: AttributeCounts, b: AttributeCounts, policy: TierPolicy
) -> float:
    """Posterior probability that hotel A is better than B on an attribute.

    "Better" means a higher share of praise, or a lower complaint rate for
    must-be attributes.

    Raises:
        ValueError: If the two counts are for different attributes.
    """
    if a.attribute is not b.attribute:
        raise ValueError("compare the same attribute")
    rng = np.random.default_rng(policy.seed)
    draws_a = _draws(a, policy, policy.compare.draws, rng)
    draws_b = _draws(b, policy, policy.compare.draws, rng)
    better = (
        draws_a < draws_b
        if policy.is_must_be(a.attribute)
        else (draws_a > draws_b)
    )
    return float(better.mean())


class TrendState(StrEnum):
    """Direction of a confirmed change."""

    IMPROVED = "improved"
    DECLINED = "declined"
    STABLE = "stable"
    TOO_FEW = "too_few"


@dataclass(frozen=True)
class Trend:
    """Recent window versus baseline window for one attribute."""

    attribute: Attribute
    state: TrendState
    recent_mean: float | None
    baseline_mean: float | None
    prob_improved: float
    prob_declined: float


def trend(
    recent: AttributeCounts, baseline: AttributeCounts, policy: TierPolicy
) -> Trend:
    """Classify the change between two windows of the same hotel."""
    must_be = policy.is_must_be(recent.attribute)
    family = family_of(recent.attribute, policy)
    size_recent = recent.reviews if must_be else recent.opinions
    size_base = baseline.reviews if must_be else baseline.opinions
    if min(size_recent, size_base) < policy.trends.min_opinions:
        return Trend(recent.attribute, TrendState.TOO_FEW, None, None, 0, 0)
    rng = np.random.default_rng(policy.seed)
    new = _draws(recent, policy, policy.trends.draws, rng)
    old = _draws(baseline, policy, policy.trends.draws, rng)
    shift = policy.trends.shift(must_be)
    gain = (old - new) if must_be else (new - old)
    p_up = float((gain >= shift).mean())
    p_down = float((gain <= -shift).mean())
    if p_up >= policy.trends.probability:
        state = TrendState.IMPROVED
    elif p_down >= policy.trends.probability:
        state = TrendState.DECLINED
    else:
        state = TrendState.STABLE
    ra, rb = posterior_params(recent, family, policy)
    ba, bb = posterior_params(baseline, family, policy)
    return Trend(
        recent.attribute, state, ra / (ra + rb), ba / (ba + bb), p_up, p_down
    )


def window_counts(
    signals: pd.DataFrame, end: pd.Period, policy: TierPolicy
) -> dict[str, dict[str, dict[Attribute, AttributeCounts]]]:
    """Per hotel, attribute counts in the recent and baseline windows.

    Args:
        signals: Review signals (``aggregate.review_signals``).
        end: Last month of the data window (inclusive).
        policy: Supplies the window lengths.

    Returns:
        ``{hotel_id: {"recent": {...}, "baseline": {...}}}``.
    """
    recent_start = end - (policy.trends.recent_months - 1)
    base_start = recent_start - policy.trends.baseline_months
    window = np.where(
        signals["month"] >= recent_start,
        "recent",
        np.where(signals["month"] >= base_start, "baseline", "older"),
    )
    frame = signals.assign(window=window)
    frame = frame[(frame["window"] != "older") & (frame["month"] <= end)]
    neg = signal_columns(NEG)
    keys = [frame["hotel_id"], frame["window"]]
    sums = frame.groupby(keys)[signal_columns(POS) + neg].sum()
    complaining = (
        (frame[neg] >= policy.complaining_review_probability)
        .groupby(keys)
        .sum()
        .to_dict("index")
    )
    sizes = frame.groupby(keys).size()
    out: dict[str, dict[str, dict[Attribute, AttributeCounts]]] = {}
    rows = sums.to_dict("records")
    for key, row in zip(sums.index, rows, strict=True):
        hotel, name = str(key[0]), str(key[1])
        out.setdefault(hotel, {})[name] = attribute_counts(
            row, int(sizes[key]), complaining[key]
        )
    return out
