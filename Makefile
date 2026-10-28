# hotel-curator v2: every command a contributor or CI runs.
# `make help` lists the targets. Each target is a thin wrapper around the
# `curator` CLI or a standard tool, so nothing here is magic.

SHELL := /bin/bash
UV ?= uv
RUN := $(UV) run --frozen
PY_SRC := $(wildcard src tests scripts notebooks)

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

# --- labels ----------------------------------------------------------------
.PHONY: labels
labels: ## Merge committed panel answers into label tables; rebuild + verify prompts
	$(RUN) curator label merge
	$(RUN) curator label prompts

# --- models (needs `make setup-ml`) ----------------------------------------
.PHONY: base-models baselines embeddings encoders train2 v1-eval compare package publish model-report models
base-models: ## Download and verify the pinned pretrained encoders (~1.3 GB)
	$(RUN) curator model fetch-base

baselines: ## Rung a: TF-IDF + logistic regression / LightGBM (~10 min)
	$(RUN) curator model run-tfidf

embeddings: ## Rung b: frozen bge-small embeddings + logistic heads (~1 min)
	$(RUN) curator model run-embeddings

encoders: ## Rung c: fine-tune ELECTRA-small, roberta-base, mmBERT-small (CPU: hours)
	$(RUN) curator model run-encoder electra-small
	$(RUN) curator model run-encoder roberta-base
	$(RUN) curator model run-encoder mmbert-small

train2: ## Rungs a, b and roberta-base again on train + train_2 (names end in -train2)
	$(RUN) curator model run-tfidf --extra-train
	$(RUN) curator model run-embeddings --extra-train
	$(RUN) curator model run-encoder roberta-base --extra-train

v1-eval: ## Score the frozen v1 checkpoint on the v2 gold sets (~5 min)
	$(RUN) curator model eval-v1

compare: ## Apply the promotion rule; write docs/model/COMPARISON.md
	$(RUN) curator model compare

ENCODER_RUNGS := electra-small-ft roberta-base-ft mmbert-small-ft \
	roberta-base-ft-train2

package: ## Package every fine-tuned encoder (ONNX, int8 check, throughput)
	for rung in $(ENCODER_RUNGS); do $(RUN) curator model package $$rung; done

publish: ## Copy the promoted package to artifacts/models/
	$(RUN) curator model publish

model-report: ## Write docs/model/ERRORS.md and the model card
	$(RUN) curator model report

models: base-models baselines embeddings v1-eval encoders train2 package compare publish model-report ## Whole ladder, end to end

# --- scoring ---------------------------------------------------------------
.PHONY: score-parts score-hotels validate-tiers scores
score-parts: ## Score every review part with the promoted model (~1-2 h on 4 CPUs)
	$(RUN) curator score parts

score-hotels: ## Tiers, explanations, quotes for every hotel; writes data/demo/
	$(RUN) curator score hotels

validate-tiers: ## ADR 006 validation; writes docs/model/TIERS.md
	$(RUN) curator score validate

scores: score-parts score-hotels validate-tiers ## Everything after the model

# --- serving ---------------------------------------------------------------
.PHONY: db serve
db: ## Create/upgrade the local database and load the demo data
	$(RUN) curator db upgrade
	$(RUN) curator db seed

serve: db ## Run the API on http://127.0.0.1:8000 (docs at /docs)
	$(RUN) curator serve

# --- deploy (Fly.io; docs/deploy/fly.md) -----------------------------------
.PHONY: fly-plan fly-secrets fly-setup fly-deploy fly-status
fly-plan: ## Print every flyctl command a first deploy runs (runs nothing)
	$(RUN) python scripts/deploy.py plan

fly-secrets: ## Check .env.fly, then stage it as Fly secrets (values on stdin)
	$(RUN) python scripts/push_fly_secrets.py push

fly-setup: ## Create the three apps, the database volume, the API private IP
	$(RUN) python scripts/deploy.py setup

fly-deploy: ## Deploy database, API, web, in that order
	$(RUN) python scripts/deploy.py deploy

fly-status: ## Machines of each app; fails if the API or database is public
	$(RUN) python scripts/deploy.py status

# --- quality ---------------------------------------------------------------
.PHONY: lint format typecheck test test-fast cov check
lint: ## Ruff lint + format check
	$(RUN) ruff check $(PY_SRC)
	$(RUN) ruff format --check $(PY_SRC)

format: ## Apply ruff formatting and safe fixes
	$(RUN) ruff check --fix $(PY_SRC)
	$(RUN) ruff format $(PY_SRC)

typecheck: ## mypy (strict) on the package (needs make setup-ml)
	$(RUN) mypy

test: ## Full Python test suite with coverage (needs make setup-ml)
	$(RUN) pytest --cov --cov-report=term-missing

test-fast: ## Unit tests only (needs make setup-ml)
	$(RUN) pytest tests/unit -q

check: lint typecheck test ## Everything CI runs for Python (needs make setup-ml)
