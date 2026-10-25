"""Validation of the tier scheme on real data (ADR 006, section 7).

Each function answers one question with numbers that go into
``docs/model/TIERS.md``; none of them feeds back into the policy.

* :func:`out_of_time`: do grades from 2015-08..2016-12 predict 2017?
* :func:`external_agreement`: how do tiers relate to the site's own average
  score, next to simple alternatives?
* :func:`stability`: how often does a hotel keep its tier when its reviews
  are resampled and its labels redrawn?
* :func:`simulate_false_claims`: with the classifier's measured behaviour,
  how often is a claim (Strong or Weak) false?
* :func:`sensitivity`: how does the tier mix move if the lines move?
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.stats import spearmanr

from hotel_curator.scoring.aggregate import (
    NEG,
    POS,
    HotelEvidence,
    hotel_evidence,
)
from hotel_curator.scoring.grades import AttributeCounts, Grade, grade_attribute
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.tiers import RANKED_TIERS, Tier, assign_tier
from hotel_curator.taxonomy import Attribute

FloatArray = npt.NDArray[np.float64]
MIN_LATER_OPINIONS = 5
"""Later-window evidence a hotel needs to count: opinions for a share of
praise, reviews for a complaint rate (noise, wifi)."""
MIN_PAIRS = 10


def spearman_ci(
    x: npt.ArrayLike, y: npt.ArrayLike, *, n_boot: int, seed: int
) -> tuple[float, float, float, int]:
    """Spearman's rho with a 95% bootstrap interval over pairs.

    Returns:
        ``(rho, low, high, n)``; NaN when there are fewer than
        ``MIN_PAIRS`` complete pairs or either side is constant (rho is
        undefined). Resamples that come out constant are left out of the
        interval.
    """
    xa, ya = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    keep = ~(np.isnan(xa) | np.isnan(ya))
    xa, ya = xa[keep], ya[keep]
    n = len(xa)
    if n < MIN_PAIRS or np.ptp(xa) == 0 or np.ptp(ya) == 0:
        return float("nan"), float("nan"), float("nan"), n
    rho = float(spearmanr(xa, ya).statistic)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        if np.ptp(xa[idx]) > 0 and np.ptp(ya[idx]) > 0:
            boots.append(spearmanr(xa[idx], ya[idx]).statistic)
    if not boots:
        return rho, float("nan"), float("nan"), n
    low, high = np.nanquantile(boots, [0.025, 0.975])
    return rho, float(low), float(high), n


def tier_score(tier: Tier, sort_score: float) -> float:
    """One number per hotel that orders hotels the way the product does."""
    if tier not in RANKED_TIERS:
        return float("nan")
    return (len(RANKED_TIERS) - RANKED_TIERS.index(tier)) + sort_score


@dataclass(frozen=True)
class OutOfTimeRow:
    """One attribute: early posterior mean versus 2017 raw share."""

    attribute: Attribute
    rho: float
    low: float
    high: float
    hotels: int


def _late_totals(
    late: pd.DataFrame,
) -> tuple[dict[str, dict[str, float]], dict[str, int]]:
    """Per hotel, summed signals and review counts of the later window."""
    columns = [f"{p}_{a.value}" for p in (POS, NEG) for a in Attribute]
    sums = {
        str(k): {str(c): float(x) for c, x in v.items()}
        for k, v in late.groupby("hotel_id")[columns]
        .sum()
        .to_dict("index")
        .items()
    }
    sizes = {str(k): int(v) for k, v in late.groupby("hotel_id").size().items()}
    return sums, sizes


def _attribute_out_of_time(
    attribute: Attribute,
    early: Mapping[str, Any],
    late: tuple[dict[str, dict[str, float]], dict[str, int]],
    policy: TierPolicy,
    n_boot: int,
) -> OutOfTimeRow:
    """Early posterior mean versus later raw share for one attribute."""
    sums, sizes = late
    must_be = policy.is_must_be(attribute)
    early_mean: list[float] = []
    later: list[float] = []
    for hotel_id, ev in early.items():
        if hotel_id not in sums:
            continue
        mean = grade_attribute(ev.counts[attribute], policy).mean
        p = sums[hotel_id][f"{POS}_{attribute.value}"]
        n_neg = sums[hotel_id][f"{NEG}_{attribute.value}"]
        if must_be:  # lower complaint rate is better: flip both signs
            n = sizes[hotel_id]
            enough = n >= MIN_LATER_OPINIONS
            later.append(-n_neg / n if enough else float("nan"))
            early_mean.append(-mean)
        else:
            enough = p + n_neg >= MIN_LATER_OPINIONS
            later.append(p / (p + n_neg) if enough else float("nan"))
            early_mean.append(mean)
    rho, low, high, n = spearman_ci(
        early_mean, later, n_boot=n_boot, seed=policy.seed
    )
    return OutOfTimeRow(attribute, rho, low, high, n)


def out_of_time(
    signals: pd.DataFrame,
    reviews: pd.DataFrame,
    hotel_ids: set[str],
    policy: TierPolicy,
    *,
    split_month: str = "2016-12",
    n_boot: int = 500,
) -> tuple[list[OutOfTimeRow], pd.DataFrame]:
    """Grades from data up to ``split_month`` versus what happened later.

    Returns:
        Per-attribute Spearman correlations between the early posterior mean
        and the later raw share (complaint rate for must-be attributes,
        reported with the sign flipped so higher is better throughout), and
        a table of later mean reviewer score by early tier.
    """
    cut = pd.Period(split_month, "M")
    own = signals[signals["hotel_id"].isin(hotel_ids)]
    early = hotel_evidence(own[own["month"] <= cut], policy)
    late = _late_totals(own[own["month"] > cut])
    rows = [
        _attribute_out_of_time(a, early, late, policy, n_boot)
        for a in Attribute
    ]
    months = pd.to_datetime(reviews["review_date"]).dt.to_period("M")
    later_score = (
        reviews[reviews["hotel_id"].isin(hotel_ids) & (months > cut)]
        .groupby("hotel_id")["reviewer_score"]
        .mean()
    )
    tiers = {
        h: assign_tier(ev.counts, ev.reviews, ev.spread, policy).tier.value
        for h, ev in early.items()
    }
    table = (
        pd.DataFrame({"tier": pd.Series(tiers), "later_score": later_score})
        .dropna()
        .groupby("tier")["later_score"]
        .agg(["count", "mean", "std"])
    )
    return rows, table


def bernoulli_tiers(
    signals: pd.DataFrame,
    policy: TierPolicy,
    rng: np.random.Generator,
) -> dict[str, Tier]:
    """Tiers after resampling each hotel's reviews and redrawing labels."""
    idx = (
        signals.groupby("hotel_id")
        .sample(frac=1.0, replace=True, random_state=int(rng.integers(2**31)))
        .index
    )
    sample = signals.loc[idx].reset_index(drop=True)
    for prefix in (POS, NEG):
        for a in Attribute:
            column = f"{prefix}_{a.value}"
            drawn = rng.random(len(sample)) < sample[column].to_numpy()
            sample[column] = drawn.astype(float)
            sample[f"{prefix}_hard_{a.value}"] = drawn
    evidence = hotel_evidence(sample, policy)
    return {
        h: assign_tier(ev.counts, ev.reviews, ev.spread, policy).tier
        for h, ev in evidence.items()
    }


