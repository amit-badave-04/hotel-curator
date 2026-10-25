"""Score every hotel: from calibrated part probabilities to API results.

This is the batch job behind ``curator score hotels``. It joins the pieces
of the scoring package in one place and produces one
:class:`hotel_curator.schemas.HotelDetail` per hotel. The site's own
``Average_Score`` is copied through as a reference field only; nothing in
the tier computation reads it (a unit test checks the scoring package never
mentions the source score columns).
"""

import logging
from collections.abc import Collection
from dataclasses import asdict, dataclass

import pandas as pd

from hotel_curator.schemas import (
    AttributeOut,
    BlockerOut,
    ExplanationOut,
    HotelDetail,
    MonthCount,
    ProvenanceOut,
    QuoteOut,
    SegmentOut,
    TrendOut,
)
from hotel_curator.scoring.aggregate import (
    HotelEvidence,
    eligible_reviews,
    hotel_evidence,
    review_signals,
)
from hotel_curator.scoring.compare import Trend, trend, window_counts
from hotel_curator.scoring.evidence import (
    PickedPart,
    Quote,
    Redactor,
    build_quotes,
    select_quotes,
)
from hotel_curator.scoring.explain import blocker_line, explain, observed_share
from hotel_curator.scoring.grades import AttributeCounts
from hotel_curator.scoring.names import NameGuard
from hotel_curator.scoring.policy import TierPolicy
from hotel_curator.scoring.segments import (
    SegmentFinding,
    finding_text,
    segment_findings,
)
from hotel_curator.scoring.tiers import TierResult, assign_tier
from hotel_curator.taxonomy import ATTRIBUTE_INFO, LABELS, Attribute

logger = logging.getLogger(__name__)

DATA_SOURCE = (
    "Booking.com 515K hotel reviews in Europe (2015-2017), via a pinned "
    "public mirror; see data/manifests/data.json"
)
DATA_LICENCE = (
    "CC0 as declared by the uploader, originally Booking.com content, "
    "unconfirmed; legal review required before commercial use"
)
CORRECTIONS = "See NOTICE for how to request a correction or removal."


@dataclass(frozen=True)
class ScoringInputs:
    """Everything the batch job reads."""

    hotels: pd.DataFrame
    reviews: pd.DataFrame
    part_probs: pd.DataFrame
    training_review_ids: Collection[str]
    model_name: str


def _month_label(period: str) -> str:
    return pd.Period(period, "M").strftime("%b %Y")


def quote_parts(
    eligible: pd.DataFrame, part_probs: pd.DataFrame
) -> pd.DataFrame:
    """Eligible parts with text, date, traveler type and probabilities."""
    melted = pd.concat(
        [
            eligible[
                ["review_id", "hotel_id", "review_date", "traveler_type"]
            ].assign(part=part, text=eligible[f"{part}_text"])
            for part in ("liked", "disliked")
        ]
    ).dropna(subset=["text"])
    return melted.merge(part_probs, on=["review_id", "part"], how="inner")


def _quote_out(quote: Quote) -> QuoteOut:
    fields = asdict(quote)
    fields.pop("review_id")  # never served: the API exposes no review ids
    return QuoteOut(**fields)


def _trend_out(value: Trend | None) -> TrendOut | None:
    if value is None:
        return None
    fields = asdict(value)
    fields.pop("attribute")
    return TrendOut(**fields)


def _attribute_out(
    result: TierResult,
    attribute: Attribute,
    policy: TierPolicy,
    trend_value: Trend | None,
    quotes: list[Quote],
) -> AttributeOut:
    grade = result.grades[attribute]
    c = grade.counts
    return AttributeOut(
        attribute=attribute,
        display_name=ATTRIBUTE_INFO[attribute].display_name,
        essential=attribute in policy.essentials,
        family=grade.family,
        grade=grade.grade,
        lean=grade.lean,
        observed=observed_share(grade),
        mean=grade.mean,
        low=grade.low,
        high=grade.high,
        prob_strong=grade.prob_strong,
        prob_weak=grade.prob_weak,
        positive=c.positive,
        negative=c.negative,
        opinions=c.opinions,
        reviews=c.reviews,
        complaint_rate=c.complaint_rate,
        material=grade.material,
        complaining_reviews=c.complaining_reviews,
        positive_hard=c.positive_hard,
        negative_hard=c.negative_hard,
        trend=_trend_out(trend_value),
        quotes=[_quote_out(q) for q in quotes],
    )


def _identity(hotel: pd.Series) -> dict[str, object]:
    """Name and location fields of a hotel row."""
    return {
        "hotel_id": str(hotel["hotel_id"]),
        "name": str(hotel["name"]),
        "city": str(hotel["city"]),
        "country": str(hotel["country"]),
        "address": str(hotel["address"]),
        "lat": None if pd.isna(hotel["lat"]) else float(hotel["lat"]),
        "lng": None if pd.isna(hotel["lng"]) else float(hotel["lng"]),
        "reviews_total": int(hotel["n_reviews"]),
        "site_average_score": None
        if pd.isna(hotel["source_average_score"])
        else float(hotel["source_average_score"]),
    }


def _provenance(
    policy: TierPolicy, model_name: str, period: str
) -> ProvenanceOut:
    return ProvenanceOut(
        params_version=policy.params_version,
        model=model_name,
        data_source=DATA_SOURCE,
        data_licence=DATA_LICENCE,
        data_period=period,
        corrections=CORRECTIONS,
    )


