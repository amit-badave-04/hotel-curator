# ADR 006: Tier scheme ("confirmed grades", essentials first)

- Status: accepted. **Pre-registered**: committed before any hotel is scored;
  the git history is the record that no value below was fitted to results.
- Date: 2026-10-24
- Amends: `docs/specs/v2-plan.md` §4 (replaces "shrunk towards the market
  average" with a fixed Jeffreys prior; see "Why no market prior").
- Decided by: three independent designs (simplest-credible, risk-first,
  traveler-first) scored by three independent judges (product, ML and
  risk lenses). Totals across judges: simplest-credible 163,
  traveler-first 153, risk-first 149. The simplest-credible design is the
  base; the best parts of the other two are grafted in and every judge's
  must-fix item is addressed.

## Context

v1 computed `pos_rate - neg_rate` per attribute, averaged eight of them and
cut tiers at values chosen by eye from the model's own scores, test hotels
included; the result was noise (v1 audit §4, D10). The brief asks for a
documented, business-interpretable scheme with weights, mention floors,
minimum review counts and uncertainty, and a "why this tier" explanation with
evidence for every hotel.

## 1. What is measured

Per hotel and attribute, over the data window (all reviews of the hotel,
2015-08 to 2017-08):

- **Eligible reviews**: the hotel's reviews minus (a) reviews used as
  classifier training data (their probabilities would be in-sample; they are
  about 1% of train-hotel reviews and are excluded rather than cross-fitted),
  and (b) near-duplicates: within a hotel, reviews whose normalised Liked +
  Disliked text of 10 or more words is identical to an earlier review.
  `n` = number of eligible reviews (placeholders included: a guest with
  nothing to dislike is a guest without a complaint).
- **Per-review judgements (noisy-OR)**: `pos_r = 1 - (1 - p_liked+)(1 - p_disliked+)`
  and likewise `neg_r`, from calibrated probabilities. A review counts at
  most once per polarity, so "10 opinions" means about 10 guests.
- `P = sum pos_r`, `N = sum neg_r`, `M = P + N` (opinions).

Two attribute families:

| Family | Attributes | Measure | Posterior |
|---|---|---|---|
| Two-sided | cleanliness, room, staff, location, value, breakfast & food | **share positive** `theta = P / M`: of the guests who gave an opinion, the share who praised it | `Beta(P + 0.5, N + 0.5)` |
| Must-be | noise, wifi | **complaint rate** `q = N / n`: guests mostly mention these only when they fail, so the share among commenters would be misleading | `Beta(N + 0.5, n - N + 0.5)` |

### Why no market prior

The prior is Jeffreys `Beta(0.5, 0.5)`, the standard reference prior for a
proportion. It contains no number from this corpus, so a hotel's grade
depends only on its own reviews: tiers computed on the committed demo subset
equal tiers computed on the full run (a CI test), and any grade can be
recomputed with a spreadsheet `BETA.INV`. Small hotels are protected by the
evidence floors and by requiring 95% certainty, not by shrinkage.

## 2. Attribute grades

A claim is **confirmed** when its posterior probability is at least
**0.95** (one-sided; equivalently the 90% interval clears the line).

| Grade | Two-sided rule | Must-be rule | Plain meaning |
|---|---|---|---|
| **Strong** | P(theta >= 0.80) >= 0.95 | P(q <= 0.05) >= 0.95 | "at least 4 praises per complaint" / "at most 1 guest in 20 complained" |
| **Weak** | P(theta < 0.50) >= 0.95 | P(q >= 0.10) >= 0.95 | "complaints outnumbered praise" / "at least 1 guest in 10 complained" |
| **OK** | neither | neither | no confirmed strength or weakness |
| **Too few opinions** | M < 10 | n < 30 | not graded |

Extra conditions:

- **Weak needs at least 3 distinct complaining reviews** (reviews with
  `neg_r >= 0.5`), so no negative claim rests on one or two guests.
- **Lean** (display only): an OK grade whose posterior mean is past a line is
  shown as "trending strong" / "trending weak".
- **Material** (for non-essential Weak grades): complaint rate `N / n >= 5%`
  ("at least 1 guest in 20 raised it"). Defined on complaint rate so that one
  more positive review can never make a hotel's tier worse (monotonicity is
  property-tested).

