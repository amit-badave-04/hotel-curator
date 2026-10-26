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
        "ece": dict.fromkeys(LABELS, 0.03),
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


def _promotion() -> dict:
    return {
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


def _packaged_card() -> str:
    """The card of a packaged rung with its throughput measured."""
    package = {
        "weights": "int8",
        "dev_review_macro_f1": {"fp32": 0.85, "int8": 0.848},
        "max_quantization_loss": 0.005,
        "gold_test": {
            "review_macro_f1": {"point": 0.9, "low": 0.88, "high": 0.92},
            "review_ece": {"a": 0.01, "b": 0.03},
        },
    }
    throughput = {
        "parts_per_second": 200.0,
        "sample_parts": 20000.0,
        "corpus_parts": 865732.0,
        "corpus_hours": 1.2,
    }
    provenance = {"title": "RoBERTa", "license": "MIT", "provenance": "S3"}
    return model_card(
        CardInputs(
            "m", _report(), package, throughput, provenance, _promotion()
        )
    )


def test_model_card_contains_headline_numbers() -> None:
    card = _packaged_card()
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
    review_ece = card.split("ECE (calibrated; rule 3) | ")[1][:6]
    assert np.isclose(float(review_ece), 0.01)


def test_model_card_flags_estimates_and_unchecked_labels() -> None:
    card = _packaged_card()
    # The corpus time is an extrapolation from the sample, and says so.
    assert "an estimated 1.20 hours (rule 4's measure), not a timed" in card
    assert "takes about" not in card
    assert "| Mean part-level ECE (calibrated) | 0.0300 |" in card
    assert (
        "No human or external check for noise_positive, noise_negative, "
        "wifi_positive, wifi_negative" in card
    )


def test_model_card_without_part_level_ece_shows_a_dash() -> None:
    report = _report()
    del report["test"]["ece"]
    card = model_card(CardInputs("m", report, None, None, None, _promotion()))
    assert "| Mean part-level ECE (calibrated) | - |" in card
