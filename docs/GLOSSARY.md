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
| **train / train_2** | The two training label sets from training hotels: `train` (4,006 reviews: a 4,000-review sample plus the six gold-dev reviews of the hotel that moved to train) and `train_2` (a second 4,000-review sample); `train_2` was drawn because more labels still helped (plan §3.2). A model trained on both has a name ending in `-train2`. |
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
| **Rung / model ladder** | One model in the comparison (for example TF-IDF + logistic regression, or fine-tuned roberta-base); the ladder is the set of rungs from simplest to most complex, all scored by the same harness on the same reviews. |

## Hotel scores and tiers

| Term | Meaning |
|---|---|
| **Share of praise** | For a two-sided attribute (cleanliness, room, staff, location, value, food): of the guests who gave an opinion, the share who praised it, `P / (P + N)`. |
| **Complaint rate** | For a must-be attribute (noise, wifi): the share of all eligible reviews that complained, `N / n`. Guests mostly mention these only when they fail. |
| **Noisy-OR** | How a review's two parts are combined: `1 - (1 - p_liked)(1 - p_disliked)`, so a review counts at most once per label. |
| **Expected count** | `P` and `N`: sums of calibrated probabilities over a hotel's eligible reviews, used instead of counting thresholded labels because they are less biased. |
| **Eligible review** | A review that counts towards hotel scores: not used to train the classifier and not a near-duplicate of an earlier review of the same hotel. |
| **Usable review** | What the web app calls an eligible review. |
| **Prior** | Jeffreys `Beta(0.5, 0.5)`, the standard reference prior. It contains no number from this data, so a hotel's grade depends only on its own reviews. Small hotels are protected by evidence floors and the 95% rule, not by shrinkage. |
| **Likely range** | The 90% credible interval of the share of praise (or complaint rate), given the reviews seen. |
| **Grade** | An attribute's verdict for a hotel: **Strong** (at least 4 praises per complaint, or at most 1 guest in 20 complained, 95% sure), **Weak** (complaints outnumber praise, or at least 1 guest in 10 complained, 95% sure, from 3+ complaining reviews), **OK** (neither confirmed), or **Too few opinions**. |
| **Essentials** | Cleanliness, room and staff: the attributes that decide the tier. |
| **Catch** | A material Weak grade outside the essentials ("Good, but noisy"). It can hold a hotel back but is shown separately. |
| **Material** | A non-essential weak spot raised by at least 1 guest in 20. |
| **Tier** | **Excellent**, **Good**, **Fair**, **Caution**, or **Not enough evidence / Not enough reviews** (not a judgement; there is too little to judge). Rules in ADR 006. |
| **Tier policy** | `config/tier_policy.yaml`: every line, floor and probability the tiers use, stamped with a `params_version`. Fixed before any hotel was scored. |
| **What would change it** | The conditions between a hotel and the next tier up, e.g. "room would be confirmed as a strength after about 12 more opinions at the current rate". |
| **For you** | The tier a hotel would have if a catch does not matter to you ("if noise doesn't bother you, this hotel is Excellent"). |
| **Trend** | The last 6 months of data versus the 18 months before; flagged only when a shift of 10 points (5 for noise and wifi) is at least 90% likely. |
| **Who it suits** | A credible difference between one kind of guest (couples, families, groups, solo, business) and the others. |
| **Evidence quote** | A whole review part, redacted (names, numbers) and cut to 200 characters, shown for or against a grade, with month and traveler type only. |

## Engineering

| Term | Meaning |
|---|---|
| **ADR** | Architecture decision record: a short document in `docs/decisions/` saying what was decided, which options were considered and why. |
| **ONNX** | A portable model format; the API runs the classifier with ONNX Runtime so the server does not need PyTorch. |
| **int8 quantisation** | Storing model weights as 8-bit integers instead of 32-bit floats: a smaller, faster model. Used only when it costs at most 0.005 gold-dev macro-F1. |
| **Alembic migration** | A versioned script that creates or changes database tables. |
| **Readiness** | `/readyz` answers OK when the database answers and hotel data is loaded (the model is reported but only `/v1/analyze` needs it); `/healthz` only says the process is alive. |
| **CSP** | Content-Security-Policy: a response header that tells the browser which scripts, styles and connections a page may use; ours allow only the app's own origin. |
