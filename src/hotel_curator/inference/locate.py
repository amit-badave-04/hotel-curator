"""Find the model package to serve or score with."""

import json
from pathlib import Path

from hotel_curator.config import Settings

PROMOTION_FILE = "promotion.json"


def promoted_package(settings: Settings) -> Path:
    """The package named by settings, else the promotion decision's winner.

    Raises:
        FileNotFoundError: If no package can be found; the message says
            which step to run.
    """
    if settings.model_package is not None:
        return settings.model_package
    decision = settings.artifacts_dir / "evals" / PROMOTION_FILE
    if not decision.is_file():
        raise FileNotFoundError(
            "no promotion decision; run curator model compare"
        )
    winner = json.loads(decision.read_text())["winner"]
    if not winner:
        raise FileNotFoundError("the promotion rule promoted no model")
    path = settings.models_dir / str(winner)
    if not (path / "model.onnx").is_file():
        raise FileNotFoundError(
            f"{path} has no model.onnx; run curator model package"
        )
    return path


def package_name(package_dir: Path) -> str:
    """The rung a package was built from (``metadata.json`` ``name``).

    A package configured by path may sit in a directory with another name;
    the rung name is what finds its evaluation and what results record.
    """
    metadata = json.loads((package_dir / "metadata.json").read_text())
    return str(metadata.get("name", package_dir.name))
