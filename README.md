# Hotel Curator

Multi-attribute hotel quality classifier and tier ranking system. Classifies hotel reviews across 8 attributes (cleanliness, room, staff, location, noise, wifi, value, breakfast) with positive/negative polarity, aggregates to hotel-level signals, and assigns quality tiers (ELITE / SUPERIOR / PREMIUM / FAIL).

## Architecture

```
Reviews (text) -> RoBERTa classifier (16 labels)
    -> per-hotel aggregation (mention rates, bootstrap CIs)
    -> tier assignment (rule-based with critical-attribute floors)
    -> FastAPI endpoints + evidence retrieval
```

### Core modules (`src/hotel_curator/core/`)

Pure ML logic with zero framework dependencies:

- **classifier.py** -- model loading, batched inference, threshold application
- **aggregator.py** -- review predictions to hotel-level signals, mention rates, weighted avg_score
- **bootstrap.py** -- 95% confidence intervals on attribute scores
- **tier.py** -- rule-based tier assignment with critical-attribute floors
- **evidence.py** -- top-K evidence reviews per (hotel, attribute, polarity)

### API (`src/hotel_curator/api/`)

FastAPI endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Model health check |
| `/v1/reviews/classify` | POST | Classify a single review |
| `/v1/reviews/batch` | POST | Classify up to 500 reviews |
| `/v1/hotels` | GET | List hotels with tier filter |
| `/v1/hotels/{id}` | GET | Hotel detail with attribute scores + CIs |
| `/v1/hotels/{id}/evidence` | GET | Top-K evidence reviews |

## Quick start

### 1. Install

```bash
pip install -e ".[dev]"
```

### 2. Place model artifacts

Copy the trained checkpoint to `artifacts/hotel_curator_clf/`:

```
artifacts/hotel_curator_clf/
  model.safetensors    # 499 MB
  config.json
  tokenizer.json
  tokenizer_config.json
  thresholds.json
  thresholds_gold_tuned.json
  label_names.json
```

### 3. Place pre-computed data

Copy NB04 outputs to `data/processed/`:

```
data/processed/
  hotel_signals.parquet
  hotel_tiers.parquet
  hotel_evidence.json
  review_predictions.parquet
```

### 4. Run the API

```bash
# Via CLI
curator serve

# Or directly
uvicorn hotel_curator.api.app:app --reload

# Or via Docker
cd docker && docker compose up
```

### 5. Classify a review

```bash
curl -X POST http://localhost:8000/v1/reviews/classify \
  -H "Content-Type: application/json" \
  -d '{"text": "great location, clean rooms, wifi was terrible"}'
```

### 6. Get hotel tier

```bash
curl http://localhost:8000/v1/hotels/37
```

## CLI

```bash
# Classify from terminal
curator classify "wonderful staff, rooms were spotless"

# Export tiers as JSON
curator export-tiers --output tiers.json

# Start server
curator serve --port 8080
```

## Training pipeline

The notebooks in `notebooks/` reproduce the full training pipeline:

```
NB01  -> Data ingest + EDA
NB02a -> Regex weak supervision
NB02b -> LLM relabeling (xAI Grok)
NB02c -> Gold set creation
NB03  -> RoBERTa fine-tuning (Phase 5 MLM + Phase 6 classification)
NB04  -> Aggregation, tier mapping, evaluation, gold-set re-tuning
```

Total pipeline runtime: ~30 min on RTX 5090. LLM cost: ~$0.12.

## Model performance

| Metric | Value |
|---|---|
| Test micro-F1 | 0.9004 |
| Test macro-F1 (all 16) | 0.7451 |
| Test macro-F1 (13 reliable) | 0.8515 |
| Hotel-level MAE (20 test hotels) | 0.021 |
| Tier agreement (pred vs silver) | 60% |
| Gold-set macro-F1 (val thresh) | 0.4596 |
| Gold-set macro-F1 (gold thresh) | 0.6187 |

## Deployment (Google Cloud Run)

```bash
# Build
docker build -t hotel-curator -f docker/Dockerfile .

# Tag and push to GCR
docker tag hotel-curator gcr.io/YOUR_PROJECT/hotel-curator
docker push gcr.io/YOUR_PROJECT/hotel-curator

# Deploy
gcloud run deploy hotel-curator \
  --image gcr.io/YOUR_PROJECT/hotel-curator \
  --memory 2Gi \
  --cpu 2 \
  --min-instances 0 \
  --max-instances 3 \
  --port 8000
```

## Tests

```bash
pytest tests/ -v
```