Hand formulas: mean = (P + 0.5) / (M + 1); 90% interval =
`BETA.INV(0.05, P+0.5, N+0.5)` to `BETA.INV(0.95, P+0.5, N+0.5)`.

## 3. Hotel tier

Essentials E = cleanliness, room, staff (`taxonomy.CRITICAL_ATTRIBUTES`).
Weights are **lexicographic**: the essentials decide the tier; a material
weak spot elsewhere can only hold a hotel back; strengths elsewhere never
raise it (they are shown as highlights). A weighted average was rejected
because it is compensatory (good wifi would hide a dirty room) and needs
arbitrary cut-offs.

First matching rule:

| # | Tier (API enum) | Rule |
|---|---|---|
| 1 | `not_enough_reviews` | n < 30 |
| 2 | `caution` | any essential is Weak, and the claim also holds when every part is counted as a hard label (p >= 0.5) |
| 3 | `not_enough_evidence` | any essential has fewer than 10 opinions |
| 4 | `excellent` | all three essentials Strong, no material Weak elsewhere, reviews in at least 6 distinct months with no month holding more than 25% of them |
| 5 | `good` | at least two essentials Strong and at most one material Weak elsewhere |
| 6 | `fair` | everything else: no essential Weak, but fewer than two confirmed essential strengths, or two or more material weak spots |

- **Catches**: material Weak non-essentials are returned as a separate
  `catches` list and named in the label ("Good, but noisy").
- Sorting inside a tier (search results only): the lowest 5th percentile of
  the essentials' share posteriors (a cautious score).
- **What would change it**: the binding condition for the next tier up, in
  guest terms, and "confirmed after about K more opinions at the current
  rate" (closed form from the Beta interval).
