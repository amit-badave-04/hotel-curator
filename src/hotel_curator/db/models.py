"""SQLModel ORM tables for hotel curator.

Used when persisting to PostgreSQL/SQLite instead of parquet.
"""

from sqlmodel import Field, SQLModel


class HotelRecord(SQLModel, table=True):
    """Hotel-level signals and tier."""

    __tablename__ = "hotels"

    hotel_id: int = Field(primary_key=True)
    tier: str = Field(index=True)
    n_reviews: int
    avg_score: float
    weighted_avg_score: float
    min_critical_score: float

    # Per-attribute scores (denormalized for query speed)
    cleanliness_score: float = 0.0
    room_score: float = 0.0
    staff_score: float = 0.0
    location_score: float = 0.0
    noise_score: float = 0.0
    wifi_score: float = 0.0
    value_score: float = 0.0
    breakfast_score: float = 0.0

    # Mention rates
    cleanliness_mention_rate: float = 0.0
    room_mention_rate: float = 0.0
    staff_mention_rate: float = 0.0
    location_mention_rate: float = 0.0
    noise_mention_rate: float = 0.0
    wifi_mention_rate: float = 0.0
    value_mention_rate: float = 0.0
    breakfast_mention_rate: float = 0.0


class FeedbackRecord(SQLModel, table=True):
    """User feedback on incorrect classifications."""

    __tablename__ = "feedback"

    id: int | None = Field(default=None, primary_key=True)
    hotel_id: int = Field(index=True)
    review_id: int | None = None
    label: str
    expected_value: bool
    comment: str = ""
