"""Pydantic models shared across API, core, and DB layers."""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


# --- Tier rules ---

@dataclass(frozen=True)
class TierRule:
    """Single tier-assignment rule (top-down match order)."""

    name: str
    min_avg_score: float
    min_critical_score: float


DEFAULT_TIER_RULES: list[TierRule] = [
    TierRule("ELITE", 0.215, 0.30),
    TierRule("SUPERIOR", 0.190, 0.20),
    TierRule("PREMIUM", 0.165, 0.10),
]

ATTRIBUTES: list[str] = [
    "cleanliness", "room", "staff", "location",
    "noise", "wifi", "value", "breakfast",
]

CRITICAL_ATTRIBUTES: tuple[str, ...] = (
    "cleanliness", "room", "staff",
)


# --- API response models ---

class LabelPrediction(BaseModel):
    """Single label prediction with probability."""

    prob: float = Field(ge=0.0, le=1.0)
    predicted: bool


class ReviewClassification(BaseModel):
    """Full review classification result."""

    text: str
    labels: dict[str, LabelPrediction]


class AttributeScore(BaseModel):
    """Per-attribute signal for a hotel."""

    score: float = Field(ge=-1.0, le=1.0)
    pos_rate: float = Field(ge=0.0, le=1.0)
    neg_rate: float = Field(ge=0.0, le=1.0)
    mention_rate: float = Field(ge=0.0, le=1.0)
    ci_lo: float | None = None
    ci_hi: float | None = None


class EvidenceSnippet(BaseModel):
    """A review used as evidence for an attribute signal."""

    review_id: int
    text: str
    prob: float
    polarity: str


class HotelDetail(BaseModel):
    """Full hotel response with tier, scores, evidence."""

    hotel_id: int
    tier: str
    n_reviews: int
    avg_score: float
    weighted_avg_score: float
    min_critical_score: float
    attributes: dict[str, AttributeScore]
    evidence: dict[str, list[EvidenceSnippet]] | None = None


class HotelSummary(BaseModel):
    """Compact hotel listing for table views."""

    hotel_id: int
    tier: str
    n_reviews: int
    avg_score: float


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    model_loaded: bool
    num_labels: int
    device: str


# --- Request models ---

class ClassifyRequest(BaseModel):
    """Request body for single review classification."""

    text: str = Field(min_length=1, max_length=10_000)


class BatchClassifyRequest(BaseModel):
    """Request body for batch review classification."""

    texts: list[str] = Field(min_length=1, max_length=500)
