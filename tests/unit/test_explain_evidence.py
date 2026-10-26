"""Tests for explanation text and evidence quotes."""

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from hotel_curator.scoring.evidence import (
    Redactor,
    build_quotes,
    select_quotes,
    supporting_polarity,
    truncate,
)
from hotel_curator.scoring.explain import explain, grade_line, tier_label
from hotel_curator.scoring.grades import AttributeCounts, grade_attribute
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.tiers import ReviewSpread, Tier, assign_tier
from hotel_curator.taxonomy import LABELS, Attribute, Polarity

POLICY = TierPolicy.load(
    Path(__file__).parents[2] / "config" / "tier_policy.yaml"
)
SPREAD = ReviewSpread(20, 0.1)


def _counts(
    **overrides: tuple[float, float],
) -> dict[Attribute, AttributeCounts]:
    values = {a: (90.0, 2.0) for a in Attribute}
    values[Attribute.NOISE] = (5.0, 1.0)
    values[Attribute.WIFI] = (5.0, 1.0)
    values.update({Attribute(k): v for k, v in overrides.items()})
    return {
        a: AttributeCounts(a, p, n, 200, int(n), round(p), round(n))
        for a, (p, n) in values.items()
    }


def test_label_names_catches() -> None:
    result = assign_tier(_counts(noise=(2.0, 40.0)), 200, SPREAD, POLICY)
    assert tier_label(result) == "Good, but noisy"


def test_explanation_numbers_match_fields() -> None:
    result = assign_tier(_counts(room=(34.0, 6.0)), 200, SPREAD, POLICY)
    text = explain(result, "Aug 2015 to Aug 2017", POLICY)
    assert result.tier is Tier.GOOD
    assert text.verdict.startswith("In reviews from Aug 2015 to Aug 2017")
    room = result.grades[Attribute.ROOM]
    line = text.essentials[1]
    numbers = [int(x) for x in re.findall(r"(\d+)%", line)]
    assert numbers == [
        round(100 * 34 / 40),
        round(100 * room.low),
        round(100 * room.high),
    ]
    assert f"of {room.counts.opinions:.0f} opinions" in line
    change = text.what_would_change[0]
    blocker = result.blockers[0]
    assert blocker.more is not None
    assert f"about {blocker.more} more opinions" in change


def test_caution_and_for_you_text() -> None:
    caution = assign_tier(
        _counts(cleanliness=(10.0, 40.0)), 200, SPREAD, POLICY
    )
    text = explain(caution, "Aug 2015 to Aug 2017", POLICY)
    assert "complaints about cleanliness outnumbered praise" in text.verdict
    noisy = assign_tier(_counts(noise=(2.0, 40.0)), 200, SPREAD, POLICY)
    assert explain(noisy, "x", POLICY).for_you == [
        "If noise doesn't bother you, this hotel would be Excellent."
    ]


def test_must_be_grade_line() -> None:
    grade = grade_attribute(
        AttributeCounts(Attribute.WIFI, 1, 12, 100, 12, 1, 12), POLICY
    )
    assert "guests complained" in grade_line(grade)


def test_truncate_at_word_boundary() -> None:
    assert truncate("short text", 200) == "short text"
    long = "word " * 100
    cut = truncate(long, 40)
    assert len(cut) <= 40
    assert cut.endswith("...")
    assert not cut.endswith(" ...")


@dataclass
class _Ent:
    start_char: int
    end_char: int
    label_: str


@dataclass
class _Doc:
    text: str
    ents: list[_Ent]


class _FakeNlp:
    def pipe(self, texts: list[str], batch_size: int = 0) -> list[_Doc]:
        docs = []
        for text in texts:
            ents = [
                _Ent(m.start(), m.end(), "PERSON")
                for m in re.finditer("Maria|Ahmed", text)
            ]
            docs.append(_Doc(text, ents))
        return docs


def test_redactor_masks_names_numbers_and_emails() -> None:
    redactor = Redactor(_FakeNlp())
    out = redactor.redact(
        ["Maria and Ahmed were great call 0770 090 0123 or x@y.com"]
    )
    assert out == ["[name] and [name] were great call [number] or [email]"]


def _parts() -> pd.DataFrame:
    rows = []
    for i, (p_pos, p_neg) in enumerate(
        [(0.95, 0.0), (0.85, 0.0), (0.81, 0.0), (0.1, 0.92), (0.1, 0.5)]
    ):
        row = {
            "review_id": f"r{i}",
            "part": "liked" if p_pos > 0.5 else "disliked",
            "text": f"Staff text {i} Maria",
            "review_date": pd.Timestamp("2017-01-01") + pd.Timedelta(days=i),
            "traveler_type": "couple",
            **dict.fromkeys(LABELS, 0.0),
        }
        row["staff_positive"] = p_pos
        row["staff_negative"] = p_neg
        rows.append(row)
    return pd.DataFrame(rows)


def test_select_and_build_quotes() -> None:
    grades = {
        Attribute.STAFF: grade_attribute(
            AttributeCounts(Attribute.STAFF, 90, 2, 200, 2, 90, 2), POLICY
        )
    }
    assert supporting_polarity(grades[Attribute.STAFF]) is Polarity.POSITIVE
    picked = select_quotes(_parts(), grades, POLICY)
    texts = Redactor(_FakeNlp()).redact(
        [str(r["text"]) for _, r, _, _ in picked]
    )
    quotes = [q for _, q in build_quotes(picked, texts, POLICY)]
    with pytest.raises(ValueError, match="one redacted text"):
        build_quotes(picked, texts[:1], POLICY)
    supporting = [q for q in quotes if q.supports]
    counter = [q for q in quotes if not q.supports]
    assert [q.review_id for q in supporting] == ["r0", "r1"]
    assert [q.review_id for q in counter] == ["r3"]
    assert supporting[0].text.endswith("[name]")
    assert supporting[0].month == "Jan 2017"
    assert supporting[0].traveler_type == "Couple"


