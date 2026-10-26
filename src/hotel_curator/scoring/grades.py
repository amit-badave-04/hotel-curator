"""Attribute grades: Strong, Weak, OK or Too few opinions (ADR 006, §2).

Two families of attributes are measured differently:

* **two-sided** (cleanliness, room, staff, location, value, food): the share
  of opinions that are praise, ``theta = P / (P + N)``;
* **must-be** (noise, wifi): the complaint rate among all eligible reviews,
  ``q = N / n``, because guests mostly mention these only when they fail.

A claim (Strong or Weak) is made only when the Beta posterior puts at least
``policy.confirmation`` (0.95) of its mass beyond the line. Counts are
expected counts (sums of calibrated probabilities), so they may be
fractional. Every number here can be recomputed by hand from ``P``, ``N``
and ``n`` with a spreadsheet's ``BETA.DIST`` / ``BETA.INV``; the grade also
needs ``complaining_reviews``, because Weak needs at least
``policy.min_complaining_reviews`` of them.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from scipy.stats import beta as beta_dist

from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.taxonomy import Attribute

Family = Literal["two_sided", "must_be"]
MAX_EXTRA_OPINIONS = 1_000_000


class Grade(StrEnum):
    """Attribute grade; the API enum."""

    STRONG = "strong"
    WEAK = "weak"
    OK = "ok"
    TOO_FEW = "too_few"


class Lean(StrEnum):
    """Display-only hint for an OK grade whose mean is past a line."""

    TRENDING_STRONG = "trending_strong"
    TRENDING_WEAK = "trending_weak"


@dataclass(frozen=True)
class AttributeCounts:
    """Per-hotel evidence for one attribute.

    Attributes:
        attribute: The attribute.
        positive: ``P``, expected number of reviews praising it.
        negative: ``N``, expected number of reviews complaining about it.
        reviews: ``n``, eligible reviews of the hotel.
        complaining_reviews: Reviews with complaint probability >= 0.5.
        positive_hard: Reviews with a part labelled positive at p >= 0.5.
        negative_hard: Reviews with a part labelled negative at p >= 0.5.
    """

    attribute: Attribute
    positive: float
    negative: float
    reviews: int
    complaining_reviews: int
    positive_hard: int
    negative_hard: int

    def __post_init__(self) -> None:
        if min(self.positive, self.negative) < 0 or self.reviews < 0:
            raise ValueError("counts must be non-negative")
        if self.negative > self.reviews + 1e-9:
            raise ValueError("more complaints than reviews")

    @property
    def opinions(self) -> float:
        """``M = P + N``."""
        return self.positive + self.negative

    @property
    def complaint_rate(self) -> float:
        """``N / n`` (0 when there are no reviews)."""
        return self.negative / self.reviews if self.reviews else 0.0

    def hard(self) -> "AttributeCounts":
        """The same evidence with every part counted as a hard label."""
        return AttributeCounts(
            self.attribute,
            float(self.positive_hard),
            float(self.negative_hard),
            self.reviews,
            self.complaining_reviews,
            self.positive_hard,
            self.negative_hard,
        )


@dataclass(frozen=True)
class AttributeGrade:
    """The grade and the numbers behind it, as shown in the API."""

    attribute: Attribute
    family: Family
    grade: Grade
    lean: Lean | None
    mean: float
    low: float
    high: float
    prob_strong: float
    prob_weak: float
    counts: AttributeCounts
    material: bool

    @property
    def confirmed(self) -> bool:
        """True for Strong and Weak."""
        return self.grade in (Grade.STRONG, Grade.WEAK)


def posterior_params(
    counts: AttributeCounts, family: Family, policy: TierPolicy
) -> tuple[float, float]:
    """Beta posterior parameters for the attribute's measure."""
    a0, b0 = policy.prior.alpha, policy.prior.beta
    if family == "two_sided":
        return a0 + counts.positive, b0 + counts.negative
    return a0 + counts.negative, b0 + counts.reviews - counts.negative


def _lean(family: Family, mean: float, policy: TierPolicy) -> Lean | None:
    if family == "two_sided":
        if mean >= policy.two_sided.strong:
            return Lean.TRENDING_STRONG
        if mean < policy.two_sided.weak:
            return Lean.TRENDING_WEAK
        return None
    if mean <= policy.must_be_lines.strong:
        return Lean.TRENDING_STRONG
    if mean >= policy.must_be_lines.weak:
        return Lean.TRENDING_WEAK
    return None


