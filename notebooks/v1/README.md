# v1 notebooks (archived)

These are the v1 prototype notebooks and write-up, kept unchanged as a
historical record. They are **not** maintained and do not run in the v2
environment. Why v2 replaced them, with cell-level evidence, is in
[`docs/v1-audit.md`](../../docs/v1-audit.md).

| Notebook | v1 role |
|---|---|
| `01_data_ingest_eda.ipynb` | Kaggle TripAdvisor ingest, synthetic hotel ids |
| `02a_weak_supervision.ipynb` | Regex labeling functions |
| `02b_llm_relabeling.ipynb` | LLM relabeling of the abstention pool |
| `02c_gold_set_creation.ipynb` | 200-review LLM "gold" set |
| `03_finetune_classifier.ipynb` | roberta-base fine-tune |
| `04_eval_aggregate_tier.ipynb` | Aggregation, tiers, evaluation |
| `REPORT.md` | v1 write-up |

The v1 checkpoint itself is kept in `artifacts/models/v1-roberta-base/` because
v2 re-scores it on the v2 gold set as the comparison baseline.
