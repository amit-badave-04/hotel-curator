"""Hotel tiers from attribute grades (ADR 006, section 3).

The essentials (cleanliness, room, staff) decide the tier; a material weak
spot elsewhere can only hold a hotel back; strengths elsewhere never raise
it. The first matching rule wins:

1. ``not_enough_reviews``: fewer than ``min_reviews`` eligible reviews;
2. ``caution``: an essential is Weak, and still Weak when every part is
   counted as a hard label;
3. ``not_enough_evidence``: an essential has too few opinions to grade;
4. ``excellent``: all essentials Strong, no material Weak elsewhere, and the
   reviews spread over enough months;
5. ``good``: at least two essentials Strong, no essential Weak, at most one
   material Weak elsewhere;
6. ``fair``: everything else.

An essential that is Weak on expected counts but not on hard labels does
not trigger Caution, but it does keep the hotel out of Good and Excellent
(ADR 006, erratum E1).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from scipy.stats import beta as beta_dist

from hotel_curator.scoring.grades import (
    AttributeCounts,
    AttributeGrade,
    Grade,
    grade_attribute,
    opinions_to_confirm_strong,
    posterior_params,
)
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.taxonomy import Attribute

SORT_QUANTILE = 0.05


class Tier(StrEnum):
    """Hotel tier; the API enum, best first."""

    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    CAUTION = "caution"
    NOT_ENOUGH_EVIDENCE = "not_enough_evidence"
    NOT_ENOUGH_REVIEWS = "not_enough_reviews"


RANKED_TIERS = (Tier.EXCELLENT, Tier.GOOD, Tier.FAIR, Tier.CAUTION)
"""Tiers that order hotels by quality; the two "not enough" tiers do not."""


class BlockerKind(StrEnum):
    """Why a hotel is not in the next tier up."""

    MORE_REVIEWS = "more_reviews"
    MORE_OPINIONS = "more_opinions"
    CONFIRM_STRONG = "confirm_strong"
    SHARE_TOO_LOW = "share_too_low"
    WEAK_ESSENTIAL = "weak_essential"
    MATERIAL_WEAK = "material_weak"
    REVIEW_SPREAD = "review_spread"


@dataclass(frozen=True)
class Blocker:
    """One condition between a hotel and the next tier up.

    Attributes:
        kind: The condition.
        attribute: The attribute involved, if any.
        more: Opinions or reviews needed at the current rate, if computable.
    """

    kind: BlockerKind
    attribute: Attribute | None = None
    more: int | None = None


@dataclass(frozen=True)
class ReviewSpread:
    """How a hotel's eligible reviews are spread over calendar months."""

    distinct_months: int
    max_month_share: float


@dataclass
class TierResult:
    """A hotel's tier and everything needed to explain it."""

    tier: Tier
    grades: dict[Attribute, AttributeGrade]
    catches: list[Attribute] = field(default_factory=list)
    highlights: list[Attribute] = field(default_factory=list)
    sort_score: float = 0.0
    next_tier: Tier | None = None
    blockers: list[Blocker] = field(default_factory=list)
    if_ignored: dict[Attribute, Tier] = field(default_factory=dict)
    spread: ReviewSpread | None = None


def _essential_weak(
    grade: AttributeGrade, policy: TierPolicy
) -> tuple[bool, bool]:
    """(Weak on expected counts, Weak on hard labels too)."""
    if grade.grade is not Grade.WEAK:
        return False, False
    hard = grade_attribute(grade.counts.hard(), policy)
    return True, hard.grade is Grade.WEAK


def _spread_ok(spread: ReviewSpread, policy: TierPolicy) -> bool:
    return (
        spread.distinct_months >= policy.excellent.min_months
        and spread.max_month_share <= policy.excellent.max_month_share
    )


def _sort_score(
    grades: Mapping[Attribute, AttributeGrade], policy: TierPolicy
) -> float:
    """Lowest 5th percentile of the essentials' share posteriors."""
    lows = []
    for attribute in policy.essentials:
        grade = grades[attribute]
        a, b = posterior_params(grade.counts, grade.family, policy)
        lows.append(float(beta_dist.ppf(SORT_QUANTILE, a, b)))
    return min(lows)


def _strength_blockers(
    grades: Mapping[Attribute, AttributeGrade],
    attributes: list[Attribute],
    policy: TierPolicy,
) -> list[Blocker]:
    out = []
    for attribute in attributes:
        k = opinions_to_confirm_strong(grades[attribute].counts, policy)
        kind = (
            BlockerKind.SHARE_TOO_LOW
            if k is None
            else BlockerKind.CONFIRM_STRONG
        )
        out.append(Blocker(kind, attribute, k))
    return out


GOOD_MIN_STRONG_ESSENTIALS = 2
GOOD_MAX_CATCHES = 1
"""Rule 5 of ADR 006: Good needs two Strong essentials, at most one catch."""

Verdict = tuple[Tier, list[Blocker]]


def _catches(
    grades: Mapping[Attribute, AttributeGrade],
    policy: TierPolicy,
    ignore: frozenset[Attribute],
) -> list[Attribute]:
    """Material Weak grades outside the essentials (not ignored)."""
    return [
        a
        for a, g in grades.items()
        if a not in policy.essentials
        and a not in ignore
        and g.grade is Grade.WEAK
        and g.material
    ]


