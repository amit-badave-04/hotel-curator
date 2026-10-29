# Runbook: hotel-curator on Fly.io

How to run the live site day to day: check it, park and resume it, read
its logs and metrics, set up alerts and error reports, and handle what
goes wrong. First-time setup and deploys are in
[docs/deploy/fly.md](deploy/fly.md); the Cloudflare cutover is in
[docs/deploy/cloudflare.md](deploy/cloudflare.md); the design and its
unverified provider assumptions (A1 to A20) are in
[ADR 011](decisions/011-deployment-fly-cloudflare.md).

Commands run from the repository root, with `flyctl` logged in
(`flyctl auth login`) or `FLY_API_TOKEN` set. Nothing here has been run
against Fly from the build environment, which cannot reach fly.io; where a
step depends on Fly behaving as documented, the assumption is named.

## At a glance

| App | Reachable from | Port | Machines |
|---|---|---|---|
| `hotel-curator` (nginx, the site) | the internet: `https://hotel-curator.fly.dev`, later the domain | 8080 (8443 in Cloudflare mode) | one, `shared-cpu-1x`, 256 MB |
| `hotel-curator-api` (FastAPI and the model) | the private network only: `hotel-curator-api.flycast:8000` | 8000; metrics on 9091 | one, `shared-cpu-1x`, 1 GB |
| `hotel-curator-db` (Postgres 16) | the private network only: `hotel-curator-db.internal:5432` | 5432 | one, `shared-cpu-1x`, 512 MB, 1 GB volume |

Everything runs in `lhr`. The database holds only data the release command
loads from the repository (`data/demo/`), never anything a visitor sends:
analyze requests are answered and forgotten.

## Is it up?

```sh
make fly-status                                       # machines, and no public address on the API or database
make smoke SMOKE_URL=https://hotel-curator.fly.dev    # 9 checks from outside, as the deploy workflow runs them
flyctl checks list --app hotel-curator-api            # Fly's liveness and readiness checks
```

`make fly-status` exits non-zero if the API or the database has a public
address (`flyctl ips release` removes one). `scripts/smoke.py` waits up to
three minutes for readiness, then prints PASS or FAIL per check.

## Parking and resuming

```sh
make fly-stop     # web and API scaled to zero, database machine stopped
make fly-start    # database started, API then web scaled back to one; then:
make smoke SMOKE_URL=https://hotel-curator.fly.dev
```

Both print every flyctl command that changes something before running
it; `uv run python scripts/ops.py stop --dry-run` prints those without
running them, but still reads the machine listings from Fly, so flyctl
must be logged in. While parked, the site does not answer (expect an error from Fly's
proxy), and the cost is the database volume, the stored images and, in
Cloudflare mode, the dedicated IPv4.

The web app and the API are scaled to zero rather than stopped, because
both start on demand: a stopped machine would start again on the first
visit (or crawler). Only the web machines record the edge mode the site
runs in, so `make fly-stop` first prints the line that deploys the web app
in that mode, for example
`scripts/deploy.py deploy web --edge cloudflare --domain example.org`.
Keep it: while the web app has no machines, `scripts/deploy.py` (and so
the deploy workflow) refuses a web deploy that does not name the mode.

`make fly-start` restores each app from its latest release (assumption
A17), then checks that the web machine kept the settings its deploy
passed. If it stops there, or if `scale count` cannot create a machine,
redeploy instead, with the line `make fly-stop` printed:
`scripts/deploy.py deploy api`, then the web line. A deploy builds from
the repository, so run it from a checkout of the commit that was live.

## Deploys and rollback