def grade_attribute(
    counts: AttributeCounts, policy: TierPolicy
) -> AttributeGrade:
    """Grade one attribute of one hotel.

    Two-sided: Strong if P(theta >= strong line) >= confirmation, Weak if
    P(theta < weak line) >= confirmation; too few below ``min_opinions``.
    Must-be: Strong if P(q <= strong rate) >= confirmation, Weak if
    P(q >= weak rate) >= confirmation; too few below ``min_reviews``.
    Weak also needs ``min_complaining_reviews`` distinct complaining reviews.
    """
    family = family_of(counts.attribute, policy)
    a, b = posterior_params(counts, family, policy)
    tail = (1 - policy.interval_level) / 2
    mean = a / (a + b)
    low = float(beta_dist.ppf(tail, a, b))
    high = float(beta_dist.ppf(1 - tail, a, b))
    if family == "two_sided":
        prob_strong = float(beta_dist.sf(policy.two_sided.strong, a, b))
        prob_weak = float(beta_dist.cdf(policy.two_sided.weak, a, b))
        enough = counts.opinions >= policy.min_opinions
    else:
        prob_strong = float(beta_dist.cdf(policy.must_be_lines.strong, a, b))
        prob_weak = float(beta_dist.sf(policy.must_be_lines.weak, a, b))
        enough = counts.reviews >= policy.min_reviews
    weak_allowed = counts.complaining_reviews >= policy.min_complaining_reviews
    if not enough:
        grade = Grade.TOO_FEW
    elif prob_strong >= policy.confirmation:
        grade = Grade.STRONG
    elif prob_weak >= policy.confirmation and weak_allowed:
        grade = Grade.WEAK
    else:
        grade = Grade.OK
    return AttributeGrade(
        attribute=counts.attribute,
        family=family,
        grade=grade,
        lean=_lean(family, mean, policy) if grade is Grade.OK else None,
        mean=mean,
        low=low,
        high=high,
        prob_strong=prob_strong,
        prob_weak=prob_weak,
        counts=counts,
        material=counts.complaint_rate >= policy.materiality,
    )


def family_of(attribute: Attribute, policy: TierPolicy) -> Family:
    """``must_be`` for complaint-rate attributes, else ``two_sided``."""
    return "must_be" if policy.is_must_be(attribute) else "two_sided"


def _extrapolate(
    counts: AttributeCounts, family: Family, rate: float, k: int
) -> AttributeCounts:
    """``counts`` plus ``k`` more opinions (reviews) at ``rate``."""
    if family == "two_sided":
        positive = counts.positive + rate * k
        negative = counts.negative + (1 - rate) * k
    else:
        positive = counts.positive
        negative = counts.negative + rate * k
    return AttributeCounts(
        counts.attribute,
        positive,
        negative,
        counts.reviews + k,
        counts.complaining_reviews,
        counts.positive_hard,
        counts.negative_hard,
    )


def smallest_true(predicate: Callable[[int], bool], limit: int) -> int | None:
    """Smallest ``k`` in ``[0, limit]`` with ``predicate(k)``.

    ``predicate`` must be monotone (false, then true). Doubling then
    bisection: O(log k) calls.
    """
    if predicate(0):
        return 0
    high = 1
    while not predicate(high):
        if high >= limit:
            return None
        high = min(2 * high, limit)  # never skip past the limit itself
    low = high // 2
    while high - low > 1:
        mid = (low + high) // 2
        low, high = (low, mid) if predicate(mid) else (mid, high)
    return high


def opinions_to_confirm_strong(
    counts: AttributeCounts, policy: TierPolicy
) -> int | None:
    """More opinions needed, at the current rate, to confirm Strong.

    For a two-sided attribute "opinions" are praise-or-complaint mentions at
    the current share of praise; for a must-be attribute they are reviews at
    the current complaint rate. Returns ``None`` when the current rate is
    not past the Strong line (more of the same would never confirm it),
    and 0 when it is already confirmed.
    """
    family = family_of(counts.attribute, policy)
    if family == "two_sided":
        total = counts.opinions
        rate = counts.positive / total if total else 0.0
        past_line = rate > policy.two_sided.strong
    else:
        total = float(counts.reviews)
        rate = counts.complaint_rate
        past_line = rate < policy.must_be_lines.strong
    if total == 0 or not past_line:
        return None
    return smallest_true(
        lambda k: (
            grade_attribute(_extrapolate(counts, family, rate, k), policy).grade
            is Grade.STRONG
        ),
        MAX_EXTRA_OPINIONS,
    )
