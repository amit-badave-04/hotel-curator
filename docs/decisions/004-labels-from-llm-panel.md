# ADR 004: Gold and training labels from an independent LLM annotator panel

- Status: accepted
- Date: 2026-10-24

## Context

Supervised training and every metric need labels. v1 had three different label
sources (regex rules, a lenient LLM prompt on 12% of train, a strict LLM prompt
on a 200-review "gold" set), no agreement statistics, and a gold set that was
80% training reviews. The v1 audit found the regex rules unusable (24 of 180
triggers never match; context-free triggers; the negation guard was not on the
code path that produced the labels).

No human annotators are available for v2 (BLOCKERS.md, B3), and no external
LLM API key is available in the build session.

## Options

1. Human annotation: the right answer, not available.
2. Repair the v1 regex rules and use them as weak labels.
3. A single LLM annotator for everything (v1's approach, minus the bugs).
4. A panel of three independent LLM annotators following one written
   guideline, majority vote for gold, a single annotator for training data;
   agreement reported per label; a separate human-labeled external set used
   to measure how the panel compares with people.

## Decision

Option 4.

- **Annotators** are independent LLM instances, each given
  only the guideline and a batch of reviews (as data between delimiters), with
  no tools and no view of other annotators' answers. Annotators A and B run
  on the same model and see the reviews in opposite orders;
  annotator C runs on a different, smaller model, so the three make less
  correlated errors. Every answer is stored with the annotator id, its role,
  the guideline SHA-256 and the prompt SHA-256.
- **Gold-test**: reviews from **test hotels only**, three annotators, majority
  vote. Used once, for the final comparison table.
- **Gold-dev**: reviews from **validation hotels only**, three annotators.
  Used for threshold tuning, calibration and model selection.
- **Training labels (silver)**: reviews from **train hotels only**, one
  annotator.
- **Sampling**: two strata per split, uniform and rare-topic enriched, with
  inverse-inclusion weights recorded (`hotel_curator.labeling.sampling`).
- **Agreement**: Fleiss' kappa and mean pairwise Cohen's kappa per label.
- **External human check**: the panel also labels a small public set that
  human annotators already labeled, and panel-vs-human agreement is reported.
- Weak (regex) labels are not used unless an experiment shows they help.

## Consequences

- The gold set is machine-annotated. Every document that shows a gold-set
  number says so. LLM-to-LLM agreement is a consistency signal, not human
  inter-annotator agreement.
- The LLM is used only to make labels, never in the serving path; the
  product runs without any LLM or API key.
- The committed label files plus the guideline are the reproducible artifact.
  Re-annotating needs an LLM; `curator label annotate` documents how to do it
  with an API key, and its unit tests mock the LLM.
- A 100-review human audit sheet is provided for the owner (BLOCKERS.md B3).
