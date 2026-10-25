"""End-to-end test of the hotel scoring batch job on synthetic data."""

import re
from pathlib import Path

import numpy as np
import pandas as pd

import hotel_curator.scoring as scoring_pkg
from hotel_curator.schemas import HotelDetail
from hotel_curator.scoring.evidence import Redactor
from hotel_curator.scoring.pipeline import ScoringInputs, score_hotels
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.tiers import Tier
from hotel_curator.taxonomy import LABELS

POLICY = TierPolicy.load(
    Path(__file__).parents[2] / "config" / "tier_policy.yaml"
)


class _NoNames:
    """Fake spaCy pipeline: finds no entities."""

    class _Doc:
        def __init__(self, text: str) -> None:
            self.text = text
            self.ents: list[object] = []

    def pipe(self, texts: list[str], batch_size: int = 0) -> list["_Doc"]:
        return [self._Doc(t) for t in texts]


def _inputs(average_score: float = 8.0) -> ScoringInputs:
    rng = np.random.default_rng(0)
    hotels = pd.DataFrame(
        {
            "hotel_id": ["h_good", "h_dirty", "h_tiny"],
            "name": ["Good Hotel", "Dirty Hotel", "Tiny Hotel"],
            "address": ["1 A St", "2 B St", "3 C St"],
            "city": ["Paris"] * 3,
            "country": ["France"] * 3,
            "lat": [48.85, 48.86, np.nan],
            "lng": [2.35, 2.36, np.nan],
            "source_average_score": [average_score] * 3,
            "source_total_reviews": [1000] * 3,
            "n_reviews": [120, 120, 10],
        }
    )
    reviews, parts = [], []
    for hotel, n in (("h_good", 120), ("h_dirty", 120), ("h_tiny", 10)):
        for i in range(n):
            rid = f"r_{hotel}_{i:03d}"
            date = pd.Timestamp("2015-09-01") + pd.Timedelta(days=6 * i)
            reviews.append(
                {
                    "review_id": rid,
                    "hotel_id": hotel,
                    "review_date": date,
                    "liked_text": f"Great staff and clean room {i}",
                    "disliked_text": f"Dirty bathroom {i}"
                    if hotel == "h_dirty"
                    else None,
                    "traveler_type": "couple",
                }
            )
            liked = dict.fromkeys(LABELS, 0.01)
            for label in (
                "staff_positive",
                "room_positive",
                "location_positive",
            ):
                liked[label] = 0.97
            if hotel != "h_dirty":
                liked["cleanliness_positive"] = 0.97
            liked["noise_positive"] = float(rng.uniform(0, 0.2))
            parts.append({"review_id": rid, "part": "liked", **liked})
            if hotel == "h_dirty":
                bad = dict.fromkeys(LABELS, 0.01)
                bad["cleanliness_negative"] = 0.97
                parts.append({"review_id": rid, "part": "disliked", **bad})
    return ScoringInputs(
        hotels=hotels,
        reviews=pd.DataFrame(reviews),
        part_probs=pd.DataFrame(parts),
        training_review_ids={"r_h_good_000"},
        model_name="test-model",
    )


def _score(average_score: float = 8.0) -> dict[str, HotelDetail]:
    results = score_hotels(_inputs(average_score), POLICY, Redactor(_NoNames()))
    return {r.hotel_id: r for r in results}


def test_tiers_explanations_and_quotes() -> None:
    results = _score()
    good, dirty, tiny = (
        results["h_good"],
        results["h_dirty"],
        results["h_tiny"],
    )
    assert good.tier is Tier.EXCELLENT
    assert good.reviews_eligible == 119  # one training review excluded
    assert dirty.tier is Tier.CAUTION
    assert "cleanliness" in dirty.explanation.verdict
    assert tiny.tier is Tier.NOT_ENOUGH_REVIEWS
    staff = next(a for a in good.attributes if a.attribute == "staff")
    assert len([q for q in staff.quotes if q.supports]) == 2
    # Codes from the data become display words in the API.
    assert {q.traveler_type for q in staff.quotes} == {"Couple"}
    assert good.provenance.params_version == POLICY.params_version
    assert good.provenance.data_period == "Sep 2015 to Aug 2017"
    HotelDetail.model_validate_json(good.model_dump_json())


def test_review_volume_covers_every_month() -> None:
    for hotel in _score().values():
        volume = hotel.review_volume
        assert sum(m.reviews for m in volume) == hotel.reviews_eligible
        assert volume[0].month == hotel.first_month
        assert volume[-1].month == hotel.last_month
        expected = pd.period_range(
            hotel.first_month, hotel.last_month, freq="M"
        )
        assert [m.month for m in volume] == [str(m) for m in expected]


def test_site_scores_never_change_tiers() -> None:
    low, high = _score(1.0), _score(10.0)
    for hotel_id in low:
        assert low[hotel_id].tier is high[hotel_id].tier
        assert low[hotel_id].sort_score == high[hotel_id].sort_score


def test_scoring_code_never_reads_site_scores() -> None:
    forbidden = re.compile(
        r"source_average_score|source_total_reviews|reviewer_score"
    )
    package = Path(scoring_pkg.__file__).parent
    for path in package.glob("*.py"):
        hits = forbidden.findall(path.read_text())
        if path.name == "pipeline.py":
            # copied through to the API as a labelled reference field only
            assert set(hits) == {"source_average_score"}, hits
        elif path.name == "validation.py":
            # validation targets for TIERS.md (ADR 006 section 7), read after
            # tiers are computed; nothing flows back into a tier
            assert set(hits) <= {"source_average_score", "reviewer_score"}
        else:
            assert not hits, f"{path.name} mentions {hits}"