Routine deploys go through GitHub: merge to `main`, CI passes, approve the
`production` deployment, and the workflow deploys the API, then the web
app, then runs the smoke test ([fly.md, "Deploying from
GitHub"](deploy/fly.md#deploying-from-github)). By hand:
`make fly-deploy`. Rolling back the API or the web app:
[fly.md, "Deploying again"](deploy/fly.md#deploying-again).

If the smoke test fails after a deploy, the new version is already live.
Read which check failed, find its request in the logs (below), then fix
forward or roll back.

## Logs

```sh
flyctl logs --app hotel-curator-api    # the API: one JSON object per line
flyctl logs --app hotel-curator        # nginx: one JSON object per request
flyctl logs --app hotel-curator-db     # Postgres
```

Fly prefixes each line with its own timestamp and machine; the JSON
follows. API lines carry `timestamp`, `level`, `logger` and `event`; a
request line (`"event": "request"`) adds `request_id`, `method`, `path`,
`status` and `ms`. uvicorn's own lines (start, shutdown), the release
command's ("up to date", "loaded 1494 hotels from …") and the metrics
listener's are JSON too; there is no second access log. nginx's
lines carry `time`, `client`, `request_id`, `api_request_id`,
`fly_request_id`, `cf_ray`, `method`, `path`, `status`, `bytes`, `ms` and
`upstream_ms`.

Following one request: a visitor can quote the `x-request-id` response
header (API errors also carry it in their body), or Fly's
`fly-request-id` (Fly mode only, assumption A19), or Cloudflare's
`cf-ray` (Cloudflare mode). Find that value in nginx's
log; its `request_id` is the id the API logged the same request under
(nginx sends it as `X-Request-ID` and the API adopts it).

```sh
flyctl logs --app hotel-curator --no-tail | grep '"cf_ray":"8c2f1a2b3c4d5e6f-LHR"'
flyctl logs --app hotel-curator-api --no-tail | grep '<request_id>'
```

What the logs never hold: submitted review text, query strings (search
terms), request bodies, or local variables in tracebacks. A database
error names its SQL but not the values bound into it (the engines hide
them), so a failed search does not log its terms. nginx's `client`
field is the visitor's address, kept because the rate limits key on it;
the API never logs it. Fly's log view keeps recent lines only, and nothing
here ships them anywhere else.

## Metrics

The API serves Prometheus metrics on port 9091 (`CURATOR_METRICS_PORT`),
a listener of its own that no Fly service exposes, so nothing outside the
private network reaches it and nginx never proxies it. Fly scrapes it
through `[metrics]` in `fly.toml` (assumption A10) and shows it in its
managed Grafana: the Fly dashboard, the app, Metrics.

| Metric | Labels | Meaning |
|---|---|---|
| `curator_http_requests_total` | `method`, `route`, `status` | requests answered |
| `curator_http_request_duration_seconds` | `method`, `route` | time to answer (histogram) |
| `curator_model_loaded` | | 1 when `/v1/analyze` has its model, else 0 |
| `process_resident_memory_bytes` and other `process_*` | | the API process |

`route` is the route's template (`/v1/hotels/{hotel_id}`), or `unmatched`
for a request answered before routing (a 404, a 429 from the rate limits,
an oversized body). `method` is GET, HEAD, POST or OPTIONS, else `other`.
No label holds request data. Metrics need the API to run one worker, as
it does on Fly: `curator serve --workers 2` with a metrics port is
refused, because each worker would count only its own requests.

To read the metrics on the machine itself:

```sh
flyctl ssh console --app hotel-curator-api -C \
  "python -c \"import urllib.request; print(urllib.request.urlopen('http://[::1]:9091/metrics').read().decode())\""
```

The web app exports no metrics of its own; Fly's checks, the smoke test
and the API's metrics cover it. An outside uptime monitor (any service
that polls `https://hotel-curator.fly.dev/api/readyz`) is optional and
would also notice the site being unreachable as a whole.

## Alerts

Set these up in Fly's Grafana (Alerting, Alert rules), with your email as
the contact point (assumption A20; if it offers no alerting, point another
Grafana or an outside uptime monitor at the site). The selectors assume
Fly labels each series with its app (assumption A18); adjust them if
Grafana shows other labels.

| Alert | PromQL | Fires when |
|---|---|---|
| No metrics | `absent_over_time(curator_http_requests_total{app="hotel-curator-api"}[10m])` | the API is down, parked, or its metrics listener did not start (it logs an error and serves on) |
| Not ready | `sum(increase(curator_http_requests_total{app="hotel-curator-api",route="/readyz",status!="200"}[10m])) > 0` | a readiness check failed: database unreachable or no hotel data |
| Errors | `sum(rate(curator_http_requests_total{app="hotel-curator-api",route!="/readyz",status=~"5.."}[10m])) / sum(rate(curator_http_requests_total{app="hotel-curator-api",route!="/readyz"}[10m])) > 0.05` | over 5% of answers are server errors |
| Slow analyze | `histogram_quantile(0.95, sum by (le) (rate(curator_http_request_duration_seconds_bucket{app="hotel-curator-api",route="/v1/analyze"}[10m]))) > 2` | analyze p95 above 2 s |
| No model | `max(curator_model_loaded{app="hotel-curator-api"}) < 1` | `/v1/analyze` answers 503 |
| Memory | `max(process_resident_memory_bytes{app="hotel-curator-api"}) > 800e6` | the API nears its 1 GB |

The thresholds are starting points. Analyze p95 measured in the build
environment was 142 ms (p50 89 ms) on 4 CPU threads; a `shared-cpu-1x`
machine has one shared vCPU, so expect it several times slower. After the
first week, set the latency threshold to about twice the p95 Grafana
shows. Peak memory measured with the model warm was 451 MiB.

## Error reports (optional)

Off unless the API has a `CURATOR_SENTRY_DSN` secret. A report is sent
only for an unexpected error (a 500 the API did not plan for) and carries
the exception, its stack without local variables, and a `request_id` tag
that finds the matching log lines. It never carries the request: no body,
query string, header, cookie or client address, and no breadcrumbs; a
database error's bound values are hidden, as in the logs. Reports still
queued at shutdown are sent before the process exits (up to 2 s).

To turn reports on, create a Python project in Sentry, then:

```sh
echo 'CURATOR_SENTRY_DSN=https://<key>@<host>/<project>' >> .env.fly
uv run python scripts/push_fly_secrets.py push --app hotel-curator-api
uv run python scripts/deploy.py deploy api    # staged secrets apply on deploy
```

To turn them off: `flyctl secrets unset CURATOR_SENTRY_DSN --app
hotel-curator-api` (this restarts the API) and remove the line from
`.env.fly`.

## Secrets and their expiry

Which app holds which secret: [ADR 011, "Secrets"](decisions/011-deployment-fly-cloudflare.md#secrets).

- **Database passwords**: [fly.md, "Changing a database
  password"](deploy/fly.md#changing-a-database-password).
- **Deploy tokens** (GitHub environment `production`): valid for 90 days.
  A deploy step failing with "unauthorized" means one expired; create and
  store new ones as in [fly.md](deploy/fly.md#deploying-from-github),
  then revoke the old ones (`flyctl tokens list --app <app>`, `flyctl
  tokens revoke <id>`).
- **Origin certificate** (Cloudflare mode): [cloudflare.md](deploy/cloudflare.md).
- **Sentry DSN**: above. A DSN only lets someone send events to the
  project; if one leaks, rotate its key in Sentry.

## The database

Losing the database loses nothing that cannot be rebuilt: every row comes
from `data/demo/` in the repository. To rebuild, create a new volume
(`scripts/deploy.py setup` creates `pg_data` if it is missing), redeploy
the database, then the API, whose release command migrates and loads the
data again. Fly also keeps daily volume snapshots (assumption A9;
`flyctl volumes snapshots list --app hotel-curator-db`).

## When something is wrong

| Symptom | Look at | Likely cause and fix |
|---|---|---|
| The site does not load | `make fly-status` | parked or crashed web machine: `make fly-start`, or the web app's logs |
| Pages load, data does not (502 or 504 on `/api`) | the API's logs and checks | the API is down or restarting: one machine, so a deploy or crash means seconds of errors; a crash loop shows in the logs |
| `/api/readyz` says "database unavailable" | `flyctl machine list --app hotel-curator-db`, the database's logs | database stopped or starting: `flyctl machine start <id> --app hotel-curator-db` |
| `/api/readyz` says "no hotel data loaded" | `flyctl releases --app hotel-curator-api` | the release command failed: its log line says why; redeploy the API |
| Analyze answers 503 | API log line "Model unavailable: …" | the image has no weights (Git LFS pointers): rebuild once `docs/BLOCKERS.md` B8 is resolved |
| Everyone gets 429 at once | nginx's `client` field | every request seems to come from one address, so the rate limits share one bucket: check assumptions A1 and A13 |
| The API restarts under load | `process_resident_memory_bytes`, "Out of memory" in the logs | raise `memory` in `fly.toml` to 2 GB and redeploy |
| A deploy stops with an edge-mode message | the message | `scripts/deploy.py` refuses to change the web app's mode quietly; pass the mode it names |
| The deploy workflow fails with "unauthorized" | the failed step | an expired deploy token: see above |
| No metrics in Grafana | the API's logs for "Metrics listener not started" | the port was taken, or the bind address does not work there (assumptions A10, A18) |

## Corrections and costs

Corrections to the data or the site go through GitHub issues.

Monthly cost ≈ Σ over machines (price of its size × hours running) +
volume GB × volume price + outbound bandwidth, plus a dedicated IPv4 in
Cloudflare mode. Prices are on Fly's pricing page; they could not be
checked from the build environment. Parking stops everything but the
volume, the stored images and, in Cloudflare mode, the dedicated IPv4.
