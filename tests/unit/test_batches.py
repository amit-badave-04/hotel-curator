"""Tests for writing annotation sets and collecting panel answers."""

import json
from pathlib import Path

import pandas as pd
import pytest

from hotel_curator.labeling.batches import (
    MAX_PART_CHARS,
    AnnotationSet,
    load_answers,
    rewrite_prompts,
    to_items,
    write_answers,
    write_set,
)
from hotel_curator.labeling.prompt import load_guideline


@pytest.fixture
def sample() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "review_id": [f"r_{i:012x}" for i in range(5)],
            "hotel_id": ["h_0000000001"] * 5,
            "liked_text": [
                "Great location",
                None,
                "Clean",
                "x" * 5000,
                "Staff",
            ],
            "disliked_text": ["Tiny room", "Noisy", None, "Bad wifi", None],
            "stratum": ["uniform"] * 5,
            "sampling_weight": [1.0] * 5,
        }
    )


def test_to_items_truncates_and_blanks_missing(sample: pd.DataFrame) -> None:
    items = to_items(sample)
    assert items[1].liked == ""
    assert len(items[3].liked) == MAX_PART_CHARS
    assert items[0].disliked == "Tiny room"


def test_write_set_creates_prompts_and_index(
    sample: pd.DataFrame, tmp_path: Path
) -> None:
    aset = write_set(
        sample, "gold_test", tmp_path, load_guideline(), batch_size=2
    )
    assert isinstance(aset, AnnotationSet)
    assert len(aset.batches) == 3
    index = json.loads((tmp_path / "gold_test" / "index.json").read_text())
    assert len(index["batches"]) == 3
    first = index["batches"][0]
    assert set(first["prompts"]) == {"forward", "reverse"}
    forward = (
        tmp_path / "gold_test" / first["prompts"]["forward"]["path"]
    ).read_text()
    reverse = (
        tmp_path / "gold_test" / first["prompts"]["reverse"]["path"]
    ).read_text()
    assert forward.index("r_000000000000") < forward.index("r_000000000001")
    assert reverse.index("r_000000000001") < reverse.index("r_000000000000")
    assert (tmp_path / "gold_test" / "sample.parquet").exists()


def test_answers_round_trip(tmp_path: Path) -> None:
    answers = [
        {"id": "r_1", "liked": "S+", "disliked": ""},
        {"id": "r_2", "liked": "", "disliked": "N-"},
    ]
    path = write_answers(
        answers, tmp_path / "a.jsonl", annotator="A", meta={"m": 1}
    )
    parsed, meta = load_answers(path, {"r_1", "r_2"})
    assert parsed.labels["r_2"] == (set(), {"noise_negative"})
    assert meta == {"annotator": "A", "m": 1}


def test_to_items_respects_custom_cap(sample: pd.DataFrame) -> None:
    items = to_items(sample, max_chars=10)
    assert len(items[3].liked) == 10


def test_rewrite_prompts_rebuilds_and_checks_hashes(
    sample: pd.DataFrame, tmp_path: Path
) -> None:
    guideline = load_guideline()
    write_set(sample, "s", tmp_path, guideline, batch_size=2)
    set_dir = tmp_path / "s"
    first = (set_dir / "prompts" / "batch_000_forward.txt").read_bytes()
    for path in (set_dir / "prompts").iterdir():
        path.unlink()
    assert rewrite_prompts(set_dir, guideline) == 6
    assert (set_dir / "prompts" / "batch_000_forward.txt").read_bytes() == first
    changed = sample.assign(liked_text=sample["liked_text"].str.upper())
    changed.to_parquet(set_dir / "sample.parquet", index=False)
    with pytest.raises(ValueError, match="does not match"):
        rewrite_prompts(set_dir, guideline)
