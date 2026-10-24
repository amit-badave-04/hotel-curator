"""Write the thin v2 notebooks (no outputs) from the cell lists below.

The notebooks only read committed artifacts and call ``hotel_curator``;
every computation lives in the package and is tested there. Keeping their
source here makes diffs reviewable and lets ``tests/unit/test_notebooks.py``
execute the same code without Jupyter. Usage::

    python scripts/build_notebooks.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SETUP = """\
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # notebook-only: inline display works without it too
import matplotlib.pyplot as plt
import pandas as pd

from hotel_curator.config import Settings

ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
settings = Settings(home=ROOT)"""

NOTEBOOKS: dict[str, list[tuple[str, str]]] = {
    "01_data_and_labels.ipynb": [
        (
            "markdown",
            "# 1. Data and labels\n\nWhat the models learn from. "
            "Everything here is read from committed files; rebuild them with "
            "`make data` and `make labels`.",
        ),
        ("code", SETUP),
        (
            "code",
            """\
import json

data = json.loads((settings.manifests_dir / "data.json").read_text())
pd.Series(data["stats"]["hotels_per_city"], name="hotels").to_frame()""",
        ),
        (
            "code",
            """\
from hotel_curator.ml.data import load_all
from hotel_curator.taxonomy import LABELS

sets = load_all(settings.labels_dir)
prevalence = pd.DataFrame(
    {name: s.frame[list(LABELS)].mean() for name, s in sets.items()}
)
prevalence.round(3)""",
        ),
        (
            "markdown",
            "## How much do the annotators agree, and how do they "
            "compare with people?",
        ),
        (
            "code",
            """\
agreement = pd.DataFrame(
    json.loads((settings.labels_dir / "agreement.json").read_text())
)
agreement.pivot(
    index="label", columns="set", values="fleiss_kappa"
).round(3)""",
        ),
        (
            "code",
            """\
external = json.loads(
    (settings.labels_dir / "external_check.json").read_text()
)
pd.DataFrame(external["explicit_implicit_triple_kappa"]).set_index(
    "label"
)[["human_human_kappa", "panel_human_kappa"]].round(2)""",
        ),
    ],
    "02_model_ladder.ipynb": [
        (
            "markdown",
            "# 2. Model ladder and promotion\n\nEvery rung scored by "
            "one harness on the same gold-test reviews. Rebuild with "
            "`make models`.",
        ),
        ("code", SETUP),
        (
            "code",
            """\
import json

promotion = json.loads(
    (settings.artifacts_dir / "evals" / "promotion.json").read_text()
)
rows = pd.DataFrame(
    [
        {
            "rung": r["name"],
            "family": r["family"],
            "test macro-F1": r["summary"]["test_review_macro_f1"],
            "low": r["summary"]["test_review_macro_f1_low"],
            "high": r["summary"]["test_review_macro_f1_high"],
        }
        for r in promotion["rows"]
    ]
).sort_values("test macro-F1")
print("promoted:", promotion["winner"], "| v1 configuration:",
      promotion["v1_config"])
rows.round(3)""",
        ),
        (
            "code",
            """\
fig, ax = plt.subplots(figsize=(7, 0.45 * len(rows) + 1))
ax.errorbar(
    rows["test macro-F1"],
    rows["rung"],
    xerr=[
        rows["test macro-F1"] - rows["low"],
        rows["high"] - rows["test macro-F1"],
    ],
    fmt="o",
    color="#2a78d6",
    capsize=3,
)
ax.set_xlabel("Review-level macro-F1 on gold-test (95% CI)")
ax.grid(axis="x", alpha=0.3)
fig.tight_layout()""",
        ),
        (
            "code",
            """\
winner = promotion["winner"]
reports = {
    name: json.loads(
        (settings.artifacts_dir / "evals" / name / "report.json").read_text()
    )["test"]
    for name in (winner, promotion["v1_config"])
}
pd.DataFrame(
    {name: {r["label"]: r["f1"] for r in rep["per_label"]}
     for name, rep in reports.items()}
).round(3)""",
        ),
    ],
    "03_tiers.ipynb": [
        (
            "markdown",
            "# 3. Tiers\n\nThe served hotel results and how one "
            "tier is recomputed by hand from its counts. Rebuild with "
            "`make scores`.",
        ),
        ("code", SETUP),
        (
            "code",
            """\
import gzip
import json

with gzip.open(settings.demo_dir / "hotel_results.json.gz", "rt") as handle:
    results = json.load(handle)
tiers = pd.DataFrame(
    [{"city": r["city"], "tier": r["tier"]} for r in results]
)
pd.crosstab(tiers["city"], tiers["tier"])""",
        ),
        (
            "code",
            """\
from scipy.stats import beta

hotel = next(r for r in results if r["tier"] == "good")
room = next(a for a in hotel["attributes"] if a["attribute"] == "room")
a, b = 0.5 + room["positive"], 0.5 + room["negative"]
print(hotel["name"], "-", hotel["label"])
print(hotel["explanation"]["verdict"])
print("Room: P(share >= 0.80) =", round(beta.sf(0.80, a, b), 4),
      "| served:", round(room["prob_strong"], 4))""",
        ),
    ],
}


def notebook(cells: list[tuple[str, str]]) -> dict[str, object]:
    """A minimal nbformat 4.5 notebook without outputs."""
    out = []
    for i, (kind, source) in enumerate(cells):
        cell: dict[str, object] = {
            "cell_type": kind,
            "id": f"cell-{i:02d}",
            "metadata": {},
            "source": source.splitlines(keepends=True),
        }
        if kind == "code":
            cell["execution_count"] = None
            cell["outputs"] = []
        out.append(cell)
    return {
        "cells": out,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> None:
    """Write every notebook under ``notebooks/``."""
    for name, cells in NOTEBOOKS.items():
        path = ROOT / "notebooks" / name
        path.write_text(json.dumps(notebook(cells), indent=1) + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
