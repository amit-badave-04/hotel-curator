"""Tier rule truth tables and monotonicity properties (ADR 006, §3, §7)."""

from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from hotel_curator.scoring.grades import (
    AttributeCounts,
    Grade,
    grade_attribute,
)
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.tiers import (
    Blocker,
    BlockerKind,
    ReviewSpread,
    Tier,
    TierResult,
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


def _floats(low: float, high: float) -> st.SearchStrategy[float]:
    return st.floats(low, high, allow_nan=False)


MAX_HARD_OFFSET = 2
"""Hard-label counts are drawn up to 2 away from the rounded expected counts,
praise and complaints each on their own: a part at p = 0.6 adds one hard
label but only 0.6 to the expected count, so the two can fall on different
sides of the opinions floor."""

Draw = tuple[tuple[float, float], tuple[int, int]]
"""Expected (praise, complaints) and the offsets of their hard-label counts."""

expected_strategy = st.one_of(
    st.tuples(_floats(60, 150), _floats(0, 6)),  # Strong-ish
    st.tuples(_floats(0, 12), _floats(0, 12)),  # around the opinions floor
    st.tuples(_floats(0, 150), _floats(0, 60)),
)
hard_offset_strategy = st.integers(-MAX_HARD_OFFSET, MAX_HARD_OFFSET)
counts_strategy = st.tuples(
    expected_strategy, st.tuples(hard_offset_strategy, hard_offset_strategy)
)
hotels_strategy = st.dictionaries(
    st.sampled_from(list(Attribute)), counts_strategy, min_size=8
)
reviews_strategy = st.integers(POLICY.min_reviews, 400)
"""Review counts from the tier floor up: at small n, fewer than 10 opinions
can be a material complaint rate (N / n >= 5%)."""
OPTIONAL_TWO_SIDED = [
    a
    for a in Attribute
    if a not in POLICY.essentials and not POLICY.is_must_be(a)
]


def _hard(expected: float, offset: int, n: int) -> int:
    """A hard-label count near ``expected``, from 0 to ``n - 1``."""
    return min(n - 1, max(0, round(expected) + offset))


def _drawn(
    values: dict[Attribute, Draw], n: int
) -> dict[Attribute, AttributeCounts]:
    out: dict[Attribute, AttributeCounts] = {}
    for a, ((p, q), (dp, dq)) in values.items():
        neg = min(q, n - 1)
        out[a] = AttributeCounts(
            a, p, neg, n, int(neg), _hard(p, dp, n), _hard(neg, dq, n)
        )
    return out


def _praised(
    counts: dict[Attribute, AttributeCounts], attribute: Attribute
) -> dict[Attribute, AttributeCounts]:
    """``counts`` with one more praise (n and month spread unchanged)."""
    c = counts[attribute]
    return counts | {
        attribute: replace(
            c, positive=c.positive + 1, positive_hard=c.positive_hard + 1
        )
    }


def test_praise_that_grades_an_attribute_can_reveal_a_catch() -> None:
    """First exception to monotonicity (ADR 006, errata E2 and E6)."""
    n = 150
    counts = hotel(n=n, food=(0.0, 9.0))  # 9 complaints: too few to grade
    before = assign_tier(counts, n, SPREAD_OK, POLICY)
    assert before.tier is Tier.EXCELLENT
    assert before.grades[Attribute.FOOD].grade is Grade.TOO_FEW
    after = assign_tier(_praised(counts, Attribute.FOOD), n, SPREAD_OK, POLICY)
    assert after.grades[Attribute.FOOD].grade is Grade.WEAK  # 10 opinions
    assert after.grades[Attribute.FOOD].material  # 9 / 150 = 6% of guests
    assert after.catches == [Attribute.FOOD]
    assert after.tier is Tier.GOOD


def test_praise_can_confirm_a_weak_essential_on_hard_labels() -> None:
    """Second exception (ADR 006, E6): rule 2 turns Fair into Caution."""
    counts = hotel()  # room: 1.0 praise, 9.5 complaints; hard 0 and 9
    counts[Attribute.ROOM] = AttributeCounts(
        Attribute.ROOM, 1.0, 9.5, N, 9, 0, 9
    )
    before = assign_tier(counts, N, SPREAD_OK, POLICY)
    room = before.grades[Attribute.ROOM]
    assert room.grade is Grade.WEAK  # 10.5 opinions on expected counts
    hard = grade_attribute(room.counts.hard(), POLICY)
    assert hard.grade is Grade.TOO_FEW  # 9 opinions on hard labels
    assert before.tier is Tier.FAIR  # Weak, not on hard labels (E1)
    after = assign_tier(_praised(counts, Attribute.ROOM), N, SPREAD_OK, POLICY)
    assert after.grades[Attribute.ROOM].grade is Grade.WEAK
    hard = grade_attribute(after.grades[Attribute.ROOM].counts.hard(), POLICY)
    assert hard.counts.opinions == POLICY.min_opinions
    assert hard.grade is Grade.WEAK
    assert after.tier is Tier.CAUTION
    assert after.blockers == [
        Blocker(BlockerKind.WEAK_ESSENTIAL, Attribute.ROOM)
    ]


def test_praise_can_lift_caution_to_not_enough_evidence() -> None:
    """Rule 2 comes before rule 3, so Caution can hide a thin essential."""
    n = 49
    counts = hotel(n=n, cleanliness=(3.0, 2.0), room=(10.0, 19.0))
    before = assign_tier(counts, n, SPREAD_OK, POLICY)
    assert before.tier is Tier.CAUTION
    after = assign_tier(_praised(counts, Attribute.ROOM), n, SPREAD_OK, POLICY)
    assert after.grades[Attribute.ROOM].grade is Grade.OK
    assert after.tier is Tier.NOT_ENOUGH_EVIDENCE


def _confirmed_on_hard_labels(
    before: TierResult, after: TierResult, attribute: Attribute
) -> bool:
    """Praise took a Weak essential to the floor on hard labels (E6)."""
    floor = POLICY.min_opinions
    hard_before = before.grades[attribute].counts.hard()
    hard_after = grade_attribute(after.grades[attribute].counts.hard(), POLICY)
    return (
        before.tier is Tier.FAIR
        and after.tier is Tier.CAUTION
        and attribute in POLICY.essentials
        and before.grades[attribute].grade is Grade.WEAK
        and hard_before.opinions < floor <= hard_after.counts.opinions
        and hard_after.grade is Grade.WEAK
    )


def _new_catch(
    before: TierResult, after: TierResult, attribute: Attribute
) -> bool:
    """Praise graded a thin attribute as a material Weak (E6)."""
    return (
        before.grades[attribute].grade is Grade.TOO_FEW
        and attribute in after.catches
    )


@settings(max_examples=200, deadline=None)
@given(hotels_strategy, st.sampled_from(list(Attribute)), reviews_strategy)
def test_one_more_praise_never_lowers_a_ranked_tier(
    values: dict[Attribute, Draw], attribute: Attribute, n: int
) -> None:
    counts = _drawn(values, n)
    before = assign_tier(counts, n, SPREAD_OK, POLICY)
    after = assign_tier(_praised(counts, attribute), n, SPREAD_OK, POLICY)
    rb, ra = tier_rank(before.tier), tier_rank(after.tier)
    if rb is None:
        return  # leaving a "not enough" tier is neither up nor down
    if ra is None:  # out of Caution, onto a thin essential (rule 2 before 3)
        assert before.tier is Tier.CAUTION
        assert after.tier is Tier.NOT_ENOUGH_EVIDENCE
    elif ra > rb:  # only at the opinions floor, the two E6 exceptions
        assert _new_catch(before, after, attribute) or (
            _confirmed_on_hard_labels(before, after, attribute)
        )


@settings(max_examples=150, deadline=None)
@given(
    st.sampled_from(OPTIONAL_TWO_SIDED),
    st.floats(POLICY.min_opinions - 1, POLICY.min_opinions, exclude_max=True),
    _floats(0, 0.3),
    st.integers(100, 400),
)
def test_praise_over_the_floor_lowers_the_tier_only_with_a_catch(
    attribute: Attribute, opinions: float, praise_share: float, n: int
) -> None:
    """One praise grades the attribute; a material Weak costs a tier."""
    praise = opinions * praise_share
    counts = hotel(n=n, **{attribute.value: (praise, opinions - praise)})
    assume(counts[attribute].opinions < POLICY.min_opinions)  # float sum
    before = assign_tier(counts, n, SPREAD_OK, POLICY)
    assert before.tier is Tier.EXCELLENT
    assert before.grades[attribute].grade is Grade.TOO_FEW
    after = assign_tier(_praised(counts, attribute), n, SPREAD_OK, POLICY)
    grade = after.grades[attribute]
    revealed = grade.grade is Grade.WEAK and grade.material
    assert after.catches == ([attribute] if revealed else [])
    assert after.tier is (Tier.GOOD if revealed else Tier.EXCELLENT)


@settings(max_examples=150, deadline=None)
@given(
    st.sampled_from(POLICY.essentials),
    _floats(0, 3),
    _floats(0, 3),
    st.integers(0, POLICY.min_opinions - 1),
)
def test_praise_over_the_hard_label_floor_lowers_fair_only_to_caution(
    essential: Attribute, praise: float, extra: float, hard_praise: int
) -> None:
    """A Weak essential one hard-label opinion short of the floor."""
    floor = POLICY.min_opinions
    complaints = floor + extra - praise  # at least 10 expected opinions
    counts = hotel()
    counts[essential] = AttributeCounts(
        essential,
        praise,
        complaints,
        N,
        int(complaints),
        hard_praise,
        floor - 1 - hard_praise,
    )
    before = assign_tier(counts, N, SPREAD_OK, POLICY)
    assume(before.grades[essential].grade is Grade.WEAK)
    assert before.tier is Tier.FAIR  # too few hard labels for rule 2
    after = assign_tier(_praised(counts, essential), N, SPREAD_OK, POLICY)
    hard = grade_attribute(after.grades[essential].counts.hard(), POLICY)
    confirmed = (
        after.grades[essential].grade is Grade.WEAK and hard.grade is Grade.WEAK
    )
    if confirmed:
        assert after.tier is Tier.CAUTION
    else:  # still Fair, or Good once the essential is no longer Weak
        assert after.tier in (Tier.FAIR, Tier.GOOD)


@settings(max_examples=200, deadline=None)
@given(hotels_strategy, st.sampled_from(list(Attribute)), reviews_strategy)
def test_one_more_complaint_never_raises_a_ranked_tier(
    values: dict[Attribute, Draw], attribute: Attribute, n: int
) -> None:
    counts = _drawn(values, n)
    before = assign_tier(counts, n, SPREAD_OK, POLICY)
    c = counts[attribute]
    after_counts = counts | {
        attribute: replace(
            c,
            negative=c.negative + 1,
            negative_hard=c.negative_hard + 1,
            complaining_reviews=c.complaining_reviews + 1,
        )
    }
    after = assign_tier(after_counts, n, SPREAD_OK, POLICY)
    rb, ra = tier_rank(before.tier), tier_rank(after.tier)
    if rb is None:
        return  # leaving a "not enough" tier is neither up nor down
    assert ra is not None  # a ranked hotel stays ranked
    assert ra >= rb


def test_essential_strengths_names_the_essential_that_caps_excellent() -> None:
    from hotel_curator.scoring.tiers_report import essential_strengths

    results = {
        "top": assign_tier(hotel(), N, SPREAD_OK, POLICY),
        "room_short": assign_tier(
            hotel(room=(70.0, 30.0)), N, SPREAD_OK, POLICY
        ),
        "plain": assign_tier(
            hotel(room=(60.0, 40.0), staff=(60.0, 40.0)), N, SPREAD_OK, POLICY
        ),
    }
    closest = results["room_short"].grades[Attribute.ROOM].prob_strong
    line = essential_strengths(
        {k: v for k, v in results.items() if k != "top"}, POLICY
    )
    assert line.startswith(
        "Strong essentials (Excellent needs all 3): "
        "cleanliness 2, room 0, staff 1. 1 hotels have cleanliness and "
        "staff Strong; for room,"
    )
    assert f"{closest:.3f} certain that at least 80%" in line
    assert "(Strong needs 0.95)" in line
    with pytest.raises(ValueError, match="no tier results"):
        essential_strengths({}, POLICY)


def test_report_names_both_exceptions_to_monotonicity() -> None:
    from hotel_curator.scoring.tiers_report import monotonicity_claim

    claim = monotonicity_claim(POLICY)
    assert "except in two cases, both at the 10 opinions" in claim
    assert "material Weak, a new catch" in claim
    assert "10 opinions on hard labels" in claim
    assert "Fair falls to Caution by rule 2" in claim
    assert claim.endswith("ADR 006, errata E2 and E6")
