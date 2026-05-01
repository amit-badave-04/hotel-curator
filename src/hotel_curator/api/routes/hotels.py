"""Hotel-level endpoints.

Reads from pre-computed parquet files in the data directory.
For production, swap to the DB repository layer.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from hotel_curator.schema import (
    AttributeScore,
    HotelDetail,
    HotelSummary,
)
from hotel_curator.settings import settings

router = APIRouter(prefix="/v1/hotels", tags=["hotels"])

# --- Data loading (parquet-backed for v0) ---

_signals_cache: dict | None = None
_evidence_cache: dict | None = None


def _load_signals() -> dict:
    """Load hotel_signals.parquet into a dict keyed by hotel_id."""
    global _signals_cache
    if _signals_cache is not None:
        return _signals_cache

    import pandas as pd

    path = Path("data/processed/hotel_signals.parquet")
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run the pipeline first."
        )
    df = pd.read_parquet(path)
    _signals_cache = {
        int(row["hotel_id"]): row.to_dict()
        for _, row in df.iterrows()
    }
    return _signals_cache


def _load_evidence() -> dict:
    """Load hotel_evidence.json."""
    global _evidence_cache
    if _evidence_cache is not None:
        return _evidence_cache

    path = Path("data/processed/hotel_evidence.json")
    if not path.exists():
        return {}
    with path.open() as f:
        _evidence_cache = json.load(f)
    return _evidence_cache


# --- Routes ---

@router.get("", response_model=list[HotelSummary])
def list_hotels(
    tier: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[HotelSummary]:
    """List hotels with optional tier filter."""
    signals = _load_signals()
    hotels = list(signals.values())
    if tier:
        tier_upper = tier.upper()
        hotels = [
            h for h in hotels if h.get("tier") == tier_upper
        ]
    hotels.sort(
        key=lambda h: h.get("avg_score", 0), reverse=True,
    )
    page = hotels[offset : offset + limit]
    return [
        HotelSummary(
            hotel_id=int(h["hotel_id"]),
            tier=h.get("tier", "UNKNOWN"),
            n_reviews=int(h.get("n_reviews", 0)),
            avg_score=float(h.get("avg_score", 0)),
        )
        for h in page
    ]


@router.get("/{hotel_id}", response_model=HotelDetail)
def get_hotel(hotel_id: int) -> HotelDetail:
    """Get full hotel detail with attribute scores."""
    signals = _load_signals()
    if hotel_id not in signals:
        raise HTTPException(
            status_code=404,
            detail=f"Hotel {hotel_id} not found.",
        )
    h = signals[hotel_id]
    attrs = settings.attributes

    attributes = {}
    for attr in attrs:
        attributes[attr] = AttributeScore(
            score=float(h.get(f"{attr}_score", 0)),
            pos_rate=float(h.get(f"{attr}_pos_rate", 0)),
            neg_rate=float(h.get(f"{attr}_neg_rate", 0)),
            mention_rate=float(
                h.get(f"{attr}_mention_rate", 0)
            ),
            ci_lo=h.get(f"{attr}_score_ci_lo"),
            ci_hi=h.get(f"{attr}_score_ci_hi"),
        )

    return HotelDetail(
        hotel_id=hotel_id,
        tier=h.get("tier", "UNKNOWN"),
        n_reviews=int(h.get("n_reviews", 0)),
        avg_score=float(h.get("avg_score", 0)),
        weighted_avg_score=float(
            h.get("weighted_avg_score", 0)
        ),
        min_critical_score=float(
            h.get("min_critical_score", 0)
        ),
        attributes=attributes,
    )


@router.get("/{hotel_id}/evidence")
def get_hotel_evidence(hotel_id: int) -> dict:
    """Get top-K evidence reviews per attribute for a hotel."""
    evidence = _load_evidence()
    key = str(hotel_id)
    if key not in evidence:
        raise HTTPException(
            status_code=404,
            detail=f"No evidence for hotel {hotel_id}.",
        )
    return evidence[key]
