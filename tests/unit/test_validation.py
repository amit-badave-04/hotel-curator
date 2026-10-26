"""Tests for the tier validation analyses."""

from pathlib import Path

import numpy as np
import pandas as pd

from hotel_curator.scoring.aggregate import (
    NEG,
    POS,
    eligible_reviews,
    hotel_evidence,
    review_signals,
    signal_columns,
)
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.tiers import Tier, assign_tier
from hotel_curator.scoring.validation import (
    SIMULATION_MARGIN,
    out_of_time,
    sensitivity,
    simulate_false_claims,
    spearman_ci,
    stability,
    tier_score,
)
from hotel_curator.taxonomy import Attribute
from tests.helpers import synthetic_inputs

POLICY = TierPolicy.load(
    Path(__file__).parents[2] / "config" / "tier_policy.yaml"
)


def _signals() -> tuple[pd.DataFrame, pd.DataFrame]:
    inputs = synthetic_inputs()
    eligible = eligible_reviews(inputs.reviews, set(), POLICY)
    reviews = eligible.assign(reviewer_score=8.0)
    return review_signals(eligible, inputs.part_probs, POLICY), reviews


def test_spearman_ci() -> None:
    x = np.arange(50, dtype=float)
    rho, low, high, n = spearman_ci(x, x**2, n_boot=50, seed=0)
    assert n == 50
    assert np.isclose(rho, 1.0)
    assert low <= rho <= high
    assert np.isnan(spearman_ci([1, 2], [1, 2], n_boot=5, seed=0)[0])
    constant = spearman_ci(x, np.ones(50), n_boot=5, seed=0)
    assert np.isnan(constant[0]) and constant[3] == 50


def test_tier_score_orders_tiers() -> None:
    assert tier_score(Tier.EXCELLENT, 0.5) > tier_score(Tier.GOOD, 0.9)
    assert np.isnan(tier_score(Tier.NOT_ENOUGH_REVIEWS, 0.9))


def test_stability_of_clear_cut_hotels() -> None:
    signals, _ = _signals()
    evidence = hotel_evidence(signals, POLICY)
    tiers = {
        h: assign_tier(ev.counts, ev.reviews, ev.spread, POLICY).tier
        for h, ev in evidence.items()
    }
    table = stability(signals, tiers, POLICY, replicates=3)
    clear = table.loc["h_00000000a1"]
    assert clear["tier"] == "excellent"
    assert clear["kept"] == 1.0


def test_out_of_time_runs_on_synthetic_hotels() -> None:
    signals, reviews = _signals()
    rows, table = out_of_time(
        signals, reviews, set(signals["hotel_id"]), POLICY, n_boot=20
    )
    assert len(rows) == 8
    assert set(table.columns) == {"count", "mean", "std"}


OOT_HOTELS = 14
FEW_LATER_VALUE = 3
"""Hotels with too few 2017 value opinions to count for value."""
EARLY = pd.period_range("2016-01", "2016-11", freq="M")
LAST_EARLY = pd.PeriodIndex(["2016-12"], freq="M")
FIRST_LATE = pd.PeriodIndex(["2017-01"], freq="M")
LATE = pd.period_range("2017-02", "2017-06", freq="M")


def _window(
    hotel: str, months: pd.PeriodIndex, n: int, shares: dict[str, float]
) -> pd.DataFrame:
    """``n`` reviews; ``shares`` gives the share of reviews with each label."""
    i = np.arange(n)
    frame = pd.DataFrame({"hotel_id": hotel, "month": months[i % len(months)]})
    for column in signal_columns(POS) + signal_columns(NEG):
        frame[column] = (i < round(shares.get(column, 0.0) * n)).astype(float)
    for prefix in (POS, NEG):
        for attribute in Attribute:
            frame[f"{prefix}_hard_{attribute.value}"] = (
                frame[f"{prefix}_{attribute.value}"] >= 0.5
            )
    return frame


def _hotel_windows(hotel: str, q: float, few_value: bool) -> pd.DataFrame:
    """One hotel whose room and noise persist past 2016 and value reverses.

    Value (and noise, before 2017) are only reviewed in December 2016 and
    from January 2017, in blocks large enough to dominate their window:
    moving the cut by one month, or using < for <=, changes the result.
    """
    room = {f"{POS}_room": q, f"{NEG}_room": 1 - q}
    noise = {f"{NEG}_noise": 0.6 - 0.5 * q}
    value_2016 = {f"{POS}_value": q, f"{NEG}_value": 1 - q}
    value_2017 = {f"{POS}_value": 1 - q, f"{NEG}_value": q}
    if few_value:  # two value opinions in all of 2017
        value_2017 = {}
    late_value = {f"{POS}_value": 2 / 60} if few_value else value_2017
    return pd.concat(
        [
            _window(hotel, EARLY, 40, room),
            _window(hotel, LAST_EARLY, 300, room | noise | value_2016),
            _window(hotel, FIRST_LATE, 500, room | noise | value_2017),
            _window(hotel, LATE, 60, room | noise | late_value),
        ],
        ignore_index=True,
    )