- **For you**: the tier recomputed with a catch ignored ("if noise doesn't
  bother you, this hotel is Excellent").
- **Compare**: a difference between two hotels on an attribute is
  highlighted only when P(theta_A > theta_B) >= 0.95 (seeded Monte Carlo
  from the two posteriors).

## 4. Explanation and evidence

Generated deterministically from API fields (a test asserts that text and
numbers agree):

- Verdict, worded as a dated statement about reviews, not a fact about the
  business: "In reviews from Aug 2015 to Aug 2017, guests praised
  cleanliness, room and staff ..." / "... complaints about cleanliness
  outnumbered praise (at least 95% sure)".
- The three essentials: grade, share positive, number of opinions, likely
  range.
- Catches and highlights; why not the next tier up; what would change it.
- Evidence: up to two supporting quotes and one counter-quote per cited
  attribute. Quotes are whole review parts truncated at a word boundary to
  200 characters with "...", with person names, emails and phone numbers
  redacted, shown with month-year and traveler type only (never nationality,
  room type or nights). Eligibility: label probability >= 0.8, and >= 0.9 for
  quotes behind a Weak grade or Caution; otherwise "no clear quote". A note
  says the publisher removed punctuation.
- Footer: data period, `params_version`, data provenance (source, mirror
  commit, licence status "CC0 as declared, originally Booking.com,
  unconfirmed; legal review required before commercial use"), and the
  correction channel in `NOTICE`.

## 5. Trends ("what changed")

For each attribute, the last 6 months of data vs the 18 months before, each
window with at least 15 opinions. A change is flagged when the posterior
probability of a shift of at least 10 points (two-sided) or 5 points of
complaint rate (must-be) is at least 0.90. Alerts use hysteresis: a new state
is announced only when it is confirmed and the old one has fallen below 0.50.

## 6. Parameters (`config/tier_policy.yaml`, stamped `params_version`)

| Parameter | Value | Source |
|---|---|---|
| Strong line, two-sided | 0.80 | "4 praises per complaint" |
| Weak line, two-sided | 0.50 | "complaints outnumber praise", the only non-arbitrary point on the scale |
| Strong / Weak lines, must-be | 0.05 / 0.10 complaint rate | "1 in 20" / "1 in 10 guests complained" |
| Confirmation level | 0.95 one-sided | conventional; both errors (false praise, false accusation) are costly |
| Prior | Beta(0.5, 0.5) | Jeffreys reference prior; no corpus input |
| Minimum opinions per graded attribute | 10 | derived: smallest count at which unanimous praise can be confirmed Strong (Beta(9.5, 0.5) 5th percentile = 0.813) |
| Minimum reviews per tier / per must-be grade | 30 | 3 complaining guests at the 10% line |
| Minimum distinct complaining reviews for Weak | 3 | no warning rests on one or two guests |
| Materiality | complaint rate >= 0.05 | "1 guest in 20 raised it" |
| Excellent spread gate | >= 6 months, busiest month <= 25% | a single burst of reviews cannot carry the top tier |
| Quote eligibility | p >= 0.8 (>= 0.9 for negative claims), 200 characters | no coin-flip quote backs a claim |
| Trend windows / flag | 6 vs 18 months, >= 15 opinions each, P >= 0.90 of >= 10 points | renovation / management-change time scale |

No value comes from the distribution of hotel scores or tiers in this corpus.
The site's Average_Score, `reviewer_score`, `Total_Number_of_Reviews` and any
Overture field are never read by the scoring code (a unit test asserts this),
so they remain independent checks. Changing a value needs a new ADR with a
business reason, confirmed on held-out cities; the tier mix is reported as an
outcome, never used as a target.

## 7. Validation (reported in `docs/model/TIERS.md`)

1. **Subset invariance**: demo-subset tiers equal full-run tiers (CI test,
   using the same stored probabilities).
2. **Monotonicity**: one more praise never lowers a tier, one more complaint
   never raises it (property tests).
3. **Truth tables**: every rule boundary, with a documented tolerance at
   exact boundary values.
4. **Out-of-time**: grades from 2015-08 to 2016-12 vs the raw 2017 shares and
   2017 mean reviewer score, on the 226 test hotels (whose predictions are
   out-of-sample for training, calibration and thresholds).
5. **External agreement**: Spearman correlation with the site's Average_Score
   reported with an effect size, next to v1 and a trivial text baseline (the
   share of reviews with a non-placeholder Disliked part), never as a
   pass/fail on correlation > 0 alone.
6. **Stability**: bootstrap of reviews with Bernoulli label draws; share of
   hotels keeping their tier; tier mix per review-count quartile.
7. **Simulation**: synthetic hotels at real review counts with labels drawn
   through the classifier's measured confusion on gold-dev, checking that
   false Strong and false Weak rates stay at or below 5% per volume stratum.
   If not, intervals are widened by a documented design effect; the lines
   never move.
8. **Sensitivity** (reported only): tier mix at Strong lines 0.75 / 0.85 and
   materiality 2.5% / 10%.

## Consequences

- Tiers are conservative: Excellent requires three confirmed strengths, and
  hotels with little evidence say so instead of being ranked.
- Every grade and tier can be recomputed by hand from the API's P, N and n.
- Expected counts treat calibrated probabilities as observations, so
  intervals are somewhat too narrow; validation items 6 and 7 measure how
  much.
- Rejected: the risk-first design's multi-scenario Monte Carlo decisions
  (harder to explain and recompute) and the traveler-first design's
  best-estimate headlines with a separate certainty badge (positive tiers
  could rest on unconfirmed statements).