def _evidence_tier(
    grades: Mapping[Attribute, AttributeGrade],
    reviews: int,
    policy: TierPolicy,
) -> Verdict | None:
    """Rules 1-3: too few reviews, Caution, too little evidence."""
    if reviews < policy.min_reviews:
        more = policy.min_reviews - reviews
        return Tier.NOT_ENOUGH_REVIEWS, [
            Blocker(BlockerKind.MORE_REVIEWS, None, more)
        ]
    essentials = list(policy.essentials)
    hard_weak = [a for a in essentials if _essential_weak(grades[a], policy)[1]]
    if hard_weak:
        return Tier.CAUTION, [
            Blocker(BlockerKind.WEAK_ESSENTIAL, a) for a in hard_weak
        ]
    thin = [a for a in essentials if grades[a].grade is Grade.TOO_FEW]
    if thin:
        return Tier.NOT_ENOUGH_EVIDENCE, [
            Blocker(
                BlockerKind.MORE_OPINIONS,
                a,
                max(
                    1,
                    math.ceil(policy.min_opinions - grades[a].counts.opinions),
                ),
            )
            for a in thin
        ]
    return None


def _quality_tier(
    grades: Mapping[Attribute, AttributeGrade],
    spread: ReviewSpread,
    catches: list[Attribute],
    policy: TierPolicy,
) -> Verdict:
    """Rules 4-6: Excellent, Good or Fair, with what blocks the next tier."""
    essentials = list(policy.essentials)
    strong = [a for a in essentials if grades[a].grade is Grade.STRONG]
    not_strong = [a for a in essentials if a not in strong]
    soft_weak = [a for a in essentials if grades[a].grade is Grade.WEAK]
    spread_ok = _spread_ok(spread, policy)
    catch_blockers = [Blocker(BlockerKind.MATERIAL_WEAK, a) for a in catches]
    if not not_strong and not catches and spread_ok:
        return Tier.EXCELLENT, []
    if (
        len(strong) >= GOOD_MIN_STRONG_ESSENTIALS
        and not soft_weak
        and len(catches) <= GOOD_MAX_CATCHES
    ):
        blockers = _strength_blockers(grades, not_strong, policy)
        blockers += catch_blockers
        if not spread_ok:
            blockers.append(Blocker(BlockerKind.REVIEW_SPREAD))
        return Tier.GOOD, blockers
    blockers = [Blocker(BlockerKind.WEAK_ESSENTIAL, a) for a in soft_weak]
    needed = max(0, GOOD_MIN_STRONG_ESSENTIALS - len(strong))
    candidates = sorted(
        (a for a in not_strong if a not in soft_weak),
        key=lambda a: -grades[a].mean,
    )
    blockers += _strength_blockers(grades, candidates[:needed], policy)
    if len(catches) > GOOD_MAX_CATCHES:
        blockers += catch_blockers
    return Tier.FAIR, blockers


def classify(
    grades: Mapping[Attribute, AttributeGrade],
    reviews: int,
    spread: ReviewSpread,
    policy: TierPolicy,
    *,
    ignore: frozenset[Attribute] = frozenset(),
) -> tuple[Tier, list[Attribute], list[Blocker]]:
    """Apply the tier rules; return the tier, catches and blockers.

    Args:
        grades: One grade per attribute.
        reviews: Eligible reviews of the hotel.
        spread: Month spread of those reviews.
        policy: Tier policy.
        ignore: Non-essential attributes the guest does not care about.
    """
    catches = _catches(grades, policy, ignore)
    verdict = _evidence_tier(grades, reviews, policy)
    tier, blockers = verdict or _quality_tier(grades, spread, catches, policy)
    return tier, catches, blockers


def assign_tier(
    counts: Mapping[Attribute, AttributeCounts],
    reviews: int,
    spread: ReviewSpread,
    policy: TierPolicy,
) -> TierResult:
    """Grade every attribute of a hotel and assign its tier.

    Raises:
        KeyError: If an attribute has no counts.
    """
    grades = {a: grade_attribute(counts[a], policy) for a in Attribute}
    tier, catches, blockers = classify(grades, reviews, spread, policy)
    essentials = set(policy.essentials)
    result = TierResult(
        tier=tier,
        grades=grades,
        catches=catches,
        highlights=[
            a
            for a, g in grades.items()
            if a not in essentials and g.grade is Grade.STRONG
        ],
        sort_score=_sort_score(grades, policy),
        blockers=blockers,
        spread=spread,
    )
    if tier in RANKED_TIERS and tier is not Tier.EXCELLENT:
        result.next_tier = RANKED_TIERS[RANKED_TIERS.index(tier) - 1]
    for attribute in catches:
        relaxed, _, _ = classify(
            grades, reviews, spread, policy, ignore=frozenset({attribute})
        )
        if relaxed is not tier:
            result.if_ignored[attribute] = relaxed
    return result


def tier_rank(tier: Tier) -> int | None:
    """0 for Excellent ... 3 for Caution; ``None`` for "not enough" tiers."""
    return RANKED_TIERS.index(tier) if tier in RANKED_TIERS else None
