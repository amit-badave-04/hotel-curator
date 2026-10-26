# hotel-curator v2: plan

- Status: committed plan (the review artifact). Updated at every milestone in
  the **Status** section at the end.
- Date: 2026-10-24
- Inputs: [`docs/v1-audit.md`](../v1-audit.md), [`docs/TOOLING.md`](../TOOLING.md),
  [`docs/BLOCKERS.md`](../BLOCKERS.md), the ADRs in [`docs/decisions/`](../decisions/).

hotel-curator turns guest reviews into **attribute-level hotel quality tiers
with evidence**: for each hotel, how guests judge cleanliness, room, staff,
location, noise, wifi, value and breakfast & food; how sure we are; which
review text says so; and what that adds up to. v1 was a notebook prototype on
a corpus without hotel identity. v2 rebuilds it as a product on real hotels.

---

## 1. Product definition

### Who uses it and what "useful" means

| User | Core journey | Useful means |
|---|---|---|
| **Traveler** | J1. "Find me a clean, quiet hotel in Paris." Explorer: city, tier, minimum grade per attribute, sort. | Finds a short list in under a minute and can see *why* each hotel is listed. |
| **Traveler / analyst** | J2. "Is this hotel good, and why?" Hotel page: tier, confidence, "why this tier", attribute grades with intervals, quotes for and against, review volume over time, who it suits. | Can state the hotel's strengths and weaknesses with evidence, and how certain they are. |
| **Hotel analyst** | J3. "Compare these three hotels." Side-by-side attributes; differences that are credible (intervals do not overlap) are highlighted. | Sees which differences are real and which are noise. |
| **Hotel analyst** | J4. "What changed?" Per-attribute trend over time and change flags (historical replay of 2015-2017). | Spots a deteriorating attribute before it shows in the overall score. |
| **Developer** | J5. "Use tiers in my app / analyse my own review text." REST API with readable OpenAPI docs, pagination, an `analyze` endpoint. | Integrates in an afternoon from the docs alone. |
| **ML engineer (owner)** | J6. "Retrain, evaluate, promote." One command per stage, eval reports, model card, comparison with v1. | Reproduces every number on a fresh machine and trusts the promotion decision. |

### Product shape

A **web app** (explorer, hotel page, compare, "about the model"), a
**REST API**, and a **CLI pipeline**. The data are a fixed historical corpus,
so everything hotel-level is **precomputed offline** and served from a
database; the model runs live only for the `analyze` endpoint (ADR 007). This
keeps the API fast and cheap and makes every displayed number reproducible
from the pipeline.

Scope statement shown everywhere: **reviews from August 2015 to August 2017
for 1,494 hotels in Amsterdam, Barcelona, London, Milan, Paris and Vienna.**
The product never implies current quality.

---

## 2. Data strategy

### 2.1 Candidates evaluated

Full research with sources: the dataset research report summarised in ADR 001.
Every candidate was checked for licence, size, schema, hotel identity,
metadata, text quality, language, time span, download mechanism and
reachability from the build machine.

| Candidate | Licence | Size | Hotel identity | Text | Verdict |
|---|---|---|---|---|---|
| **A. Booking.com 515K Europe** (Kaggle) | CC0 as declared by the uploader; content originally Booking.com's | 515,738 reviews, 1,492 names | name + address + coordinates (real) | case kept, punctuation removed by the publisher | **Primary** |
| B. Booking.com official 2023 (`bookingcom/ml-dataset-reviews`) | wording says non-commercial, link is CC BY-SA 4.0 (contradictory) | 1.63M reviews, 40k properties | anonymised ids only | raw, punctuated | Evaluation only, opt-in, frozen model, native fields only |
| C1. OpinRank (TripAdvisor 2001-2009) | CC BY 4.0 (UCI) | 255k reviews, 2,568 hotels | name + address | raw | Not primary (stale, no Liked/Disliked split); possible evaluation-only validity check (future work) |
| C2. TripAdvisor Annotated (442 reviews, 73 triple-annotated by humans) | unverified | small | TripAdvisor ids | raw | Evaluation only: LLM-panel vs human agreement |
| D. Datafiniti | CC BY-NC-SA | 36k reviews | US hotels, corrupted geography | raw | Rejected |
| HotelRec, LARA, R2A, CMU TripAdvisor | research-only or unreachable | | | | Rejected |
| HF hub hotel datasets | blocked from the build machine | | | | Not usable (B1) |
| Overture Maps places | CDLA-Permissive-2.0 | global POIs | — | — | **Enrichment** (display metadata only) |
| OSM / Wikidata | ODbL / CC0 | | | | Blocked (B4) |

