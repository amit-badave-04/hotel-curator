"""Shared test fixtures."""

import numpy as np
import pandas as pd
import pytest

from hotel_curator.schema import ATTRIBUTES


@pytest.fixture
def sample_predictions() -> pd.DataFrame:
    """Minimal predictions DataFrame for unit tests."""
    rng = np.random.default_rng(42)
    n = 50
    data = {
        "review_id": list(range(n)),
        "hotel_id": [1] * 25 + [2] * 25,
    }
    for attr in ATTRIBUTES:
        for pol in ("positive", "negative"):
            lbl = f"{attr}_{pol}"
            data[f"pred_{lbl}"] = rng.integers(
                0, 2, size=n,
            ).astype(np.int8)
            data[f"prob_{lbl}"] = rng.random(n).astype(
                np.float32,
            )
    return pd.DataFrame(data)


@pytest.fixture
def sample_reviews() -> pd.DataFrame:
    """Minimal reviews DataFrame."""
    return pd.DataFrame({
        "review_id": list(range(50)),
        "text": [f"Review text {i}" for i in range(50)],
    })
