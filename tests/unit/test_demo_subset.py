"""Subset invariance on the committed demo data (ADR 006, section 7.1).

``data/demo/subset_signals.parquet`` holds the stored review signals of a
few hotels per city. Recomputing their tiers from those signals alone must
give exactly the tiers in the served results, which were computed from the
full corpus run: a hotel's tier depends only on its own reviews.
"""

import gzip
import json
from pathlib import Path

import pandas as pd
import pytest

from hotel_curator.scoring.aggregate import hotel_evidence
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.tiers import assign_tier

ROOT = Path(__file__).parents[2]
DEMO = ROOT / "data" / "demo"
POLICY = TierPolicy.load(ROOT / "config" / "tier_policy.yaml")

pytestmark = pytest.mark.skipif(
    not (DEMO / "subset_signals.parquet").is_file(),
    reason="demo data not built yet (curator score hotels)",
)


def test_subset_tiers_equal_full_run_tiers() -> None:
    signals = pd.read_parquet(DEMO / "subset_signals.parquet")
    signals["month"] = pd.PeriodIndex(signals["month"], freq="M")
    with gzip.open(DEMO / "hotel_results.json.gz", "rt") as handle:
        served = {r["hotel_id"]: r for r in json.load(handle)}
    evidence = hotel_evidence(signals, POLICY)
    assert len(evidence) >= 20
    for hotel_id, ev in evidence.items():
        result = assign_tier(ev.counts, ev.reviews, ev.spread, POLICY)
        assert result.tier.value == served[hotel_id]["tier"], hotel_id
        assert result.sort_score == pytest.approx(
            served[hotel_id]["sort_score"], abs=1e-12
        )
        assert ev.reviews == served[hotel_id]["reviews_eligible"]


def test_served_results_carry_the_registered_policy() -> None:
    with gzip.open(DEMO / "hotel_results.json.gz", "rt") as handle:
        results = json.load(handle)
    versions = {r["provenance"]["params_version"] for r in results}
    assert versions == {POLICY.params_version}
