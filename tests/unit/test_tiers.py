"""Tier rule truth tables and monotonicity properties (ADR 006, §3, §7)."""

from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from hotel_curator.scoring.grades import AttributeCounts
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.tiers import (
    BlockerKind,
    ReviewSpread,
    Tier,
    assign_tier,
    tier_rank,
)
from hotel_curator.taxonomy import Attribute

POLICY = TierPolicy.load(
    Path(__file__).parents[2] / "config" / "tier_policy.yaml"
)
SPREAD_OK = ReviewSpread(distinct_months=20, max_month_share=0.1)
N = 200


def test_registered_essentials_are_cleanliness_room_staff() -> None:
    assert POLICY.essentials == (
        Attribute.CLEANLINESS,
        Attribute.ROOM,
        Attribute.STAFF,
    )
    assert POLICY.must_be == (Attribute.NOISE, Attribute.WIFI)


def test_policy_rejects_an_essential_that_is_must_be() -> None:
    raw = POLICY.model_dump()
    raw["essentials"] = [Attribute.CLEANLINESS, Attribute.NOISE]
    with pytest.raises(ValidationError, match="must-be"):
        TierPolicy.model_validate(raw)


def _c(
    attribute: Attribute, pos: float, neg: float, n: int = N
) -> AttributeCounts:
    return AttributeCounts(
        attribute, pos, neg, n, int(neg), round(pos), round(neg)
    )


def hotel(
    n: int = N, **overrides: tuple[float, float]
) -> dict[Attribute, AttributeCounts]:
    """Every attribute Strong-ish by default; override (pos, neg) by name."""
    base = {a: (90.0, 2.0) for a in Attribute}
    base[Attribute.NOISE] = (5.0, 1.0)
    base[Attribute.WIFI] = (5.0, 1.0)
    for name, value in overrides.items():
        base[Attribute(name)] = value
    return {a: _c(a, *base[a], n=n) for a in Attribute}


def test_excellent() -> None:
    result = assign_tier(hotel(), N, SPREAD_OK, POLICY)
    assert result.tier is Tier.EXCELLENT
    assert result.next_tier is None
    assert Attribute.LOCATION in result.highlights


def test_not_enough_reviews_first() -> None:
    result = assign_tier(hotel(n=29), 29, SPREAD_OK, POLICY)
    assert result.tier is Tier.NOT_ENOUGH_REVIEWS
    assert result.blockers[0].kind is BlockerKind.MORE_REVIEWS
    assert result.blockers[0].more == 1


def test_caution_needs_hard_label_confirmation() -> None:
    dirty = hotel(cleanliness=(10.0, 40.0))
    assert assign_tier(dirty, N, SPREAD_OK, POLICY).tier is Tier.CAUTION
    soft_only = dirty.copy()
    soft_only[Attribute.CLEANLINESS] = AttributeCounts(
        Attribute.CLEANLINESS, 10.0, 40.0, N, 40, 30, 5
    )
    result = assign_tier(soft_only, N, SPREAD_OK, POLICY)
    assert result.tier is Tier.FAIR  # Weak, but not on hard labels
    assert result.blockers[0].kind is BlockerKind.WEAK_ESSENTIAL


def test_not_enough_evidence_on_thin_essential() -> None:
    result = assign_tier(hotel(staff=(5.0, 1.0)), N, SPREAD_OK, POLICY)
    assert result.tier is Tier.NOT_ENOUGH_EVIDENCE
    assert result.blockers[0].attribute is Attribute.STAFF
    assert result.blockers[0].more == 4


def test_good_with_one_catch_and_for_you() -> None:
    noisy = hotel(noise=(2.0, 40.0))
    result = assign_tier(noisy, N, SPREAD_OK, POLICY)
    assert result.tier is Tier.GOOD
    assert result.catches == [Attribute.NOISE]
    assert result.if_ignored == {Attribute.NOISE: Tier.EXCELLENT}
    kinds = {b.kind for b in result.blockers}
    assert BlockerKind.MATERIAL_WEAK in kinds


