# ADR 001: Dataset

- Status: accepted
- Date: 2026-10-24
- Decided by: a three-judge panel (product, ML/data-science and risk/governance
  lenses, working independently on the same research report).

## Context

v1 used Kaggle `andrewmvd/trip-advisor-hotel-reviews`: 20,491 reviews with only
`Review` and `Rating`, no hotel identity, lowercased and stopword-stripped
text. Its hotel-level results were noise by construction (v1 audit, §4). v2
needs real hotel identity, rich metadata and usable text, under these
constraints: Kaggle and Hugging Face are blocked from the build machine,
GitHub, S3 and public GCS are reachable, no purchases or new accounts.

## Options (all measured in the build container)

| Option | Summary | Panel scores (product / ML / risk, 0-10) |
|---|---|---|
| O1 | A = Booking.com 515K Europe only | 7 / 6.5 / 6 |
| O2 | A + Overture Maps places enrichment | 8.5 / 6.5 / 7 |
| O3 | A + B (Booking.com official 2023) as out-of-domain evaluation only | 6 / 8 / 6.5 |
| O4 | A + B as extra training text | 3 / 3 / 2 |
| O5 | B as primary (anonymised properties) | 1 / 3 / 1 |
| O6 | OpinRank (TripAdvisor 2001-2009) as primary | 3 / 4 / 5 |
| O7 | A + TripAdvisor Annotated only to measure panel-vs-human agreement | 5 / 7.5 / 6 |

Facts behind the scores:

- **A**: 515,738 x 17 (exact documented shape), 1,492 names / 1,493 addresses,
  real coordinates for all but 17 hotels, 2015-08-04 to 2017-08-03, Liked /
  Disliked parts, traveler and trip tags, site average score. Text keeps case
  but the publisher removed punctuation. Licence: CC0 as declared by the
  uploader, who also states the content was originally owned by Booking.com
  (first-hand confirmation impossible: Kaggle is blocked). Reachable as a
  GitHub copy split into 11 chunks (commit `903600f2`), pinned by SHA-256.
- **B**: Booking.com's own release, 1.63M reviews from 2023, 40,000
  properties, raw punctuated text, but anonymised ids (no names, no
  coordinates), month without year, and a licence statement that says
  "non-commercial" while linking CC BY-SA 4.0.
- **Overture Maps places**: CDLA-Permissive-2.0, reachable on S3, has website,
  category and coordinates for named hotels.

## Decision

1. **A is the only source** for training text, labels, gold sets, tiers and
   the demo dataset. Fetched from the pinned mirror; the build fails closed on
   any digest, shape or contract mismatch. Raw data are never committed.
2. **Overture Maps places** as display-only enrichment (website, category,
   coordinates for the 17 hotels without them), matched conservatively
   (distance + name similarity + category + confidence), unmatched hotels left
   empty, match score stored and shown. Committed as a small table with the
   release id and an attribution notice. Never used as a model feature, label,
   tier input or evaluation signal. Priority P2: built after the core product.
3. **TripAdvisor Annotated** (O7) is fetched by pinned hash, never committed,
   author field dropped, and used only to report panel-vs-human agreement on
   the six attributes it covers (it has no noise or wifi labels).
4. **B** (O3) only as an opt-in, out-of-domain evaluation of a frozen model,
   using B's own fields (part polarity, accommodation score). No B text goes
   into the repo, the image, the labelling panel, training, calibration or
   threshold tuning. Priority P3 (future work if time runs out).
5. Rejected: O4 (licence risk in shipped weights; destroys the independent
   out-of-domain check), O5 (no names, no year, licence ambiguity, PII), O6 as
   primary (17-25-year-old claims about named hotels; no Liked/Disliked split).

## Conditions adopted from the panel

- The data period (Aug 2015 to Aug 2017) is shown on every surface; the
  product never implies current quality; "what changed" is a historical
  replay.
- Evidence snippets are verbatim review parts, length-capped, with person
  names redacted; nationality is never shown next to date, room type and
  nights.
- Thin cells (few mentions per hotel x attribute, per period or per segment)
  are suppressed and shown as "not enough evidence".
- Same-address aliases (one property listed under two names) are kept in one
  split.
- One shared text normalisation is applied to training, scoring and API input.
- A `NOTICE` file states that the MIT licence covers code only, lists each data
  source with its claimed licence and allowed uses, and says legal review is
  needed before commercial use. No Booking.com branding.
- Model weights trained on A inherit A's licence uncertainty; the model card
  says so.
- Noise and wifi have no human or external gold anywhere reachable; their
  metrics rest on the panel gold only and are flagged (marked in the
  per-label and calibration tables of `docs/model/COMPARISON.md` and listed
  in the model card's limits).

## Consequences

- Real hotels, cities, coordinates and dates make every user journey possible.
- The punctuation-stripped source text is a known limit for the `analyze`
  endpoint; mitigated by normalising API input the same way.
- Reproducibility depends on a personal GitHub mirror that could disappear;
  mitigated by the content fingerprint, which lets the owner validate any
  other copy (Kaggle with a token: BLOCKERS B2).
