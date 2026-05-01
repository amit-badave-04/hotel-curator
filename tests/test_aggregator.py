"""Tests for hotel_curator.core.aggregator."""

import pandas as pd

from hotel_curator.core.aggregator import (
    aggregate_hotel_signals,
    compute_mention_rates,
    compute_weighted_avg_score,
)
from hotel_curator.schema import ATTRIBUTES, CRITICAL_ATTRIBUTES


def test_aggregate_produces_expected_columns(
    sample_predictions: pd.DataFrame,
) -> None:
    signals = aggregate_hotel_signals(
        sample_predictions, ATTRIBUTES, CRITICAL_ATTRIBUTES,
    )
    assert "hotel_id" in signals.columns
    assert "avg_score" in signals.columns
    assert "min_critical_score" in signals.columns
    for attr in ATTRIBUTES:
        assert f"{attr}_score" in signals.columns
    assert len(signals) == 2  # two hotels


def test_aggregate_score_range(
    sample_predictions: pd.DataFrame,
) -> None:
    signals = aggregate_hotel_signals(
        sample_predictions, ATTRIBUTES, CRITICAL_ATTRIBUTES,
    )
    for attr in ATTRIBUTES:
        col = signals[f"{attr}_score"]
        assert col.min() >= -1.0
        assert col.max() <= 1.0


def test_mention_rates_range(
    sample_predictions: pd.DataFrame,
) -> None:
    rates = compute_mention_rates(
        sample_predictions, ATTRIBUTES,
    )
    for attr in ATTRIBUTES:
        col = rates[f"{attr}_mention_rate"]
        assert col.min() >= 0.0
        assert col.max() <= 1.0


def test_weighted_avg_needs_mention_rates(
    sample_predictions: pd.DataFrame,
) -> None:
    signals = aggregate_hotel_signals(
        sample_predictions, ATTRIBUTES, CRITICAL_ATTRIBUTES,
    )
    rates = compute_mention_rates(
        sample_predictions, ATTRIBUTES,
    )
    signals = signals.merge(rates, on="hotel_id")
    weighted = compute_weighted_avg_score(
        signals, ATTRIBUTES,
    )
    assert len(weighted) == 2
    assert weighted.notna().all()
