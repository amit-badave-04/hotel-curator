"""Integration test of the data stage on a small synthetic raw file."""

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

import hotel_curator.data.build as build_module
from hotel_curator.config import Settings
from hotel_curator.data.booking515k import RAW_COLUMNS
from hotel_curator.data.contracts import validate_hotels, validate_reviews
from hotel_curator.data.sources import content_fingerprint

TAGS = "[' Leisure trip ', ' Couple ', ' Double Room ', ' Stayed 2 nights ']"


def _raw_rows(n_hotels: int = 30, per_hotel: int = 12) -> pd.DataFrame:
    rows = []
    cities = [
        ("Amsterdam Netherlands", 52.37, 4.89),
        ("Paris France", 48.85, 2.35),
        ("London United Kingdom", 51.5, -0.12),
    ]
    for h in range(n_hotels):
        city, lat, lng = cities[h % 3]
        for r in range(per_hotel):
            rows.append(
                {
                    "Hotel_Address": f"{h} Some Street {city}",
                    "Additional_Number_of_Scoring": "10",
                    "Review_Date": f"{1 + r % 12}/{1 + h % 28}/2016",
                    "Average_Score": "8.1",
                    "Hotel_Name": f"Hotel {h}",
                    "Reviewer_Nationality": " Germany ",
                    "Negative_Review": "No Negative"
                    if r % 3
                    else " Small room ",
                    "Review_Total_Negative_Word_Counts": "2",
                    "Total_Number_of_Reviews": "900",
                    "Positive_Review": f" Great staff number {r} ",
                    "Review_Total_Positive_Word_Counts": "3",
                    "Total_Number_of_Reviews_Reviewer_Has_Given": "3",
                    "Reviewer_Score": "8.8",
                    "Tags": TAGS,
                    "days_since_review": "10 days",
                    "lat": str(lat),
                    "lng": str(lng),
                }
            )
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


@pytest.fixture
def raw_csv(tmp_path: Path) -> Path:
    path = tmp_path / "raw.csv"
    _raw_rows().to_csv(path, index=False)
    return path


def test_build_writes_valid_tables_and_manifest(
    tmp_path: Path, raw_csv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = pd.read_csv(raw_csv, dtype=str, keep_default_na=False)
    monkeypatch.setattr(
        build_module, "fetch", lambda source, raw_dir: [raw_csv]
    )
    monkeypatch.setattr(build_module, "RAW_ROW_COUNT", len(raw))
    settings = Settings(home=tmp_path)

    manifest_path = build_module.build(settings)

    hotels = pd.read_parquet(settings.processed_dir / "hotels.parquet")
    reviews = pd.read_parquet(settings.processed_dir / "reviews.parquet")
    validate_hotels(hotels, require_split=True)
    validate_reviews(reviews, hotels)
    assert len(hotels) == 30
    assert len(reviews) == 360
    assert set(hotels["split"]) == {"train", "val", "test"}
    manifest = json.loads(manifest_path.read_text())
    assert manifest["stats"]["reviews"] == 360
    assert manifest["inputs"]["raw_content_fingerprint"] == content_fingerprint(
        raw
    )
    assert {o["path"] for o in manifest["outputs"]} == {
        "data/processed/hotels.parquet",
        "data/processed/reviews.parquet",
    }


def test_read_raw_rejects_wrong_row_count(raw_csv: Path) -> None:
    with pytest.raises(ValueError, match="raw rows"):
        build_module.read_raw([raw_csv])


def test_fingerprint_command_matches_function(
    raw_csv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hotel_curator.cli.main import app

    raw = pd.read_csv(raw_csv, dtype=str, keep_default_na=False)
    monkeypatch.setattr(build_module, "RAW_ROW_COUNT", len(raw))
    result = CliRunner().invoke(app, ["data", "fingerprint", str(raw_csv)])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == content_fingerprint(raw)


def test_build_then_report_writes_eda_and_figures(
    tmp_path: Path, raw_csv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hotel_curator.cli.main import app
    from hotel_curator.config import get_settings

    raw = pd.read_csv(raw_csv, dtype=str, keep_default_na=False)
    monkeypatch.setattr(
        build_module, "fetch", lambda source, raw_dir: [raw_csv]
    )
    monkeypatch.setattr(build_module, "RAW_ROW_COUNT", len(raw))
    monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
    get_settings.cache_clear()
    try:
        runner = CliRunner()
        built = runner.invoke(app, ["data", "build"])
        assert built.exit_code == 0, built.output
        reported = runner.invoke(app, ["data", "report"])
        assert reported.exit_code == 0, reported.output
    finally:
        get_settings.cache_clear()

    eda = (tmp_path / "docs" / "data" / "EDA.md").read_text()
    assert "| Hotels | 30 |" in eda
    figures = sorted((tmp_path / "docs" / "data" / "figures").glob("*.png"))
    assert [f.name for f in figures] == [
        "part_lengths.png",
        "reviewer_scores.png",
        "reviews_per_hotel.png",
        "reviews_per_month.png",
    ]
    for figure in figures:
        assert f"](figures/{figure.name})" in eda
