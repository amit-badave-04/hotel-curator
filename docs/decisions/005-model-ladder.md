# ADR 005: Model ladder and where the pretrained weights come from

- Status: accepted
- Date: 2026-10-24

## Context

The brief asks for a ladder from strong baselines to the best model under one
evaluation harness: TF-IDF, sentence embeddings + linear heads, a fine-tuned
encoder (roberta-base versus a modern small encoder such as DeBERTa-v3 or
ModernBERT), and an LLM used only to label data.

The build machine has 4 CPU cores and no GPU, and **huggingface.co is
blocked**, so `from_pretrained("<hub id>")` fails. A research pass checked
every reachable host for pretrained weights and measured CPU speed.

## What is reachable (measured 2026-10-24)

| Model | Params | Source | Pinning | Licence |
|---|---|---|---|---|
| roberta-base | 125M | Hugging Face's legacy public S3 bucket (`models.huggingface.co`), the original 2019 upload | SHA-256 of the weight file | MIT (as published by the authors; not re-verified in-session) |
| ELECTRA-small discriminator | 14M | same S3 bucket | SHA-256 | Apache-2.0 |
| MiniLM-L12-H384 | 33M | same S3 bucket | SHA-256 | unverified |
| bge-small-en-v1.5 (embeddings) | 33M | Docker Hub layer of a third-party inference image | layer digest + HF content hashes | MIT |
| mmBERT-small (ModernBERT architecture) | 140M | Docker Hub layer, third-party | layer digest + HF content hashes | MIT |
| ModernBERT-base, DeBERTa-v3, Ettin, NeoBERT | | huggingface.co only | | blocked (BLOCKERS B1) |

The research also confirmed, tensor by tensor, that **v1 was fine-tuned from
the same roberta-base** that is on the S3 bucket (median relative weight
difference 0.89%), so a v2 roberta-base model is a like-for-like comparison.

Measured CPU throughput at fixed padding (batch 16, 4 threads; review parts
are much shorter than 128 tokens, so dynamic padding is faster in practice):
roberta-base trains at 4.4 sequences/s at length 128, ELECTRA-small at 19.8,
MiniLM-L12 at 10.3.

## Decision

| Rung | Model | Notes |
|---|---|---|
| a | TF-IDF + logistic regression; TF-IDF + LightGBM | `hotel_curator.ml.baselines` |
| b | frozen bge-small embeddings + logistic heads | embeddings computed once, cached |
| c | fine-tuned roberta-base (primary), ELECTRA-small, mmBERT-small | `hotel_curator.ml.encoder`, one training loop for all |
| v1 | v1 checkpoint, frozen | scored on the v2 gold set by the same harness |
| d | LLM annotator panel | labels only (ADR 004) |

- Weights are fetched by `curator model fetch-base`, verified by SHA-256,
  loaded with `torch.load(weights_only=True)` semantics (no pickle code
  execution) and re-saved as safetensors. Pretrained weights are not committed.
- The v1 checkpoint is **not** used to initialise v2 models (its encoder saw
  v1's training reviews).
- The same command trains ModernBERT, DeBERTa-v3 or Ettin as soon as the owner
  unblocks huggingface.co; that run is listed as an owner action.

## Consequences

- The modern-encoder comparison the brief asks for is partial: the only
  ModernBERT-architecture model available is mmBERT-small (multilingual). This
  is stated in the model card and the final report.
- Third-party Docker Hub layers are pinned by digest and cross-checked against
  the content hashes Hugging Face assigned to the files; they are used only as
  a transport.
- Promotion follows the written rule in the plan (§3.6); a smaller model wins
  ties, which matters for the size of the serving package.
