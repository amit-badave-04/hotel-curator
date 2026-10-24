"""Execute the thin notebooks' code cells (no Jupyter needed).

A notebook is skipped only when the artifacts it reads have not been built
in this checkout; in the committed repository they all exist.
"""

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
NEEDS = {
    "01_data_and_labels.ipynb": ROOT / "data" / "labels" / "agreement.json",
    "02_model_ladder.ipynb": ROOT / "artifacts" / "evals" / "promotion.json",
    "03_tiers.ipynb": ROOT / "data" / "demo" / "hotel_results.json.gz",
}


@pytest.mark.parametrize("name", sorted(NEEDS))
def test_notebook_runs(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if not NEEDS[name].is_file():
        pytest.skip(f"{NEEDS[name]} not built")
    notebook = json.loads((ROOT / "notebooks" / name).read_text())
    code = "\n\n".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )
    monkeypatch.chdir(ROOT / "notebooks")
    monkeypatch.setitem(os.environ, "MPLBACKEND", "Agg")
    exec(compile(code, name, "exec"), {"__name__": "__notebook__"})
