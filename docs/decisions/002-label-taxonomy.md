# ADR 002: Label taxonomy and unit of annotation

- Status: accepted
- Date: 2026-10-24

## Context

v1 labeled whole reviews with 16 binary labels (8 attributes x positive /
negative). Its attribute boundaries were never written down in one place:
the training prompt and the gold prompt defined them differently, "breakfast"
silently meant all food and drink, and price complaints landed in different
attributes depending on the prompt. Whole-review labels also made evidence
impossible to show: a label could not be traced to the text that caused it.

The v2 source (Booking.com reviews, see ADR 001) splits every review into a
**Liked** and a **Disliked** part.

## Options

1. Keep v1's 16 labels and whole-review granularity.
2. Keep the 8 attributes, label each review part, and write the boundaries
   down in one guideline.
3. Extend to a richer scheme: more attributes (bathroom, bed, facilities,
   parking), a neutral/mixed state, and span-level opinion targets.
4. Sentence-level labels.

## Decision

Option 2.

- The same **8 attributes** as v1 (cleanliness, room, staff, location, noise,
  wifi, value, breakfast & food) and **two polarities**: 16 labels. The
  attribute id `breakfast` becomes `food` with the display name "Breakfast &
  food"; `V1_TO_V2_LABEL` maps v1 names so v1 can be scored on v2 data.
- The unit of annotation and prediction is the **review part** (Liked or
  Disliked text). A review's labels are the union of its parts' labels.
- One written guideline, `docs/labeling/GUIDELINES.md` (v2.0), used for every
  label: training, development and test. It fixes the boundaries v1 left open
  (price vs quality, middling words, hearsay, placeholders, negation).
- Codes in `hotel_curator.taxonomy` are the single source of truth.

## Consequences

- v1 and v2 can be compared label for label on the same gold set.
- Part-level labels give evidence for free: the part that carries a label is
  the evidence snippet.
- Option 3 would add value (bathroom and bed are frequent Booking topics) but
  multiplies annotation cost and breaks comparability with v1. Deferred and
  listed as future work.
- Option 4 is not viable: the source text has no punctuation, so sentence
  splitting is unreliable. Parts are already short (Liked: mean 17.6 words,
  Disliked: mean 23.1 words; measured on the full corpus).
- Neutral mentions are not modeled. Hotel scores therefore count *opinionated*
  mentions only, which is what a satisfaction share needs.