Decision, options and conditions: **ADR 001** (decided by a three-judge panel:
product, ML and risk lenses; unanimous on A as primary).

### 2.2 Canonical schema

Defined once in `hotel_curator.data.contracts` (pandera) and documented in
[`docs/data/DATA_CARD.md`](../data/DATA_CARD.md).

| Table | Key | Main columns |
|---|---|---|
| `hotels` | `hotel_id` = hash of normalised name + address | name, address, city, country, lat, lng, source average score, source review count, `n_reviews`, `split` |
| `reviews` | `review_id` = hash of all raw fields | hotel_id, date, reviewer score, nationality (aggregate use only), liked_text, disliked_text, trip type, traveler type, room type, nights, flags, `split` |
| `labels` (per annotation set) | (review_id, part) | 16 boolean labels, annotator answers, sampling stratum and weight, guideline and prompt SHA-256 |
| `predictions` | (review_id, part) | 16 calibrated probabilities and thresholded labels, model id |
| hotel aggregates (DB) | (hotel_id, attribute[, period, segment]) | expected positive / negative counts, satisfaction posterior, grade, evidence |

### 2.3 How each v1 data shortcut is removed (not patched)

| v1 shortcut | v2 |
|---|---|
| Synthetic `hotel_id` (hash mod 200) | Real hotels from the source; id from name + address. Same-address aliases grouped into one split. |
| Lowercased, stopword-stripped text | Source text kept verbatim apart from whitespace; one shared `normalize()` applied identically to training, scoring and API input. |
| Document-level labels, evidence = review prefix | Labels per review part; the labelled part *is* the evidence. |
| Regex weak labels with context-free triggers and an untested negation path | Not used. Labels come from a written guideline applied by an annotator panel (ADR 004). |
| Different label processes for train and test; two prompts | One guideline, one process, every split. |
| Gold set from train hotels, sampled by rule output | Gold-test only from test hotels; uniform + enriched strata with weights. |
| Silent parse fallbacks to "all false" | Strict parser: parsed, error or missing; nothing filled in. |
| Thresholds tuned on the scored rows | Thresholds and calibration on gold-dev; gold-test touched once. |

### 2.4 Versioning and validation gates

- **Pinned inputs**: URL + SHA-256 per raw file, plus a parse-level content
  fingerprint that is independent of chunking and line endings (so the Kaggle
  original can be compared with the mirror: BLOCKERS B2).
- **Manifests** (`data/manifests/*.json`, committed): SHA-256, size and row
  counts of every built file, the input digests and the code commit. Raw and
  processed data are gitignored and rebuilt by `make data` (66 s measured).
  ADR 003 explains why this beats DVC for this repository.
- **Gates that fail loudly**: raw shape (515,738 x 17, columns in order);
  contracts on `hotels` and `reviews` (types, ranges, uniqueness, value sets,
  foreign keys, `n_reviews` consistency); hotel-disjoint splits; label files
  parsed strictly; promotion rule (§3.6).
- **Data card** with provenance, licence status, quirks, PII handling, and
  allowed uses per source. A `NOTICE` separates the code licence (MIT) from
  data licences.

---

## 3. Modeling strategy

### 3.1 Task

Multi-label classification of a **review part** into 16 labels
(8 attributes x positive/negative). Input: `"Liked: <text>"` or
`"Disliked: <text>"` after the shared normalisation. A review's labels are the
union of its parts' labels.

### 3.2 Labels (ADR 004)