def stability(
    signals: pd.DataFrame,
    tiers: Mapping[str, Tier],
    policy: TierPolicy,
    *,
    replicates: int,
) -> pd.DataFrame:
    """Share of replicates in which each hotel keeps its tier."""
    rng = np.random.default_rng(policy.seed)
    kept = {h: 0 for h in tiers}
    for _ in range(replicates):
        for hotel_id, tier in bernoulli_tiers(signals, policy, rng).items():
            kept[hotel_id] += int(tier is tiers[hotel_id])
    return pd.DataFrame(
        {
            "tier": {h: t.value for h, t in tiers.items()},
            "kept": {h: kept[h] / replicates for h in tiers},
        }
    )


@dataclass(frozen=True)
class SimulationRow:
    """False-claim rates in one volume stratum."""

    reviews: int
    true_share: float
    claim: str
    rate: float
    hotels: int


SIMULATION_MARGIN = 0.02
"""True share used in the simulation: this far on the safe side of a line."""


def _synthetic_counts(
    pools: Mapping[str, tuple[FloatArray, FloatArray]],
    n: int,
    mention_rate: float,
    share: float,
    policy: TierPolicy,
    rng: np.random.Generator,
) -> AttributeCounts:
    """One synthetic hotel's counts with classifier-like probabilities."""
    mentions = rng.random(n) < mention_rate
    praise = mentions & (rng.random(n) < share)
    complaint = mentions & ~praise
    pos_true, pos_false = pools["positive"]
    neg_true, neg_false = pools["negative"]
    p_pos = np.where(praise, rng.choice(pos_true, n), rng.choice(pos_false, n))
    p_neg = np.where(
        complaint, rng.choice(neg_true, n), rng.choice(neg_false, n)
    )
    hard = policy.hard_label_probability
    return AttributeCounts(
        Attribute.ROOM,
        float(p_pos.sum()),
        float(p_neg.sum()),
        n,
        int((p_neg >= policy.complaining_review_probability).sum()),
        int((p_pos >= hard).sum()),
        int((p_neg >= hard).sum()),
    )


