"""Parse the Booking.com "515K Hotel Reviews Data in Europe" raw CSV.

Source: Kaggle ``jiashenliu/515k-hotel-reviews-data-in-europe`` (CC0),
scraped from Booking.com, 2015-08 to 2017-08, 1,492 hotels in six cities.
See ``docs/data/DATA_CARD.md`` for provenance and known quirks.

This module turns the 17 raw columns into the two canonical tables
(``hotels`` and ``reviews``). What it does, and why:

* **Real hotel identity.** ``hotel_id`` is a hash of the normalised hotel
  name and address. v1 had no hotel identity and invented one; v2 never
  does.
* **Placeholders become missing parts.** The site writes "No Positive" /
  "No Negative" when a guest leaves a box empty; those become ``None``.
  Guest-written text such as "Nothing" is kept verbatim: whether it carries
  a judgement is the classifier's job, not a hand-made list's.
* **Exact duplicate rows are dropped**, and their count is reported.
* **Tags are parsed** into trip type, traveler type, room type, nights,
  pet and mobile flags.
* **No lossy text preprocessing.** The source already removed punctuation;
  v2 only strips and collapses whitespace.
"""

import hashlib
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

RAW_COLUMNS: tuple[str, ...] = (
    "Hotel_Address",
    "Additional_Number_of_Scoring",
    "Review_Date",
    "Average_Score",
    "Hotel_Name",
    "Reviewer_Nationality",
    "Negative_Review",
    "Review_Total_Negative_Word_Counts",
    "Total_Number_of_Reviews",
    "Positive_Review",
    "Review_Total_Positive_Word_Counts",
    "Total_Number_of_Reviews_Reviewer_Has_Given",
    "Reviewer_Score",
    "Tags",
    "days_since_review",
    "lat",
    "lng",
)
RAW_ROW_COUNT = 515_738
"""Row count of the original Kaggle file, used as an integrity check."""

LIKED_PLACEHOLDER = "No Positive"
DISLIKED_PLACEHOLDER = "No Negative"
COUNTRY_TO_CITY: dict[str, str] = {
    "Austria": "Vienna",
    "France": "Paris",
    "Italy": "Milan",
    "Netherlands": "Amsterdam",
    "Spain": "Barcelona",
    "United Kingdom": "London",
}
_TRIP = {"Leisure trip": "leisure", "Business trip": "business"}
_TRAVELER = {
    "Couple": "couple",
    "Solo traveler": "solo",
    "Family with young children": "family_young_children",
    "Family with older children": "family_older_children",
    "Group": "group",
    "Travelers with friends": "friends",
}
_TAG_RE = re.compile(r"'\s*([^']*?)\s*'")
_NIGHTS_RE = re.compile(r"^Stayed (\d+) nights?$")
_ROOMS_RE = re.compile(r"^\d+ rooms?$")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Tags:
    """Structured view of the raw ``Tags`` column."""

    trip_type: str
    traveler_type: str
    room_type: str | None
    nights: int | None
    with_pet: bool
    from_mobile: bool


@dataclass(frozen=True)
class BuildReport:
    """Counts recorded in the data manifest and data card."""

    raw_rows: int
    duplicate_rows_dropped: int
    hotels: int
    reviews: int
    reviews_without_text: int
    hotels_missing_coordinates: int