| Set | Source hotels | Reviews | Annotators | Use |
|---|---|---|---|---|
| gold-test | test (226 hotels) | 1,200 | 3, majority vote | Final comparison table, once |
| gold-dev | val (220 hotels) | 600 (*as built:* 594; the 6 reviews of the one val hotel moved to train went to the train labels, DATA_CARD §5) | 3, majority vote | Thresholds, calibration, model selection |
| train (silver) | train (1,048 hotels) | 4,000 (expand if the learning curve has not flattened; *as built:* 4,006 with those 6 reviews) | 1 | Training |
| train_2 (silver, *as built*) | train hotels | 4,000 more, no review shared with train (the learning curve had not flattened) | 1 | Training (rungs named `*-train2`) |
| external human check | TripAdvisor Annotated | up to 442 | 3 | Panel-vs-human agreement on C, R, S, L, V, F |

Each set: 30% from a rare-topic enriched stratum (wifi words, or location
words in the Disliked part), 70% uniform; inverse-inclusion weights recorded.

**Learning curve (M2).** `python scripts/learning_curve.py` (TF-IDF +
logistic regression, gold-dev only, 3 seeds): review-level macro-F1 0.803,
0.832, 0.843, 0.852 at 25/50/75/100% of the first 4,006 training reviews.
The last quarter still adds 0.010, so the curve has not flattened and, as
planned, a second training sample of 4,000 reviews (`train_2`, same
stratification, disjoint from every set, `curator label sample-extra`) was
labelled by the same annotator: 4,000 reviews, 6,901 parts, 829 hotels,
every answer parsed (`data/manifests/labels.json`). Its per-label
prevalences are within 0.014 of the first sample's (largest: room
praise, 0.244 vs 0.231 of parts). The cheap rungs (TF-IDF,
embeddings) and roberta-base were re-run on train + train_2
(`--extra-train`; rung names end in `-train2`, `make train2`); ELECTRA-small
and mmBERT-small were not, for CPU time.
Agreement: Fleiss' kappa per label. A 100-review human audit sheet for the
owner is generated (BLOCKERS B3). Weak supervision is out unless an
experiment shows it helps.

### 3.3 Ladder (one harness, same rows)

| Rung | Model | Source (reachable, pinned) | Why |
|---|---|---|---|
| a1 | TF-IDF word 1-2 + char 3-5 grams, logistic regression | scikit-learn | Strong cheap baseline |
| a2 | Same features, LightGBM per label | lightgbm | Non-linear baseline |
| b | Frozen sentence embeddings (bge-small-en-v1.5) + logistic heads | Docker Hub layer, digest-pinned | Is semantics enough without fine-tuning? |
| c1 | Fine-tuned **roberta-base** | Hugging Face legacy S3, SHA-256 pinned | Same base model as v1: architecture-for-architecture comparison |
| c2 | Fine-tuned ELECTRA-small (14M params) | Hugging Face legacy S3 | Fast, small serving footprint |
| c3 | Fine-tuned mmBERT-small (ModernBERT architecture) | Docker Hub layer | The only modern-architecture encoder reachable |
| v1 | v1 checkpoint, frozen, scored in four configurations (whole review or parts, shipped or gold-dev-tuned thresholds) | Git LFS | The comparison target is the configuration with the best gold-dev macro-F1 (§3.6); *as built:* parts with gold-dev-tuned thresholds |
| d | LLM annotator panel | three independent LLM annotators | Labels only; never served |

ModernBERT-base, DeBERTa-v3 and Ettin are the preferred modern encoders but
are only distributed through huggingface.co, which is blocked (BLOCKERS B1).
`uv run curator model run-encoder <key> --base-dir <dir>` runs any of them
once the owner unblocks it (BLOCKERS B1).

Training details: class-weighted BCE (sqrt(neg/pos), capped), AdamW, linear
warmup/decay, dynamic padding, max 128 tokens (covers the 95th percentile
part), selection on **dev macro-F1 at dev-tuned thresholds** (the deployment
metric; v1 selected at 0.5). Seeds fixed and recorded. *As built:* the epoch
is selected on **part-level** gold-dev macro-F1 (each part scored on its
own), not on review level; every encoder was trained this way, so the rungs
are comparable, and the per-epoch history in each model card says so. Found
by the independent review after training; not changed, to avoid retraining.

### 3.4 Calibration and thresholds

