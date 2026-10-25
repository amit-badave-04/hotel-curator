"""Write the API's OpenAPI schema to web/openapi.json (the client's input).

CI regenerates it and fails if the committed copy differs, so the frontend's
generated client can never drift from the backend. Usage::

    python scripts/export_openapi.py
"""

import json
from pathlib import Path

from hotel_curator.api.app import create_app
from hotel_curator.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Export the schema with stable key order."""
    app = create_app(Settings(home=ROOT, log_format="console"))
    out = ROOT / "web" / "openapi.json"
    out.write_text(json.dumps(app.openapi(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
