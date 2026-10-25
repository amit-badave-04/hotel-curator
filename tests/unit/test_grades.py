"""Truth tables for attribute grades (ADR 006, section 2)."""

from pathlib import Path

import pytest
from scipy.stats import beta as beta_dist

from hotel_curator.scoring.grades import (
    AttributeCounts,
    Grade,
    Lean,
    grade_attribute,
    opinions_to_confirm_strong,
)
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.taxonomy import Attribute

POLICY = TierPolicy.load(
    Path(__file__).parents[2] / "config" / "tier_policy.yaml"
)


def counts(
    attribute: Attribute,
    pos: float,
    neg: float,
    reviews: int = 100,
    complaining: int | None = None,
) -> AttributeCounts:
    return AttributeCounts(
        attribute,
        pos,
        neg,
        reviews,
        int(neg) if complaining is None else complaining,
        int(pos),
        int(neg),
    )


def test_policy_file_matches_adr_values() -> None:
    assert POLICY.confirmation == 0.95
    assert (POLICY.two_sided.strong, POLICY.two_sided.weak) == (0.8, 0.5)
    assert (POLICY.must_be_lines.strong, POLICY.must_be_lines.weak) == (
        0.05,
        0.10,
    )
    assert (POLICY.min_opinions, POLICY.min_reviews) == (10, 30)
    assert (POLICY.prior.alpha, POLICY.prior.beta) == (0.5, 0.5)


@pytest.mark.parametrize(
    ("pos", "neg", "expected"),
    [
        (10, 0, Grade.STRONG),  # unanimous at the minimum
        (9, 0, Grade.TOO_FEW),  # below the minimum (see ADR erratum)
        (95, 5, Grade.STRONG),
        (80, 20, Grade.OK),  # mean on the line, not confirmed
        (20, 40, Grade.WEAK),
        (45, 55, Grade.OK),
    ],
)
def test_two_sided_truth_table(pos: float, neg: float, expected: Grade) -> None:
    assert (
        grade_attribute(counts(Attribute.STAFF, pos, neg), POLICY).grade
        is expected
    )


def test_two_sided_probabilities_match_beta() -> None:
    graded = grade_attribute(counts(Attribute.ROOM, 30.4, 6.2), POLICY)
    a, b = 30.9, 6.7
    assert graded.prob_strong == pytest.approx(beta_dist.sf(0.8, a, b))
    assert graded.prob_weak == pytest.approx(beta_dist.cdf(0.5, a, b))
    assert graded.mean == pytest.approx(a / (a + b))
    assert graded.low == pytest.approx(beta_dist.ppf(0.05, a, b))


def test_weak_needs_three_complaining_reviews() -> None:
    few = counts(Attribute.CLEANLINESS, 2, 20, complaining=2)
    assert grade_attribute(few, POLICY).grade is Grade.OK
    enough = counts(Attribute.CLEANLINESS, 2, 20, complaining=3)
    assert grade_attribute(enough, POLICY).grade is Grade.WEAK


@pytest.mark.parametrize(
    ("neg", "reviews", "expected"),
    [
        (0, 40, Grade.STRONG),  # P(q <= 5%) = 0.958
        (0, 30, Grade.OK),  # P(q <= 5%) = 0.922
        (0, 29, Grade.TOO_FEW),
        (6, 30, Grade.WEAK),  # P(q >= 10%) = 0.956
        (5, 30, Grade.OK),
    ],
)
def test_must_be_truth_table(neg: float, reviews: int, expected: Grade) -> None:
    graded = grade_attribute(counts(Attribute.NOISE, 3, neg, reviews), POLICY)
    assert graded.family == "must_be"
    assert graded.grade is expected


def test_lean_only_for_ok_grades() -> None:
    near = grade_attribute(counts(Attribute.VALUE, 9.0, 1.0, 50), POLICY)
    assert near.grade is Grade.OK
    assert near.lean is Lean.TRENDING_STRONG
    strong = grade_attribute(counts(Attribute.VALUE, 50, 2), POLICY)
    assert strong.lean is None


def test_material_is_complaint_rate() -> None:
    assert grade_attribute(counts(Attribute.FOOD, 0, 5, 100), POLICY).material
    assert not grade_attribute(
        counts(Attribute.FOOD, 0, 4.9, 100), POLICY
    ).material


def test_counts_validation() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        counts(Attribute.ROOM, -1, 0)
    with pytest.raises(ValueError, match="more complaints"):
        counts(Attribute.ROOM, 0, 11, reviews=10)


def test_opinions_to_confirm_strong() -> None:
    already = counts(Attribute.STAFF, 95, 5)
    assert opinions_to_confirm_strong(already, POLICY) == 0
    close = counts(Attribute.STAFF, 17, 3)  # 85% praise, not yet confirmed
    k = opinions_to_confirm_strong(close, POLICY)
    assert k is not None and k > 0
    rate = 17 / 20
    more = counts(Attribute.STAFF, 17 + rate * k, 3 + (1 - rate) * k)
    less = counts(
        Attribute.STAFF, 17 + rate * (k - 1), 3 + (1 - rate) * (k - 1)
    )
    assert grade_attribute(more, POLICY).grade is Grade.STRONG
    assert grade_attribute(less, POLICY).grade is not Grade.STRONG
    assert (
        opinions_to_confirm_strong(counts(Attribute.STAFF, 8, 2), POLICY)
        is None
    )
    quiet = counts(Attribute.WIFI, 0, 0, reviews=30)
    assert opinions_to_confirm_strong(quiet, POLICY) == 8


@pytest.mark.parametrize("answer", [0, 1, 7, 524_289, 999_999, 1_000_000])
def test_smallest_true_finds_answers_up_to_the_limit(answer: int) -> None:
    from hotel_curator.scoring.grades import smallest_true

    assert smallest_true(lambda k: k >= answer, 1_000_000) == answer


def test_smallest_true_is_none_past_the_limit() -> None:
    from hotel_curator.scoring.grades import smallest_true

    assert smallest_true(lambda k: k >= 1_000_001, 1_000_000) is None
