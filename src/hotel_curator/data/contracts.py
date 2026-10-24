"""Data contracts for the canonical ``hotels`` and ``reviews`` tables.

Every later step (labeling, training, scoring, the API seed) reads these two
tables, so their shape is pinned here once. Validation is lazy (all failures
are collected) and raises ``pandera.errors.SchemaErrors``, which stops the
pipeline with a table of offending rows instead of letting bad data flow on.

The value sets below describe the Booking.com 515K source; a new source must
extend them deliberately rather than slip through.
"""

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

CITIES = ("Amsterdam", "Barcelona", "London", "Milan", "Paris", "Vienna")
COUNTRIES = (
    "Austria",
    "France",
    "Italy",
    "Netherlands",
    "Spain",
    "United Kingdom",
)
SPLITS = ("train", "val", "test")
TRIP_TYPES = ("leisure", "business", "unknown")
TRAVELER_TYPES = (
    "couple",
    "solo",
    "family_young_children",
    "family_older_children",
    "group",
    "friends",
    "unknown",
)

HOTEL_ID_PATTERN = r"^h_[0-9a-f]{10}$"
REVIEW_ID_PATTERN = r"^r_[0-9a-f]{12}$"


class HotelSchema(pa.DataFrameModel):
    """One row per real hotel (identified by name + address)."""

    hotel_id: Series[str] = pa.Field(unique=True, str_matches=HOTEL_ID_PATTERN)
    name: Series[str] = pa.Field(str_length={"min_value": 1})
    address: Series[str] = pa.Field(str_length={"min_value": 1})
    city: Series[str] = pa.Field(isin=CITIES)
    country: Series[str] = pa.Field(isin=COUNTRIES)
    lat: Series[float] = pa.Field(ge=35.0, le=60.0, nullable=True)
    lng: Series[float] = pa.Field(ge=-10.0, le=20.0, nullable=True)
    source_average_score: Series[float] = pa.Field(ge=0.0, le=10.0)
    source_total_reviews: Series[int] = pa.Field(ge=0)
    n_reviews: Series[int] = pa.Field(ge=1)

    class Config:
        strict = False
        coerce = False


class ReviewSchema(pa.DataFrameModel):
    """One row per (deduplicated) guest review."""

    review_id: Series[str] = pa.Field(
        unique=True, str_matches=REVIEW_ID_PATTERN
    )
    hotel_id: Series[str] = pa.Field(str_matches=HOTEL_ID_PATTERN)
    review_date: Series[pd.Timestamp] = pa.Field(
        ge=pd.Timestamp("2010-01-01"), le=pd.Timestamp("2030-01-01")
    )
    reviewer_nationality: Series[str] = pa.Field(nullable=True)
    reviewer_score: Series[float] = pa.Field(ge=1.0, le=10.0)
    reviewer_review_count: Series[int] = pa.Field(ge=0)
    liked_text: Series[str] = pa.Field(
        nullable=True, str_length={"min_value": 1}
    )
    disliked_text: Series[str] = pa.Field(
        nullable=True, str_length={"min_value": 1}
    )
    trip_type: Series[str] = pa.Field(isin=TRIP_TYPES)
    traveler_type: Series[str] = pa.Field(isin=TRAVELER_TYPES)
    room_type: Series[str] = pa.Field(nullable=True)
    nights: Series[float] = pa.Field(ge=1, le=365, nullable=True)
    with_pet: Series[bool]
    from_mobile: Series[bool]

    class Config:
        strict = False
        coerce = False


def validate_hotels(
    hotels: pd.DataFrame, *, require_split: bool = False
) -> None:
    """Validate the hotels table; optionally require a ``split`` column.

    Raises:
        pandera.errors.SchemaErrors: On any contract violation.
        ValueError: If ``require_split`` and splits are missing or invalid.
    """
    HotelSchema.validate(hotels, lazy=True)
    if require_split:
        if "split" not in hotels.columns:
            raise ValueError("hotels table has no 'split' column")
        bad = set(hotels["split"].unique()) - set(SPLITS)
        if bad or hotels["split"].isna().any():
            raise ValueError(f"invalid split values: {sorted(map(str, bad))}")


def validate_reviews(reviews: pd.DataFrame, hotels: pd.DataFrame) -> None:
    """Validate reviews and their consistency with the hotels table.

    Checks the column contract, that every review's hotel exists, and that
    ``hotels.n_reviews`` matches the review counts.

    Raises:
        pandera.errors.SchemaErrors: On a column contract violation.
        ValueError: On referential or count inconsistencies.
    """
    ReviewSchema.validate(reviews, lazy=True)
    orphans = set(reviews["hotel_id"]) - set(hotels["hotel_id"])
    if orphans:
        raise ValueError(f"{len(orphans)} reviews reference unknown hotels")
    counts = reviews.groupby("hotel_id").size()
    expected = hotels.set_index("hotel_id")["n_reviews"]
    mismatch = expected.sub(counts, fill_value=0).ne(0)
    if mismatch.any():
        raise ValueError(
            f"n_reviews disagrees with review counts for {int(mismatch.sum())} "
            "hotels"
        )
