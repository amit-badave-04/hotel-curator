# hotel-curator v2: every command a contributor or CI runs.
# `make help` lists the targets. Each target is a thin wrapper around the
# `curator` CLI or a standard tool, so nothing here is magic.

SHELL := /bin/bash
UV ?= uv
RUN := $(UV) run --frozen
PY_SRC := $(wildcard src tests scripts)

.DEFAULT_GOAL := help

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-18s %s\n", $$1, $$2}'

# --- setup -----------------------------------------------------------------
.PHONY: setup setup-ml
setup: ## Install the API, data and dev dependencies from uv.lock
	$(UV) sync --frozen --extra data --extra dev --extra postgres

setup-ml: ## Also install the training stack (torch, transformers, scikit-learn, ...)
	$(UV) sync --frozen --extra data --extra dev --extra postgres --extra ml

# --- data ------------------------------------------------------------------
.PHONY: data eda
data: ## Download the pinned source, build and validate canonical tables (~1 min)
	$(RUN) curator data build

eda: ## Regenerate docs/data/EDA.md and figures from the canonical tables
	$(RUN) curator data report

# --- quality ---------------------------------------------------------------
.PHONY: lint format typecheck test test-fast cov check
lint: ## Ruff lint + format check
	$(RUN) ruff check $(PY_SRC)
	$(RUN) ruff format --check $(PY_SRC)

format: ## Apply ruff formatting and safe fixes
	$(RUN) ruff check --fix $(PY_SRC)
	$(RUN) ruff format $(PY_SRC)

typecheck: ## mypy (strict) on the package
	$(RUN) mypy

test: ## Full Python test suite with coverage
	$(RUN) pytest --cov --cov-report=term-missing

test-fast: ## Unit tests only
	$(RUN) pytest tests/unit -q

check: lint typecheck test ## Everything CI runs for Python
