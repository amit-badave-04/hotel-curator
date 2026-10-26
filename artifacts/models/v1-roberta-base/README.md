# v1 checkpoint (frozen, comparison only)

The v1 classifier: roberta-base fine-tuned by `notebooks/v1/03_finetune_classifier.ipynb`
on 20,475 lowercased TripAdvisor reviews with regex and LLM silver labels
(see `docs/v1-audit.md`). Kept unchanged so v2 can be compared with it on the
v2 gold set (`curator model eval-v1`, results in `docs/model/COMPARISON.md`).

| File | What it is |
|---|---|
| `model.safetensors` (Git LFS) | weights, 16 sigmoid outputs in `label_names.json` order |
| `config.json`, `tokenizer.json`, `tokenizer_config.json` | model and tokenizer configuration |
| `label_names.json` | v1 label order (`breakfast_*` is v2's `food_*`) |
| `thresholds.json` | v1's shipped per-label thresholds (tuned on v1's validation split) |
| `metrics.json`, `training_log.json` | v1's own reported numbers (on v1's synthetic setup) |

Removed in v2: `thresholds_gold_tuned.json` (tuned on the same rows v1 then
reported as its gold score) and `training_args.bin` (a pickle, not needed to
run the model). Not used to initialise any v2 model.
