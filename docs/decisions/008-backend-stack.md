# ADR 008: Backend stack

- Status: accepted
- Date: 2026-10-24
- Versions checked against the live PyPI JSON API on 2026-10-24 (release
  dates are the first upload of that version).

## Context

FastAPI was v1's choice and the brief keeps it. The rest of the stack is new:
an ORM with migrations, Postgres in Docker and SQLite locally, typed settings,
structured logs, rate limiting, input validation, a CLI.

## Decision

| Concern | Choice | Version (released) | Licence | Why / alternatives |
|---|---|---|---|---|
| Web framework | FastAPI | 0.162.0 (2026-10-23) | MIT | Project standard. Still 0.x with breaking changes in minors, so the lock pins it. |
| ASGI toolkit | Starlette | 1.10.0 (2026-10-24) | BSD-3 | 1.0 removed `on_startup`/`on_event`; v2 uses the `lifespan` context only. |
| Server | uvicorn | 0.54.0 (2026-09-25) | BSD-3 | Standard ASGI server. |
| Validation | Pydantic | 2.14.1 (2026-10-11) | MIT | Request/response models. |
| Settings | pydantic-settings | 2.16.1 (2026-10-20) | MIT | Typed `CURATOR_*` env settings. |
| ORM | SQLAlchemy | 2.1.6 (2026-10-21) | MIT | Current series; typed `Mapped[]` models. Sync engine: the API is read-mostly with short queries, so a thread pool is simpler than async and avoids the greenlet extra. |
| Migrations | Alembic | 1.20.1 (2026-10-22) | MIT | `render_as_batch` for SQLite; the same migrations run on Postgres. |
| Postgres driver | psycopg 3 (binary) | 3.3.6 (2026-09-18) | LGPL-3.0 | SQLAlchemy 2.1's default Postgres driver; LGPL is fine for an unmodified, dynamically loaded dependency. Optional extra `postgres`. |
| Logging | structlog | 26.1.0 (2026-06-06) | MIT or Apache-2.0 | JSON lines in production, readable in a terminal; routes stdlib loggers too. |
| Rate limiting | `limits` + a small FastAPI dependency | 5.8.0 (2026-02-05) | MIT | SlowAPI (0.1.10) is barely maintained (one commit in 12 months, 64 open PRs) and needs a `request` argument on every endpoint. In-memory moving-window limiter per process; Redis storage is a configuration change for multi-worker deployments. |
| CLI | Typer | 0.27.3 (2026-10-06) | MIT | Already used by v1; transformers 5 depends on it too. |
| HTTP client (downloads, tests) | httpx | 0.28.1 (2024-12-06) | BSD-3 | Stable but stalled; httpx2 (Pydantic-stewarded, first release 2026-05) is the migration path once it matures. |
| DataFrame contracts | pandera | 0.34.1 (2026-10-06) | MIT | Column/row checks with all errors collected; pydantic stays for API I/O. Great Expectations is too heavy for an in-repo pipeline. |

Not chosen: SQLModel (still 0.0.x, adds a layer over SQLAlchemy); asyncpg
(async-only); great-expectations (dependency weight); DVC (ADR 003).

## Consequences

- One codebase, two databases: SQLite file for `make serve` and tests,
  Postgres 16 in `docker compose`; migrations are tested on both (Postgres in
  CI and locally when `CURATOR_TEST_POSTGRES_URL` is set).
- Rate-limit counters are per process; documented in the API docs.
- Python 3.12 is the target (numpy 2.5 and scipy 1.18 need >= 3.12; 3.13
  also resolves).
- PyTorch is not needed by the server (ADR 007). On Linux the PyPI torch wheel
  is a ~3 GB CUDA build; CPU-only machines can install `pytorch-cpu` from
  conda-forge instead (documented in the README).
