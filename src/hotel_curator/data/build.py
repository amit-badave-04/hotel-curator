"""The data stage: fetch the pinned source, build and validate canonical tables.

Steps (each fails loudly):

1. Download the 11 pinned chunks (resumable, SHA-256 verified).
2. Read them as strings and check the documented raw shape
   (515,738 rows, 17 columns in order).
3. Build ``hotels`` and ``reviews`` (see :mod:`hotel_curator.data.booking515k`).
4. Assign the hotel-disjoint split, keeping same-address hotels together
   (see :mod:`hotel_curator.data.split`).
5. Validate both tables against the data contracts.
6. Write Parquet and a manifest with digests and counts.

Runtime: about one minute on 4 CPU cores after the ~240 MB download.
"""

import logging
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from hotel_curator.config import Settings
from hotel_curator.data.booking515k import (
    RAW_COLUMNS,
    RAW_ROW_COUNT,
    build_canonical,
)
from hotel_curator.data.contracts import validate_hotels, validate_reviews
from hotel_curator.data.download import download_file
from hotel_curator.data.manifest import file_record, new_manifest
from hotel_curator.data.sources import BOOKING_515K, Source, content_fingerprint
from hotel_curator.data.split import (
    SplitConfig,
    assign_splits,
    unify_address_groups,
)

logger = logging.getLogger(__name__)


def fetch(source: Source, raw_dir: Path) -> list[Path]:
    """Download every file of ``source`` into ``raw_dir/<key>/``."""
    target_dir = raw_dir / source.key
    return [
        download_file(f.url, target_dir / f.name, f.sha256)
        for f in source.files
    ]


def read_raw(paths: list[Path]) -> pd.DataFrame:
    """Read raw chunks as strings and check the documented shape.

    Raises:
        ValueError: If columns or row count differ from the original.
    """
    frames = []
    for path in paths:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        if tuple(frame.columns) != RAW_COLUMNS:
            raise ValueError(
                f"{path.name}: unexpected columns {list(frame.columns)}"
            )
        frames.append(frame)
    raw = pd.concat(frames, ignore_index=True)
    if len(raw) != RAW_ROW_COUNT:
        raise ValueError(f"expected {RAW_ROW_COUNT} raw rows, got {len(raw)}")
    return raw


def build(settings: Settings, split_config: SplitConfig | None = None) -> Path:
    """Run the full data stage and return the manifest path."""
    paths = fetch(BOOKING_515K, settings.raw_dir)
    raw = read_raw(paths)
    hotels, reviews, report = build_canonical(raw)
    hotels = assign_splits(
        hotels, split_config or SplitConfig(seed=settings.seed)
    )
    hotels = unify_address_groups(hotels)
    reviews = reviews.merge(hotels[["hotel_id", "split"]], on="hotel_id")
    validate_hotels(hotels, require_split=True)
    validate_reviews(reviews, hotels)

    out = settings.processed_dir
    out.mkdir(parents=True, exist_ok=True)
    hotels_path, reviews_path = out / "hotels.parquet", out / "reviews.parquet"
    hotels.to_parquet(hotels_path, index=False)
    reviews.to_parquet(reviews_path, index=False)

    manifest = new_manifest("data", settings.home)
    manifest.inputs = {f.name: f.sha256 for f in BOOKING_515K.files}
    manifest.inputs["raw_content_fingerprint"] = content_fingerprint(raw)
    manifest.outputs = [
        file_record(hotels_path, settings.home, rows=len(hotels)),
        file_record(reviews_path, settings.home, rows=len(reviews)),
    ]
    manifest.stats = {
        **asdict(report),
        "hotels_per_split": hotels["split"]
        .value_counts()
        .sort_index()
        .to_dict(),
        "reviews_per_split": reviews["split"]
        .value_counts()
        .sort_index()
        .to_dict(),
        "hotels_per_city": hotels["city"].value_counts().sort_index().to_dict(),
    }
    path = settings.manifests_dir / "data.json"
    manifest.write(path)
    logger.info("Data stage done: %s", manifest.stats)
    return path
