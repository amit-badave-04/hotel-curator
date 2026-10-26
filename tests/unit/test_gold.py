"""Tests for turning annotation sets into part-level label tables."""

import json
from pathlib import Path

import pandas as pd
import pytest

from hotel_curator.labeling.batches import write_answers, write_set
from hotel_curator.labeling.gold import (
    PanelResult,
    build_label_table,
    human_audit_sheet,
    merge_set,
)
from hotel_curator.labeling.prompt import load_guideline
from hotel_curator.taxonomy import LABELS


@pytest.fixture
def labeled_set(tmp_path: Path) -> Path:
    sample = pd.DataFrame(
        {
            "review_id": ["r_a", "r_b", "r_c"],
            "hotel_id": ["h_1", "h_1", "h_2"],
            "liked_text": ["Friendly staff", None, "Great location"],
            "disliked_text": ["Tiny room", "Noisy street", None],
            "stratum": ["uniform", "enriched", "uniform"],
            "sampling_weight": [2.0, 1.0, 2.0],
        }
    )
    write_set(sample, "gold_test", tmp_path, load_guideline(), batch_size=2)
    answers = {
        "A": [("r_a", "S+", "R-"), ("r_b", "", "N-"), ("r_c", "L+", "")],
        "B": [("r_a", "S+", "R- N-"), ("r_b", "", "N-"), ("r_c", "L+", "")],
        "C": [("r_a", "S+", ""), ("r_b", "", "N- W-"), ("r_c", "", "")],
    }
    for annotator, rows in answers.items():
        write_answers(
            [{"id": r, "liked": lk, "disliked": dl} for r, lk, dl in rows],
            tmp_path / "gold_test" / "answers" / f"{annotator}.jsonl",
            annotator=annotator,
            meta={},
        )
    return tmp_path / "gold_test"


def test_merge_set_majority_and_agreement(labeled_set: Path) -> None:
    result = merge_set(labeled_set, ("A", "B", "C"))
    assert isinstance(result, PanelResult)
    table = result.parts
    # Only parts with text: r_a liked+disliked, r_b disliked, r_c liked.
    assert len(table) == 4
    row = table.set_index(["review_id", "part"]).loc[("r_a", "disliked")]
    assert row["room_negative"]  # A and B agree
    assert not row["noise_negative"]  # only B
    r_c = table.set_index(["review_id", "part"]).loc[("r_c", "liked")]
    assert r_c["location_positive"]  # A and B
    assert set(LABELS) <= set(table.columns)
    assert {a.label for a in result.agreement} == set(LABELS)


def test_merge_set_single_annotator(labeled_set: Path) -> None:
    result = merge_set(labeled_set, ("A",))
    row = result.parts.set_index(["review_id", "part"]).loc[("r_a", "disliked")]
    assert row["room_negative"]
    assert result.agreement == []


def test_merge_set_missing_answers_fail(labeled_set: Path) -> None:
    (labeled_set / "answers" / "C.jsonl").write_text(
        json.dumps({"_meta": {"annotator": "C"}}) + "\n"
    )
    with pytest.raises(ValueError, match="missing"):
        merge_set(labeled_set, ("A", "B", "C"))


def test_build_label_table_joins_hotel_metadata(labeled_set: Path) -> None:
    hotels = pd.DataFrame(
        {"hotel_id": ["h_1", "h_2"], "city": ["Paris", "Vienna"]}
    )
    table = build_label_table(merge_set(labeled_set, ("A",)).parts, hotels)
    assert set(table["city"]) == {"Paris", "Vienna"}


def test_human_audit_sheet_hides_labels(labeled_set: Path) -> None:
    sample = pd.read_parquet(labeled_set / "sample.parquet")
    sheet = human_audit_sheet(sample, n=2, seed=0)
    assert len(sheet) == 2
    assert {
        "review_id",
        "liked_text",
        "disliked_text",
        "liked_codes",
        "disliked_codes",
    } <= set(sheet.columns)
    assert (sheet["liked_codes"] == "").all()
