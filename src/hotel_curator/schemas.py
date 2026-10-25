"""Public data shapes: hotel results as stored, served and documented.

The scoring pipeline writes these objects, the database stores them and the
API returns them, so the OpenAPI schema, the demo data and the frontend all
share one definition. Field descriptions are written for API users.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hotel_curator.scoring.compare import TrendState
from hotel_curator.scoring.grades import Grade, Lean
from hotel_curator.scoring.tiers import BlockerKind, Tier
from hotel_curator.taxonomy import Attribute, Polarity


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class QuoteOut(_Model):
    """A review part quoted as evidence (redacted, truncated)."""

    text: str = Field(
        description="Redacted review text; punctuation was "
        "removed by the data publisher."
    )
    part: Literal["liked", "disliked"]
    month: str = Field(description='Month of the review, e.g. "Aug 2017".')
    traveler_type: str | None = Field(
        description="Couple, Solo traveller, Family with young children, "
        "Family with older children, Group or Friends; null when the "
        "reviewer gave none."
    )
    probability: float = Field(
        ge=0,
        le=1,
        description="Calibrated probability of the cited label for this part.",
    )
    polarity: Polarity
    supports: bool = Field(
        description="True when the quote backs the grade, "
        "false for a counter-example."
    )


class TrendOut(_Model):
    """Last 6 months of data versus the 18 months before."""

    state: TrendState
    recent_mean: float | None
    baseline_mean: float | None
    prob_improved: float
    prob_declined: float


class AttributeOut(_Model):
    """One attribute's grade and every number behind it."""

    attribute: Attribute
    display_name: str
    essential: bool
    family: Literal["two_sided", "must_be"] = Field(
        description="two_sided: share of opinions that are praise; must_be: "
        "complaint rate among all reviews (noise, wifi)."
    )
    grade: Grade
    lean: Lean | None
    observed: float = Field(
        description="Raw share of praise (two_sided) or "
        "complaint rate (must_be)."
    )
    mean: float = Field(description="Posterior mean of the same measure.")
    low: float = Field(description="Lower end of the 90% likely range.")
    high: float = Field(description="Upper end of the 90% likely range.")
    prob_strong: float
    prob_weak: float
    positive: float = Field(
        description="P: expected number of reviews praising the attribute."
    )
    negative: float = Field(
        description="N: expected number of reviews complaining about it."
    )
    opinions: float = Field(description="M = P + N.")
    reviews: int = Field(description="n: eligible reviews of the hotel.")
    complaint_rate: float
    material: bool
    complaining_reviews: int
    positive_hard: int = Field(
        description="Reviews with a part labelled as praise at probability "
        ">= 0.5 (used to confirm a Weak essential before Caution)."
    )
    negative_hard: int = Field(
        description="Reviews with a part labelled as a complaint at "
        "probability >= 0.5."
    )
    trend: TrendOut | None = None
    quotes: list[QuoteOut] = Field(default_factory=list)


class BlockerOut(_Model):
    """One condition between the hotel and the next tier up."""

    kind: BlockerKind
    attribute: Attribute | None
    more: int | None
    text: str


class SegmentOut(_Model):
    """A credible difference between one kind of guest and the others."""

    segment: str = Field(
        description="couples, families, groups and "
        "friends, solo travellers or business trips"
    )
    attribute: Attribute
    direction: Literal["better", "worse"]
    segment_value: float = Field(
        description="Posterior mean share of "
        "praise (complaint rate for noise and wifi) "
        "among this segment's guests."
    )
    others_value: float = Field(description="The same for all other guests.")
    segment_size: float = Field(
        description="Opinions (reviews for noise and wifi) from this segment."
    )
    probability: float = Field(
        description="Posterior probability that the "
        "difference is at least the trend shift."
    )
    text: str


class ExplanationOut(_Model):
    """Plain-language explanation generated from the fields above."""

    label: str
    verdict: str
    essentials: list[str]
    what_would_change: list[str]
    for_you: list[str]
    who_it_suits: list[str] = Field(default_factory=list)


class ProvenanceOut(_Model):
    """Where a result comes from, so it can be traced and corrected."""

    params_version: str
    model: str
    data_source: str
    data_licence: str
    data_period: str
    corrections: str


class MonthCount(_Model):
    """Usable reviews in one calendar month."""

    month: str = Field(pattern=r"^\d{4}-\d{2}$", examples=["2016-03"])
    reviews: int = Field(ge=0)


class HotelSummary(_Model):
    """A hotel as listed in search results."""

    hotel_id: str
    name: str
    city: str
    country: str
    address: str
    lat: float | None
    lng: float | None
    tier: Tier
    label: str
    sort_score: float = Field(
        description="Cautious score used to order hotels within a tier."
    )
    reviews_eligible: int
    reviews_total: int
    catches: list[Attribute]
    highlights: list[Attribute]
    essentials: dict[Attribute, Grade]


class HotelDetail(HotelSummary):
    """Everything about one hotel's tier, with evidence."""

    attributes: list[AttributeOut]
    explanation: ExplanationOut
    blockers: list[BlockerOut]
    if_ignored: dict[Attribute, Tier]
    segments: list[SegmentOut] = Field(default_factory=list)
    first_month: str
    last_month: str
    review_volume: list[MonthCount] = Field(
        default_factory=list,
        description="Usable reviews per month from first_month to "
        "last_month, months without reviews included as 0.",
    )
    site_average_score: float | None = Field(
        description="The booking site's own average score, shown for "
        "reference only; it is never used by the tier rules."
    )
    provenance: ProvenanceOut
