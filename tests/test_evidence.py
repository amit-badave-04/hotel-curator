"""Tests for hotel_curator.core.evidence."""

import pandas as pd

from hotel_curator.core.evidence import build_evidence
from hotel_curator.schema import ATTRIBUTES


def test_evidence_structure(
    sample_predictions: pd.DataFrame,
    sample_reviews: pd.DataFrame,
) -> None:
    label_names = [
        f"{attr}_{pol}"
        for attr in ATTRIBUTES
        for pol in ("positive", "negative")
    ]
    evidence = build_evidence(
        sample_predictions, sample_reviews,
        ATTRIBUTES, label_names, top_k=2,
    )
    assert isinstance(evidence, dict)
    assert 1 in evidence or 2 in evidence
    for hid, attrs in evidence.items():
        for lbl, snippets in attrs.items():
            assert len(snippets) <= 2
            for s in snippets:
                assert "review_id" in s
                assert "text" in s
                assert "prob" in s
