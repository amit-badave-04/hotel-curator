"""Tests for parsing the Booking.com 515K raw format into canonical tables."""

import pandas as pd
import pytest

from hotel_curator.data.booking515k import (
    RAW_COLUMNS,
    build_canonical,
    clean_part,
    hotel_id_for,
    parse_tags,
)
from hotel_curator.data.contracts import validate_hotels, validate_reviews


def _raw_row(**overrides: str) -> dict[str, str]:
    row = {
        "Hotel_Address": (
            " s Gravesandestraat 55 Oost 1092 AA Amsterdam Netherlands"
        ),
        "Additional_Number_of_Scoring": "194",
        "Review_Date": "8/3/2017",
        "Average_Score": "7.7",
        "Hotel_Name": "Hotel Arena",
        "Reviewer_Nationality": " Russia ",
        "Negative_Review": " Room was tiny ",
        "Review_Total_Negative_Word_Counts": "3",
        "Total_Number_of_Reviews": "1403",
        "Positive_Review": " Great location ",
        "Review_Total_Positive_Word_Counts": "2",
        "Total_Number_of_Reviews_Reviewer_Has_Given": "7",
        "Reviewer_Score": "6.3",
        "Tags": "[' Leisure trip ', ' Couple ', ' Duplex Double Room ', "
        "' Stayed 6 nights ', ' Submitted from a mobile device ']",
        "days_since_review": "0 days",
        "lat": "52.3605759",
        "lng": "4.9159683",
    }
    row.update(overrides)
    return row


@pytest.fixture
def raw() -> pd.DataFrame:
    rows = [
        _raw_row(),
        _raw_row(Negative_Review="No Negative", Reviewer_Score="9.6"),
        _raw_row(Positive_Review="No Positive", Review_Date="12/24/2016"),
        # exact duplicate of the first row: must be dropped
        _raw_row(),
        _raw_row(
            Hotel_Name="Mayflower Hotel Apartments",
            Hotel_Address="26 28 Trebovir Rd London SW5 9NJ United Kingdom",
            Average_Score="7.5",
            Total_Number_of_Reviews="2197",
            Tags="[' Business trip ', ' Solo traveler ', ' Single Room ', "
            "' Stayed 1 night ']",
            lat="",
            lng="",
        ),
    ]
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


def test_parse_tags() -> None:
    tags = parse_tags(
        "[' Leisure trip ', ' Family with young children ', ' Twin Room ', "
        "' Stayed 2 nights ', ' With a pet ', "
        "' Submitted from a mobile device ']"
    )
    assert tags.trip_type == "leisure"
    assert tags.traveler_type == "family_young_children"
    assert tags.room_type == "Twin Room"
    assert tags.nights == 2
    assert tags.with_pet is True
    assert tags.from_mobile is True


def test_parse_tags_unknowns() -> None:
    tags = parse_tags("[' Stayed 1 night ']")
    assert tags.trip_type == "unknown"
    assert tags.traveler_type == "unknown"
    assert tags.room_type is None
    assert tags.nights == 1


@pytest.mark.parametrize(
    ("text", "placeholder", "expected"),
    [
        (" Great location ", "No Positive", "Great location"),
        ("No Positive", "No Positive", None),
        ("  ", "No Negative", None),
        ("Nothing", "No Negative", "Nothing"),
    ],
)
def test_clean_part(text: str, placeholder: str, expected: str | None) -> None:
    assert clean_part(text, placeholder) == expected


def test_hotel_id_is_stable_and_normalised() -> None:
    a = hotel_id_for("Hotel Arena", " Amsterdam Netherlands ")
    b = hotel_id_for("Hotel Arena ", "Amsterdam  Netherlands")
    assert a == b
    assert a.startswith("h_") and len(a) == 12


def test_build_canonical_tables(raw: pd.DataFrame) -> None:
    hotels, reviews, report = build_canonical(raw)
    validate_hotels(hotels)
    validate_reviews(reviews, hotels)
    assert len(hotels) == 2
    assert report.duplicate_rows_dropped == 1
    assert len(reviews) == 4
    arena = hotels.set_index("name").loc["Hotel Arena"]
    assert arena["city"] == "Amsterdam"
    assert arena["country"] == "Netherlands"
    assert arena["n_reviews"] == 3
    mayflower = hotels.set_index("name").loc["Mayflower Hotel Apartments"]
    assert mayflower["city"] == "London"
    assert pd.isna(mayflower["lat"])
    assert reviews["liked_text"].isna().sum() == 1
    assert reviews["disliked_text"].isna().sum() == 1
    assert reviews["review_date"].min() == pd.Timestamp("2016-12-24")
    assert set(reviews["traveler_type"]) == {"couple", "solo"}


def test_review_ids_are_deterministic(raw: pd.DataFrame) -> None:
    _, first, _ = build_canonical(raw)
    _, second, _ = build_canonical(raw.iloc[::-1].reset_index(drop=True))
    assert set(first["review_id"]) == set(second["review_id"])


def test_unknown_country_fails_loudly(raw: pd.DataFrame) -> None:
    raw.loc[0, "Hotel_Address"] = "1 Main Street Springfield Atlantis"
    with pytest.raises(ValueError, match="country"):
        build_canonical(raw)
