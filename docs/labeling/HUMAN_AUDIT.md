# Human audit of the gold labels (owner action, BLOCKERS B3)

The v2 gold labels were made by an LLM annotator panel (ADR 004). Their
agreement with people was measured on an external dataset
([EXTERNAL_CHECK.md](EXTERNAL_CHECK.md)), but not yet on v2's own reviews.
This audit closes that gap. It takes about an hour.

## What to do

1. Open `data/labels/human_audit_template.csv` (100 reviews drawn at random
   from the gold-test sample; the panel's labels are hidden). Work on a copy
   if you prefer and pass its path in step 3.
2. For each review, read the guideline in [GUIDELINES.md](GUIDELINES.md) and
   fill `liked_codes` and `disliked_codes` with space-separated codes, one
   per opinion: attribute letter plus `+` (praise) or `-` (complaint).

   | Code | Attribute | Code | Attribute |
   |---|---|---|---|
   | C | cleanliness | N | noise |
   | R | room | W | wifi |
   | S | staff | V | value |
   | L | location | F | breakfast & food |

   Example: `C+ S+ R-`. Write `none` when a part has text but no opinion
   about any attribute. Leave the cell empty to skip a part (skipped parts
   are not scored). Parts without text need nothing.
3. Run `uv run curator label audit-score` (or `--path your_copy.csv`). It
   writes `data/labels/human_audit_result.json` with per-label precision,
   recall, F1 and Cohen's kappa of the panel against you.

## How to read the result

Treat your labels as the reference. Labels with low kappa are where the
panel (and therefore the model and the tiers) is least trustworthy; compare
with the human-human kappa in EXTERNAL_CHECK.md to see whether the gap is the
panel's or the task's.
