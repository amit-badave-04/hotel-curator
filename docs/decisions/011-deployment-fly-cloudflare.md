# ADR 011: Deployment on Fly.io behind Cloudflare

- Status: accepted (design). Provider behaviour marked **(unverified here)**
  is checked at the first deploy; the table at the end lists each check.
- Date: 2026-10-28
- Inputs: [the deployment gap audit](../deploy/gap-audit.md); the owner's
  decisions (launch on `hotel-curator.fly.dev`, region `lhr`, Postgres in
  its own private app, non-commercial demo).

## Context

`docker compose up` runs three containers: nginx (web), the API and
Postgres. Postgres has no network at all and is reached through a Unix
socket that only the API mounts; ports are bound to `127.0.0.1`; the API's
root filesystem is read-only; the API's database role is not a superuser
([online.md](../architecture/online.md),
[security review](../security/v2-security-review.md)). A public deployment
must keep those properties where the platform allows it, say plainly where
it does not, and add what a public site needs: TLS, a real client IP for the
rate limits, an origin that cannot be reached around the edge, a release
step, health checks and secrets.

The owner's other projects deploy to Fly.io with one `fly.toml` per app,
manual `flyctl deploy`, `force_https`, health checks every 30 s and
always-warm machines where start-up is slow (graphrag). None of them has a
deploy workflow, a release command or Cloudflare in front, so those parts
are designed here.

## Decision

### Apps and network path

```
Browser
  -> (after cutover) Cloudflare edge: TLS, WAF, cache
  -> Fly edge, region lhr
  -> hotel-curator (nginx, the only public app)
       /api/* over Fly's private network to
  -> hotel-curator-api.flycast:8000 (FastAPI + ONNX model; no public address)
       SQL over TCP + TLS on the private network to
  -> hotel-curator-db.internal:5432 (Postgres 16; no public address, volume)
```

| App | Image | Public | Port | Machines | Size | Volume |
|---|---|---|---|---|---|---|
| `hotel-curator` | `web/Dockerfile` (nginx-unprivileged, uid 101) | yes | 8080 (Fly mode), 8443 (Cloudflare mode) | 1 | `shared-cpu-1x`, 256 MB | none |
| `hotel-curator-api` | `Dockerfile` (uid 10001, weights baked in) | no (Flycast only) | 8000 | 1 (`--ha=false`) | `shared-cpu-1x`, 1 GB | none |
| `hotel-curator-db` | `deploy/postgres/Dockerfile` (postgres:16-alpine, digest-pinned) | no (`.internal` only) | 5432 | 1 | `shared-cpu-1x`, 512 MB | `pg_data`, 1 GB |

- The browser sees one origin, so the frontend needs no CORS and the CSP
  keeps `default-src 'self'`.
- The web app reaches the API at a Flycast address: stable, routed by
  Fly's proxy, never public (unverified here). nginx resolves it once at
  start, which is safe because the address does not change.
- The API reaches Postgres at `hotel-curator-db.internal`, the machine's
  private address. Postgres has no `[[services]]` block at all, as in
  graphrag's private Neo4j app.

### Two edge modes for the web app

| | Fly mode (launch on `hotel-curator.fly.dev`) | Cloudflare mode (after DNS cutover) |
|---|---|---|
| Who terminates browser TLS | Fly's edge (`[http_service]`, `force_https`) | Cloudflare's edge |
| Fly service | HTTP service on 80/443 to nginx's plain port 8080 | TCP service on 443 with `handlers = ["proxy_proto"]`, passed through to nginx's TLS port 8443, on a dedicated IPv4 (unverified here; A7, A14) |
| Origin TLS | none past Fly's edge | Cloudflare Origin Certificate in nginx; SSL mode Full (strict) |
| Origin lock | none (the fly.dev hostname is the site) | nginx refuses the TLS handshake for any other name, answers 400 without Cloudflare's origin-pull client certificate (Authenticated Origin Pulls), 403 when the PROXY source is outside Cloudflare's committed ranges, and 421 for any other `Host` |
| Client IP for rate limits | `Fly-Client-IP`, set by Fly's edge from the connection | `CF-Connecting-IP`, trusted only from a PROXY source in Cloudflare's ranges after the client certificate verified |
| Health checks | `GET /` on the HTTP service's own port 8080 | `GET /healthz` on port 8080, which no service exposes and which answers nothing else |

