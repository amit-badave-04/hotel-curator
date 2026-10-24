"""Tests for the generated error analysis and model card."""

import numpy as np
import pandas as pd

from hotel_curator.ml.reports import (
    CardInputs,
    error_table,
    errors_markdown,
    model_card,
)
from hotel_curator.taxonomy import LABELS


def _gold_and_scores() -> tuple[pd.DataFrame, pd.DataFrame]:
    gold = pd.DataFrame(
        {
            "review_id": ["r1", "r2", "r3"],
            "part": ["liked", "disliked", "liked"],
            "text": ["clean room", "dirty bath", "nice staff"],
            **{label: [False, False, False] for label in LABELS},
        }
    )
    gold.loc[1, "cleanliness_negative"] = True
    gold.loc[2, "staff_positive"] = True
    scores = gold[["review_id", "part"]].copy()
    for label in LABELS:
        scores[label] = 0.05
    scores.loc[0, "cleanliness_negative"] = 0.9  # confident false positive
    scores.loc[1, "cleanliness_negative"] = 0.2  # false negative
    scores.loc[2, "staff_positive"] = 0.95  # true positive
    return gold, scores


def test_error_table_and_markdown() -> None:
    gold, scores = _gold_and_scores()
    table = error_table(gold, scores, dict.fromkeys(LABELS, 0.5))
    assert len(table) == 3 * len(LABELS)
    text = errors_markdown("m", table)
    assert "| cleanliness_negative | 1 | 1 | 1 |" in text
    assert 'False positive (score 0.90, liked): "clean room"' in text
    assert 'False negative (score 0.20, disliked): "dirty bath"' in text
    assert "## staff_positive" not in text  # no mistakes, no section


def _report() -> dict:
    ci = {"point": 0.9, "low": 0.88, "high": 0.92}
    split = {
        "review_macro_f1": ci,
        "review_micro_f1": 0.93,
        "part_macro_f1": ci,
        "review_ece": dict.fromkeys(LABELS, 0.01),
        "review_brier": 0.02,
        "cross_polarity": {"f1": 0.7, "support": 100.0},
        "per_label": [
            {"label": label, "support": 100 + k, "f1": 0.95 - k / 100}
            for k, label in enumerate(LABELS)
        ],
    }
    return {
        "summary": {"seconds_train": 600.0},
        "params": {
            "base_model": "roberta-base",
            "n_train_parts": 6955,
            "history": [{"epoch": 1, "train_loss": 0.3, "dev_macro_f1": 0.8}],
        },
        "dev": split,
        "test": split,
    }


def test_model_card_contains_headline_numbers() -> None:
    promotion = {
        "v1_config": "v1-x",
        "rows": [
            {"name": "m", "vs_v1": {"point": 0.2, "low": 0.18, "high": 0.22}},
            {
                "name": "v1-x",
                "vs_v1": None,
                "summary": {
                    "test_review_macro_f1": 0.7,
                    "test_review_macro_f1_low": 0.68,
                    "test_review_macro_f1_high": 0.72,
                },
            },
        ],
    }
    card = model_card(
        CardInputs(
            "m",
            _report(),
            {
                "weights": "int8",
                "dev_review_macro_f1": {"fp32": 0.85, "int8": 0.848},
                "max_quantization_loss": 0.005,
                "gold_test": {
                    "review_macro_f1": {
                        "point": 0.9,
                        "low": 0.88,
                        "high": 0.92,
                    },
                    "review_ece": {"a": 0.01, "b": 0.03},
                },
            },
            {
                "parts_per_second": 200.0,
                "sample_parts": 20000.0,
                "corpus_parts": 865732.0,
                "corpus_hours": 1.2,
            },
            {"title": "RoBERTa", "license": "MIT", "provenance": "S3"},
            promotion,
        )
    )
    assert "| Review-level macro-F1 | 0.900 [0.880, 0.920] |" in card
    assert "+0.200 [+0.180, +0.220]" in card
    assert "int8 weights" in card
    assert "loses at most 0.005" in card
    assert "mean review-level ECE 0.0200" in card
    assert (
        "Fewest gold-test examples: cleanliness_positive (100 reviews)" in card
    )
    assert "lowest F1: food_negative (0.800)" in card
    assert "comparison with modern encoders is partial" in card
    assert "about 1.20 hours" in card
    assert np.isclose(float(card.split("ECE (calibrated) | ")[1][:6]), 0.01)
