"""Write the API's OpenAPI schema and default tier policy for the frontend.

web/openapi.json is the generated client's input; web/src/lib/tier-policy.json
holds the pre-registered policy values (config/tier_policy.yaml) that pages
show before /v1/meta answers. CI regenerates both and fails if a committed
copy differs, so the frontend can never drift from the backend. Usage::

    python scripts/export_openapi.py
"""

import json
from pathlib import Path

from hotel_curator.api.app import create_app
from hotel_curator.config import Settings
from hotel_curator.scoring.policy import TierPolicy

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    print(f"wrote {path}")


def main() -> None:
    """Export the schema and the policy with stable key order."""
    settings = Settings(home=ROOT, log_format="console")
    _write(ROOT / "web" / "openapi.json", create_app(settings).openapi())
    policy = TierPolicy.load(settings.resolved_tier_policy)
    _write(
        ROOT / "web" / "src" / "lib" / "tier-policy.json",
        policy.model_dump(mode="json"),
    )


if __name__ == "__main__":
    main()
