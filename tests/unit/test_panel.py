"""Tests for annotation batch building, strict parsing and panel merging."""

import json

import numpy as np
import pytest

from hotel_curator.labeling.panel import (
    AnnotationItem,
    ParsedAnnotation,
    merge_panel,
    parse_annotations,
    render_batch,
)

ITEMS = [
    AnnotationItem("r1", "Friendly staff, great location", "Tiny room"),
    AnnotationItem("r2", "", "Noisy street"),
]


def test_render_batch_is_jsonl_with_ids_and_parts() -> None:
    lines = render_batch(ITEMS).splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first == {
        "id": "r1",
        "liked": "Friendly staff, great location",
        "disliked": "Tiny room",
    }


def test_parse_valid_output_with_code_fence() -> None:
    raw = (
        "```json\n"
        '{"id": "r1", "liked": "S+ L+", "disliked": "R-"}\n'
        '{"id": "r2", "liked": "", "disliked": "N-"}\n'
        "```"
    )
    parsed = parse_annotations(raw, {"r1", "r2"})
    assert parsed.labels["r1"] == (
        {"staff_positive", "location_positive"},
        {"room_negative"},
    )
    assert parsed.labels["r2"] == (set(), {"noise_negative"})
    assert parsed.missing == set()
    assert parsed.errors == {}


def test_parse_reports_missing_and_invalid_items() -> None:
    raw = '{"id": "r1", "liked": "S+ X+", "disliked": ""}\n'
    parsed = parse_annotations(raw, {"r1", "r2"})
    assert "r1" not in parsed.labels
    assert "r1" in parsed.errors
    assert parsed.missing == {"r2"}


def test_parse_ignores_unknown_ids_but_records_them() -> None:
    raw = '{"id": "zzz", "liked": "S+", "disliked": ""}\n'
    parsed = parse_annotations(raw, {"r1"})
    assert parsed.unexpected == {"zzz"}
    assert parsed.missing == {"r1"}


def test_parse_rejects_non_string_fields() -> None:
    raw = '{"id": "r1", "liked": true, "disliked": ""}\n'
    parsed = parse_annotations(raw, {"r1"})
    assert "r1" in parsed.errors


def test_merge_panel_majority_and_rater_matrix() -> None:
    labels = ["staff_positive", "room_negative"]
    raters = [
        ParsedAnnotation(
            labels={"r1": ({"staff_positive"}, {"room_negative"})}
        ),
        ParsedAnnotation(labels={"r1": ({"staff_positive"}, set())}),
        ParsedAnnotation(labels={"r1": (set(), {"room_negative"})}),
    ]
    merged = merge_panel(raters, ["r1"], labels)
    # (items, raters, labels) per part
    assert merged.liked_ratings.shape == (1, 3, 2)
    assert merged.liked_majority.tolist() == [[True, False]]
    assert merged.disliked_majority.tolist() == [[False, True]]


def test_merge_panel_requires_complete_coverage() -> None:
    raters = [ParsedAnnotation(labels={}) for _ in range(3)]
    with pytest.raises(ValueError, match="missing"):
        merge_panel(raters, ["r1"], ["staff_positive"])


def test_merge_panel_matrix_dtype() -> None:
    raters = [ParsedAnnotation(labels={"r1": (set(), set())}) for _ in range(3)]
    merged = merge_panel(raters, ["r1"], ["staff_positive"])
    assert merged.liked_majority.dtype == np.bool_


def test_duplicate_ids_are_reported_as_errors() -> None:
    raw = (
        '{"id": "r1", "liked": "S+", "disliked": ""}\n'
        '{"id": "r1", "liked": "S-", "disliked": ""}\n'
    )
    parsed = parse_annotations(raw, {"r1"})
    assert "r1" not in parsed.labels
    assert "duplicate" in parsed.errors["r1"]


def test_update_clears_errors_fixed_by_a_later_batch() -> None:
    first = parse_annotations(
        '{"id": "r1", "liked": "X+", "disliked": ""}', {"r1"}
    )
    second = parse_annotations(
        '{"id": "r1", "liked": "S+", "disliked": ""}', {"r1"}
    )
    first.update(second)
    assert first.labels["r1"] == ({"staff_positive"}, set())
    assert "r1" not in first.errors
    assert first.missing == set()
