"""Review parts: the unit the classifier scores.

A review has up to two free-text parts, what the guest liked and what they
disliked. Models score each non-empty part separately; review-level results
combine them (noisy-OR) downstream.
"""

from typing import Literal

import pandas as pd

Part = Literal["liked", "disliked"]
PARTS: tuple[Part, ...] = ("liked", "disliked")


def corpus_parts(reviews: pd.DataFrame) -> pd.DataFrame:
    """Every non-empty review part, liked parts first.

    Args:
        reviews: Canonical reviews with ``liked_text`` and
            ``disliked_text``; ``review_id`` is carried over when present.

    Returns:
        Columns ``[review_id,] part, text``; empty parts are dropped.

    Raises:
        KeyError: If a part's text column is missing.
    """
    missing = [f"{p}_text" for p in PARTS if f"{p}_text" not in reviews]
    if missing:
        raise KeyError(f"reviews lack columns {missing}")
    keep = ["review_id"] if "review_id" in reviews.columns else []
    frames = [
        reviews[keep].assign(part=part, text=reviews[f"{part}_text"])
        for part in PARTS
    ]
    return pd.concat(frames).dropna(subset=["text"])