def review_volume(evidence: HotelEvidence) -> list[MonthCount]:
    """Reviews per month over the hotel's whole period, gaps as zero."""
    months = pd.period_range(
        evidence.first_month, evidence.last_month, freq="M"
    )
    return [
        MonthCount(
            month=str(m), reviews=evidence.monthly_reviews.get(str(m), 0)
        )
        for m in months
    ]


def hotel_detail(
    hotel: pd.Series,
    evidence: HotelEvidence,
    result: TierResult,
    trends: dict[Attribute, Trend],
    quotes: dict[Attribute, list[Quote]],
    policy: TierPolicy,
    model_name: str,
    segments: list[SegmentFinding] | None = None,
) -> HotelDetail:
    """Assemble the API object for one hotel."""
    period = (
        f"{_month_label(evidence.first_month)} to "
        f"{_month_label(evidence.last_month)}"
    )
    text = explain(result, period, policy)
    segment_out = [
        SegmentOut(
            **asdict(f),
            text=finding_text(f, result.grades[f.attribute].family),
        )
        for f in segments or []
    ]
    return HotelDetail(
        **_identity(hotel),
        tier=result.tier,
        label=text.label,
        sort_score=result.sort_score,
        reviews_eligible=evidence.reviews,
        catches=result.catches,
        highlights=result.highlights,
        essentials={a: result.grades[a].grade for a in policy.essentials},
        attributes=[
            _attribute_out(result, a, policy, trends.get(a), quotes.get(a, []))
            for a in Attribute
        ],
        explanation=ExplanationOut(
            **asdict(text), who_it_suits=[s.text for s in segment_out]
        ),
        blockers=[
            BlockerOut(**asdict(b), text=blocker_line(b, result, policy))
            for b in result.blockers
        ],
        if_ignored=result.if_ignored,
        segments=segment_out,
        first_month=evidence.first_month,
        last_month=evidence.last_month,
        review_volume=review_volume(evidence),
        provenance=_provenance(policy, model_name, period),
    )


def _quotes(
    evidence: dict[str, HotelEvidence],
    results: dict[str, TierResult],
    parts: pd.DataFrame,
    policy: TierPolicy,
    redactor: Redactor,
) -> dict[str, dict[Attribute, list[Quote]]]:
    """Select, redact (one batch) and package every hotel's quotes."""
    guard = NameGuard.from_texts(parts["text"].astype(str))
    keep = parts[list(LABELS)].max(axis=1) >= policy.quotes.min_probability
    parts_by_hotel = dict(tuple(parts[keep].groupby("hotel_id")))
    picked: list[tuple[str, PickedPart]] = []
    for hotel_id in evidence:
        hotel_parts = parts_by_hotel.get(hotel_id)
        if hotel_parts is not None:
            picked.extend(
                (hotel_id, item)
                for item in select_quotes(
                    hotel_parts,
                    results[hotel_id].grades,
                    policy,
                    guard.names_someone,
                )
            )
    logger.info("Redacting %d quotes", len(picked))
    items = [item for _, item in picked]
    texts = redactor.redact([str(row["text"]) for _, row, _, _ in items])
    out: dict[str, dict[Attribute, list[Quote]]] = {}
    for (hotel_id, _), (attribute, quote) in zip(
        picked, build_quotes(items, texts, policy), strict=True
    ):
        out.setdefault(hotel_id, {}).setdefault(attribute, []).append(quote)
    return out


def _trends(
    windows: dict[str, dict[str, dict[Attribute, AttributeCounts]]],
    hotel_id: str,
    policy: TierPolicy,
) -> dict[Attribute, Trend]:
    window = windows.get(hotel_id, {})
    if "recent" not in window or "baseline" not in window:
        return {}
    return {
        a: trend(window["recent"][a], window["baseline"][a], policy)
        for a in Attribute
    }


def score_hotels(
    inputs: ScoringInputs, policy: TierPolicy, redactor: Redactor
) -> list[HotelDetail]:
    """Tier, explain and quote every hotel that has eligible reviews."""
    eligible = eligible_reviews(
        inputs.reviews, inputs.training_review_ids, policy
    )
    signals = review_signals(eligible, inputs.part_probs, policy)
    evidence = hotel_evidence(signals, policy)
    kinds = eligible[["review_id", "traveler_type"]].assign(
        trip_type=eligible["trip_type"]
        if "trip_type" in eligible.columns
        else "unknown"
    )
    findings = segment_findings(
        signals.merge(kinds, on="review_id", how="left"), policy
    )
    windows = window_counts(signals, signals["month"].max(), policy)
    results = {
        h: assign_tier(ev.counts, ev.reviews, ev.spread, policy)
        for h, ev in evidence.items()
    }
    quotes = _quotes(
        evidence,
        results,
        quote_parts(eligible, inputs.part_probs),
        policy,
        redactor,
    )
    hotels = {str(row["hotel_id"]): row for _, row in inputs.hotels.iterrows()}
    return [
        hotel_detail(
            hotels[hotel_id],
            ev,
            results[hotel_id],
            _trends(windows, hotel_id, policy),
            quotes.get(hotel_id, {}),
            policy,
            inputs.model_name,
            findings.get(hotel_id),
        )
        for hotel_id, ev in evidence.items()
    ]
