"""Tests for the label taxonomy and the compact label-code format."""

import pytest

from hotel_curator.taxonomy import (
    ATTRIBUTE_INFO,
    LABELS,
    V1_TO_V2_LABEL,
    Attribute,
    Polarity,
    format_codes,
    label_name,
    parse_codes,
)


def test_sixteen_labels_in_attribute_major_order() -> None:
    assert len(LABELS) == 16
    assert LABELS[0] == "cleanliness_positive"
    assert LABELS[1] == "cleanliness_negative"
    assert LABELS[-1] == "food_negative"


def test_every_attribute_has_unique_single_letter_code() -> None:
    codes = [info.code for info in ATTRIBUTE_INFO.values()]
    assert len(set(codes)) == len(Attribute)
    assert all(len(code) == 1 and code.isupper() for code in codes)


def test_label_name() -> None:
    assert label_name(Attribute.WIFI, Polarity.NEGATIVE) == "wifi_negative"


def test_parse_codes_round_trip() -> None:
    labels = parse_codes("S+ C+ L+ R-")
    assert labels == {
        "staff_positive",
        "cleanliness_positive",
        "location_positive",
        "room_negative",
    }
    assert parse_codes(format_codes(labels)) == labels


def test_parse_codes_accepts_empty_and_extra_whitespace() -> None:
    assert parse_codes("") == set()
    assert parse_codes("   ") == set()
    assert parse_codes("  N-   W+ ") == {"noise_negative", "wifi_positive"}


def test_parse_codes_allows_both_polarities() -> None:
    assert parse_codes("R+ R-") == {"room_positive", "room_negative"}


@pytest.mark.parametrize("bad", ["X+", "C", "C*", "c+", "C+S+", "C++"])
def test_parse_codes_rejects_invalid_tokens(bad: str) -> None:
    with pytest.raises(ValueError, match="label code"):
        parse_codes(bad)


def test_format_codes_is_canonical_order() -> None:
    assert format_codes({"food_negative", "cleanliness_positive"}) == ("C+ F-")


def test_format_codes_rejects_unknown_label() -> None:
    with pytest.raises(KeyError):
        format_codes({"parking_negative"})


def test_v1_mapping_renames_breakfast_to_food() -> None:
    assert V1_TO_V2_LABEL["breakfast_positive"] == "food_positive"
    assert V1_TO_V2_LABEL["noise_negative"] == "noise_negative"
    assert set(V1_TO_V2_LABEL.values()) == set(LABELS)


def test_critical_attributes_are_cleanliness_room_staff() -> None:
    critical = {a for a, info in ATTRIBUTE_INFO.items() if info.critical}
    assert critical == {Attribute.CLEANLINESS, Attribute.ROOM, Attribute.STAFF}