Per-label isotonic calibration and F1-optimal thresholds, both fitted on
gold-dev only, thresholds kept within [0.1, 0.9] (*as built:* the F1-optimal
threshold is searched among the gold-dev scores inside that range, rather
than found unconstrained and then clipped), fallback 0.5 for labels with
fewer than 10 dev positives. Calibration uses the recorded inverse-inclusion
sampling weights (gold-dev oversamples rare topics). Calibration quality is
reported as ECE and Brier on gold-test, and calibration-in-the-large
(predicted versus observed share of positives, per label) on all of
gold-test, weighted by the inverse-inclusion sampling weights so both shares
estimate the corpus rate. (*Correction:* this first said "on the uniform
stratum"; that stratum excludes every review with rare-topic words, so it
contains no wifi reviews and is not a random sample of the corpus.) Hotel aggregates use **expected counts** (sums of
calibrated probabilities), not thresholded counts; reviews that were used as
training data are excluded from hotel aggregates (2.2% of train-hotel
reviews once both training samples are counted) so no hotel score rests on
in-sample predictions.

### 3.5 Evaluation

- Headline: **review-level macro-F1 over 16 labels on gold-test**, 95% CI by a
  bootstrap that resamples hotels.
- Also: micro-F1, part-level F1, per-label precision/recall/F1 with support,
  ECE.
- Slices: Liked vs Disliked part; **cross-polarity** labels (negative labels in
  Liked parts, positive labels in Disliked parts; catches a "part implies
  polarity" shortcut); sampling stratum ("uniform": reviews without
  rare-topic words; "enriched": reviews with them); city; length band.
- Error analysis: per-label false positives / negatives with text, written to
  `docs/model/ERRORS.md`.
- Hotel-level validity (non-circular): (1) **out-of-time**: attribute scores
  from 2015-16 text vs each test hotel's 2017 mean reviewer score; (2)
  agreement with the site's Average_Score, always shown next to a trivial text
  baseline; (3) tier stability under bootstrap.
- Experiment tracking: MLflow with a local SQLite store (ADR 010); a JSON
  summary of every run is committed under `artifacts/`.

### 3.6 Promotion rule (written before results)

A model ships only if, on **gold-test**:

1. its review-level macro-F1 is higher than the **v1 checkpoint's** and the
   95% interval of the paired difference (hotel-cluster bootstrap) lies above
   0; and
2. among the candidates that pass (1), it has the best gold-dev macro-F1;
   ties within 0.01 go to the smaller, faster model; and
3. its mean ECE after calibration is at most 0.05; and
4. it scores the full corpus (about 0.9M parts) on the 4-core build machine in
   under 4 hours.

The comparison table (all rungs plus v1, with intervals) goes in
`docs/model/COMPARISON.md`. Eval tests with thresholds (`pytest -m eval`)
encode rule 1 and minimum per-label recall on the fixture.

**Clarifications fixed before any v2 model was scored** (written in M2, before
the first `curator model run-*`; they share the M2 commit with the results,
so git history alone cannot prove the order):

- *Rule 2's dev metric* is review-level macro-F1 on gold-dev at the
  thresholds tuned on gold-dev (the same harness and level as rule 1).
- *Rule 3's ECE* is measured on **review-level probabilities** (noisy-OR of
  the calibrated part probabilities), because that is the quantity the hotel
  scores add up (ADR 006). It is the mean over the 16 labels of 10-bin ECE on
  gold-test. Part-level ECE is reported next to it. *(As built: the "Part
  ECE" columns of `docs/model/COMPARISON.md` and a row of the model card,
  added after review; per label it is `test.ece` in each rung's
  `report.json`. It is empty for v1's whole-review configurations, whose
  review probabilities are calibrated directly instead of combined from
  parts. No rule uses it.)*