def test_a_part_naming_someone_is_replaced_by_the_next() -> None:
    grades = {
        Attribute.STAFF: grade_attribute(
            AttributeCounts(Attribute.STAFF, 90, 2, 200, 2, 90, 2), POLICY
        )
    }
    named = {"r0"}  # the highest-scoring part names a member of staff
    picked = select_quotes(
        _parts(),
        grades,
        POLICY,
        lambda text: text.startswith("Staff text 0"),
    )
    supporting = [row["review_id"] for _, row, _, s in picked if s]
    assert supporting == ["r1", "r2"]
    assert not named & set(supporting)


def test_weak_grades_need_stronger_quotes() -> None:
    weak = grade_attribute(
        AttributeCounts(Attribute.STAFF, 2, 40, 200, 40, 2, 40), POLICY
    )
    parts = _parts()
    between = parts.iloc[[3]].assign(review_id="r5", staff_negative=0.85)
    parts = pd.concat([parts, between], ignore_index=True)
    bars = (
        POLICY.quotes.min_probability,
        POLICY.quotes.min_probability_negative,
    )
    assert bars[0] < 0.85 < bars[1]
    picked = select_quotes(parts, {Attribute.STAFF: weak}, POLICY)
    support = [row["review_id"] for _, row, _, s in picked if s]
    # 0.92 passes the stricter bar for complaints; 0.85 would pass only
    # the ordinary one; 0.5 passes neither.
    assert support == ["r3"]


@pytest.mark.parametrize("tier", list(Tier))
def test_every_tier_has_a_label(tier: Tier) -> None:
    from hotel_curator.scoring.explain import TIER_LABELS

    assert TIER_LABELS[tier]


def test_verdict_names_what_holds_a_strong_hotel_back() -> None:
    noisy = assign_tier(_counts(noise=(2.0, 40.0)), 200, SPREAD, POLICY)
    verdict = explain(noisy, "Aug 2015 to Aug 2017", POLICY).verdict
    assert "praised cleanliness, room and staff" in verdict
    assert "complaints about noise hold it back" in verdict
    burst = assign_tier(_counts(), 200, ReviewSpread(3, 0.5), POLICY)
    assert "concentrated in time (3 months, the busiest with 50%)" in (
        explain(burst, "x", POLICY).verdict
    )
    partial = assign_tier(_counts(room=(34.0, 6.0)), 200, SPREAD, POLICY)
    assert (
        "room is not a confirmed strength"
        in explain(partial, "x", POLICY).verdict
    )


def test_counts_in_text_are_singular_or_fractional_as_needed() -> None:
    from hotel_curator.scoring.explain import _count

    assert _count(1, "review") == "1 review"
    assert _count(3, "review") == "3 reviews"
    assert _count(9.6, "opinion") == "about 9.6 opinions"  # not "10 opinions"
    assert _count(118.4, "opinion") == "about 118 opinions"


def test_one_more_review_is_singular() -> None:
    from hotel_curator.scoring.explain import blocker_line
    from hotel_curator.scoring.tiers import Blocker, BlockerKind

    few = assign_tier(_counts(), 29, SPREAD, POLICY)
    line = blocker_line(Blocker(BlockerKind.MORE_REVIEWS, more=1), few, POLICY)
    assert line == "1 more review is needed before a tier is given."


def test_spread_text_names_the_failing_condition() -> None:
    from hotel_curator.scoring.explain import blocker_line
    from hotel_curator.scoring.tiers import Blocker, BlockerKind

    busy = assign_tier(_counts(), 200, ReviewSpread(12, 0.4), POLICY)
    line = blocker_line(Blocker(BlockerKind.REVIEW_SPREAD), busy, POLICY)
    assert line.startswith("Too many reviews come from one month (40%)")


def test_a_share_beside_its_line_never_prints_as_the_line() -> None:
    from hotel_curator.scoring.explain import _pct_beside

    assert _pct_beside(0.70, 0.80) == "70%"
    assert _pct_beside(0.7951, 0.80) == "79.5%"  # not "80%"
    assert _pct_beside(0.79996, 0.80) == "79.9%"  # not "80.0%"
    assert _pct_beside(0.80, 0.80) == "80%"
    assert _pct_beside(0.8034, 0.80, places=1) == "80.3%"
    assert _pct_beside(0.80003, 0.80, places=1) == "80.01%"  # not "80.0%"


@pytest.mark.parametrize(
    ("room", "text"),
    [
        ((79.6, 20.4), "(now 79.6%; the line is 80%)"),
        ((160.05, 39.95), "(80.03% against 80%)"),
    ],
)
def test_share_too_low_text_keeps_the_share_off_the_line(
    room: tuple[float, float], text: str
) -> None:
    from hotel_curator.scoring.explain import blocker_line
    from hotel_curator.scoring.tiers import BlockerKind

    result = assign_tier(_counts(room=room), 200, SPREAD, POLICY)
    blocker = result.blockers[0]
    assert blocker.kind is BlockerKind.SHARE_TOO_LOW
    assert text in blocker_line(blocker, result, POLICY)
