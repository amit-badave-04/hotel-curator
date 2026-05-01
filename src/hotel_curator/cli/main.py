"""CLI commands for Hotel Curator."""

import json
from pathlib import Path

import typer

app = typer.Typer(
    name="curator",
    help="Hotel Curator -- CLI tools.",
)


@app.command()
def serve(
    host: str = "0.0.0.0",
    port: int = 8000,
    reload: bool = False,
) -> None:
    """Start the FastAPI server."""
    import uvicorn

    uvicorn.run(
        "hotel_curator.api.app:app",
        host=host,
        port=port,
        reload=reload,
    )


@app.command()
def classify(
    text: str = typer.Argument(..., help="Review text"),
) -> None:
    """Classify a single review from the command line."""
    import torch

    from hotel_curator.core.classifier import (
        apply_thresholds,
        infer_probs,
        load_model_bundle,
    )
    from hotel_curator.settings import settings

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, tokenizer, labels, thresholds = load_model_bundle(
        settings.model_dir,
        device=device,
        threshold_set=settings.threshold_set,
    )
    probs = infer_probs(
        model, tokenizer, [text],
        max_length=settings.max_seq_length,
        batch_size=1, device=device,
    )
    preds = apply_thresholds(probs, thresholds, labels)

    typer.echo("Label predictions:")
    for i, lbl in enumerate(labels):
        flag = "*" if preds[0, i] else " "
        typer.echo(
            f"  {flag} {lbl:<26s} "
            f"prob={probs[0, i]:.3f}"
        )


@app.command()
def export_tiers(
    output: Path = typer.Option(
        "hotel_tiers.json", help="Output path",
    ),
) -> None:
    """Export hotel tiers as JSON."""
    import pandas as pd

    path = Path("data/processed/hotel_tiers.parquet")
    if not path.exists():
        typer.echo(f"Not found: {path}. Run the pipeline.")
        raise typer.Exit(code=1)

    df = pd.read_parquet(path)
    records = df.to_dict(orient="records")
    with output.open("w") as f:
        json.dump(records, f, indent=2)
    typer.echo(f"Exported {len(records)} hotels to {output}")


if __name__ == "__main__":
    app()