- *v1's configuration*: v1 is scored with two inputs (the whole review,
  since v1 was trained on whole reviews; or each part separately; both
  lowercased) and two threshold sets (its shipped thresholds; thresholds
  tuned on gold-dev). The configuration with the best gold-dev macro-F1 is
  "the v1 checkpoint" in rule 1. All four are reported. *(Corrected after
  review: this line first called the lowercased whole review "the input
  shape it was trained on", but v1's training text was also
  stopword-stripped with split contractions (v1 audit, D3). That
  undocumented preprocessing is not reproduced, so v1's "best
  configuration" is the best of these four.)*
- *Encoder hyperparameters* are fixed per base model from the published
  fine-tuning recipes and not searched on gold data: roberta-base lr 3e-5,
  4 epochs; ELECTRA-small lr 1e-4, 5 epochs; mmBERT-small lr 5e-5, 4 epochs;
  batch 32, warmup 6%, weight decay 0.01, max length 128, seed 20261024. The
  epoch is selected on gold-dev (§3.3).
- *Rule 4* is checked by timing the packaged ONNX model on a fixed sample of
  20,000 corpus parts and extrapolating to the corpus.
- *The served package* (added on 24 October at 21:01 UTC, before any
  package had been built or scored): what ships is the candidate's ONNX
  package, int8 when that loses at most 0.005 gold-dev macro-F1. Rules 1
  and 3 are therefore checked again on the package's own gold-test scores
  ("package holds", column P in `COMPARISON.md`). This check can only
  block a promotion; it never selects a different model.
- *Ties under rule 2* ("the smaller, faster model") are broken by
  parameter count, as coded before any result. When measured corpus speed
  would break the tie differently, `COMPARISON.md` says so in a note; the
  decision does not change. *(As built: the counts are the table coded
  before any result for every base it lists, so all runs of one base have
  one size and a tie between them falls to gold-dev macro-F1; a run's
  recorded `n_parameters` sizes only a base the table lacks.)*

### 3.7 Model card

`artifacts/models/<name>/MODEL_CARD.md`: intended use, training data and
labels (machine-annotated), metrics with intervals, slices, calibration,
known failure modes, licence status of the weights' inputs.

---

## 4. Tiering and aggregation

Decided in **ADR 006** (pre-registered before any hotel is scored; it
amends the earlier draft of this section, which proposed shrinkage towards
the market average). Summary:

- **Measures** per hotel and attribute, from calibrated probabilities
  combined per review with a noisy-OR (a review counts at most once per
  polarity): for cleanliness, room, staff, location, value and breakfast &
  food, the **share positive** among guests who gave an opinion; for noise
  and wifi, which guests mention mostly when they fail, the **complaint
  rate** among all guests. Beta posteriors with the Jeffreys prior (no
  corpus input, so any grade is hand-recomputable and the demo subset gives
  the same tiers as the full run).
- **Grades**: Strong ("at least 4 praises per complaint", or at most 1 guest
  in 20 complained), Weak ("complaints outnumbered praise", or at least 1 in
  10 complained), OK, or too few opinions; Strong and Weak require 95%
  posterior certainty, Weak also at least 3 distinct complaining reviews.
- **Tiers** (lexicographic weights: the essentials decide): Excellent (three
  confirmed essential strengths, no material weak spot, reviews spread over
  at least 6 months), Good (two confirmed essential strengths, at most one
  material weak spot), Fair, Caution (a confirmed weak essential), plus "not
  enough reviews" (< 30) and "not enough evidence" (< 10 opinions on an
  essential). Material weak spots are named as **catches** ("Good, but
  noisy").
- **Explanation**: generated from API fields only; dated statements about
  reviews, quotes for and against (redacted, length-capped, p >= 0.8), why
  not the next tier, what would change it, "if X doesn't bother you".
- **Validation**: subset invariance, monotonicity property tests, truth
  tables, out-of-time validity on test hotels, external agreement with
  effect sizes and baselines, bootstrap stability with label draws, a
  ground-truth simulation of false-claim rates, sensitivity (reported only).

## 5. Backend

- **FastAPI + Pydantic v2** (already the project standard), **SQLAlchemy 2**
  with **Alembic** migrations; **PostgreSQL** in Docker, **SQLite** for local
  development and tests; the same migrations run on both.
- Typed settings (`hotel_curator.config`), structured JSON logs with request
  ids (structlog), `/healthz` (liveness) and `/readyz` (database and hotel
  data; the model is reported, not required).
- Rate limiting per client, request body size limit, strict input validation on
  every endpoint (lengths, enums, page bounds), consistent error schema,
  CORS allow-list, no echo of input in responses.
- Pagination (`page`, `page_size`, `total`), sorting and filtering on the
  hotel list.
- OpenAPI with tags, summaries, examples and documented error responses.
- Endpoints (v2 contract): hotel list/search, hotel detail, evidence, trends,
  segments, compare, cities, model/tier-policy metadata, review analysis.
- CLI `curator` with one command per stage (data, label, train, evaluate,
  score, aggregate, db, serve).
- Stack versions verified against PyPI on 2026-10-24: ADR 008.

---

## 6. Frontend

- **React + TypeScript + Vite + Tailwind + shadcn/ui** (ADR 009: a read-only
  SPA over a separate API does not need Next.js's server rendering), TanStack
  Query for data fetching, React Router, Recharts, a typed client generated
  from the OpenAPI schema.
- Pages: **Explorer** (filter/sort by tier and attribute grades), **Hotel**
  (tier, why, attribute grades with intervals, evidence, trends, segments),
  **Compare** (2-4 hotels), **About the model** (methodology in plain
  language, metrics with intervals, limits).
- Accessibility: WCAG 2.2 AA basics (contrast, keyboard, focus, labels, no
  colour-only meaning), checked with axe in Playwright.
- Responsive; works after `docker compose up` with the committed demo data.
- Tests: Vitest + Testing Library + MSW for components; Playwright end-to-end
  for J1-J3 and J5.

---

## 7. Quality and security plan

### 7.1 Tests and gates

| Gate | Target | Tool |
|---|---|---|
| Unit + integration + API tests | pass | pytest |
| Python coverage | **≥ 80%** of `hotel_curator` | pytest-cov |
| Model behaviour | eval thresholds (promotion rule, fixture recall) | `pytest -m eval` |
| Lint / format | clean | ruff |
| Types | clean, strict | mypy |
| Frontend | lint, type-check, unit tests, build | eslint, tsc, vitest, vite |
| End-to-end + a11y | critical journeys pass, no serious axe violations | Playwright + axe |
| CI | all of the above on pushes to `main` and `v2` and on every pull request | GitHub Actions |
| Containers | `docker compose up` serves the demo end to end | docker compose |

### 7.2 Security requirements and abuse cases (checkable claims)

| ID | Requirement | How it is verified |
|---|---|---|
| SR-1 | Every endpoint validates input: string lengths, enum values, page and page-size bounds, id formats | API tests send out-of-range and malformed values and expect 422 |
| SR-2 | Request bodies above a size limit are rejected before parsing | API test with an oversized body expects 413 |
| SR-3 | Per-client rate limits on all endpoints, stricter on `analyze` | API test exceeds the limit and expects 429 with `Retry-After` |
| SR-4 | Responses never echo submitted review text back unvalidated, and errors never leak stack traces | API tests on error paths; error schema review |
| SR-5 | No secrets in code or git history | gitleaks on full history |
| SR-6 | Dependencies have no known high/critical vulnerabilities, or each is documented | pip-audit, osv-scanner (offline DB), npm audit |
| SR-7 | SAST clean or each finding triaged | semgrep (python, security-audit, javascript rules), bandit |
| SR-8 | No pickle or arbitrary-code model loading; safetensors / ONNX only; no `trust_remote_code` | code review + grep in CI |
| SR-9 | Displayed review snippets are length-capped and person names redacted; nationality never shown next to date + room type | unit tests on the redaction; data card |
| SR-10 | File handling: downloads only over HTTPS with SHA-256 pins; archive extraction rejects absolute and `..` paths | unit tests |
| SR-11 | LLM labelling treats review text as data (delimiters, explicit instruction, `</reviews>` neutralised); no LLM in the serving path | unit tests on the prompt builder; architecture |
| SR-12 | Container runs as non-root with a pinned base image and a healthcheck | Dockerfile review; `docker inspect` |
| SR-13 | CORS allow-list from settings; no wildcard with credentials | config test |
| SR-14 | SQL only through SQLAlchemy with bound parameters; sort fields from an allow-list | code review + API tests with injection strings |

Abuse cases considered: scraping the API (rate limits, page-size cap); CPU
exhaustion through `analyze` (part length cap, batch cap, stricter rate limit,
inference in a bounded thread pool); prompt injection in review text into the
labelling LLM (SR-11); poisoning tiers by submitting reviews (not possible:
`analyze` never writes); de-anonymising reviewers (SR-9).

### 7.3 Review gates before the final push

Code review by independent reviewers (Python, FastAPI, ML
engineering, React, general); security review (a structured
security-review pass, a security reviewer, gitleaks, pip-audit,
osv-scanner, npm audit, semgrep, bandit; every candidate finding
re-verified before it is reported); a separate verifier on the full
diff. Results in `docs/security/v2-security-review.md`. `/red-team`
is left to the owner.

---

## 8. Differentiators ("surprise me")

Built (each testable and explained in plain language):

1. **What changed at this hotel.** For each attribute, the satisfaction share in
   the most recent six months of the data vs the 18 months before, with a
   posterior probability that it really moved. Flags only credible changes.
2. **Who this hotel suits.** Attribute satisfaction by traveler segment
   (couples, families, groups and friends, solo travellers, business trips)
   where evidence is sufficient: "families complain
   about room size twice as often as couples here".
3. **What would change the tier.** For each hotel, the smallest attribute
   improvement that would move it up one tier, computed by re-running the tier
   policy on a modified attribute.

Future work (not built): a natural-language question endpoint answered only
from evidence; dataset-drift monitoring on new reviews (label prevalence and
length drift); OpinRank sub-rating validity check; aspect span extraction.

---

## 9. Milestones and commit plan

| # | Commit | Definition of done |
|---|---|---|
| M0 | `docs: v2 plan, v1 audit, decisions` | Plan, audit, tooling, blockers, glossary, ADRs, labeling guideline committed |
| M1 | `feat(data): real-hotel data foundation` | `make data` rebuilds from pinned sources; contracts pass; manifest, data card, EDA committed; v1 synthetic data removed |
| M2 | `feat(ml): labels, model ladder and evaluation` | Gold/silver labels with agreement; all rungs evaluated by one harness; calibration; comparison with v1 with CIs; promotion decision; model card; thin notebooks |
| M3 | `feat(api): tiers, scoring and backend` | Tier policy + explanations; corpus scored; DB + migrations + seed; API with tests; OpenAPI reviewed |
| M4 | `feat(web): explorer, hotel, compare, about` | Pages wired to the API with demo data; component + e2e + a11y tests |
| M5 | `docs: product polish` | Outsider README, docs index, `docker compose up`, Makefile, CI green |
| M6 | `fix/test/ci: quality and security gates` | All gates in §7 with evidence; security review document; verifier findings fixed; PR opened |
| M7 | `docs: architecture views` | System, data-flow, request, ML-lifecycle views in `docs/architecture/` |

---

## 10. Decisions (ADRs)

| ADR | Decision |
|---|---|
| [001](../decisions/001-dataset.md) | Primary dataset: Booking.com 515K Europe; Overture enrichment; evaluation-only side sets |
| [002](../decisions/002-label-taxonomy.md) | 8 attributes x 2 polarities, labelled per review part, one guideline |
| [003](../decisions/003-data-versioning.md) | Pinned sources + committed manifests instead of DVC |
| [004](../decisions/004-labels-from-llm-panel.md) | Labels from an independent LLM annotator panel |
| [005](../decisions/005-model-ladder.md) | Model ladder and sources under the network constraints |
| [006](../decisions/006-tier-scheme.md) | Tier scheme |
| [007](../decisions/007-serving-onnx.md) | Serve with ONNX Runtime; precompute hotel results |
| [008](../decisions/008-backend-stack.md) | Backend stack and versions |
| [009](../decisions/009-frontend-stack.md) | Frontend stack and versions |
| [010](../decisions/010-experiment-tracking.md) | MLflow with a local store |

---

## Status

| Stage | Gate | Evidence | State |
|---|---|---|---|
| Step 0 tooling | inventory + baseline | `docs/TOOLING.md`; v1 tests 11 passed, 35% coverage | done |
| Step 1 audit | `docs/v1-audit.md` | four independent reviewers; numbers cited by cell/file | done |
| Step 2 plan | this file + ADRs 001-010 | dataset panel unanimous on A; tier panel: simplest-credible design 163 / traveler-first 153 / risk-first 149 | done (M0) |
| M1 data | contracts pass on full data | `curator data build`: 515,738 raw rows -> 515,212 reviews, 1,494 hotels (1,048/220/226 train/val/test); contracts pass; rebuild byte-identical (sha256 of both tables unchanged); 47 s with cached downloads | done (M1) |
| M2 labels/models | promotion rule | Labels: gold-test 1,200 and gold-dev 594 reviews (3 LLM annotators, majority; Fleiss' kappa 0.943-0.997 per label), train 4,006 + train_2 4,000 reviews (1 annotator). 14 rungs scored by one harness; `curator model compare` promotes `roberta-base-ft-train2`: gold-test review macro-F1 0.922 [0.911, 0.932] vs v1 0.687 [0.668, 0.705], paired difference +0.236 [+0.216, +0.255], review ECE 0.010; its int8 package 0.920 [0.909, 0.930], corpus estimated at 1.07 h from a 20,000-part benchmark (224 parts/s, 4 threads); `pytest -m eval`: 3 passed | done (M2) |
| M3 backend | API tests, coverage | `curator score parts`: 865,732 parts in 5,987 s on 4 threads; `curator score hotels`: 1,494 hotels (good 58, fair 960, caution 271, not enough evidence 129, not enough reviews 76; excellent 0: no hotel's room is 95% certain to reach the 80% Strong line, closest 0.941; 64 hotels have cleanliness and staff Strong); `curator score validate` (docs/model/TIERS.md): out-of-time rho 0.43-0.72, all intervals above 0; highest simulated false-claim rate 0.028 (target <= 0.05); tier kept in 88% of replicates; API, DB and scoring tests in the M3 commit message | done (M3) |
| M4 frontend | component, e2e, a11y | `npm run check` (eslint, tsc, vitest, vite build): 65 unit tests passed; `npx playwright test`: 37 passed (17 journeys, 20 axe checks in light and dark mode, no serious or critical violations); `npm audit --audit-level=high`: 0 vulnerabilities; `prettier --check`: clean | done (M4) |
| M5 polish | compose up, CI | Both images built from the M5 tree (`docker build`; API 260 MB compressed, 1.11 GB unpacked; web 26 MB); `docker compose up --no-build -d`: db, api and web healthy, web answering after 24 s; `/` 200 with CSP and security headers, `/api/readyz` ready with 1,494 hotels, `/api/docs` and `:8000/docs` 200, `POST /api/v1/analyze` 503 (no weights, B8); Postgres not reachable from the host; API filesystem read-only, uid 10001; `docker compose down -v` leaves no containers or volumes. `pytest -m "not eval" --cov`: 375 passed, coverage 90%. The CI workflow's first GitHub run follows the M5 push; its result is recorded in M6 | done (M5) |
| M6 gates | security review | `docs/security/v2-security-review.md`: SR-1..SR-14 with evidence (SR-3 narrower than registered: probes and docs are not rate-limited); gitleaks (PR range, all refs, tree, staged), trufflehog (history, tree): 0 findings in the pull request, 1 false positive (a file hash) on the session backup branch only; pip-audit (188 packages), osv-scanner (780 locked packages), npm audit (600): 0 known vulnerabilities; bandit 0 findings; semgrep 286 results triaged, 0 security issues; container images scanned with osv-scanner (pip removed from the API image; the rest has no upstream fix or is not reachable); independent automated reviews: a four-area security review with a skeptic per finding (3 low findings, fixed), a structured security-review pass (nothing reportable), a separate review whose finding moved Postgres off the network, and a verification of the whole diff by five reviewers and five skeptics (44 confirmed findings, each fixed or documented with its reason in two rounds, each fix checked by a separate skeptic); a red-team exercise with four attacker teams and a skeptic each (6 findings reproduced, 2 confirmed low: the database role is no longer a superuser, and the compose setup's shared rate-limit bucket is documented; 74 attacks resisted). Final tree: `pytest --cov` 398 passed, 2 skipped (the eval gates that need the weights), coverage 90%; with the weights present locally `pytest -m eval` 3 passed; ruff, mypy strict clean; web `npm run check` 74 tests, prettier clean, npm audit 0; `npx playwright test` 37 passed. CI: the first GitHub run (run 40149142437, on the M5 commit before its message was rewritten; same code apart from documentation wording) passed all three jobs; the M6 run is recorded in M7 | done (M6) |
| M7 architecture | views | — | not started |
