# Blockers and owner actions

Everything here is something only the repository owner can provide or decide.
Work that did not depend on these items was completed; each entry says what
was done instead and what changes once the blocker is lifted.

Status legend: **OPEN** (still limits v2), **WORKED AROUND** (v2 is complete
without it, but the owner should still act), **CLOSED**.

| ID | Blocker | Status | What v2 did instead | Owner action |
|---|---|---|---|---|
| B1 | `huggingface.co` (and its CDN) is blocked by the build session's network policy, so standard pretrained encoders and sentence-embedding models cannot be downloaded | OPEN | See `docs/decisions/` for the model-source ADR: only models obtainable from reachable hosts were used | In the cloud environment settings, add `huggingface.co`, `cdn-lfs.huggingface.co`, `cas-bridge.xethub.hf.co` to allowed domains, then run `make train-encoder` to execute the full ladder |
| B2 | `www.kaggle.com` is blocked and no Kaggle token is available | WORKED AROUND | Dataset fetched from a public mirror with a pinned SHA-256, validated against the documented shape of the original | Optional: run `make data-verify-kaggle` with `KAGGLE_USERNAME`/`KAGGLE_KEY` set to compare the mirror byte-for-byte with Kaggle |
| B3 | No human annotator | WORKED AROUND | Gold set annotated by a panel of independent LLM annotators with a written guideline; agreement reported; never presented as human ground truth | Label the 100-row audit sheet in `data/labels/human_audit_template.csv` (instructions inside) so human-vs-panel agreement can be reported |
| B4 | `query.wikidata.org`, OpenStreetMap APIs blocked | WORKED AROUND | No external enrichment; hotel metadata comes from the primary dataset only | Optional enrichment, listed as future work |
| B5 | `api.osv.dev`, `semgrep.dev` blocked | WORKED AROUND | osv-scanner run in offline mode against the public OSV database export; semgrep run with rules cloned from GitHub | None required; CI on GitHub Actions runs the online versions |
| B6 | `download.pytorch.org` blocked | WORKED AROUND | PyTorch installed from PyPI (CUDA-enabled wheel, runs on CPU) | None required |
| B7 | `/red-team` is manual-only | OPEN (by design) | Not run | Run it yourself; command in `docs/V2_REPORT.md` |

_This table is updated at every milestone._