nginx can only verify Cloudflare's client certificate if it terminates TLS
itself; behind Fly's TLS it never sees one. That is why Cloudflare mode
switches the Fly service to TCP passthrough.

Zone-level origin pulls with Cloudflare's shared certificate prove "came
through Cloudflare", not "came through this zone": another Cloudflare
customer could point a zone at the origin. The strict `Host` check narrows
that. Per-hostname origin pulls with the owner's own client certificate
close it, where the Cloudflare plan offers them (unverified here).

### Client-IP trust chain

In every mode nginx computes the client address once and **overwrites**
`X-Real-IP` with it. It blanks every other identity header it would
otherwise pass on (`X-Forwarded-For`, `Forwarded`, `Fly-Client-IP`,
`CF-Connecting-IP`, `True-Client-IP`). The API keys its rate limits on
`X-Real-IP` only (IPv6 clients per /64), and only from peers in
`CURATOR_FORWARDED_ALLOW_IPS`. The API has no public address, so only
nginx and the other apps of the owner's Fly organisation can reach it. On
Fly that setting is `*`: Fly's proxy sits between nginx and the API on the
Flycast hop, so the API's TCP peer is never nginx's own address, and an app
in the same organisation could choose its own rate-limit bucket (keep the
organisation to these three apps, or give them one of their own).

nginx's site is a template rendered at start by
`web/nginx/15-curator-site.sh` for `CURATOR_EDGE` (`local`, `fly` or
`cloudflare`). A missing or malformed setting stops the container. nginx
also sends its `$request_id` as `X-Request-ID`, and the API adopts it from
a trusted peer, so one id follows a request through both logs.
`tests/edge/test_nginx.py` runs real nginx in all three modes.

| Mode | Source of the client address | Why it cannot be forged |
|---|---|---|
| compose | the TCP peer (`$remote_addr`) | the API is not reachable from outside the compose network except on loopback |
| Fly | `Fly-Client-IP` | Fly's edge sets it from the connection; a client-sent value is replaced (unverified here) |
| Cloudflare | `CF-Connecting-IP` | Cloudflare sets it; nginx believes it only from a PROXY source in Cloudflare's ranges, after Cloudflare's client certificate verified |

### TLS chain

- **Fly mode.** Browser to Fly's edge: TLS with Fly's certificate for
  `fly.dev`. Fly's edge to nginx: plain HTTP inside Fly.
- **Cloudflare mode.** Browser to Cloudflare: TLS with Cloudflare's edge
  certificate. Cloudflare to nginx: TLS end to end through Fly's TCP
  passthrough, with the Origin Certificate on the server side and
  Cloudflare's client certificate on the other.
