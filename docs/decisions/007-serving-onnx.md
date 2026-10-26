# ADR 007: Serve the classifier with ONNX Runtime, precompute everything else

- Status: accepted
- Date: 2026-10-24

## Context

The product is read-mostly: hotel tiers, grades, trends and evidence are
derived from a fixed review corpus and change only when the pipeline reruns.
The classifier is needed live only for one endpoint, which analyses text a
developer submits. v1 served from PyTorch, which made the image multi-GB
(the Linux PyTorch wheel pulls CUDA libraries), loaded the model lazily on
the first request, and retried the ~500 MB load on every request after a
failure.

## Options

1. Serve PyTorch (v1).
2. Export the model to ONNX and serve with ONNX Runtime plus the Hugging Face
   `tokenizers` library.
3. Serve only precomputed results; no live model.

## Decision

Option 2 for the live endpoint, with option 3 for everything else:

- The offline pipeline scores the whole corpus once and writes hotel-level
  results to the database; the API reads them.
- The live `analyze` endpoint runs the ONNX export of the promoted model,
  loaded once at startup. A unit test checks that ONNX and PyTorch outputs
  agree within 1e-4.
- If the promoted model is a TF-IDF baseline, the same interface is served by
  a numpy implementation; the API does not care which.

## Consequences

- The API image needs no PyTorch or scikit-learn.
- Model files are a versioned "package" directory (`model.onnx`,
  `tokenizer.json`, `metadata.json`, thresholds, calibration, model card).
- Readiness (`/readyz`) reports not ready until the database answers and hotel
  data is loaded. The model package is loaded once at startup and its name is
  reported, but it is not required: if it is missing or broken, the reason is
  logged and only `/v1/analyze` answers 503.
