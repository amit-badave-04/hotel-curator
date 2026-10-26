"""Tests for the TripAdvisor Annotated external human-label check."""

import json
from pathlib import Path

import pytest

from hotel_curator.labeling.external import (
    EXTERNAL_LABELS,
    human_labels,
    load_reviews,
    review_labels,
)

ROW = {
    "annotatorId": 0,
    "author": "someone",
    "hotelId": "1",
    "reviewId": "1:1",
    "segmentLabels": [
        {"OTHER": "p", "SERVICE": "p"},
        {"ROOMS": "n", "CLEANLINESS": "in"},
        {"BUILDING": "x", "VALUE": "x"},
        {"CHECKIN": "ip"},
    ],
    "segments": ["Friendly staff.", "Tiny room.", "Old.", "Fast check-in."],
}


def test_review_labels_explicit_only() -> None:
    labels = review_labels(ROW["segmentLabels"], implicit=False)
    assert labels == {"staff_positive", "room_negative"}


def test_review_labels_with_implicit() -> None:
    labels = review_labels(ROW["segmentLabels"], implicit=True)
    assert labels == {
        "staff_positive",
        "room_negative",
        "cleanliness_negative",
    }


def test_external_labels_exclude_noise_and_wifi() -> None:
    assert not any(lbl.startswith(("noise", "wifi")) for lbl in EXTERNAL_LABELS)
    assert len(EXTERNAL_LABELS) == 12


def test_load_reviews_drops_author_and_joins_text(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    path.write_text(json.dumps(ROW) + "\n")
    reviews = load_reviews([path])
    assert "author" not in reviews.columns
    assert reviews.loc[0, "text"].startswith("Friendly staff. Tiny room.")


def test_human_labels_majority(tmp_path: Path) -> None:
    files = []
    for k, rooms in enumerate(["n", "n", "p"]):
        row = dict(ROW, annotatorId=k)
        row["segmentLabels"] = [{"ROOMS": rooms}]
        path = tmp_path / f"{k}.json"
        path.write_text(json.dumps(row) + "\n")
        files.append(path)
    per_annotator = human_labels(files, implicit=False)
    assert per_annotator.shape == (1, 3, len(EXTERNAL_LABELS))


@pytest.mark.parametrize("bad", ["q", "", "nn"])
def test_unknown_polarity_codes_are_ignored(bad: str) -> None:
    assert review_labels([{"ROOMS": bad}], implicit=True) == set()


def test_compare_label_sets() -> None:
    from hotel_curator.labeling.external import compare_label_sets

    reference = {"a": {"room_negative"}, "b": set(), "c": {"staff_positive"}}
    panel = {
        "a": {"room_negative"},
        "b": {"staff_positive"},
        "c": {"staff_positive"},
    }
    rows = compare_label_sets(
        panel, reference, ["room_negative", "staff_positive"]
    )
    by_label = {r["label"]: r for r in rows}
    assert by_label["room_negative"]["f1"] == 1.0
    assert by_label["staff_positive"]["precision"] == 0.5
    assert by_label["staff_positive"]["recall"] == 1.0
    assert by_label["room_negative"]["support"] == 1


def test_triple_set_kappas_panel_equal_to_a_human() -> None:
    import numpy as np

    from hotel_curator.labeling.external import triple_set_kappas

    rng = np.random.default_rng(0)
    humans = rng.random((60, 3, 2)) < 0.4
    panel = [
        {lbl for k, lbl in enumerate(["a", "b"]) if humans[i, 0, k]}
        for i in range(60)
    ]
    rows = triple_set_kappas(humans, panel, ["a", "b"])
    assert [r["label"] for r in rows] == ["a", "b"]
    for row in rows:
        assert -1.0 <= float(row["human_human_kappa"]) <= 1.0
        assert float(row["panel_human_kappa"]) > float(
            row["human_human_kappa"]
        )  # one of its three pairs is a perfect copy


def test_triple_set_kappas_rejects_the_wrong_number_of_raters() -> None:
    import numpy as np

    from hotel_curator.labeling.external import triple_set_kappas

    two_raters = np.zeros((4, 2, 1), dtype=bool)
    with pytest.raises(ValueError, match="shape"):
        triple_set_kappas(two_raters, [set()] * 4, ["a"])
