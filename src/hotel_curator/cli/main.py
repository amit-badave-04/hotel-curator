"""The ``curator`` command line: one sub-command per pipeline stage.

Run ``curator --help`` for the list. Every stage reads and writes the
locations defined in :mod:`hotel_curator.config`, so the Makefile targets
are thin wrappers around these commands.
"""

import typer

from hotel_curator.cli import data as data_cli
from hotel_curator.cli import label as label_cli
from hotel_curator.cli import model as model_cli
from hotel_curator.config import get_settings
from hotel_curator.logging import configure_logging

app = typer.Typer(
    name="curator",
    help="hotel-curator v2: data, labels, models, scoring and serving.",
    no_args_is_help=True,
)
app.add_typer(data_cli.app, name="data")
app.add_typer(label_cli.app, name="label")
app.add_typer(model_cli.app, name="model")


@app.callback()
def main() -> None:
    """Configure logging from settings before any command runs."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