def _out_of_time_corpus() -> tuple[pd.DataFrame, pd.DataFrame]:
    frames, scores = [], {}
    for k, q in enumerate(np.linspace(0.3, 0.95, OOT_HOTELS)):
        hotel = f"h_{k:010d}"
        frames.append(_hotel_windows(hotel, q, k < FEW_LATER_VALUE))
        scores[hotel] = 10 * q
    signals = pd.concat(frames, ignore_index=True)
    reviews = pd.DataFrame(
        {
            "hotel_id": signals["hotel_id"],
            "review_date": signals["month"].dt.to_timestamp(),
            "reviewer_score": signals["hotel_id"].map(scores),
        }
    )
    return signals, reviews


def test_out_of_time_splits_at_the_cut_and_orients_must_be() -> None:
    signals, reviews = _out_of_time_corpus()
    rows, table = out_of_time(
        signals, reviews, set(signals["hotel_id"]), POLICY, n_boot=50
    )
    rho = {r.attribute: r.rho for r in rows}
    hotels = {r.attribute: r.hotels for r in rows}
    assert hotels[Attribute.ROOM] == hotels[Attribute.NOISE] == OOT_HOTELS
    # 2016 value opinions must not leak into the 2017 side (a swapped or
    # overlapping window would count every hotel).
    assert hotels[Attribute.VALUE] == OOT_HOTELS - FEW_LATER_VALUE
    assert np.isnan(rho[Attribute.WIFI])  # never mentioned: undefined
    assert rho[Attribute.ROOM] > 0.9
    # A lower complaint rate is better for noise: persisting complaint
    # rates must correlate positively once both sides are oriented.
    assert rho[Attribute.NOISE] > 0.9
    # Value reverses after the cut: only grades built from the early
    # window alone correlate negatively with 2017.
    assert rho[Attribute.VALUE] < -0.9
    assert table["count"].sum() == OOT_HOTELS


def test_simulation_with_a_perfect_classifier() -> None:
    perfect = {
        "positive": (np.array([0.99]), np.array([0.01])),
        "negative": (np.array([0.99]), np.array([0.01])),
    }
    rows = simulate_false_claims(
        perfect, [50, 400], 0.5, POLICY, hotels_per_cell=50
    )
    assert {r.claim for r in rows} == {"strong", "weak"}
    assert all(r.rate <= 0.1 for r in rows)
    # Each case sits on the side of its line where the claim is false.
    shares = {r.claim: r.true_share for r in rows}
    margin = SIMULATION_MARGIN
    assert np.isclose(shares["strong"], POLICY.two_sided.strong - margin)
    assert np.isclose(shares["weak"], POLICY.two_sided.weak + margin)


def test_sensitivity_has_one_column_per_variant() -> None:
    signals, _ = _signals()
    evidence = hotel_evidence(signals, POLICY)
    table = sensitivity(
        evidence,
        POLICY,
        {
            "strong 0.75": lambda p: p.model_copy(
                update={
                    "two_sided": p.two_sided.model_copy(update={"strong": 0.75})
                }
            )
        },
    )
    assert list(table.columns) == ["as registered", "strong 0.75"]
    assert table.sum().tolist() == [5, 5]


def test_tiers_markdown_renders_every_section() -> None:
    from hotel_curator.scoring.tiers_report import (
        TierValidation,
        tiers_markdown,
    )
    from hotel_curator.scoring.validation import external_agreement_table

    signals, reviews = _signals()
    evidence = hotel_evidence(signals, POLICY)
    results = {
        h: assign_tier(ev.counts, ev.reviews, ev.spread, POLICY)
        for h, ev in evidence.items()
    }
    sizes = pd.Series({h: ev.reviews for h, ev in evidence.items()})
    oot_rows, oot_table = out_of_time(
        signals, reviews, set(sizes.index), POLICY, n_boot=10
    )
    hotels = pd.DataFrame(
        {"hotel_id": list(sizes.index), "source_average_score": [8.0] * 5}
    )
    agreement = external_agreement_table(
        results, signals, reviews, hotels, POLICY, n_boot=10
    )
    text = tiers_markdown(
        TierValidation(
            policy=POLICY,
            results=results,
            sizes=sizes,
            oot_rows=oot_rows,
            oot_table=oot_table,
            agreement=agreement,
            stable=stability(
                signals,
                {h: r.tier for h, r in results.items()},
                POLICY,
                replicates=2,
            ),
            simulation=simulate_false_claims(
                {
                    "positive": (np.array([0.9]), np.array([0.05])),
                    "negative": (np.array([0.9]), np.array([0.05])),
                },
                [40],
                0.5,
                POLICY,
                hotels_per_cell=20,
            ),
            mention_rate=0.5,
            sensitivity=sensitivity(evidence, POLICY, {}),
            replicates=2,
            seconds=1.0,
        )
    )
    for heading in (
        "## Tier mix",
        "## 4. Out of time",
        "## 5. Agreement",
        "## 6. Stability",
        "## 7. Simulation",
        "## 8. Sensitivity",
    ):
        assert heading in text
    assert "| excellent |" in text