def _normalise(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def parse_tags(raw: str) -> Tags:
    """Parse a raw tag string such as ``"[' Leisure trip ', ' Couple ']"``."""
    trip, traveler, room, nights = "unknown", "unknown", None, None
    with_pet = from_mobile = False
    for tag in _TAG_RE.findall(raw):
        if tag in _TRIP:
            trip = _TRIP[tag]
        elif tag in _TRAVELER:
            traveler = _TRAVELER[tag]
        elif tag == "With a pet":
            with_pet = True
        elif tag == "Submitted from a mobile device":
            from_mobile = True
        elif match := _NIGHTS_RE.match(tag):
            nights = int(match.group(1))
        elif _ROOMS_RE.match(tag) or not tag:
            continue
        elif room is None:
            room = tag
    return Tags(trip, traveler, room, nights, with_pet, from_mobile)


def clean_part(text: str, placeholder: str) -> str | None:
    """Collapse whitespace; ``None`` for empty text or the placeholder."""
    cleaned = _normalise(text)
    if not cleaned or cleaned == placeholder:
        return None
    return cleaned


def _short_hash(*parts: str, length: int) -> str:
    joined = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha1(joined, usedforsecurity=False).hexdigest()[:length]


def hotel_id_for(name: str, address: str) -> str:
    """Stable id from normalised, case-folded hotel name and address."""
    key = (_normalise(name).casefold(), _normalise(address).casefold())
    return "h_" + _short_hash(*key, length=10)


def _country_of(address: str) -> str:
    for country in COUNTRY_TO_CITY:
        if address.endswith(country):
            return country
    raise ValueError(f"Unrecognised country in hotel address: {address!r}")


def _build_hotels(raw: pd.DataFrame) -> pd.DataFrame:
    first = raw.drop_duplicates("hotel_id").copy()
    country = first["address"].map(_country_of)
    hotels = pd.DataFrame(
        {
            "hotel_id": first["hotel_id"],
            "name": first["name"],
            "address": first["address"],
            "city": country.map(COUNTRY_TO_CITY),
            "country": country,
            "lat": pd.to_numeric(first["lat"], errors="coerce").astype(float),
            "lng": pd.to_numeric(first["lng"], errors="coerce").astype(float),
            "source_average_score": first["Average_Score"].astype(float),
            "source_total_reviews": first["Total_Number_of_Reviews"].astype(
                np.int64
            ),
        }
    )
    counts = raw.groupby("hotel_id").size().rename("n_reviews")
    hotels = hotels.merge(counts, on="hotel_id").astype({"n_reviews": np.int64})
    return hotels.sort_values("hotel_id").reset_index(drop=True)


def _build_reviews(raw: pd.DataFrame) -> pd.DataFrame:
    tags = raw["Tags"].map(parse_tags)
    nationality = raw["Reviewer_Nationality"].map(_normalise)
    review_id = [
        "r_" + _short_hash(*map(str, row), length=12)
        for row in raw[list(RAW_COLUMNS)].itertuples(index=False)
    ]
    reviews = pd.DataFrame(
        {
            "review_id": review_id,
            "hotel_id": raw["hotel_id"].to_numpy(),
            "review_date": pd.to_datetime(
                raw["Review_Date"], format="%m/%d/%Y"
            ),
            "reviewer_nationality": nationality.where(nationality != "", None),
            "reviewer_score": raw["Reviewer_Score"].astype(float).to_numpy(),
            "reviewer_review_count": raw[
                "Total_Number_of_Reviews_Reviewer_Has_Given"
            ]
            .astype(np.int64)
            .to_numpy(),
            "liked_text": raw["Positive_Review"].map(
                lambda t: clean_part(t, LIKED_PLACEHOLDER)
            ),
            "disliked_text": raw["Negative_Review"].map(
                lambda t: clean_part(t, DISLIKED_PLACEHOLDER)
            ),
            "trip_type": [t.trip_type for t in tags],
            "traveler_type": [t.traveler_type for t in tags],
            "room_type": [t.room_type for t in tags],
            "nights": pd.array(
                [t.nights for t in tags], dtype="Float64"
            ).astype(float),
            "with_pet": [t.with_pet for t in tags],
            "from_mobile": [t.from_mobile for t in tags],
        }
    )
    return reviews.sort_values("review_id").reset_index(drop=True)


def build_canonical(
    raw: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, BuildReport]:
    """Turn raw 515K rows (all columns as strings) into canonical tables.

    Args:
        raw: The raw CSV read with ``dtype=str, keep_default_na=False``.

    Returns:
        ``(hotels, reviews, report)``.

    Raises:
        KeyError: If raw columns are missing.
        ValueError: If a hotel address has an unknown country.
    """
    missing = set(RAW_COLUMNS) - set(raw.columns)
    if missing:
        raise KeyError(f"Raw data is missing columns: {sorted(missing)}")
    deduped = raw.drop_duplicates(subset=list(RAW_COLUMNS)).copy()
    deduped["name"] = deduped["Hotel_Name"].map(_normalise)
    deduped["address"] = deduped["Hotel_Address"].map(_normalise)
    deduped["hotel_id"] = [
        hotel_id_for(n, a)
        for n, a in zip(deduped["name"], deduped["address"], strict=True)
    ]
    hotels = _build_hotels(deduped)
    reviews = _build_reviews(deduped)
    report = BuildReport(
        raw_rows=len(raw),
        duplicate_rows_dropped=len(raw) - len(deduped),
        hotels=len(hotels),
        reviews=len(reviews),
        reviews_without_text=int(
            (
                reviews["liked_text"].isna() & reviews["disliked_text"].isna()
            ).sum()
        ),
        hotels_missing_coordinates=int(hotels["lat"].isna().sum()),
    )
    return hotels, reviews, report
