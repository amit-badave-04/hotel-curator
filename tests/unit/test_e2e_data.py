"""The web e2e tests' data file must match the current scoring pipeline."""

import gzip
import json
from pathlib import Path

from hotel_curator.db.seed import read_results, write_results
from tests.helpers import synthetic_results

ROOT = Path(__file__).resolve().parents[2]
E2E_DATA = ROOT / "web" / "e2e" / "data" / "hotel_results.json.gz"


def test_write_results_is_deterministic_and_round_trips(tmp_path: Path) -> None:
    results = synthetic_results()
    first, second = tmp_path / "a.json.gz", tmp_path / "b.json.gz"
    write_results(first, results)
    write_results(second, results)
    assert first.read_bytes() == second.read_bytes()
    loaded, _ = read_results(first)
    assert loaded == results


def test_committed_e2e_data_is_current(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh.json.gz"
    write_results(fresh, synthetic_results())
    committed = json.loads(gzip.decompress(E2E_DATA.read_bytes()))
    assert committed == json.loads(gzip.decompress(fresh.read_bytes())), (
        "run: uv run python scripts/make_e2e_data.py"
    )
