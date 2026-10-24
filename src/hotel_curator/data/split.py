"""Hotel-disjoint train/val/test split, stratified by city and hotel size.

Why hotel-disjoint: reviews of one hotel share names, landmarks and
recurring complaints. If the same hotel appears in training and test, the
test score rewards memorising hotels rather than understanding reviews.
v1's "hotel split" was over synthetic hotels, so in practice it was a random
review split; v2 splits real hotels.

Why stratified: every city and every hotel-size band (by review count) must
appear in every split, so slice metrics exist for all of them.

Determinism: the split depends only on the hotel ids and the seed, not on
row order, so rebuilding the data reproduces it exactly.
"""

import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

SIZE_BAND_EDGES = (0, 50, 200, 800, np.inf)
"""Review-count bands: <50, 50-199, 200-799, 800+ reviews."""


@dataclass(frozen=True)
class SplitConfig:
    """Split proportions and seed."""

    val_fraction: float = 0.15
    test_fraction: float = 0.15
    seed: int = 20261024

    def __post_init__(self) -> None:
        total = self.val_fraction + self.test_fraction
        if not (0 < self.val_fraction < 1 and 0 < self.test_fraction < 1):
            raise ValueError("fractions must be in (0, 1)")
        if total >= 0.9:
            raise ValueError("val + test must leave at least 10% for train")


def _order_key(hotel_id: str, seed: int) -> str:
    """Seeded, row-order-independent shuffle key for a hotel."""
    return hashlib.sha256(f"{seed}:{hotel_id}".encode()).hexdigest()


def assign_splits(hotels: pd.DataFrame, config: SplitConfig) -> pd.DataFrame:
    """Return ``hotels`` with a ``split`` column (train / val / test).

    Within each (city, size band) stratum, hotels are ordered by a seeded
    hash of their id and cut at the configured fractions. Strata of about
    seven or more hotels contribute proportionally to every split; smaller
    ones may miss a split, but every city and size band still appears in
    each split.

    Args:
        hotels: Needs ``hotel_id``, ``city`` and ``n_reviews`` columns.
        config: Fractions and seed.

    Raises:
        KeyError: If a required column is missing.
    """
    missing = {"hotel_id", "city", "n_reviews"} - set(hotels.columns)
    if missing:
        raise KeyError(f"hotels is missing columns {sorted(missing)}")
    out = hotels.copy()
    band = pd.cut(out["n_reviews"], bins=list(SIZE_BAND_EDGES), right=False)
    out["_key"] = out["hotel_id"].map(lambda h: _order_key(h, config.seed))
    out["_stratum"] = out["city"].astype(str) + "|" + band.astype(str)
    out = out.sort_values(["_stratum", "_key"])
    rank = out.groupby("_stratum").cumcount()
    size = out.groupby("_stratum")["hotel_id"].transform("size")
    position = (rank + 0.5) / size
    out["split"] = np.select(
        [
            position < config.test_fraction,
            position < config.test_fraction + config.val_fraction,
        ],
        ["test", "val"],
        default="train",
    )
    return (
        out.drop(columns=["_key", "_stratum"])
        .sort_values("hotel_id")
        .reset_index(drop=True)
    )


def unify_address_groups(hotels: pd.DataFrame) -> pd.DataFrame:
    """Put hotels that share a normalised address into the same split.

    The source lists one property under two names at the same address (a
    rename). Splitting them apart would leak that property's reviews across
    train and evaluation. Each address group takes the split of its member
    with the most reviews; all other hotels are unchanged.
    """
    out = hotels.copy()
    key = out["address"].str.casefold().str.split().str.join(" ")
    leader = (
        out.assign(_key=key)
        .sort_values(
            ["_key", "n_reviews", "hotel_id"], ascending=[True, False, True]
        )
        .drop_duplicates("_key")
        .set_index("_key")["split"]
    )
    out["split"] = key.map(leader).to_numpy()
    return out
