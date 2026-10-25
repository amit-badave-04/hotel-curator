"""Tests for spotting review parts that probably name a person."""

import pytest

from hotel_curator.scoring.names import NameGuard

COMMON = {
    "friendly",
    "helpful",
    "breakfast",
    "rude",
    "good",
    "staff",
    "location",
    "excellent",
}
GUARD = NameGuard(COMMON)


@pytest.mark.parametrize(
    "text",
    [
        # Corpus parts the NER model left unmasked (security review).
        "Friendly staff especially Maria good breakfast excellent location",
        "especially one of the receptionist called Laura Aguirrebengoa",
        "especially nicoletta at breakfast",
        "thank you maria for everything",
        "our waitress Maria was not very friendly or helpful",
        "staff is great specially Islam",
        # Served before the second rule: no trigger word before the name.
        "Great service Andreea was so helpful Great location",
        "professional staff on Reception Nicola was so helpful",
        "Petra the Staff member on reception was excellent",
        "Stephania at the desk was marvellous Very helpful",
    ],
)
def test_parts_naming_staff_are_caught(text: str) -> None:
    assert GUARD.names_someone(text)


@pytest.mark.parametrize(
    "text",
    [
        "staff especially friendly and helpful",
        "the manager was very rude",
        "especially the breakfast",
        "excellent location",
        "thanks",
        "Excellent location Friendly staff",
        "Lovely Andreea Street apartment",  # rare capital, but not about staff
    ],
)
def test_ordinary_parts_are_kept(text: str) -> None:
    assert not GUARD.names_someone(text)


def test_common_words_come_from_document_frequency() -> None:
    texts = ["the waiter maria"] + ["the waiter was friendly friendly"] * 3
    guard = NameGuard.from_texts(texts, min_share=0.5)  # in 2 of 4 parts
    assert guard.names_someone("the waiter maria")
    assert not guard.names_someone("the waiter friendly")


def test_in_a_small_corpus_every_seen_word_is_common() -> None:
    guard = NameGuard.from_texts(["Lovely staff Maria"] * 10)
    assert not guard.names_someone("Lovely staff Maria")
