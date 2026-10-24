# Tooling inventory (v2 build session)

This file records which tools the v2 build expected, which ones were actually
available in the cloud session that built v2, and what was used instead. A gate
is never skipped because a tool is missing: the fallback column says what ran
in its place. Measured on 2026-10-24.

## Session environment

| Item | Value | How measured |
|---|---|---|
| Hardware | 4 CPU cores, 15 GB RAM, no GPU | `nproc`, `free -g`, `nvidia-smi` (absent) |
| Python | 3.12.3 (`/usr/bin/python3.12`); 3.13.17 default | `uv python list` |
| uv | 0.11.32 | `uv --version` |
| Node / npm / pnpm | 22.22.0 / 10.9.4 / 10.28.0 | `node --version` etc. |
| Docker | Engine 29.9.2 (daemon started manually with `dockerd`), Compose v5.6.1 | `docker info`, `docker compose version` |
| PostgreSQL | 16 (system package) | `ls /usr/lib/postgresql` |
| Browser for e2e | Chromium build 1243 preinstalled at `/opt/pw-browsers`, global `playwright@1.63.0` | `ls /opt/pw-browsers`, `npm ls -g` |
| Git LFS | 3.4.1; `git lfs pull` fetched the v1 checkpoint (499 MB) | `git lfs pull` |

### Network policy (egress proxy)

| Reachable | Blocked (HTTP 403 on CONNECT) |
|---|---|
| pypi.org, files.pythonhosted.org, registry.npmjs.org, conda.anaconda.org, github.com (git + release assets), raw.githubusercontent.com, media.githubusercontent.com (LFS), codeload.github.com, s3.amazonaws.com, storage.googleapis.com (public objects), Docker Hub, ghcr.io | huggingface.co (+ CDN, hf-mirror.com), www.kaggle.com, query.wikidata.org, overpass-api.de / openstreetmap.org, data.world, zenodo.org, figshare.com, api.osv.dev, semgrep.dev, archive.ics.uci.edu, download.pytorch.org |

Consequences are tracked in [`BLOCKERS.md`](BLOCKERS.md).

## Command-line gates

| Tool | Available at start? | Version used | Notes |
|---|---|---|---|
| ruff | Yes | 0.16.9 | lint + format |
| mypy | Yes | 2.3.1 | type-check |
| pytest, pytest-cov | Installed into the project env | see `uv.lock` | |
| gitleaks | No | _pending_ | installed from GitHub release into a scratch dir |
| semgrep | No | _pending_ | rules registry blocked; rules cloned from GitHub |
| osv-scanner | No | _pending_ | api.osv.dev blocked; offline DB from public GCS bucket |
| pip-audit | No | _pending_ | |
| bandit | No | _pending_ | |
| npm audit | Yes (npm 10.9.4) | | |
