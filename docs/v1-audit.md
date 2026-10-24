# v1 audit

What hotel-curator v1 is, what it measured, what was wrong with it, and what
v2 keeps, rewrites or deletes. Written at the start of v2 (2026-10-24) from
the v1 commit `85813b4` on `main`.

**How this audit was done.** Four independent reviewers read every v1
notebook (cells *and* saved outputs), the SDK, the tests and the artifacts.
One re-ran NB01 and NB02a line for line against a GitHub copy of the Kaggle
CSV and reproduced every printed number. Two others recomputed results from
the committed Parquet files and checkpoint metadata. Cell references are
written `NB04 c32` (notebook 04, cell index 32, 0-based). Reviewer scratch
scripts are not committed; every number below was either printed by a v1
notebook or recomputed from a committed v1 file, and the source is named.

## 1. What v1 does, in plain language

v1 reads 20,491 TripAdvisor reviews and asks, for each review, which of eight
hotel attributes the guest praised or criticised. It then averages those
signals per "hotel", sorts hotels into four tiers (ELITE, SUPERIOR, PREMIUM,
FAIL) and serves the result through a small FastAPI app.

```
Kaggle CSV (Review, Rating)            no hotel id, lowercased, stopwords removed
   │  NB01  clean, English filter, hotel_id = sha256(review_id) % 200
   ▼
reviews_clean.parquet (20,475 reviews, 200 synthetic "hotels")
   │  NB02a 16 regex rules ──► silver labels (OR of rule firings)
   │  NB02b Grok relabels the 1,990 train reviews no rule fired on
   │  NB02c Grok labels a 200-review "gold" set (no human)
   ▼
NB03  roberta-base, 16 sigmoid heads, thresholds tuned on val
   ▼
NB04  predict all 20,475 reviews ─► per-hotel pos_rate − neg_rate
      ─► avg_score, min(cleanliness, room, staff) ─► hand-set tier cut-offs
      ─► top-3 reviews by probability as "evidence"
   ▼
data/processed/*.parquet, hotel_evidence.json ─► FastAPI (/v1/hotels, /v1/reviews)
```

## 2. Baseline as found

Measured in this session on a fresh Python 3.12.3 virtualenv with
`uv pip install -e ".[dev]"` (torch 2.11 resolved from PyPI).

| Check | Command | Result |
|---|---|---|
| Tests | `python -m pytest tests/ -q --cov=hotel_curator` | **11 passed**, 17.6 s, **35% line coverage**. `tests/test_api/` is an empty package: no API tests exist. |
| Lint | `ruff check src tests scripts` | **10 errors** (unused imports, B008, B007, unsorted imports) |
| Format | `ruff format --check src tests scripts` | **14 files** would be reformatted |
| Types | `mypy --python-executable <venv>/bin/python src` | **11 errors in 6 files** |
| Model | `git lfs pull`, then load + classify 96 short texts on 4 CPU threads | Loads in 1.0 s; 183 texts/s |

A three-sentence smoke test already shows the kind of error the gold set
later quantifies: "wifi was terrible" gets no wifi label, and "room was small"
is labeled `room_positive`.

## 3. Limitations and future work from REPORT.md, tagged for v2

