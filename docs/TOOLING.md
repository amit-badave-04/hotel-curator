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
| pypi.org, files.pythonhosted.org, registry.npmjs.org, conda.anaconda.org, github.com (git + release assets), raw.githubusercontent.com, media.githubusercontent.com (LFS), codeload.github.com, s3.amazonaws.com, storage.googleapis.com (public objects), Docker Hub, ghcr.io | huggingface.co (+ CDN, hf-mirror.com), www.kaggle.com, query.wikidata.org, overpass-api.de / openstreetmap.org, data.world, zenodo.org, figshare.com, api.osv.dev, semgrep.dev, archive.ics.uci.edu, download.pytorch.org, lfs.github.com (Git LFS uploads), dl-cdn.alpinelinux.org (Alpine packages) |

Consequences are tracked in [`BLOCKERS.md`](BLOCKERS.md).

## Command-line gates

| Tool | Available at start? | Version used | Notes |
|---|---|---|---|
| ruff | Yes (0.16.9 preinstalled) | 0.17.2 (`uv.lock`) | lint + format; `make` and CI run the locked version |
| mypy | Yes (2.3.1 preinstalled) | 2.4.0 (`uv.lock`) | type-check; `make` and CI run the locked version |
| pytest, pytest-cov | Installed into the project env | see `uv.lock` | |
| gitleaks | No | 8.30.1 | GitHub release, checksum verified, installed in a scratch dir; `.gitleaks.toml` allow-lists only local paths v2 gitignores (`.venv/`, `var/`, raw data); v1 tracked `data/processed/`, and a history scan with no allow-list finds nothing more |
| trufflehog | No | 3.99.7 | second secret scanner; `--no-verification` (offline) |
| semgrep | No | 1.182.1 (PyPI) | registry `semgrep.dev` blocked, so rules come from a clone of `semgrep/semgrep-rules` (python, generic/secrets, dockerfile, docker-compose, javascript, typescript) |
| osv-scanner | No | 2.7.0 | `api.osv.dev` blocked; runs `--offline` against the PyPI, npm, Debian, Alpine and Go databases downloaded from the public GCS bucket; scans the lockfiles and the three container images (`scan image --archive`) |
| pip-audit | No | 2.10.1 (PyPI) | audits `uv export` output against PyPI's advisory data (`-s osv` needs the blocked API) |
| bandit | No | 1.9.4 (PyPI) | Python SAST |
| npm audit | Yes (npm 10.9.4) | | registry reachable |
| docker, docker compose | Yes (29.9.2, compose 5.6.1) | | Docker Hub pulls work; GitHub's container registry blobs are blocked |

Each tool was first run against a known-bad fixture (a planted secret, a
vulnerable requirement pin, an insecure Python snippet) and caught it, so a
clean result means "no findings", not "tool not working". Results of the
gates on v2 are in `docs/security/v2-security-review.md`.