def simulate_false_claims(
    pools: Mapping[str, tuple[FloatArray, FloatArray]],
    volumes: list[int],
    mention_rate: float,
    policy: TierPolicy,
    *,
    hotels_per_cell: int = 400,
) -> list[SimulationRow]:
    """Rate of false Strong / false Weak grades for a two-sided attribute.

    Synthetic hotels have ``n`` reviews; each review mentions the attribute
    with probability ``mention_rate`` and, if it does, praises it with the
    true share. The "model" then reports a calibrated probability drawn
    from the classifier's gold-dev outputs for reviews whose gold label was
    positive (or negative): ``pools[label] = (scores when gold is true,
    scores when gold is false)``. This carries the classifier's real errors
    into the grades.

    False Strong: graded Strong although the true share is below the Strong
    line. False Weak: graded Weak although the true share is at or above
    the Weak line.
    """
    rng = np.random.default_rng(policy.seed)
    cases = (
        ("strong", Grade.STRONG, policy.two_sided.strong - SIMULATION_MARGIN),
        ("weak", Grade.WEAK, policy.two_sided.weak + SIMULATION_MARGIN),
    )
    rows = []
    for n in volumes:
        for claim, wrong, share in cases:
            false_claims = sum(
                grade_attribute(
                    _synthetic_counts(
                        pools, n, mention_rate, share, policy, rng
                    ),
                    policy,
                ).grade
                is wrong
                for _ in range(hotels_per_cell)
            )
            rows.append(
                SimulationRow(
                    n,
                    share,
                    claim,
                    false_claims / hotels_per_cell,
                    hotels_per_cell,
                )
            )
    return rows


def sensitivity(
    evidence: Mapping[str, HotelEvidence],
    policy: TierPolicy,
    variants: Mapping[str, Callable[[TierPolicy], TierPolicy]],
) -> pd.DataFrame:
    """Tier mix under policy variants (reported, never used)."""
    out = {}
    for name, change in {"as registered": lambda p: p, **variants}.items():
        varied = change(policy)
        tiers = [
            assign_tier(ev.counts, ev.reviews, ev.spread, varied).tier.value
            for ev in evidence.values()
        ]
        out[name] = pd.Series(tiers).value_counts()
    return pd.DataFrame(out).fillna(0).astype(int)


def hotel_orderings(
    results: Mapping[str, Any],
    signals: pd.DataFrame,
    eligible: pd.DataFrame,
) -> dict[str, pd.Series]:
    """Three ways to order hotels: v2 tiers, the v1 formula, a text baseline."""
    grouped = signals.groupby("hotel_id")
    pos = grouped[[f"{POS}_{a.value}" for a in Attribute]].sum().to_numpy()
    neg = grouped[[f"{NEG}_{a.value}" for a in Attribute]].sum().to_numpy()
    n = grouped.size()
    v1_formula = (pos - neg).mean(axis=1) / n.to_numpy()
    return {
        "v2 tiers (tier, then cautious score)": pd.Series(
            {h: tier_score(r.tier, r.sort_score) for h, r in results.items()}
        ),
        "v1 formula on v2 labels": pd.Series(v1_formula, index=n.index),
        "share of reviews with nothing disliked": eligible["disliked_text"]
        .isna()
        .groupby(eligible["hotel_id"])
        .mean(),
    }


def external_agreement_table(
    results: Mapping[str, Any],
    signals: pd.DataFrame,
    eligible: pd.DataFrame,
    hotels: pd.DataFrame,
    policy: TierPolicy,
    *,
    n_boot: int = 500,
) -> pd.DataFrame:
    """Spearman correlation of three hotel orderings with the site's score.

    The site's ``Average_Score`` is an independent check (the scoring code
    never reads it). Next to the v2 ordering: the v1 formula (mean over
    attributes of praise rate minus complaint rate) applied to the same v2
    labels, and a trivial text baseline (the share of reviews that left the
    Disliked box empty), so the correlation can be judged against what
    simple alternatives achieve. The v2 ordering covers hotels with a
    ranked tier only; the others cover every hotel.
    """
    site = hotels.set_index("hotel_id")["source_average_score"].rename("y")
    rows = []
    for name, values in hotel_orderings(results, signals, eligible).items():
        joined = pd.concat([values.rename("x"), site], axis=1)
        rho, low, high, count = spearman_ci(
            joined["x"], joined["y"], n_boot=n_boot, seed=policy.seed
        )
        rows.append(
            {
                "ordering": name,
                "rho": rho,
                "low": low,
                "high": high,
                "hotels": count,
            }
        )
    return pd.DataFrame(rows)
