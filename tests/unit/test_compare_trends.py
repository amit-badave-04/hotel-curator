"""Tests for credible hotel comparisons and recent-versus-baseline trends."""

from pathlib import Path

import pandas as pd
import pytest

from hotel_curator.scoring.compare import (
    TrendState,
    prob_better,
    trend,
    window_counts,
)
from hotel_curator.scoring.grades import AttributeCounts
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.taxonomy import Attribute

POLICY = TierPolicy.load(
    Path(__file__).parents[2] / "config" / "tier_policy.yaml"
)


def _c(
    attribute: Attribute, pos: float, neg: float, n: int = 100
) -> AttributeCounts:
    return AttributeCounts(attribute, pos, neg, n, int(neg), int(pos), int(neg))


def test_prob_better_is_seeded_and_directional() -> None:
    a = _c(Attribute.STAFF, 90, 10)
    b = _c(Attribute.STAFF, 60, 40)
    p = prob_better(a, b, POLICY)
    assert p > 0.99
    assert prob_better(a, b, POLICY) == p  # reproducible
    assert prob_better(b, a, POLICY) < 0.01


def test_prob_better_must_be_prefers_fewer_complaints() -> None:
    quiet = _c(Attribute.NOISE, 1, 2, 200)
    loud = _c(Attribute.NOISE, 1, 40, 200)
    assert prob_better(quiet, loud, POLICY) > 0.99


def test_prob_better_rejects_mixed_attributes() -> None:
    with pytest.raises(ValueError, match="same attribute"):
        prob_better(_c(Attribute.ROOM, 1, 1), _c(Attribute.STAFF, 1, 1), POLICY)


@pytest.mark.parametrize(
    ("recent", "baseline", "state"),
    [
        ((45, 5), (60, 40), TrendState.IMPROVED),
        ((25, 25), (90, 10), TrendState.DECLINED),
        ((40, 10), (80, 20), TrendState.STABLE),
        ((5, 5), (80, 20), TrendState.TOO_FEW),
    ],
)
def test_trend_states(
    recent: tuple[float, float],
    baseline: tuple[float, float],
    state: TrendState,
) -> None:
    result = trend(
        _c(Attribute.ROOM, *recent), _c(Attribute.ROOM, *baseline), POLICY
    )
    assert result.state is state


@pytest.mark.parametrize(
    ("recent", "baseline", "state"),
    [
        (5, 40, TrendState.IMPROVED),  # complaints fell from 40% to 5%
        (40, 5, TrendState.DECLINED),
        (20, 21, TrendState.STABLE),
    ],
)
def test_must_be_trends_follow_the_complaint_rate(
    recent: int, baseline: int, state: TrendState
) -> None:
    result = trend(
        _c(Attribute.NOISE, 1, recent), _c(Attribute.NOISE, 1, baseline), POLICY
    )
    assert result.state is state
    assert result.recent_mean is not None
    assert abs(result.recent_mean - recent / 100) < 0.02  # complaint rate


def test_window_counts_split_by_month() -> None:
    signals = pd.DataFrame(
        {
            "hotel_id": ["h1"] * 4,
            "month": pd.PeriodIndex(
                ["2017-08", "2017-03", "2016-06", "2014-01"], freq="M"
            ),
            **{f"pos_{a.value}": [1.0, 0.0, 1.0, 1.0] for a in Attribute},
            **{f"neg_{a.value}": [0.0, 0.9, 0.0, 0.0] for a in Attribute},
        }
    )
    windows = window_counts(signals, pd.Period("2017-08", "M"), POLICY)
    recent = windows["h1"]["recent"][Attribute.ROOM]
    base = windows["h1"]["baseline"][Attribute.ROOM]
    assert (recent.reviews, recent.positive, recent.negative) == (2, 1.0, 0.9)
    assert recent.complaining_reviews == 1
    assert (base.reviews, base.positive) == (1, 1.0)
