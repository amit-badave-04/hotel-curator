"""Tests for the shared text normalisation and input formatting."""

import pytest

from hotel_curator.text import format_input, normalize_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Great location!", "Great location"),
        ("The room didn't have a kettle.", "The room didn t have a kettle"),
        ("Café & bar &#39;OK&#39;", "Cafe bar OK"),
        ("  lots   of\n\twhitespace ", "lots of whitespace"),
        ("Earl s Court", "Earl s Court"),
        ("€120/night -- too much", "120 night too much"),
        ("", ""),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_normalize_is_idempotent() -> None:
    text = "Ünïcödé, punctuation; and CASE!"
    once = normalize_text(text)
    assert normalize_text(once) == once
    assert once == "Unicode punctuation and CASE"


def test_format_input_normalises_and_prefixes() -> None:
    assert format_input("Great location!", "liked") == "Liked: Great location"
    assert format_input("Tiny.", "disliked") == "Disliked: Tiny"
    with pytest.raises(ValueError, match="part"):
        format_input("x", "other")