def test_good_when_one_essential_unconfirmed() -> None:
    result = assign_tier(hotel(room=(40.0, 10.0)), N, SPREAD_OK, POLICY)
    assert result.tier is Tier.GOOD
    assert result.next_tier is Tier.EXCELLENT
    assert result.blockers[0].attribute is Attribute.ROOM


def test_fair_with_two_catches() -> None:
    result = assign_tier(
        hotel(noise=(2.0, 40.0), wifi=(1.0, 40.0)), N, SPREAD_OK, POLICY
    )
    assert result.tier is Tier.FAIR
    assert sorted(result.catches) == [Attribute.NOISE, Attribute.WIFI]


def test_excellent_needs_review_spread() -> None:
    burst = ReviewSpread(distinct_months=3, max_month_share=0.5)
    result = assign_tier(hotel(), N, burst, POLICY)
    assert result.tier is Tier.GOOD
    assert result.blockers[-1].kind is BlockerKind.REVIEW_SPREAD


def test_strengths_elsewhere_never_raise_the_tier() -> None:
    plain = hotel(room=(40.0, 10.0), staff=(40.0, 10.0))
    assert assign_tier(plain, N, SPREAD_OK, POLICY).tier is Tier.FAIR


def test_sort_score_prefers_more_certain_hotels() -> None:
    small = assign_tier(hotel(n=60), 60, SPREAD_OK, POLICY)
    large = assign_tier(
        {
            a: replace(c, positive=c.positive * 3, negative=c.negative * 3)
            for a, c in hotel(n=600).items()
        },
        600,
        SPREAD_OK,
        POLICY,
    )
    assert large.sort_score > small.sort_score


counts_strategy = st.tuples(
    st.floats(0, 150, allow_nan=False), st.floats(0, 60, allow_nan=False)
)


@settings(max_examples=150, deadline=None)
@given(
    st.dictionaries(
        st.sampled_from(list(Attribute)), counts_strategy, min_size=8
    ),
    st.sampled_from(list(Attribute)),
)
def test_one_more_praise_never_lowers_the_tier(
    values: dict[Attribute, tuple[float, float]], attribute: Attribute
) -> None:
    n = 300
    counts = {a: _c(a, p, min(q, n), n) for a, (p, q) in values.items()}
    before = assign_tier(counts, n, SPREAD_OK, POLICY)
    c = counts[attribute]
    after_counts = dict(counts)
    after_counts[attribute] = replace(
        c, positive=c.positive + 1, positive_hard=c.positive_hard + 1
    )
    after = assign_tier(after_counts, n, SPREAD_OK, POLICY)
    rb, ra = tier_rank(before.tier), tier_rank(after.tier)
    if rb is not None and ra is not None:
        assert ra <= rb


@settings(max_examples=150, deadline=None)
@given(
    st.dictionaries(
        st.sampled_from(list(Attribute)), counts_strategy, min_size=8
    ),
    st.sampled_from(list(Attribute)),
)
def test_one_more_complaint_never_raises_the_tier(
    values: dict[Attribute, tuple[float, float]], attribute: Attribute
) -> None:
    n = 300
    counts = {a: _c(a, p, min(q, n - 1), n) for a, (p, q) in values.items()}
    before = assign_tier(counts, n, SPREAD_OK, POLICY)
    c = counts[attribute]
    after_counts = dict(counts)
    after_counts[attribute] = replace(
        c,
        negative=c.negative + 1,
        negative_hard=c.negative_hard + 1,
        complaining_reviews=c.complaining_reviews + 1,
    )
    after = assign_tier(after_counts, n, SPREAD_OK, POLICY)
    rb, ra = tier_rank(before.tier), tier_rank(after.tier)
    if rb is not None and ra is not None:
        assert ra >= rb
