# ADR 010: Experiment tracking with MLflow and a local SQLite store

- Status: accepted
- Date: 2026-10-24

## Context

Every model run (rung, seed, hyperparameters, data manifest, metrics with
intervals) must be recorded so that the comparison table and the promotion
decision can be traced. No external services or accounts may be created.

## Options

| Option | Notes |
|---|---|
| **MLflow 3** with a local SQLite backend | De-facto standard, UI via `mlflow ui`; works with no server (default URI is `sqlite:///mlflow.db`). Heavy dependency tree (Flask, gunicorn, ...) but only in the `ml` extra. 3.17.1 had CVE-2026-53995 (file-store path traversal), fixed in 3.17.2 (pip-audit, 2026-10-24). The plain `./mlruns` file store is in maintenance mode. |
| Weights & Biases / Neptune / Comet | Hosted: needs an account (not allowed). |
| Plain JSON files only | Simple, but no run browser or comparison UI. |

## Decision

MLflow >= 3.17.2 with `sqlite:///var/mlflow.db` and artifacts in
`var/mlartifacts/` (both under the gitignored `var/`). Every training and
evaluation run logs parameters, data and label manifest digests, metrics and
the evaluation report. Because the tracking store is not committed, each run
also writes its report to `artifacts/evals/<rung>/report.json`, which **is**
committed and is the source for `docs/model/COMPARISON.md`.

## Consequences

- `uv run mlflow ui --backend-store-uri sqlite:///var/mlflow.db` opens the
  runs in a local browser.
- MLflow is optional at runtime: if it is not installed, runs still write the
  JSON summaries.
