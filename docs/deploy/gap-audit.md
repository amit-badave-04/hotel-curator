# Deployment gap audit: hotel-curator on Fly.io and Cloudflare

**Basis.** `main` at `77a934b` (clean tree), audited on 2026-10-28 against
the owner's three Fly.io projects for conventions:
[refit-nilm-pipeline](https://github.com/amit-badave-04/refit-nilm-pipeline) (`5e8bbdd`),
[ai-semiconductor-risk-intelligence-graphrag](https://github.com/amit-badave-04/ai-semiconductor-risk-intelligence-graphrag) ("graphrag", `638f5c3`) and
[clinic-voice-agent](https://github.com/amit-badave-04/clinic-voice-agent) ("clinic", `e74cb5a`).
Nothing here was checked against Fly or Cloudflare; statements about how
those platforms behave are marked **(unverified here)** and are listed with
a live check in ADR 011.

### What the build environment can and cannot run (checked 2026-10-28)

| Available | Absent or blocked |
|---|---|
| `.venv` with pytest, ruff, mypy, alembic and `curator` | `flyctl`: absent |
| Node 22 and npm; Playwright's Chromium | `nginx` binary: absent, so `nginx -t` runs only in CI |
| Postgres 16 server binaries and `openssl`, so a local TCP + TLS Postgres can be run | Docker daemon: not running, so no image builds here (CI builds them) |
| git-lfs with its pre-push hook installed | Network policy blocks fly.io, api.fly.io, api.cloudflare.com and lfs.github.com |
| The weights file on local disk: `artifacts/models/roberta-base-ft-train2/model.onnx`, 126,503,367 bytes, SHA-256 `b94a3a61…` (matches `docs/BLOCKERS.md` B8) | |

Measured here for sizing: `curator serve` with the int8 model, after 30
`POST /v1/analyze` requests (the 31st hit the app's own 30-per-minute
limit): peak RSS 451 MiB (`VmHWM`), latency p50 89 ms and p95 142 ms on 4
CPU threads. SIGTERM shut the server down cleanly.

---

## 1. Gap audit

Severities come from three independent audits (reference conventions, serving side, frontend). *Note:* marks a remark added when the audits were combined. "Step" means the plan step that closes the gap.

### Blockers

| ID | Gap | Evidence | Effort | Step |
|---|---|---|---|---|
| B1 | **The served weights are not in git.** Any image built from a clean checkout (CI, Fly's remote builder, a deploy workflow) ships without them, and `POST /v1/analyze` then answers 503. CI still passes: the LFS pull fetches nothing, the eval gates skip, and the smoke test never calls analyze. *Note:* `.gitattributes:4` already sends `*.onnx` to LFS. While lfs.github.com is blocked, the installed LFS pre-push hook (`.git/hooks/pre-push`) will fail the **whole** push, not only the weights. | `docs/BLOCKERS.md:19`; `.gitignore:42-44`; `Dockerfile:42`; `.github/workflows/ci.yml:31-32,141-142,145-150`; `tests/eval/test_promoted_model.py:54-59`; `src/hotel_curator/api/routes/analyze.py:92-94`; `artifacts/evals/promotion.json:355-358` | M | 1 |
| B2 | **The database design assumes Postgres over a Unix socket with `trust` auth.** There are no TCP or TLS parameters, no connect_timeout and no pool sizing. No Postgres test runs over TCP. Compatibility with PgBouncer in transaction mode is unverified. | `docker-compose.yml:44`; `docker/db-init/10-app-role.sh:14-18`; `src/hotel_curator/db/session.py:12-28`; `src/hotel_curator/config.py:31`; `tests/unit/test_compose_config.py:47-63`; `docs/decisions/008-backend-stack.md:37-42` | M | 3 |
| B3 | **No setting is required.** If the database URL is missing, the API silently uses a per-machine SQLite file and still reports ready. A `postgres://` URL crashes SQLAlchemy 2.1.6. A plain `DATABASE_URL` is ignored because only the `CURATOR_` prefix is read. | `src/hotel_curator/config.py:23-25,31`; `Dockerfile:46`; `.env.example:1` | S | 3 |
| B4 | **Every API start runs migrations and the seed.** The entrypoint ignores its arguments, so a release command could not run anything else. With several machines, `upgrade` and `seed` race with no lock. *Note:* a new entrypoint-only variable cannot go into compose or `.env.example` unless it is a Settings field (`tests/unit/test_compose_config.py:35-36`, `:39-44`). | `docker/api-entrypoint.sh:1-6`; `Dockerfile:51`; `src/hotel_curator/db/seed.py:92-126`; `src/hotel_curator/db/migrations/env.py:24-40` | S | 3 |
| B5 | **Rate limits are keyed on the ASGI peer address.** No code reads `CF-Connecting-IP` or `Fly-Client-IP`, and nginx appends to `X-Forwarded-For`. On Fly, either every user shares one 120-per-minute bucket, or `X-Forwarded-For[0]` can be spoofed. | `src/hotel_curator/api/ratelimit.py:74-77,87-89`; `src/hotel_curator/api/app.py:117-122`; `src/hotel_curator/cli/db.py:63-72`; `web/nginx.conf:47-48`; `.venv/.../uvicorn/middleware/proxy_headers.py:176-183`; `docker-compose.yml:47` | M | 4 |
| B6 | **The nginx upstream is hard-coded** as `api:8000` and resolved once at startup, with no templating. The compose fixed IP `172.28.0.10` has no Fly equivalent, and a test pins the literal upstream. | `web/nginx.conf:44,69`; `web/Dockerfile:16`; `docker-compose.yml:73-75,80-84`; `web/test/nginx-headers.test.ts:52-57` | S | 4 |
| B7 | **There is no landing page and no `/explore` route.** Old `/?city=…` links would lose their filters once `/` becomes the landing page. | `web/src/routes.tsx:13-24,37`; `web/src/lib/filters.ts:53-79` | M | 6 |
| B8 | **Nine internal links or hrefs use `/` to mean "the explorer".** | `AppShell.tsx:82-88,93-95`; `RouteError.tsx:18`; `NotFoundPage.tsx:10`; `HotelPage.tsx:447,674`; `ComparePage.tsx:292,438`; `ExplorerPage.tsx:369` | S | 6 |
| B9 | **Vitest page tests assume the Explorer is at `/`.** | `ExplorerPage.test.tsx:16,44,54,84,98,108,116,127,140,167,188-198,208,217`; `ComparePage.test.tsx:199`; `AppShell.test.tsx:35-37` | S | 6 |
| B10 | **Playwright journeys and the axe suite assume the Explorer is at `/`.** There is no e2e test for the landing page or for old-link redirects. | `web/e2e/a11y.spec.ts:7-8,75-78`; `web/e2e/journeys.spec.ts:5-7,42,45,108,220,239,254,315,328-330` | M | 6 |
| B11 | **"Try the model" exists only inside AboutPage.** The frontend cannot know before submit that analyze is unavailable: `/v1/meta` has no flag, and `/readyz` reports the model only after it loads. The 503 message offers no other path. | `AboutPage.tsx:516-526,555-645`; `routes/meta.py:53-62`; `routes/health.py:39-53`; `api/state.py:33-56`; `routes/analyze.py:83,92-94` | M | 1 (weights) and 6 (flag, UI) |

### Should-fix

| ID | Gap | Evidence | Effort | Step |
|---|---|---|---|---|
| S1 | The API binds IPv4 `0.0.0.0` only. Reaching it at `.internal` would need `::`. This is a blocker only if the web app addresses the API by `.internal`; with Flycast (decision D3) it is a configurability item. Docker disables IPv6 inside containers (`web/nginx.conf:13`), so `::` cannot become the compose default. | `docker/api-entrypoint.sh:6`; `src/hotel_curator/cli/db.py:55-58` | S | 4 |
| S2 | The non-superuser database role exists only in the compose init script. One URL serves both DDL and reads. Whether the provider's default user is a superuser is unchecked. | `docker/db-init/10-app-role.sh:1-18`; `api/deps.py:18-23`; `cli/db.py:20-24,44-49`; `api/app.py:71`; `docs/security/v2-security-review.md:160` | M | 3 |
| S3 | `database_url` is a plain `str`, so it would appear in a repr. There is no deploy token. The security review states "Secrets: none are needed". | `config.py:31`; `v2-security-review.md:20`; `ci.yml:1-156` (no `secrets.` reference) | S | 3, 7, 9 |
| S4 | Nothing emits HSTS. In nginx, an `add_header` in a location replaces the server-level headers, so a server-level HSTS line would not reach `/api/` responses. | `web/nginx.conf:29-37,40-42,54-55,76-78`; `api/middleware.py:15-20`; `web/security-headers.ts:19-26` | S | 4 (and 5) |
| S5 | The strict CSP would block scripts Cloudflare can inject: Rocket Loader, automatic Web Analytics, Email Obfuscation, Zaraz (unverified here). There is no CSP reporting, so the breakage would be silent. | `web/nginx.conf:31,54,76`; `web/security-headers.ts:6-17`; `web/test/nginx-headers.test.ts:23-29`; `web/e2e/fixtures.ts:7-23` | S | 5 |
| S6 | No Fly health checks exist, only Docker HEALTHCHECKs (Fly's use of them is unverified here). `/readyz` does not require the model. With no connect_timeout, `/readyz` can hang. The web check only probes the static root. `/api/healthz` and `/api/readyz` are public and not rate-limited (an accepted risk). *Merged:* the serving audit's "make /readyz require the model" idea is folded in here. | `Dockerfile:49-50`; `web/Dockerfile:20-21`; `routes/health.py:22-53`; `db/session.py:28`; `v2-security-review.md:196-198` | S | 3 (flag added once B1 is closed) |
| S7 | `timeout_graceful_shutdown` is not set (uvicorn's default `None` waits without limit). `sh` is PID 1 while migrations run. nginx stops gracefully only on SIGQUIT. Neither Dockerfile sets STOPSIGNAL. Fly's default kill signal and timeout are unverified. | `cli/db.py:64-72`; `.venv/.../uvicorn/config.py:236`; `.venv/.../uvicorn/server.py:45-47,296-305` | S | 3 |
| S8 | uvicorn's own plain-text loggers, including a second access log, are mixed into the JSON stream. The request id is a fresh uuid4 rather than Fly's or Cloudflare's id. nginx uses its default log format. There are no metrics and no Sentry. | `cli/db.py:64-72`; `.venv/.../uvicorn/config.py:83-114,214-216`; `api/middleware.py:72,98-105`; `src/hotel_curator/logging.py:15-70`; `pyproject.toml:16-33`; `web/nginx.conf:1-89` | M | 8 |
| S9 | API memory had never been measured (now measured for this audit: 451 MiB peak, see above). Each worker loads its own ONNX session, and ONNX Runtime threads are left on automatic. | `artifacts/evals/roberta-base-ft-train2/throughput.json:1-9` (speed only); `inference/onnx_predictor.py:57-63`; `inference/package.py:37`; `v2-security-review.md:210-215` | S | 3 |
| S10 | CI never pushes an image, never deploys, never tests Postgres over TCP and never calls analyze. *Merged:* the image vulnerability scan is tracked under N2. | `ci.yml:7-13,134-156`; `docs/architecture/online.md:151` | M | 3 (TCP Postgres job), 7 |
| S11 | **Origin exposure.** Compose's `127.0.0.1` port binds have no Fly equivalent. The API must get no public IP. The fly.dev hostname or public IPs would let clients bypass Cloudflare. The SSL mode and Fly's certificate behind a proxied record are documented nowhere. | `docker-compose.yml:51-52,71-72`; `v2-security-review.md:193`; `docs/architecture/online.md:48-49` | M | 3, 4, 5 |
| S12 | Neither image serves NOTICE, and the web app has no link to the repository. NOTICE has no model section, although the README says it does. A legal review is required before commercial use. *Note:* NOTICE lies outside the web build context (`docker-compose.yml:66` `context: ./web`), so serving it needs a synced copy. | `NOTICE:1-38` (`:23`, `:36-38`); `README.md:183-185,200-201`; `scoring/pipeline.py:60-64`; `AppShell.tsx:134`; `AboutPage.tsx:739-741`; `Dockerfile:16,38-43`; `web/Dockerfile:15-16` | S | 9 |
| S13 | Only `evaluation` carries intervals. It can be null, and in e2e it always is, because the seed model is `test-model`. `policy` is an untyped dict. Test-set sizes and the data period are not exposed. | `routes/meta.py:29-62,106-113,147-166`; `AboutPage.tsx:36-55,70-87,311-375`; `web/e2e/journeys.spec.ts:185-190` | M | 6 |
| S14 | Pages hardcode policy and evaluation numbers that `/v1/meta` already serves or should serve. | `AppShell.tsx:49`; `AboutPage.tsx:373,660,689,702-707,735`; `HotelPage.tsx:107,199,234,241,300-334,386,586-600`; `ComparePage.tsx:92-95,157,231,385-387`; `web/src/lib/domain.ts:1-5,106-334` | M | 6 |
| S15 | There are no OpenGraph, Twitter, canonical, icon or manifest tags, and no robots.txt or sitemap. The SPA fallback answers `/robots.txt`, `/sitemap.xml` and unknown paths with index.html and a 200. No build-time public-origin setting exists. | `web/index.html:1-19`; `web/public/` (favicon.svg only); `web/nginx.conf:86-88`; `web/vite.config.ts:10` | M | 6 |
| S16 | e2e accessibility and responsive coverage lacks the landing page, `/explore`, the redirect and a 768 px width. | `web/e2e/a11y.spec.ts:6-80`; `web/e2e/journeys.spec.ts:218-341`; `web/playwright.config.ts:35` | S | 6 |

### Nice-to-have

| ID | Gap | Evidence | Effort | Step |
|---|---|---|---|---|
| N1 | Compose-only hardening (read-only root filesystem, tmpfs, no-new-privileges, network-less DB, loopback binds) does not carry to Fly. `/app/var` is writable only to support SQLite. | `Dockerfile:35-36,46-47`; `docker-compose.yml:29-30,54-60,76-77`; `v2-security-review.md:40,61` | S | 2 (record), 9 |
| N2 | The only image sizes come from a weightless build. pandas and pyarrow are in the serving deps. There is no image scan in CI and no Renovate or Dependabot. | `docs/specs/v2-plan.md:479`; `pyproject.toml:16-33`; `scoring/grades.py:24`; `v2-security-review.md:206-218` | M | 7 |
| N3 | The `/api` 301 is absolute, so behind TLS its Location header becomes `http://<host>:8080/api/`. | `web/nginx.conf:14,58-60` | S | 4 |
| N4 | The default CORS allow-list is localhost, which a single-origin deployment does not need. | `config.py:35`; `api/app.py:124-130`; `web/orval.config.ts` | S | 3 |
| N5 | The landing page has no loading, error or unavailable states yet. There is no pending indicator for lazy routes. After a deploy, a stale tab requesting a removed chunk gets a 404 and the generic RouteError. | `RouteError.tsx:4-21`; `web/nginx.conf:81-83`; `AppShell.tsx:36-54` | S | 6 |
| N6 | Landing assets must be self-hosted to keep the CSP unchanged. `gzip_types` lacks XML. The og-image has no long cache lifetime. | `web/security-headers.ts:6-26`; `web/nginx.conf:7-10,27` | S | 6 |
| N7 | The Explorer is bundled eagerly because it used to be the landing page. | `web/src/routes.tsx:6,10-11,24` | S | 6 |
| N8 | The web README describes `/` as the explorer. | `web/README.md:3,73,94-99,167` | S | 9 |

**Merges:** B1's "/readyz requires the model" moved into S6. The image scan from S10 and the image-size gap both sit in N2. B11 stays separate from B1 because it needs API and frontend work even after the weights land. All other gaps are kept as the audits reported them; none were dropped.

---

## 2. Reference conventions to copy

| Topic | refit | graphrag | clinic | Follow for hotel-curator, and why |
|---|---|---|---|---|
| **fly.toml layout** | `service/fly.toml` next to its Dockerfile (L1-2 header) | Root `fly.toml` for the API (L1-6) and `deploy/neo4j/fly.toml` for the database (L1-5) | Root `fly.toml` (L1-2) | **graphrag**: the API config is the root `fly.toml`, because its Dockerfile and build context are the root. refit's rule puts `web/fly.toml` next to `web/Dockerfile`. A self-run database gets `deploy/postgres/fly.toml`. Every file opens with a comment saying why it is configured that way (all three do this). |
| `[build]` / `[env]` | `dockerfile = "Dockerfile"` plus non-secret `[env]` (L6-13) | Same (L11-21) | Empty `[build]`, no `[env]` (L6) | **refit/graphrag**: an explicit dockerfile, non-secret config in `[env]`, secrets only through `fly secrets`. |
| `http_service` | port 8080, `force_https`, `auto_start` (L15-20) | Same (L23-28) | Same (L8-13) | Web app: the same shape with nginx on 8080. API: **no public `http_service`.** It gets a private Flycast service on 8000. No reference has a private HTTP service, so this part is new. |
| Idle policy and HA | Scales to zero; two machines "for availability" (`service/README.md` L87-88) | Always warm (`auto_stop_machines = "off"`, min 1); `--ha=false`; parked with `scale count 0` (`scripts/ops.ps1` L46, L57) | Always on, min 1; the code assumes no HA but nothing enforces it (`guard.py` L34-35) | **graphrag**: start-up includes model load, as graphrag's embedder load does (fly.toml L1-4), and the in-memory limiter needs one process (`api/ratelimit.py:87-89`; ADR 008 lines 27 and 43). Enforce `--ha=false` in commands rather than only in docs, which is clinic's gap. |
| VM sizing | `performance-1x`, 2 GB, for CPU-bound ONNX (L36-40) | `shared-cpu-1x`, 2 GB, plus 1 GB swap (L9, L34-36) | `shared-cpu-1x`, 512 MB (L15-17) | **Measure first** (step 3). Provisional: web `shared-cpu-1x`/256 MB; API `shared-cpu-1x`/1 GB with `swap_size_mb = 512`. Escalate to refit's `performance-1x`/2 GB if analyze latency suffers from shared-CPU throttling. |
| Concurrency | `requests`, 2/4 (inference only) | `requests`, 20/40 | Fly defaults | **graphrag's form** for the API, because most calls are short DB reads and analyze already has its own 30-per-minute limit (`api/app.py:119-121`). Tune after step 3 measurements. |
| Checks | `[[http_service.checks]]` GET /health, grace 20s | Top-level `[checks.health]` /healthz, grace 180s | Top-level `[checks.health]` /healthz, grace 10s | All three agree on **interval 30s and timeout 5s**. Syntax differs. Follow **refit's service-level form**, because service checks are the ones tied to proxy routing (unverified here). Web checks GET /; API checks GET /readyz. Grace period = measured start time plus margin, which is graphrag's reasoning (fly.toml L45). |
| Deploy trigger | Manual `flyctl deploy`; CI only tests and builds | Manual, from `ops.ps1`, `--remote-only` | Manual `make deploy` | **None of the three has a deploy workflow, a FLY_API_TOKEN or flyctl-actions.** The CI/CD deploy must be designed fresh (step 7). Manual `flyctl deploy --ha=false --remote-only` stays as the documented fallback (graphrag `ops.ps1` L46). |
| Migrations | None | Done in the image entrypoint | Manual `alembic upgrade head` | **No reference uses `release_command`.** Designed fresh (step 3). |
| Secrets | None | `push_fly_secrets.py`: `.env.fly`, `-a`, `--stage`, values on stdin (L45, L74-76) | Same idea, but reads `.env` and passes no `-a` (L65, L89) | **graphrag**: hotel-curator's Settings read `.env` (`config.py:24`), so production values must not live there. With three apps, `-a` is mandatory. |
| Client IP | `fly-client-ip`, falling back to the socket (main.py L98) | Only the configured header; a test proves `CF-Connecting-IP` is ignored (`tests/test_serve_api.py` L206-212) | Trusts `cf-connecting-ip` **unconditionally** (router.py L127-129), which a client can spoof | **graphrag's principle** (trust only what a hop you can verify sets), extended because no reference sits behind a Cloudflare proxy: trust `CF-Connecting-IP` only when the hop that reached Fly is in Cloudflare's IP ranges. **Do not copy clinic.** |
| Cloudflare | Absent | Turnstile only, on `semigraph.fly.dev` (RUNBOOK L82-84) | Turnstile, plus an *optional* proxy step with no evidence it was ever done (`hardening_runbook.md` L35-45) | **No reference documents Cloudflare DNS, proxying, SSL mode, Full (strict), origin certificates, WAF or real-IP handling.** All of step 5 must be designed fresh. Turnstile is not adopted (D13). |
| HSTS | None | App sends `max-age=31536000; includeSubDomains` (`serve/routes.py` L33) | None | **graphrag**: the app layer emits HSTS, which here means nginx, starting with a short max-age (D12). |
| Observability | JSON line per request; logs never hold submitted data (`service/README.md` L102-103) | `flyctl logs`, Fly checks, `ops.ps1 status` | Optional Sentry (`app/main.py` L52-56), UptimeRobot, Healthchecks.io | **refit** for log hygiene; **clinic's** optional Sentry and UptimeRobot, both off unless configured. |
| Runbook | Sections in `service/README.md` L72-107 | `docs/RUNBOOK.md` (START, STOP, status, cost, secrets, first-time setup, troubleshooting) plus an ADR | `scripts/*_runbook.md` | **graphrag's** `docs/RUNBOOK.md` structure plus an ADR. |
| CI conventions | push to main + PR | gitleaks via `gitleaks-action@v2` (a tag pin) | Same | **Keep hotel-curator's own CI**, which is stricter: SHA-pinned actions (`ci.yml:1-4`) and a checksum-verified gitleaks. Do not copy the references' tag pins. |
| Region and org | `lhr` | `sin`, `--org personal` (RUNBOOK L114) | `sjc` | Each region sits near its data or dependencies. For hotel-curator that means Europe; the owner chose `lhr`. |

**Designed fresh, with no reference to copy:** the GitHub Actions deploy workflow and token handling; `release_command`; a private HTTP API on Flycast; the Cloudflare proxy, DNS, SSL, WAF and cache settings; trusting the real client IP behind Cloudflare; locking the origin to Cloudflare. graphrag uses `.internal` only for raw Neo4j bolt traffic (`deploy/neo4j/fly.toml` L2-3), not for HTTP.

---

## 3. Decisions

### Owner decisions (2026-10-28)

| Question | Decision |
|---|---|
| Public hostname | Launch first on `hotel-curator.fly.dev`; everything is built for a later Cloudflare cutover to a custom domain, which the owner names then. |
| Region and database | Fly region `lhr`; Postgres 16 in its own private Fly app with a volume (the graphrag pattern). |
| Licence and contact | Launch as a free, non-commercial demo before the data-licence review; corrections through the repository's GitHub issues. The commercial-use question stays open (`NOTICE`). |

### Decisions taken in this plan

| # | Decision | Reason |
|---|---|---|
| D1 | Three Fly apps: `hotel-curator` (nginx, the only public app), `hotel-curator-api` (private Flycast address only), `hotel-curator-db` (Postgres 16, private, volume-backed). | One browser origin (`web/orval.config.ts` uses `/api`), so no CORS and the CSP stays strict; the API and the database never get a public address. Slug naming as in the reference repos. |
| D2 | API config in the root `fly.toml`, web in `web/fly.toml`, database in `deploy/postgres/fly.toml`. | Each config sits next to its Dockerfile and build context (graphrag's layout). |
| D3 | nginx config becomes a template; the API upstream is `${CURATOR_API_UPSTREAM}`: `hotel-curator-api.flycast:8000` on Fly, `api:8000` in compose. | A Flycast address is stable, so resolving it once at nginx start stays correct. |
| D4 | **Model weights: option (a), Git LFS, copied into the API image.** | The API image is about 1.1 GB unpacked without the model (`docs/specs/v2-plan.md`, M5 row), so 126.5 MB adds roughly 11%. Baking it in means no download at start (the API is kept warm and must answer its first request), no object-storage credentials to manage, and a rebuild only when a new model is promoted. Option (b) would add a storage secret, a start-up download and either a volume or about 130 MB more RAM in a tmpfs on top of the measured 451 MiB. |
| D5 | Builds for Fly fail without the right weights: a `REQUIRE_MODEL` build argument, set on Fly and in the deploy workflow, checks `model.onnx`, `tokenizer.json` and `metadata.json` against `files_sha256` in `artifacts/evals/promotion.json`. | Closes the "CI green, analyze answers 503" hole for anything that ships. |
| D6 | New `curator db release`: migrate, then seed, under a Postgres advisory lock; Fly's `[deploy] release_command` runs it. On Fly, migrate-on-start is off. | Idempotent and safe on every deploy; no race between machines. |
| D7 | `CURATOR_ENVIRONMENT=production` refuses to start without a Postgres URL; `postgres://` and `postgresql://` are normalised to the psycopg driver; the URL is a secret value type; a separate migration URL is used only by `db release`. | No silent SQLite in production; no URL in a repr. |
| D8 | Postgres over TCP on the private network: TLS required (`sslmode=require`, or `verify-full` when a CA is supplied), scram-sha-256, `hostssl`-only pg_hba, `connect_timeout=5`, bounded pool. | Replaces the compose socket-only design. |
| D9 | Two non-superuser roles: an owner role that runs migrations and the seed, and a read-only runtime role for serving. | Serving only reads (`src/hotel_curator/api/deps.py`). |
| D10 | **Two edge modes for the web app.** *Fly mode (launch):* Fly terminates TLS (`[http_service]`, `force_https`), nginx listens in plain HTTP, and the client IP is `Fly-Client-IP`, which Fly's edge sets from the connection. *Cloudflare mode (cutover):* the web app switches to a TCP service on 443 with `handlers = ["proxy_proto"]`, so TLS passes through Fly; nginx terminates TLS with a Cloudflare Origin Certificate and requires Cloudflare's origin-pull client certificate (Authenticated Origin Pulls); the client IP is `CF-Connecting-IP`, trusted only because that handshake proves the request came through Cloudflare; any other Host header is closed. In both modes nginx overwrites one header, `X-Real-IP`, so nothing a client sends survives. Fly's health checks use a plain internal port with no public service. | nginx can only verify origin pulls if it terminates TLS itself; with Fly's TLS in front it never sees the client certificate. Zone-level origin pulls with Cloudflare's shared certificate prove "came through Cloudflare", not "came through this zone"; the strict Host check narrows that, and per-hostname origin pulls with an own certificate is the stronger option where the plan allows it (unverified here). |
| D11 | The API keys its rate limits on `X-Real-IP` only, and only nginx can reach the API (no public address). uvicorn's own `X-Forwarded-For` handling is not used on Fly. | One trusted header, set by one hop; forged headers are tested to be ignored. |
| D12 | HSTS comes from nginx in every header block, starting with a short max-age; Cloudflare's edge HSTS stays off. | One value only; nothing emits HSTS today. |
| D13 | No Turnstile. | Analyze is local CPU work with a 30-per-minute limit and no per-call cost; `script-src 'self'` stays. |
| D14 | API: `shared-cpu-1x`, 1 GB, `min_machines_running = 1`, one machine (`--ha=false`), one worker. Web: `shared-cpu-1x`, 256 MB. Database: `shared-cpu-1x`, 512 MB, 1 GB volume. | Measured peak RSS of the API with the model warm: 451 MiB (30 analyze requests on this machine); with 30% headroom 586 MiB, more than 512 MB. The rate limiter is in memory, so one process. Prices could not be checked here; the runbook gives the formula. |
| D15 | Checks every 30 s with a 5 s timeout; grace periods from measured start-up. API: `kill_signal = "SIGTERM"`, `kill_timeout` above uvicorn's graceful-shutdown timeout. Web: `SIGQUIT` (nginx's graceful stop). | A local SIGTERM test shut the API down cleanly ("Application shutdown complete"). |
| D16 | JSON-only logs (uvicorn's own loggers off); request id from `Fly-Request-Id` when present, `cf-ray` logged beside it; logs never hold submitted text. | Matches refit's log hygiene and the existing no-echo test. |
| D17 | Prometheus metrics on an internal-only port, scraped by Fly through `[metrics]`; alert rules (readiness failing, p95 analyze latency, 5xx rate) set up in Fly's Grafana and documented; Sentry optional, off unless a DSN secret is set, with PII scrubbing. | Matches clinic's optional Sentry; metrics are new. |
| D18 | `scripts/push_fly_secrets.py` reads a git-ignored `.env.fly` and passes values on stdin, with `-a` always set (graphrag's pattern). | `.gitignore` and `.dockerignore` already exclude `.env.*`. |
| D19 | `.github/workflows/deploy.yml`: after a successful `ci` run on `main` in this repository, or by hand; GitHub environment `production` with the owner as required reviewer; per-app deploy tokens; flyctl pinned by version and checksum, actions by SHA; API first (with its release command), then web, then a public smoke test. | No reference repo has a deploy workflow; this follows hotel-curator's own pinning. |
| D20 | `/` becomes the landing page; the Explorer moves to `/explore`. `/` redirects to `/explore` with the query string only when an Explorer filter is present. | Old shared links keep working; the target is a constant, so no open redirect. |
| D21 | Landing numbers come only from `/v1/meta`, with their intervals; `/v1/meta` gains `analyze_available`. | No hardcoded numbers; the analyze box knows when the model is missing. |
| D22 | `VITE_PUBLIC_ORIGIN` build argument (default `https://hotel-curator.fly.dev`). | Absolute OpenGraph and canonical URLs. |
| D23 | `NOTICE` gains a model section and is served at `/NOTICE.txt` from a synced copy, linked in the footer. | The web image's build context is `web/`. |
| D24 | `scripts/ops.py start|stop|status` and `scripts/deploy.py` (`plan`, `setup`, `deploy`, `status`; `make fly-*`) for first-time setup and routine deploys; both print what they will do first. | Python and shell run on the owner's machines. |
| D25 | Work on branch `deploy/fly-cloudflare`, one commit per step, then a pull request to `main`. | CI runs on pull requests. |

## 4. Steps 1 to 9

Every step ends with a commit (What / Why / Evidence). "Here" means the build environment, which cannot reach Fly or Cloudflare.

| Step | Builds | Verified here | Verified only after the owner deploys |
|---|---|---|---|
| 1 Weights | `REQUIRE_MODEL` check in the Dockerfile; `curator model verify-served`; CI verify step after the LFS pull, LFS cache; eval tests fail when the weights are tracked but missing; containers smoke calls analyze; the weights commit (LFS pointer); `docs/BLOCKERS.md` B8 | SHA-256 against `promotion.json`; unit tests for good, wrong and missing files; `pytest -m eval` with the weights | CI containers job: analyze 200; deployed `/api/readyz` shows the model |
| 2 ADR | `docs/decisions/011-deployment-fly-cloudflare.md`: apps, network path, client-IP and TLS chains, database, secrets, region, sizing, cost formula, the two edge modes, every unverified provider assumption with its live check, controls that apply on Fly versus compose only | review that each decision cites evidence | each assumption marked verified with a date |
| 3 Fly config | `fly.toml`, `web/fly.toml`, `deploy/postgres/*`; settings guards (D7, D8); `curator db release`; entrypoint runs given commands; graceful-shutdown timeout; secrets script; `scripts/deploy.py`; a CI job that starts the database image and runs the release, role and TLS tests (`tests/postgres`) against it; `tests/unit/test_fly_config.py` | local Postgres 16 with TLS and scram: wrong CA fails, two releases serialise, the runtime role cannot write, SQLite refused in production, URL normalised | release ran once; `flyctl ips list` shows no public address for API and database |
| 4 Networking | nginx template (upstream, edge mode, origin certificate and client-certificate paths), one trusted client-IP header, `X-Request-ID` forwarding, HSTS, `absolute_redirect off`; API keys on `X-Real-IP`; tests | vitest on the template; pytest: two clients get separate buckets, forged headers on a direct request are ignored | forged headers ignored live; logs show real client IPs |
| 5 Cloudflare | `docs/deploy/cloudflare.md` with exact setting names and values; `scripts/check_edge.py` | unit tests against recorded responses | owner applies settings, runs the checker; `.fly.dev` refused after cutover |
| 6 Landing | landing page, `/explore`, redirect, `analyze_available`, typed meta fields, analyze box, FAQ, footer, SEO and sharing assets, chunk-error reload | pytest and OpenAPI drift; eslint, tsc, vitest, build, client drift; Playwright with axe in light and dark at 360 and 768 px; Lighthouse on a local build | social cards on the real domain |
| 7 CI/CD | `deploy.yml`, `scripts/smoke.py`, image scan, workflow tests | workflow tests; smoke against a local stack | first deploy run and its smoke output |
| 8 Operations | metrics endpoint and `[metrics]`, JSON logs, request ids, optional Sentry, `scripts/ops.py`, `docs/RUNBOOK.md` | every log line parses as JSON; SIGTERM during a request; ops script against a fake flyctl | logs, metrics and alerts in Fly |
| 9 Docs | README Deploy section; production view in `docs/architecture/online.md` and its SVG; security review update; NOTICE model section and `/NOTICE.txt`; licence paragraph | link check; sync test; e2e footer link | `/NOTICE.txt` live |
| Final | red-team exercise (client-IP spoofing and rate-limit evasion; exposure of API and database; supply chain and secrets; web) and a structured security review of the branch diff; fixes; push; pull request | proofs of concept here and in CI | live-only checks listed for the owner |

## Owner actions, in order

| Phase | Action |
|---|---|
| Before the final push | Allow `lfs.github.com` in the cloud environment's network settings, so the weights can be pushed. |
| Fly, after merging | `flyctl auth login`; create the three apps in `lhr`; create the database volume; push secrets with `scripts/push_fly_secrets.py`; deploy database, API, web in that order with `scripts/deploy.py`; run `scripts/smoke.py https://hotel-curator.fly.dev`; confirm `flyctl ips list` shows public addresses for the web app only. |
| CI/CD | Create per-app deploy tokens; create the GitHub environment `production` with the owner as required reviewer and the tokens as secrets; run the deploy workflow once by hand. |
| Cloudflare cutover (when the domain is chosen) | Add the zone or hostname; create a Cloudflare Origin Certificate and push it as web-app secrets; turn on Authenticated Origin Pulls; apply `docs/deploy/cloudflare.md`; switch the web app to Cloudflare mode and redeploy; run `scripts/check_edge.py` and `scripts/smoke.py` against the domain; confirm `.fly.dev` is refused. |
| Before commercial use | Legal review of the data licence (`NOTICE`). |

---

## 5. Risks and how each is contained

| # | Risk | Containment |
|---|---|---|
| R1 | lfs.github.com stays blocked. The LFS pre-push hook (`.git/hooks/pre-push`) then fails the whole push. | Keep the weights commit separate and last, and push without it if needed. `REQUIRE_MODEL=1` in `fly.toml` makes any weightless Fly build fail. `analyze_available` hides the demo. Fallback: a release asset or object storage, which would need a new owner decision. |
| R2 | GitHub LFS bandwidth or storage quota (not checked here). Two CI jobs pull 126.5 MB on every run (`ci.yml:31-32,141-142`), and the deploy pulls it again. | Cache LFS objects in `actions/cache`, keyed by the pointer's object id, and pull only in jobs that need the file. |
| R3 | Fly or Cloudflare behaves differently from what this plan assumes. | Every assumption is listed in ADR 011 with a post-deploy check. Designs fail safe: a wrong trust setting gives a stricter, unspoofable shared bucket, and the origin lock is off by default. |
| R4 | Spoofed client IPs. | One trusted header, set by nginx: `Fly-Client-IP` in Fly mode, `CF-Connecting-IP` only after Cloudflare's origin-pull client certificate is verified in Cloudflare mode; nginx overwrites `X-Real-IP`; forged-header tests (D10, D11). |
| R5 | All users share one rate-limit bucket and get 429s. | The CI 121-request test; post-deploy log check for distinct client IPs; the Cloudflare WAF rule as the first layer. |
| R6 | Migrations or the seed race. | `release_command` plus an advisory lock; one API machine (`--ha=false`); migrate-on-start off on Fly. |
| R7 | Silent SQLite in production. | The production guard is set in the image and the start fails loudly; a unit test covers it. |
| R8 | The API or database gets a public address. | No public service in their tomls (asserted by `test_fly_config`); `ips list` in the runbook and smoke checklist. |
| R9 | Secrets leak. | `.env*` excluded from images and git (`.dockerignore:27-28`, `.gitignore:19-20`); `SecretStr`; stdin-only secret pushes; gitleaks; per-app deploy tokens in a protected environment. |
| R10 | Cloudflare features silently break the CSP. | The checklist turns them off; `check_edge.py` compares the CSP and scripts against the repo; the e2e CSP fixture (`web/e2e/fixtures.ts:7-23`). |
| R11 | Old shared links break. | The loader redirect with unit and e2e tests. |
| R12 | Cost overruns. | Smallest provisional sizes; `ops.py stop`; the Q2 ceiling. Prices could not be checked here. |
| R13 | The API runs out of memory. | Measured RSS before sizing; swap; Fly restarts; optional Sentry. |
| R14 | Slow cold starts or a failing first check. | API kept warm; grace period from measurement. |
| R15 | Licence or legal exposure. | Q3; NOTICE served and linked; the correction channel; non-commercial framing. |
| R16 | The deploy pipeline is abused. | `workflow_run` limited to this repo and main; required reviewer; SHA pins; minimal permissions; no `pull_request_target`. |
| R17 | A bad release reaches production. | API deploys before web; the smoke test gates the deploy; rollback documented in the runbook (unverified here); only one migration so far (`0001`). |
| R18 | HSTS locks in a mistake. | `max-age=86400`, no includeSubDomains or preload until verified. |
| R19 | App names are already taken, or the region is unavailable. | Names live only in the tomls and workflow variables; the owner confirms at phase 4; the ADR lists alternatives. |
| R20 | nginx's envsubst substitutes something unintended. | Only `CURATOR_`-prefixed template variables; `nginx -t` and a rendered-config diff in CI. |
| R21 | Stale browser tabs hit removed chunks after a deploy. | The RouteError reload button; Cloudflare respects the existing immutable and no-cache headers (`web/nginx.conf:7-10`). |