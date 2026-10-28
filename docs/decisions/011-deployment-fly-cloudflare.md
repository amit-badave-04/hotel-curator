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
| Fly service | HTTP service on 80/443 to nginx's plain port 8080 | TCP service on 443 with `handlers = ["proxy_proto"]`, passed through to nginx's TLS port 8443 (unverified here) |
| Origin TLS | none past Fly's edge | Cloudflare Origin Certificate in nginx; SSL mode Full (strict) |
| Origin lock | none (the fly.dev hostname is the site) | nginx requires Cloudflare's origin-pull client certificate (Authenticated Origin Pulls) and closes requests for any other `Host` |
| Client IP for rate limits | `Fly-Client-IP`, set by Fly's edge from the connection | `CF-Connecting-IP`, trusted only because the client-certificate handshake proved the request came through Cloudflare |
| Health checks | plain internal port, no public service | same |

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
`X-Real-IP` with it. The API keys its rate limits on `X-Real-IP` only and
ignores `X-Forwarded-For`. Only nginx can reach the API, because the API
has no public address.

| Mode | Source of the client address | Why it cannot be forged |
|---|---|---|
| compose | the TCP peer (`$remote_addr`) | the API is not reachable from outside the compose network except on loopback |
| Fly | `Fly-Client-IP` | Fly's edge sets it from the connection; a client-sent value is replaced (unverified here) |
| Cloudflare | `CF-Connecting-IP` | Cloudflare sets it; only connections that present Cloudflare's client certificate reach nginx |

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

### Sizing, idle policy and cost

- **API.** Measured on the build machine with the int8 model warm, after 30
  analyze requests: peak RSS 451 MiB. With 30% headroom that is 586 MiB, so
  1 GB is the smallest fitting size.
- **Machine counts.**
  - One API machine (`--ha=false`), because the rate limiter is in memory.
  - `min_machines_running = 1` on API and web, with auto-stop off, because
    a cold start would load the model on a visitor's first request.
  - The database runs one machine.
- **Parking.** `scripts/ops.py stop` stops all three (graphrag's pattern);
  `start` brings them back in order.
- **Cost.** Monthly cost ≈ Σ over machines (price of its size × hours
  running) + volume GB × volume price + outbound bandwidth. Prices are on
  Fly's pricing page; they could not be checked from the build environment.

### Secrets

| App | Secret | Used by |
|---|---|---|
| `hotel-curator-api` | `CURATOR_DATABASE_URL` (`curator_app`) | serving |
| `hotel-curator-api` | `CURATOR_MIGRATION_DATABASE_URL` (`curator_owner`) | the release command only |
| `hotel-curator-api` | `CURATOR_SENTRY_DSN` (optional) | error reports |
| `hotel-curator-db` | `POSTGRES_PASSWORD`, `CURATOR_OWNER_PASSWORD`, `CURATOR_APP_PASSWORD` | first initialisation of the volume |
| `hotel-curator` | `CURATOR_ORIGIN_CERT`, `CURATOR_ORIGIN_KEY` | Cloudflare mode only |
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

## Unverified provider assumptions and how each is checked

| # | Assumption | Check at first deploy |
|---|---|---|
| A1 | Fly's edge sets `Fly-Client-IP` from the connection and replaces a client-sent value | send `Fly-Client-IP: 203.0.113.9` to `https://hotel-curator.fly.dev/api/v1/hotels`; nginx's log shows the real address |
| A2 | `hotel-curator-api.flycast:8000` reaches the API after a private IPv6 is allocated | `scripts/smoke.py` gets analyze 200 through the web app |
| A3 | An app with only a private address and `[[services]]` gets no public IP | `flyctl ips list -a hotel-curator-api` and `-a hotel-curator-db` list no public address |
| A4 | `release_command` runs once per deploy on a temporary machine with the app's secrets | `flyctl releases -a hotel-curator-api` and the release log line "seed: up to date" or "loaded 1494 hotels" |
| A5 | `kill_signal` and `kill_timeout` are honoured | the deploy log shows "Application shutdown complete" before the machine stops |
| A6 | Top-level `[checks]` may target a port without a public service | `flyctl checks list` shows the checks passing |
| A7 | A TCP service with `handlers = ["proxy_proto"]` passes TLS through with a PROXY header | after cutover, nginx's log shows a Cloudflare address as the proxy-protocol peer and the visitor's address as the client |
| A8 | Cloudflare sets `CF-Connecting-IP` and presents its client certificate on every origin request | `scripts/check_edge.py` passes; a direct TLS connection without the certificate is refused |
| A9 | Fly snapshots volumes daily | `flyctl volumes snapshots list` |
| A10 | `[metrics]` scrapes a port without a public service | metrics appear in Fly's Grafana |
| A11 | Rocket Loader, Email Obfuscation, Web Analytics injection and Zaraz are off | `scripts/check_edge.py` finds the CSP unchanged and no injected script |
| A12 | Traffic between Fly hosts on the private network is encrypted | Fly's documentation at deploy time; recorded here |

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
| Pinned images and actions | digests and SHAs | the same, plus flyctl pinned by version and checksum |

## Consequences

- Two web edge modes, switched by a config change at the Cloudflare
  cutover, both covered by tests.
- One API machine: a deploy or crash means seconds of 503 on `/api`. That
  is acceptable for a demo and documented in the runbook.
- A standing monthly cost, controlled by `scripts/ops.py stop` when the
  demo is not needed.
