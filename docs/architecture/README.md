# Architecture

hotel-curator has two halves that meet at a handful of committed files.

- **Offline** ([offline.md](offline.md)): run by hand on a workstation. It
  turns the public review dataset into labels, a model ladder, one promoted
  model package and the results for every hotel. Every stage except the LLM
  annotator panel is a `curator` command; the data, label and scoring stages
  write manifests with the SHA-256 of each output.
- **Online** ([online.md](online.md)): `docker compose up` on a laptop, or
  three Fly.io apps in production. A web app and an API serve the committed
  results and run the promoted model on text a user submits. It never
  trains, never reads the raw dataset and never calls an LLM.

[`index.html`](index.html) draws the main views as standalone diagrams (open
the file in a browser; it needs no network). GitHub renders the Mermaid
blocks on these pages.

## The two halves

```mermaid
flowchart LR
  subgraph offline[Offline: curator CLI]
    direction TB
    data[data<br/>download, build, split] --> label[label<br/>sample, panel, merge]
    label --> model[model<br/>ladder, package, compare, publish]
    model --> score[score<br/>parts, hotels, validate]
  end
  subgraph handoff[Committed hand-off]
    direction TB
    pkg[[artifacts/models/&lt;winner&gt;/]]
    promo[[artifacts/evals/promotion.json]]
    results[[data/demo/hotel_results.json.gz]]
    policy[[config/tier_policy.yaml]]
  end
  subgraph online[Online: docker compose or Fly.io]
    direction TB
    web[web<br/>nginx + React] --> api[api<br/>FastAPI + ONNX Runtime]
    api --> db[(Postgres<br/>socket in compose, TLS on Fly)]
  end
  model --> pkg & promo
  score --> results
  policy --> score
  pkg & promo & policy --> api
  results -->|curator db release| db
```

## The hand-off

The only coupling between the halves is these files. The API image copies
them at build time (`Dockerfile`); nothing online writes to them.

| File | Written by (offline) | Read by (online) |
|---|---|---|
| `artifacts/evals/promotion.json` | `curator model compare` (the promotion rule, plan section 3.6; it also records the winner's package file hashes) | API start-up, to find the winning package; `GET /v1/meta`, for the evaluation numbers |
| `artifacts/models/<winner>/`: `model.onnx`, `tokenizer.json`, `metadata.json` | `curator model publish`, which copies the files `curator model package` built in `var/packages/<winner>/` and refuses any whose SHA-256 differs from `promotion.json` | API start-up: loaded once; if it is missing, `POST /v1/analyze` answers 503 and every other route works |
| `artifacts/models/<winner>/MODEL_CARD.md` | `curator model report` | not read; documentation |
| `data/demo/hotel_results.json.gz` | `curator score hotels` | `curator db release`: in the API container's entrypoint in compose, as Fly's `release_command` once per deploy; migrates first, then loads unless the file's SHA-256 is already loaded, under a database lock |
| `config/tier_policy.yaml` | by hand (values from ADR 006); read by `curator score hotels` and `validate` | API start-up; `GET /v1/meta` shows every value |

`model.onnx` is stored in Git LFS (`.gitattributes`). A checkout that has
only its LFS pointer (Git LFS not installed, or not pulled) cannot load
it: `/v1/analyze` answers 503; tiers, search, hotel pages and comparison
work from the committed results.

## System context

```mermaid
flowchart LR
  traveler([Traveler]) -->|search, read why| web
  analyst([Hotel analyst]) -->|compare, trends| web
  developer([Developer]) -->|REST + OpenAPI| api
  owner([ML owner]) -->|curator data / label / model / score| pipeline

  subgraph hc[hotel-curator]
    web[Web app<br/>React + TypeScript]
    api[API<br/>FastAPI + ONNX Runtime]
    pipeline[Offline pipeline<br/>curator CLI]
  end

  web -->|/api| api
  pipeline -->|committed results, model package| api
  mirror[(Public dataset mirror<br/>pinned SHA-256)] --> pipeline
  weights[(Pretrained encoders<br/>pinned SHA-256)] --> pipeline
  panel{{LLM annotator panel<br/>build time only}} -.->|labels, never at serving| pipeline
```

## Code modules and the half they serve

```mermaid
flowchart LR
  cli[cli/*<br/>typer commands] --> data & labeling & ml & scoring & db
  data[data/<br/>download, contracts, split, manifest]
  labeling[labeling/<br/>sampling, prompts, panel, agreement]
  ml[ml/<br/>baselines, encoder, calibration,<br/>thresholds, evaluate, compare, package]
  scoring[scoring/<br/>aggregate, grades, tiers, explain, evidence,<br/>compare and trends, segments, validation, pipeline]
  inference[inference/<br/>ONNX predictor, package, locate]
  api[api/<br/>app, routes, middleware, rate limit,<br/>client address, metrics, error reports]
  db[db/<br/>models, migrations, seed, release]
  schemas[schemas.py<br/>shared response models]
  api --> db & inference & schemas & scoring
  scoring --> schemas
  ml --> inference
  inference -->|ml.calibration| ml
```

| Half | Modules |
|---|---|
| Offline only | `data`, `labeling`, and `ml` apart from `ml.calibration` (training needs `make setup-ml`) |
| Online only | `api` |
| Both | `inference` (scoring parts offline, `/v1/analyze` online); `ml.calibration` (numpy only: applies the stored calibration when a package predicts); `scoring` (computes tiers offline; the API uses its value types and the comparison rule); `db` (models and migrations serve the API; `db.seed` writes the results file in `curator score hotels` and loads it in `curator db seed`); `schemas`, `taxonomy`, `text`, `config` |

The API imports no third-party training library (PyTorch, transformers,
scikit-learn, LightGBM, MLflow, spaCy). The API image installs only the core,
`postgres` and `sentry` dependencies, so those libraries are absent from it, and
`test_api_does_not_import_the_training_stack` (`tests/api/test_api.py`)
checks what importing `hotel_curator.api.app` loads in an environment where
they are installed.
