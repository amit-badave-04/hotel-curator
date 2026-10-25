"""Write the synthetic hotel results the web end-to-end tests run against.

The five synthetic hotels come from ``tests/helpers.py`` and go through the
real scoring pipeline, so the file always matches the current tier rules,
explanations and API schema. ``tests/unit/test_e2e_data.py`` fails when the
committed file is stale. Usage (from the repository root)::

    uv run python scripts/make_e2e_data.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # the synthetic hotels live in tests/helpers.py

from hotel_curator.db.seed import write_results  # noqa: E402
from tests.helpers import synthetic_results  # noqa: E402

E2E_DATA = ROOT / "web" / "e2e" / "data" / "hotel_results.json.gz"


def main() -> None:
    write_results(E2E_DATA, synthetic_results())
    print(f"wrote {E2E_DATA}")


if __name__ == "__main__":
    main()