| v1 item (REPORT.md) | v2 status | How / why |
|---|---|---|
| §6.1 Synthetic hotel IDs | **Addressed** | v2 uses a dataset with real hotel identity (name, address, coordinates); see the data ADR. |
| §6.2 Only 20 test hotels | **Addressed** | Hotel-disjoint split over the real hotels gives hundreds of test hotels; every metric carries a hotel-cluster bootstrap CI. |
| §6.3 Gold set LLM-annotated, no kappa | **Partly addressed** | Larger gold set, three independent annotators, per-label Fleiss' kappa, majority vote, gold-test drawn only from test hotels. Still machine-annotated: a human audit sheet is provided and listed in `BLOCKERS.md` (B3). |
| §6.4 Rare labels corpus-limited | **Addressed** | Stratified gold sampling oversamples rare-attribute text, with sampling weights recorded; labels below a support floor are flagged, not silently dropped. |
| §6.5 Tier thresholds corpus-calibrated | **Addressed** | Tier policy is a documented config of business rules (standards, weights, evidence floors) with uncertainty; see the tier ADR. |
| §6.6 60% tier agreement | **Addressed (metric replaced)** | Agreement against a silver-derived tier was circular. v2 reports tier stability under bootstrap and agreement with an external signal (the source site's own hotel score). |
| §6.7 Evidence misclassifications | **Addressed** | Evidence is the labeled review *part* (Liked or Disliked), ranked by calibrated probability, so the snippet is the text that carries the label. |
| §6.8 MLM adaptation did not help | **Deferred** | The claim is unverified (no notebook output shows the MLM run). Domain adaptation is not on v2's critical path; listed as future work. |
| §8.1 Human-validate the gold set | **Deferred (owner action)** | Needs a human; template and scoring script provided (`BLOCKERS.md` B3). |
| §8.2 Real-hotel-ID corpus | **Addressed** | Same as §6.1. |
| §8.3 Snorkel LabelModel | **Deferred** | The audit found the v1 rules weak (24 of 180 never fire, context-free triggers, negation guard not on the production path). v2 replaces rule labels with panel labels; weak supervision returns only if it measurably helps. |
| §8.4 Integrated Gradients explanations | **Deferred** | v2 explains at the review-part level (which Liked/Disliked text drove a label). Token attributions are future work. |
| §8.5 Business-calibrated tiers | **Addressed** | Same as §6.5. |
| §8 (done in v1) Gold-set threshold re-tuning | **Reversed** | It was tuned and scored on the same 200 rows (see §5). v2 tunes on gold-dev and reports on gold-test only. `thresholds_gold_tuned.json` is deleted. |

## 4. Shortcuts ("monkey fixes") in the v1 data path

| # | Shortcut | Evidence | Why it is a problem | v2 removal |
|---|---|---|---|---|
| D1 | `hotel_id = sha256(review_id) % 200` | NB01 c23 | Every "hotel" is a random bag of reviews. Re-running NB01's logic shows the spread of hotel mean ratings (std 0.1228) equals pure sampling noise (0.1219); a permutation test on `hotel_signals.parquet` gives p = 0.05 for the spread of `avg_score`. All tiers rank noise. | Real hotels from the source; `hotel_id` derived deterministically from name + address. |
| D2 | Real hotel identity destroyed | Reviews of "hotel monaco seattle" (rows 0–59) are spread over 53 synthetic hotels and all three splits | The "hotel-level split" leaks real-hotel wording into test. | Split on real hotels. |
| D3 | Preprocessed source text | NB01 c15 checks punctuation and case only; re-run shows stopwords removed ("was" in 0.31% of reviews) and Treebank-split contractions ("did n't") | The model trains on text no real user writes; serving would need the same undocumented preprocessing. | v2 dataset choice weighs text quality; no lossy preprocessing is applied; the data card documents what the source already did. |
| D4 | Document-level labels | NB01 c27 `granularity: document` | Cannot say which text carried a label; evidence is a 240-char review prefix. | Labels per review part (Liked / Disliked). |
| D5 | Regex weak supervision | NB02a c11–c15 | 24 of 180 triggers never match; context-free triggers (`remote` = TV remote for 49% of `location_negative`; "free wifi" = 94.5% of `wifi_positive`); the negation guard is not on the code path that produced the labels (958 firings it would have suppressed). | Dropped as a label source; panel labels instead. |
| D6 | Train vs. val/test labeled by different processes | LLM relabeling applied to train only (NB02a c26) | Label prevalence shifts by split (`staff_negative` 7.57% / 4.84% / 3.82%). | One guideline, one process for every split. |
| D7 | Two different LLM prompts | NB02b c11 (lenient, train) vs NB02c c10 (strict, gold) | Gold-set F1 measured guideline mismatch. | One guideline (`docs/labeling/GUIDELINES.md`). |
| D8 | Gold set sampled from rule outputs, 80% from train hotels | NB02a c28; 157/200 rows from train hotels | Contaminated and biased; abstained reviews never sampled. | Gold-test only from test hotels; random + documented enriched strata with weights. |
| D9 | Silent parse fallbacks | NB02b c13, NB04 c30 `fillna(0)` | Failed or demo LLM calls become "all labels false" gold rows. | Strict parser: every item is parsed, reported as an error, or reported missing. |
| D10 | Tier cut-offs fitted by eye to the model's own predictions, including test hotels | NB04 c3 comment | Corpus-calibrated and test-leaking. | Policy file with stated business meaning; resulting tier mix reported as an outcome, not a target. |
| D11 | `thresholds_gold_tuned.json` | NB04 c32 | Tuned on the scored rows; applied to the corpus it flags `location_negative` on 50.8% and `room_positive` on 92.7% of reviews. | Deleted. |

## 5. Evaluation problems

1. **Test F1 is agreement with regex.** Val and test labels are regex-only
   (NB02a c26, NB02b c23). The same checkpoint scores 0.4596 macro-F1 on the
   (LLM) gold set versus 0.7451 on silver test.
2. **Gold 0.6187 is in-sample.** NB04 c32 tunes thresholds on all 200 gold
   rows and reports F1 on the same rows.
3. **Model selection on a different metric than deployment.** Early stopping
   uses macro-F1 at a fixed 0.5 threshold; deployment uses tuned thresholds
   between 0.576 and 0.859. Best checkpoint is step 900 (val 0.7174), not step
   1000 / 0.7200 as REPORT.md says.
4. **No uncertainty on headline numbers.** One seed; no CI on F1; tier
   stability under bootstrap averages 0.528 (no hotel keeps its tier in
   ≥ 95% of resamples).
5. **In-sample hotel outputs.** Tiers and evidence for the 160 train hotels
   come from predictions on the model's own training reviews.
6. **Docs disagree with outputs** in several places (abstention pool 1,976
   vs 1,990; MLM described as implemented but `RUN_MLM_ADAPTATION=False`;
   stale tier table in NB04 c18). v2 docs quote only numbers from commands
   recorded next to them.

## 6. SDK, API and packaging

Measured on the v1 code with the session's virtualenv; file:line references
are to commit `85813b4`.

| # | Finding | Evidence | v2 |
|---|---|---|---|
| A1 | `docker build` cannot succeed: the builder copies only `pyproject.toml` and `src/`, `.dockerignore` excludes `*.md`, and hatchling stops with "Readme file does not exist" | Reproduced with `uv build` on the same file set | New multi-stage Dockerfile, verified with `docker compose up` |
| A2 | Batch endpoint is a DoS surface: up to 500 texts, no per-text length limit, input echoed back | A 1,000,000-char text returned 200 with a 1,001,061-byte body | Per-item limits, no echo, body-size limit, rate limiting |
| A3 | Pagination unvalidated: `limit=-1` returns 199 items; `tier=bogus` returns `[]` | `GET /v1/hotels?limit=-1` | Pydantic-validated query models, enums, bounded page size |
| A4 | Evidence data exists in three incompatible shapes (`build_evidence` output, shipped JSON, `schema.EvidenceSnippet`); `HotelDetail.evidence` is never filled | `src/hotel_curator/core/evidence.py`, `schema.py:68` | One typed evidence model end to end |
| A5 | DB layer is dead; `seed_db.py` writes 0.0 to all 16 attribute columns for all 200 hotels | Ran with sqlmodel 0.0.48 | SQLAlchemy 2 + Alembic, seeded and tested |
| A6 | 10 of 23 settings fields are read nowhere (e.g. `CURATOR_ELITE_MIN_AVG` has no effect); `threshold_set` is a free string, so `Gold` silently means `val` | grep; `classifier.py:67` | Typed settings with `Literal`s; every field used and documented |
| A7 | `core/__init__.py` imports torch eagerly, so pure-pandas helpers need torch (5.1 s import) | `python -X importtime` | Serving uses ONNX Runtime; torch only in the `ml` extra |
| A8 | Health check triggers the model load and hard-codes `model_loaded=True`; no readiness probe | `routes/health.py` | `/healthz` (liveness) and `/readyz` (DB + model) |
| A9 | Model load failures are retried on every request (`lru_cache` does not cache exceptions) | `api/deps.py:18` | Load once at startup in the lifespan hook |
| A10 | Container runs as root, floating base tag, no healthcheck, ~500 MB model and a pickle baked in | `docker/Dockerfile` | Non-root user, pinned base, healthcheck, no pickles |
| A11 | Review text with staff first names, room numbers and stay details is shipped in `hotel_evidence.json` | Regex scan of 7,242 unique snippets | Evidence snippets length-capped; DATA_CARD documents what is shown and why |
| A12 | mypy `--strict`: 20 errors; ruff: 10 findings; 35% coverage, 0% on API, CLI, settings and DB | Commands in §2 | Gates: ruff + mypy strict clean, ≥80% coverage |

The same reviewer also checked the first v2 modules while they were being
written and found three defects, all fixed test-first before the first v2
commit: the downloader got stuck when a complete `.part` file existed (HTTP
416); the annotation parser kept the last of duplicate ids silently; and the
bootstrap allocated a 2000 × N weights matrix (about 320 MB at N = 20k).

## 7. Keep, rewrite, delete

| Item | Verdict | Reason |
|---|---|---|
| 8 attributes × 2 polarities | **Keep, sharpened** | Sound core and enables a like-for-like comparison with v1. Boundaries written down; "breakfast" renamed "breakfast & food" (v1's gold prompt already meant all F&B). |
| v1 strict gold prompt (NB02c c10) | **Keep as seed** | Its rules seeded `docs/labeling/GUIDELINES.md`, which adds rules for middling words, hearsay, price vs quality and placeholders. |
| Per-label thresholds | **Keep, fixed** | Tuned on gold-dev only, clamped, with a fallback for labels with too few positives. |
| Critical-attribute floor | **Keep** | Cleanliness, room and staff can block the top tiers. |
| Mention rate | **Keep, used** | v1 computed `weighted_avg_score` but never used it; v2 uses evidence volume to gate grades. |
| v1 checkpoint (`artifacts/hotel_curator_clf`) | **Keep as baseline** | Re-scored on v2's held-out gold set: the fair comparison target. Moved to `artifacts/models/v1-roberta-base/`. |
| v1 notebooks + REPORT.md | **Archive** | Moved to `notebooks/v1/` as a historical record; not maintained or run in v2. |
| Synthetic hotel grouping and everything derived from it | **Delete** | `data/processed/*` (tiers, signals, evidence, predictions on synthetic hotels). |
| `thresholds_gold_tuned.json`, `CURATOR_THRESHOLD_SET=gold` | **Delete** | In-sample and harmful (§4 D11). |
| `training_args.bin` | **Delete** | A pickle; v2 never loads pickles. Config is stored as JSON. |
| Regex labeling functions | **Delete** | See §4 D5. |
| `src/hotel_curator/*` | **Rewrite** | See §6. |

## 8. v1 baseline metrics (for reference only)

Copied from `artifacts/hotel_curator_clf/metrics.json`. These are measured
against **v1's own silver test labels** on v1's corpus, so they are **not**
comparable to any v2 number. The only fair comparison is the v1 checkpoint
re-scored on v2's gold-test set by v2's evaluation harness; that table lives
in `docs/model/COMPARISON.md`.

| Metric | Value | Fair v2 comparison? |
|---|---:|---|
| Test micro-F1 (silver) | 0.9004 | No: circular labels |
| Test macro-F1, 16 labels (silver) | 0.7451 | No: circular labels |
| Test weighted-F1 (silver) | 0.9030 | No |
| Test macro-F1, 13 labels | 0.8515 | No: worst labels dropped after seeing test |
| Gold macro-F1, val thresholds (REPORT.md) | 0.4596 | Weak: 200 rows, ~80% from train hotels |
| Gold macro-F1, gold-tuned thresholds | 0.6187 | No: tuned on the scored rows |
| Hotel MAE / Pearson, tier agreement 60% | — | No: synthetic hotels |

Per-label test metrics (silver), from `metrics.json` → `test_per_label`:

| Label | Test support | Precision | Recall | F1 | Threshold (val-tuned) |
|---|---:|---:|---:|---:|---:|
| cleanliness_positive | 782 | 0.979 | 0.959 | 0.969 | 0.613 |
| cleanliness_negative | 114 | 0.774 | 0.781 | 0.777 | 0.613 |
| room_positive | 746 | 0.933 | 0.890 | 0.911 | 0.715 |
| room_negative | 208 | 0.760 | 0.702 | 0.730 | 0.751 |
| staff_positive | 938 | 0.914 | 0.939 | 0.926 | 0.622 |
| staff_negative | 77 | 0.528 | 0.857 | 0.653 | 0.736 |
| location_positive | 650 | 0.897 | 0.965 | 0.930 | 0.739 |
| location_negative | 27 | 0.611 | 0.407 | 0.489 | 0.742 |
| noise_positive | 283 | 0.977 | 0.919 | 0.947 | 0.657 |
| noise_negative | 179 | 0.928 | 0.933 | 0.930 | 0.662 |
| wifi_positive | 56 | 0.946 | 0.946 | 0.946 | 0.856 |
| wifi_negative | 3 | 0.250 | 0.667 | 0.364 | 0.576 |
| value_positive | 204 | 0.897 | 0.941 | 0.919 | 0.783 |
| value_negative | 84 | 0.626 | 0.679 | 0.651 | 0.819 |
| breakfast_positive | 72 | 0.732 | 0.833 | 0.779 | 0.859 |
| breakfast_negative | 8 | 0.000 | 0.000 | 0.000 | 0.684 |

Training configuration (metrics.json): `roberta-base`, max length 256, batch
64, LR 2e-5, sqrt `pos_weight` capped at 50, configured for 5 epochs (stopped
at epoch 4.67, step 1200, per `training_log.json`); splits 16,412 / 2,046 /
2,017 reviews.
