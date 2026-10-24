# Glossary

Plain-language definitions of every term used in the code, API and docs.
Terms are grouped by where you first meet them.

## Data

| Term | Meaning |
|---|---|
| **Review** | One guest's feedback about one stay at one hotel, with a date, a reviewer score and two free-text parts. |
| **Part** (Liked / Disliked) | The two text boxes of a booking-site review: "What did you like?" and "What did you not like?". v2 labels each part separately. Also called a *segment*. |
| **Placeholder** | Text the site inserts when a part is left empty ("No Negative", "No Positive"), or a guest's "Nothing"/"N/A". Placeholders carry no labels. |
| **Hotel identity** | The stable `hotel_id` v2 derives from the hotel's name and address in the source data. v1 had no hotel identity and hashed reviews into 200 fake hotels; v2 removes that. |
| **Canonical tables** | The cleaned, validated `hotels` and `reviews` Parquet files every later step reads. Their columns are defined once in `hotel_curator.data.contracts`. |
| **Data contract** | A schema with types, ranges and uniqueness rules that a table must satisfy; the pipeline stops with an error if it does not. |
| **Manifest** | A JSON file recording the source URL, SHA-256 digest, size and row counts of every input and output, so a rebuild can be checked byte for byte. |
| **Hotel-disjoint split** | Train, validation and test sets that never share a hotel, so the model is always evaluated on hotels it has not seen. |

## Labels

| Term | Meaning |
|---|---|
| **Attribute** | One of eight things guests judge: cleanliness, room, staff, location, noise, wifi, value, breakfast & food. |
| **Polarity** | Positive (praise) or negative (complaint). A part can be both for the same attribute ("small but comfortable room"). |
| **Label** | An (attribute, polarity) pair, e.g. `noise_negative`. There are 16. |
| **Opinionated mention** | A positive or negative judgement of an attribute. Neutral facts ("breakfast included") are not mentions. |
| **Annotation guideline** | The written rules in `docs/labeling/GUIDELINES.md` that every annotator follows. |
| **Annotator panel** | Three independent LLM annotators that label the same reviews without seeing each other's answers. |
| **Gold set** | Reviews labeled by the full panel, with the final label decided by majority vote. Split into *gold-dev* (for tuning) and *gold-test* (for the final, untouched evaluation). Machine-annotated, not human ground truth. |
| **Silver labels** | Labels from a single annotator pass, used for training only. |
| **Weak labels** | Labels produced by keyword rules. Kept only if they measurably improve the model. |
| **Fleiss' kappa** | Agreement between several annotators beyond what chance would give: 1 is perfect, 0 is chance level. |

## Model and evaluation

| Term | Meaning |
|---|---|
| **Classifier** | The model that reads a review part and outputs a probability for each of the 16 labels. |
| **Threshold** | The probability above which a label is shown as present. Tuned per label on gold-dev only. |
| **Calibration** | Making a probability of 0.7 mean "right about 70% of the time". Needed because hotel scores add up probabilities. |
| **ECE** (expected calibration error) | Average gap between predicted probability and observed frequency. Lower is better; 0 is perfect. |
| **Precision / recall / F1** | Precision: of the labels the model gave, the share that were right. Recall: of the true labels, the share the model found. F1: their harmonic mean. |
| **Macro-F1** | F1 averaged over labels with equal weight, so rare labels count as much as common ones. The headline metric. |
| **Micro-F1** | F1 over all label decisions pooled, dominated by common labels. |
| **Cluster bootstrap** | Resampling whole hotels (not single reviews) to get a confidence interval that respects the fact that reviews of one hotel are related. |
| **95% CI** | A range that, under the bootstrap, contains the true value 95% of the time. |
| **Promotion rule** | The written rule that decides whether a new model may replace the current one (see the plan). |
| **Model card** | A one-page description of a trained model: data, metrics with intervals, intended use, limits. |
| **Slice** | A subset of the test data (short reviews, one city, ...) evaluated separately to find where the model is weak. |

## Hotel scores and tiers

| Term | Meaning |
|---|---|
| **Satisfaction share** | For one hotel and attribute: of all opinionated guest judgements, the share that are positive. |
| **Expected count** | The sum of calibrated probabilities over reviews, used instead of counting thresholded labels because it is less biased. |
| **Prior / shrinkage** | A starting belief centred on the market average. Hotels with few reviews are pulled toward it; hotels with many reviews are not. |
| **Credible interval** | The range in which the true satisfaction share lies with 90% probability, given the reviews seen. |
| **Grade** | An attribute's verdict for a hotel, derived from its satisfaction share and the evidence available. |
| **Tier** | A hotel's overall quality band, assigned by the documented tier policy, with a confidence and a "why this tier" explanation. |
| **Tier policy** | The configuration file that holds attribute weights, standards, minimum evidence and tier rules. |
| **Evidence** | Short quotes from real reviews that support an attribute's grade. |
| **Provisional** | Shown when the evidence is too thin for a confident tier. |

## Engineering

| Term | Meaning |
|---|---|
| **ADR** | Architecture decision record: a short document in `docs/decisions/` saying what was decided, which options were considered and why. |
| **ONNX** | A portable model format; the API runs the classifier with ONNX Runtime so the server does not need PyTorch. |
| **Alembic migration** | A versioned script that creates or changes database tables. |
| **Readiness** | `/readyz` answers OK only when the database and model are loaded; `/healthz` only says the process is alive. |
