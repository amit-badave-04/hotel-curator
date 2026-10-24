"""Experiment tracking with MLflow and a local SQLite store (ADR 010).

The store lives in the gitignored ``var/`` directory; a JSON summary of every
run is also committed next to its evaluation report, so the record survives
without the database. Browse runs with
``mlflow ui --backend-store-uri sqlite:///var/mlflow.db``.
"""

import logging
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EXPERIMENT = "hotel-curator-v2-ladder"


def tracking_uri(work_dir: Path) -> str:
    """SQLite tracking URI under ``work_dir``."""
    return f"sqlite:///{(work_dir / 'mlflow.db').resolve()}"


def log_run(
    work_dir: Path,
    run_name: str,
    params: Mapping[str, Any],
    metrics: Mapping[str, float],
    artifacts: list[Path],
) -> str:
    """Log one rung to MLflow and return the run id.

    Non-finite metrics are skipped (MLflow rejects NaN); params are
    stringified.
    """
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow

    work_dir.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(tracking_uri(work_dir))
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(
            EXPERIMENT,
            artifact_location=(work_dir / "mlartifacts").resolve().as_uri(),
        )
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params({k: str(v)[:500] for k, v in params.items()})
        mlflow.log_metrics(
            {
                k: float(v)
                for k, v in metrics.items()
                if isinstance(v, int | float) and math.isfinite(v)
            }
        )
        for path in artifacts:
            if path.is_file():
                mlflow.log_artifact(str(path))
        run_id: str = run.info.run_id
    logger.info("MLflow run %s (%s)", run_id, run_name)
    return run_id
