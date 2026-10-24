"""``curator data``: fetch, build and describe the canonical tables."""

from pathlib import Path

import typer

from hotel_curator.config import get_settings

app = typer.Typer(help="Fetch the pinned source and build canonical tables.")


@app.command("build")
def build_cmd() -> None:
    """Download (if needed), validate and write hotels/reviews Parquet."""
    from hotel_curator.data.build import build

    path = build(get_settings())
    typer.echo(f"wrote manifest {path}")


@app.command("report")
def report_cmd() -> None:
    """Write docs/data/EDA.md and figures from the canonical tables."""
    import pandas as pd

    from hotel_curator.data.eda import write_report

    settings = get_settings()
    hotels = pd.read_parquet(settings.processed_dir / "hotels.parquet")
    reviews = pd.read_parquet(settings.processed_dir / "reviews.parquet")
    path = write_report(hotels, reviews, settings.home / "docs" / "data")
    typer.echo(f"wrote {path}")


@app.command("fingerprint")
def fingerprint_cmd(
    csv_path: Path = typer.Argument(..., exists=True, dir_okay=False),
) -> None:
    """Print the content fingerprint of a raw CSV (e.g. Kaggle's original).

    Compare the output with ``inputs.raw_content_fingerprint`` in
    ``data/manifests/data.json``: equal values mean the same rows.
    """
    from hotel_curator.data.build import read_raw
    from hotel_curator.data.sources import content_fingerprint

    typer.echo(content_fingerprint(read_raw([csv_path])))
