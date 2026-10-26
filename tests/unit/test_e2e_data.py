"""The web e2e tests' data file must match the current scoring pipeline."""

import gzip
import json
import math
from pathlib import Path

from hotel_curator.db.seed import read_results, write_results
from tests.helpers import synthetic_results

ROOT = Path(__file__).resolve().parents[2]
E2E_DATA = ROOT / "web" / "e2e" / "data" / "hotel_results.json.gz"
# numpy picks a CPU-specific SIMD version of exp and log1p at run time
# (AVX-512 on some machines, not on others), and the versions can differ in
# the last bit, so the noisy-OR signals and the sums and Beta probabilities
# built from them can differ in their last digits from one runner to another.
REL_TOL = 1e-9
# Beta tail probabilities go down to 1e-34, where a relative tolerance means
# little; the app never shows them, so differences below this are ignored.
ABS_TOL = 1e-12


def _close(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if isinstance(left, int | float) and isinstance(right, int | float):
        return math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return False


def _first_difference(
    left: object, right: object, path: str = "$"
) -> str | None:
    """Where two decoded JSON values differ; floats compare within tolerance."""
    if isinstance(left, float) or isinstance(right, float):
        return None if _close(left, right) else f"{path}: {left!r} != {right!r}"
    if isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            return f"{path}: keys {sorted(left)} != {sorted(right)}"
        pairs = [(left[k], right[k], f"{path}.{k}") for k in sorted(left)]
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return f"{path}: length {len(left)} != {len(right)}"
        pairs = [
            (a, b, f"{path}[{i}]")
            for i, (a, b) in enumerate(zip(left, right, strict=True))
        ]
    else:
        return None if left == right else f"{path}: {left!r} != {right!r}"
    for a, b, where in pairs:
        if (diff := _first_difference(a, b, where)) is not None:
            return diff
    return None


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
    diff = _first_difference(
        committed, json.loads(gzip.decompress(fresh.read_bytes()))
    )
    assert diff is None, f"{diff}; run: uv run python scripts/make_e2e_data.py"


def test_comparison_ignores_last_digits_but_not_real_changes() -> None:
    # Two of the values that differed between a CI runner and this machine.
    base = {
        "tier": "good",
        "scores": [59.49306787570351, 2.4006843655203814e-17],
        "n": 3,
    }
    last_digits = {
        "tier": "good",
        "scores": [59.49306787570352, 2.4006843655203995e-17],
        "n": 3,
    }
    assert _first_difference(base, last_digits) is None
    near_zero = {**base, "scores": [59.49306787570351, 0.0]}
    assert _first_difference(base, near_zero) is None
    for changed, where in (
        ({**base, "tier": "fair"}, "$.tier"),
        ({**base, "scores": [59.5, 2.4006843655203814e-17]}, "$.scores[0]"),
        ({**base, "scores": [59.49306787570351]}, "$.scores"),
        ({**base, "n": 4}, "$.n"),
        ({**base, "extra": 1}, "$: keys"),
    ):
        diff = _first_difference(base, changed)
        assert diff is not None and diff.startswith(where), (changed, diff)
