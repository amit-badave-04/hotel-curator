"""Tests for hotel_curator.core.tier."""

from hotel_curator.core.tier import assign_tier
from hotel_curator.schema import DEFAULT_TIER_RULES


def test_elite_assignment() -> None:
    assert assign_tier(0.25, 0.35) == "ELITE"


def test_superior_assignment() -> None:
    assert assign_tier(0.20, 0.22) == "SUPERIOR"


def test_premium_assignment() -> None:
    assert assign_tier(0.17, 0.12) == "PREMIUM"


def test_fail_low_avg() -> None:
    assert assign_tier(0.10, 0.50) == "FAIL"


def test_fail_low_critical() -> None:
    # High avg but critical below PREMIUM floor
    assert assign_tier(0.25, 0.05) == "FAIL"


def test_critical_floor_prevents_elite() -> None:
    # avg_score qualifies for ELITE but critical is too low
    result = assign_tier(0.25, 0.15)
    assert result != "ELITE"
    # Should fall to PREMIUM (critical 0.15 >= 0.10)
    assert result == "PREMIUM"