- **Inside Fly.** nginx to the API: plain HTTP on the private network, which
  Fly carries over WireGuard between hosts (unverified here). The API to
  Postgres: TLS required (`sslmode=require`; `verify-full` once the
  server's CA is supplied).

### Database

| | Compose | Fly |
|---|---|---|
| Transport | Unix socket in a shared volume; Postgres has no network | TCP on the private network; `hostssl` lines only; no public address |
| Authentication | `trust` on the socket for the app role; peer for the superuser | scram-sha-256 passwords (Fly secrets); the superuser only from inside the database machine |
| Roles | one non-superuser role that owns the database | `curator_owner` (owns the schema, NOSUPERUSER, used by the release command) and `curator_app` (read-only: CONNECT, USAGE, SELECT) for serving |
| Data | rebuilt from the committed results | the same; restore means redeploy (the release command reseeds); Fly's daily volume snapshots as a second line (unverified here) |

**Lost:** the socket-only control. On compose, Postgres has no network
interface an attacker could reach.

**What replaces it:**
- no public IP and no Fly service on the database;
- `hostssl` plus scram on every network connection;
- a serving role that cannot write;
- an owner role that is not a superuser;
- a connect timeout and a bounded pool on the client side.

### Release and start-up

- Fly's `[deploy] release_command` runs `curator db release`, which
  migrates and seeds under a Postgres advisory lock. It is idempotent, so
  running it on every deploy is safe, and two releases cannot interleave.
  It runs on a temporary machine with the API image, its secrets and its
  network (unverified here).
- On Fly the API does not migrate or seed at start. It loads the policy and
  the model, then serves. Compose keeps migrate-and-seed on start.
- `/readyz` reports the model name once the model is loaded.
  `POST /v1/analyze` answers 503 without it, and `/v1/meta` says whether
  analyze is available.

### Deploy pipeline

- `ci.yml` ends with a `deploy` job that runs only for a push to `main`
  and only after every other job passed. It calls the reusable
  `.github/workflows/deploy.yml`, which has no other trigger: every deploy
  is of a commit that passed CI, in that commit's own run, and nothing
  from a fork or a pull request can start it. Re-running that job deploys
  again.
- The job runs in the GitHub environment `production`. Its required
  reviewer (the owner) approves each run, and only it holds the two
  app-scoped deploy tokens, `FLY_API_TOKEN_API` and `FLY_API_TOKEN_WEB`,
  each passed only to the steps for its app.
- flyctl runs from its official image pinned by tag and digest in
  `deploy/tools/flyctl/Dockerfile` (`scripts/flyctl_docker.sh`); the first
  step checks it reports the pinned version. No binary is downloaded.
- Order: a dry run of both deploys (missing weights or, when no mode
  variable is set, a parked web app or a web deploy that would quietly
  leave Cloudflare mode or a recorded domain, refuse here, before
  anything moves; a mode variable is applied as given), the API
  (its release command migrates and seeds), the web app, then
  `scripts/smoke.py` against the public address. The database app is
  deployed by hand.
- On `main`, CI runs once per commit and is never cancelled, so a deploy
  waiting for approval holds up no later tests. Deploys run one at a
  time: a running one is never cancelled, a newer one replaces one still
  waiting, and a job whose commit is no longer `main`'s newest deploys
  nothing.
- CI scans both images with Trivy (pinned by digest in
  `deploy/tools/trivy/Dockerfile`): a CRITICAL vulnerability with a fix
  fails the build. Dependabot proposes weekly updates for actions, the
  Python lock file, npm, the base images and the two tool images, each
  release at least two weeks old; the CLI tools CI runs at pinned
  versions through uvx or curl are updated by hand.

### Observability

- **Logs.** The API writes one JSON object per line and nothing else:
  `curator serve` starts uvicorn without its own logging config, so
  uvicorn's lines go through the app's formatter, and without uvicorn's
  access log, because the app logs one line per request (request id,
  method, path, status, time). nginx logs one JSON object per request:
  the path asked for without its query string, the client address the
  rate limits use, its own request id and the API's, and, beside them,
  Fly's `Fly-Request-Id` (Fly mode, assumption A19) and Cloudflare's
  `CF-Ray` when they look like ids. nginx's id stays the one that joins both logs: it exists in every
  mode, while a client can send either provider header in some modes (gap
  audit D16 had proposed adopting `Fly-Request-Id`). nginx's error log
  keeps only critical errors, since its messages quote the request line,
  query string included. Logs never hold
  submitted text, query strings or local variables, and the database
  engines hide bound values from error messages, so a failed search does
  not log its terms. The release command's lines are JSON too.
- **Metrics.** The API serves Prometheus metrics on port 9091, a listener
  of its own bound to `::` (Fly's scraper comes over the private network),
  which no Fly service exposes; `[metrics]` in `fly.toml` has Fly scrape
  it. Labels are bounded and carry no request data: the method (from a
  fixed set), the matched route's template and the status. Counts live in
  the process, so a metrics port needs one worker (`curator serve`
  refuses more). A port that cannot be bound is logged as an error and
  the API serves on; the missing series trip an alert. Alert rules (no metrics, readiness
  failing, 5xx rate, analyze p95, no model, memory) are written out in
  the runbook for the owner to set in Fly's Grafana (assumption A20).
- **Error reports.** Sentry, off unless the API has `CURATOR_SENTRY_DSN`
  (the `sentry` extra is in the image). Only the request middleware's
  unexpected 500s are reported, with the request id as a tag: no
  integrations of any kind (framework, logging, thread or exit hooks), no
  breadcrumbs, no local variables, and a
  scrubber that drops any request, user or extra data from every event.
  The lifespan flushes queued reports at shutdown, since uvicorn exits by
  re-raising the signal and Sentry's exit handler never runs.
- **Shutdown.** Fly sends SIGTERM; uvicorn stops accepting connections,
  lets requests in flight finish for up to 20 s (`kill_timeout` is 30),
  runs the app's shutdown, then exits by the signal it caught.

### Sizing, idle policy and cost

- **API.** Measured on the build machine with the int8 model warm, after 30
  analyze requests: peak RSS 451 MiB. With 30% headroom that is 586 MiB, so
  1 GB is the smallest fitting size.
- **Machine counts.**
  - One API machine (`--ha=false`), because the rate limiter is in memory.
  - `min_machines_running = 1` on API and web, with auto-stop off, because
    a cold start would load the model on a visitor's first request.
  - The database runs one machine.
- **Parking.** `scripts/ops.py stop` scales the web app and the API to
  zero machines and stops the database machine; `start` reverses it in
  dependency order and checks that the web machine kept its deploy's
  settings. Only the web machines record the edge mode, so `stop` first
  prints the flags that deploy the web app in that mode, and while it has
  no machines `scripts/deploy.py` refuses a web deploy that does not
  name one. Stopping the
  web and API machines instead (graphrag's pattern) would not hold: both
  start on demand, so the first visit would wake them. flyctl runs from a
  directory without a `fly.toml`, so it uses each app's deployed config.
- **Cost.** Monthly cost ≈ Σ over machines (price of its size × hours
  running) + volume GB × volume price + outbound bandwidth. Prices are on
  Fly's pricing page; they could not be checked from the build environment.

### Secrets

| App | Secret | Used by |
|---|---|---|
| `hotel-curator-api` | `CURATOR_DATABASE_URL` (`curator_app`) | serving |
| `hotel-curator-api` | `CURATOR_MIGRATION_DATABASE_URL` (`curator_owner`) | the release command only. Fly puts every secret into every machine of the app, so the API's entrypoint unsets it (in any spelling) before `curator serve` starts. That keeps it out of the server's environment but is not a hard boundary: Fly's in-machine API and an `ssh console` session still reach it. A separate release app holding only this secret would be one. |
| `hotel-curator-api` | `CURATOR_SENTRY_DSN` (optional) | error reports |
| `hotel-curator-db` | `POSTGRES_PASSWORD`, `CURATOR_OWNER_PASSWORD`, `CURATOR_APP_PASSWORD` | first initialisation of the volume |
| `hotel-curator` | `CURATOR_ORIGIN_CERT_B64`, `CURATOR_ORIGIN_KEY_B64`, `CURATOR_ORIGIN_PULL_CA_B64` | Cloudflare mode only |
| GitHub environment `production` | per-app deploy tokens | the deploy workflow |

Secrets are pushed with `scripts/push_fly_secrets.py` from a git-ignored
`.env.fly`, values on stdin. Nothing secret goes into `fly.toml`, images or
git.

### Region

Everything runs in `lhr`: the six cities served are European, and the
owner's refit project already runs there.

**A second region would need:**
- a shared rate-limit store (for example Redis), because the counters are
  in memory;
- a Postgres replica, or SQLite-in-image for this read-only data;
- web machines added freely, since nginx is stateless.

## Alternatives considered

- **One app running nginx and uvicorn together.** Fewer machines and lower
  cost, but two processes with different users in one image, one size for
  both, and no way to keep the API off the public internet.
- **Read-only SQLite inside the API image.** The served data never changes
  between deploys, so this would remove the database app. Rejected because
  ADR 008 commits to Postgres; it remains the cheapest fallback.
- **Fly Managed Postgres.** Backups, upgrades and failover managed for us,
  at a higher monthly cost. The owner chose a private app.
- **`.internal` for the API.** Flycast gives a stable address through Fly's
  proxy.
- **Cloudflare Tunnel.** Would hide the origin entirely, but adds a
  `cloudflared` process; Authenticated Origin Pulls give the same lock with
  nginx alone.
- **Fly TLS plus an allow-list of Cloudflare's IP ranges.** Header- and
  list-based, needs list upkeep, and does not prove the request came
  through Cloudflare.
- **A deploy workflow triggered by `workflow_run` after CI** (the gap
  audit's first plan, D19). It runs with secrets in the default branch's
  context for any completed CI run, including runs for forks, so every
  condition guarding it has to be right. Calling the deploy from CI's own
  run on a push to `main` needs none of them.
- **A manual "Run workflow" trigger for deploys.** It would deploy
  `main`'s head whether or not CI passed for it; re-running the `deploy`
  job of the commit's CI run gives the same control with the gate kept.
- **Metrics on the API's own port, blocked in nginx.** One listener
  fewer, but a single missed `location` rule would publish them; a port no
  service exposes cannot leak through nginx.
- **Sentry's FastAPI and logging integrations.** Less code, but they
  attach request data and log lines to events by default, and the app
  already answers every unexpected error in one place, which can report
  it with nothing but the exception and the request id.
- **flyctl downloaded as a release archive and checked against a pinned
  SHA-256.** Equivalent, but the archive's checksum could not be read from
  the build environment; the image digest could.

## Unverified provider assumptions and how each is checked

| # | Assumption | Check at first deploy |
|---|---|---|
| A1 | Fly's edge sets `Fly-Client-IP` from the connection and replaces a client-sent value | send `Fly-Client-IP: 203.0.113.9` to `https://hotel-curator.fly.dev/api/v1/hotels`; nginx's log shows the real address |
| A2 | `hotel-curator-api.flycast:8000` reaches the API after a private IPv6 is allocated | `scripts/smoke.py` gets analyze 200 through the web app |
| A3 | An app with only a private address and `[[services]]` gets no public IP | `flyctl ips list -a hotel-curator-api` and `-a hotel-curator-db` list no public address |
| A4 | `release_command` runs once per deploy on a temporary machine with the app's secrets and the private network, so it reaches `hotel-curator-db.internal:5432` | `flyctl releases -a hotel-curator-api` and the release log line "up to date" or "loaded 1494 hotels from …" |
| A5 | `kill_signal` and `kill_timeout` are honoured | the deploy log shows "Application shutdown complete" before the machine stops |
| A6 | Top-level `[checks]` may target a port without a public service | `flyctl checks list` shows the checks passing |
| A7 | A TCP service with `handlers = ["proxy_proto"]` passes TLS through with a PROXY header | after cutover, nginx's log shows a Cloudflare address as the proxy-protocol peer and the visitor's address as the client |
| A8 | Cloudflare sets `CF-Connecting-IP` and presents its client certificate on every origin request | `scripts/check_edge.py` passes; a direct TLS connection without the certificate is refused |
| A9 | Fly snapshots volumes daily | `flyctl volumes snapshots list` |
| A10 | `[metrics]` scrapes a port without a public service | metrics appear in Fly's Grafana |
| A11 | Rocket Loader, Email Obfuscation, Web Analytics injection and Zaraz are off | `scripts/check_edge.py` finds the CSP unchanged and no injected script |
| A12 | Traffic between Fly hosts on the private network is encrypted | Fly's documentation at deploy time; recorded here |
| A13 | Fly's proxy passes `X-Real-IP` unchanged on the Flycast hop from nginx to the API | two clients through the web app get separate 429s from `/api/v1/analyze`; the API's log shows the visitors' request ids |
| A14 | Fly routes the Cloudflare-mode TCP passthrough service only on a dedicated IPv4, not a shared one | `scripts/deploy.py setup --edge cloudflare` allocates one before the cutover; `scripts/check_edge.py <domain>` probes every address the origin name resolves to, IPv4 and IPv6, and each must refuse as expected (an address that cannot route the service fails). Anyone can open connections to that address; nginx closes one that sends nothing after 10 s (`nc <address> 443` ends by itself), and Fly's connection limit (250) is shared with Cloudflare's own (security review 7.5) |
| A15 | The `flyio/flyctl` image's entrypoint is flyctl, so `docker run <image> <args>` runs flyctl | the deploy workflow's first step: `flyctl version` must report the pinned version, before anything is deployed |
| A16 | An app-scoped deploy token can deploy with Fly's remote builder and list that app's machines (and its releases, when it has no machines) | the first deploy run: its check step lists the web machines with the web token before anything is deployed |
| A17 | `flyctl scale count 1` on an app with no machines creates one from the latest release, with the settings its deploy passed as `--env` | `make fly-stop`, then `make fly-start` (which checks that the web machine has the setting its mode's deploy passes), then `make smoke` |
| A18 | Fly's scraper reaches a listener bound to `::` over the private network and labels each series with its `app` | `curator_http_requests_total{app="hotel-curator-api"}` returns series in Fly's Grafana |
| A19 | In Fly mode, Fly's proxy passes the request to nginx with a `Fly-Request-Id` header holding the id it returns to the client as `fly-request-id` (in Cloudflare mode Fly only passes TCP through, so there is none) | `curl -sI https://hotel-curator.fly.dev/`, then find its `fly-request-id` in `flyctl logs --app hotel-curator` |
| A20 | Fly's managed Grafana can hold alert rules and send them to an email contact point | create the runbook's "No metrics" rule and send a test notification; if it cannot, point another Grafana or an outside uptime monitor at the site |
| A21 | A job in a reusable workflow that names an environment gets that environment's secrets without the caller passing any | the first deploy run: its check step authenticates with the web token; without it the run stops there, before anything is deployed |
| A22 | The first `flyctl deploy` of the web app in Fly mode gives it public addresses for `hotel-curator.fly.dev` (no code allocates them) | `flyctl ips list -a hotel-curator`, and the smoke test reaching the site |

## Controls: compose versus Fly

| Control | Compose | Fly |
|---|---|---|
| Non-root users | API uid 10001, web uid 101 | same images |
| API root filesystem read-only | `read_only: true`, tmpfs `/tmp` | not available as a `fly.toml` setting (unverified here). Nothing in the API writes outside `/tmp` in production, because SQLite is refused there. Recorded as an accepted risk |
| `no-new-privileges` | on all three | not a Fly setting; each machine is its own microVM |
| Database unreachable | no network at all | private network only, TLS and scram, read-only serving role |
| Ports | bound to `127.0.0.1` | only the web app has public addresses |
| Rate limits | per client, but one shared bucket for host clients (accepted risk) | per real client through one trusted header |
| CSP and security headers | nginx | nginx, unchanged, plus HSTS |
| Pinned images and actions | digests and SHAs | the same, plus flyctl's image pinned by digest and its version checked |

## Consequences

- Two web edge modes, switched by a config change at the Cloudflare
  cutover, both covered by tests.
- One API machine: a deploy or crash means seconds of errors on `/api`
  (502 to 504, depending on where the request stops). That is acceptable
  for a demo and documented in the runbook.
- A standing monthly cost, controlled by `scripts/ops.py stop` when the
  demo is not needed.
