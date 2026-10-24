# ADR 003: Data versioning with pinned sources and committed manifests

- Status: accepted
- Date: 2026-10-24

## Context

v1 committed derived Parquet files with no provenance. v2's raw input is
~240 MB of CSV and its processed tables are ~65 MB; neither belongs in git.
A fresh machine must be able to rebuild the exact same data and prove it.

## Options

| Option | For | Against (measured or sourced 2026-10-24) |
|---|---|---|
| DVC | Pipeline caching, remote storage | Activity fell sharply after the 2025 ownership change (last release 2026-03-31; monthly commits down from 16-18 to 1-6); pulls `diskcache` 5.6.3 with an unfixed CVE (pip-audit); heavy dependency tree; still needs a remote we do not have |
| lakeFS | Branching data lake | Needs a server and an object store: overkill for one dataset |
| Git LFS for data | Already used for the v1 checkpoint | Every version counts against storage and CI bandwidth quotas; raw data should not be redistributed anyway (licence caution, ADR 001) |
| **Pinned sources + manifests** | No new tool; deterministic rebuild; diffable JSON in git | No pipeline caching (not needed: the data stage takes ~1 minute) |

## Decision

- Every raw input is pinned by URL and SHA-256 in `hotel_curator.data.sources`,
  plus a parse-level content fingerprint independent of chunking and line
  endings (lets the owner validate the Kaggle original, BLOCKERS B2).
- Every stage writes a manifest to `data/manifests/<stage>.json` (committed):
  output paths, SHA-256, sizes, row counts, input digests, code commit,
  summary statistics.
- Raw and processed data are gitignored and rebuilt by `make data`.
- Small derived artifacts that the product needs offline (label files, the
  demo dataset, model packages) are committed, each with its own manifest.

## Consequences

- Reproducibility check = rebuild and compare manifests.
- If the pinned mirror disappears, any other copy can be validated against the
  committed fingerprint.
- Parquet bytes can differ across pyarrow versions; `uv.lock` pins pyarrow so
  digests are stable for a given lock.
