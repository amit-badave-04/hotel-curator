# Deploying to Fly.io

How to create and deploy hotel-curator's three Fly apps. The design and
the reasons behind it are in
[ADR 011](../decisions/011-deployment-fly-cloudflare.md). The Cloudflare
cutover comes later; until then the site is `https://hotel-curator.fly.dev`.
Running the site once it is up (parking, logs, metrics, alerts, error
reports) is in the [runbook](../RUNBOOK.md).

| App | Config | Reachable at |
|---|---|---|
| `hotel-curator-db` | [`deploy/postgres/fly.toml`](../../deploy/postgres/fly.toml) | `hotel-curator-db.internal:5432`, private network only |
| `hotel-curator-api` | [`fly.toml`](../../fly.toml) | `hotel-curator-api.flycast:8000`, private network only |
| `hotel-curator` | [`web/fly.toml`](../../web/fly.toml) | `https://hotel-curator.fly.dev` |

## Before the first deploy

- Install `flyctl` and log in: `flyctl auth login`. The deploy workflow
  uses app-scoped tokens instead ([Deploying from GitHub](#deploying-from-github)).
- Fetch the served model weights: `git lfs pull`. The API image refuses to
  build unless the weights match `artifacts/evals/promotion.json`, and
  `scripts/deploy.py` stops before uploading if they are LFS pointers.
- `make setup`, so `uv run` works.

## First deploy

Every step prints each `flyctl` command before running it, and each step can
be run again safely.

```sh
make fly-plan                                  # review: prints, runs nothing
uv run python scripts/deploy.py setup --org <your-fly-org>
uv run python scripts/push_fly_secrets.py init # writes .env.fly (mode 0600)
make fly-secrets                               # checks .env.fly, stages it
make fly-deploy                                # database, then API, then web
make fly-status                                # fails if API or db is public
```

- **`setup`** creates the three apps, the 1 GB `pg_data` volume in `lhr`
  and the API's private IPv6 (Flycast). It skips anything that exists.
- **`fly-secrets`** must come after `setup`, because secrets belong to
  apps.
- **`fly-deploy`** builds each image remotely and deploys one machine per
  app (`--ha=false`). The API and the database are deployed with
  `--no-public-ips`.
- **On the database's first start**, its init scripts create the roles
  `curator_owner` and `curator_app`, turn on TLS with a self-signed
  certificate, and write a `pg_hba.conf` that admits only those two roles,
  only over TLS, with scram-sha-256.
- **On every API deploy**, Fly runs the release command
  `curator db release` once, before the new machine starts. It migrates and
  seeds as `curator_owner`, under a Postgres advisory lock, and does nothing
  if the data is already loaded. The API then serves as `curator_app`,
  which can only read.

Check the result:

```sh
curl -sS https://hotel-curator.fly.dev/api/readyz
```

A local release of the same data answers
`{"status":"ready","hotels":1494,"model":"roberta-base-ft-train2"}`; the
deployed API should answer the same. For the whole site:

```sh
uv run python scripts/smoke.py https://hotel-curator.fly.dev
```

It waits up to three minutes for readiness, then checks the pages, the
hashed script's cache lifetime, the API's readiness, metadata, search and
a hotel, an analyze call answered by the model, robots.txt, and that
/NOTICE.txt and /THIRD-PARTY-NOTICES.txt are served; any FAIL exits 1.

## Secrets

`.env.fly` is git-ignored (`.env.*`). `push_fly_secrets.py` refuses a file
in which the URLs do not match the passwords, host, port, database or
roles, or in which the TLS mode is weaker than `require`. Passwords must be
24 or more of `A-Z a-z 0-9 _ -` (what `init` generates), so they need no
escaping inside a URL. Every value must be printable ASCII without `#` or
quotes, which `flyctl secrets import` would alter. Errors name the line
and the key, never the value. It sends values
to `flyctl secrets import` on stdin and prints only their names.

| App | Secret | Needed |
|---|---|---|
| `hotel-curator-db` | `POSTGRES_PASSWORD`, `CURATOR_OWNER_PASSWORD`, `CURATOR_APP_PASSWORD` | before the first database deploy (init fails without them) |
| `hotel-curator-api` | `CURATOR_DATABASE_URL`, `CURATOR_MIGRATION_DATABASE_URL` | before the first API deploy |
| `hotel-curator-api` | `CURATOR_SENTRY_DSN` | optional: error reports ([runbook](../RUNBOOK.md#error-reports-optional)); checked for the shape `https://<key>@<host>/<project>` |
| `hotel-curator` | `CURATOR_ORIGIN_CERT_B64`, `CURATOR_ORIGIN_KEY_B64`, `CURATOR_ORIGIN_PULL_CA_B64` | Cloudflare mode only (`origin-tls`) |

The same, by hand. Placeholders are in angle brackets. These commands
leave the values in your shell history, so prefer the script.

```sh
flyctl secrets set --app hotel-curator-db --stage \
  POSTGRES_PASSWORD='<superuser-password>' \
  CURATOR_OWNER_PASSWORD='<owner-password>' \
  CURATOR_APP_PASSWORD='<app-password>'
flyctl secrets set --app hotel-curator-api --stage \
  CURATOR_DATABASE_URL='postgresql+psycopg://curator_app:<app-password>@hotel-curator-db.internal:5432/curator?sslmode=require' \
  CURATOR_MIGRATION_DATABASE_URL='postgresql+psycopg://curator_owner:<owner-password>@hotel-curator-db.internal:5432/curator?sslmode=require'
```

Settings that are not secret are in each app's `[env]` block. The API image
sets `CURATOR_ENVIRONMENT=production`, so it refuses to start unless both
database URLs are Postgres with `sslmode=require` (or stricter) or a Unix
socket. The error names the setting, never the URL.

### Changing a database password

The init scripts run only on an empty volume, so a new value in
`.env.fly` alone changes nothing inside Postgres. Change the role's
password first, then the secrets:

```sh
flyctl ssh console --app hotel-curator-db
su postgres -c 'psql -d postgres'     # superuser: local socket only
\password curator_app                 # prompts; nothing in history
```

The superuser connects to `postgres`, not `curator`, because `curator` is
controlled by `curator_owner`, which could plant objects there.

Then put the new value in `.env.fly` (`CURATOR_APP_PASSWORD` and the
password inside `CURATOR_DATABASE_URL`), run `make fly-secrets`, and
`uv run python scripts/deploy.py deploy api`.

### If the database's first start fails

The init scripts run once, on an empty volume. If one fails (usually a
missing `CURATOR_*_PASSWORD`), later starts skip them and the database
refuses every network connection: its image sets
`POSTGRES_HOST_AUTH_METHOD=reject` and initdb's local and loopback defaults
to peer and scram-sha-256, and `pg_hba.conf` is written before anything that
can fail. The
API's release command then fails and nothing is served. Fix the secrets,
then start again from an empty volume:

```sh
flyctl volumes list --app hotel-curator-db
flyctl machine destroy --force --app hotel-curator-db <machine-id>
flyctl volumes destroy --yes <volume-id>
uv run python scripts/deploy.py setup         # recreates pg_data
uv run python scripts/deploy.py deploy db
```

## The web app's edge mode

nginx's site is rendered at start for `CURATOR_EDGE`: `fly` in
`web/fly.toml` (the client address is `Fly-Client-IP`), `local` in docker
compose. After the Cloudflare cutover the web app is deployed in
`cloudflare` mode instead. The whole cutover, in order, is in
[cloudflare.md](cloudflare.md); its Fly side is:

```sh
uv run python scripts/push_fly_secrets.py origin-tls \
  --cert origin.pem --key origin.key --ca origin-pull-ca.pem
uv run python scripts/push_fly_secrets.py push --app hotel-curator
uv run python scripts/deploy.py setup --edge cloudflare   # dedicated IPv4
uv run python scripts/deploy.py deploy web --edge cloudflare --domain <domain>
```

Do these only as part of the checklist: Cloudflare must already require
origin pulls when nginx starts requiring them.

The three files are the Cloudflare Origin Certificate and its key, and the
CA that signs Cloudflare's origin-pull client certificate. They go into
`.env.fly` as base64 and reach the machine as secrets; nginx writes them to
a directory only its user can read.

## Deploying again

`make fly-deploy` redeploys all three apps.
`uv run python scripts/deploy.py deploy api` (or `db`, `web`) deploys
one. A database redeploy restarts Postgres on the same volume; the data
stays.

A web deploy keeps the edge mode its machines run in. Once the web app is
in Cloudflare mode, a deploy without `--edge` stops with a message instead
of quietly switching back to Fly mode (which would drop the origin
certificate, origin pulls and the client-address rule). While the site is
parked (`make fly-stop`, see the [runbook](../RUNBOOK.md#parking-and-resuming))
the web app has no machines to read the mode from, so every web deploy
needs it. Pass the mode each time:

```sh
make fly-deploy FLY_DEPLOY_ARGS="--edge cloudflare --domain <domain>"
```

Switching back to Fly mode needs Fly certificates for the domain first;
follow [the rollback](cloudflare.md#rolling-back-to-fly-mode).

To roll the API back to an earlier image, deploy that image
(`flyctl deploy --app hotel-curator-api --image <image> --config fly.toml`;
`flyctl releases --app hotel-curator-api --image` lists them). Its release
command leaves a schema that a newer image migrated further as it is, with
a warning in the release log: migrations are additive, so the earlier code
still runs on it (`src/hotel_curator/db/migrate.py`).

## Deploying from GitHub

After the first deploy, deploys can run from GitHub Actions. On every push
to `main`, once every CI job has passed, CI's last job runs
[`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml) for
that commit: it waits for your approval, checks that both deploys can go
ahead (the weights are there; the web app's mode and domain match, as
`scripts/deploy.py` decides), then deploys the API (its release command
migrates and seeds), then the web app, then runs `scripts/smoke.py`
against the public address. A refusal at the check leaves both apps as
they were. Only the commit `main`
points at when the job starts is deployed: approving the run for an older
commit, after a newer push, deploys nothing. Each commit on `main` gets its
own CI run, so an approval left waiting never holds up the tests of later
pushes; deploys run one at a time, and a newer one replaces one still
waiting.

There is no separate manual trigger, so every deploy has passed CI for its
commit. To deploy again (after a failed deploy, or a rejected approval),
open the CI run of `main`'s latest commit and re-run its `deploy` job. To
deploy one app, or from a machine with `flyctl`, use `scripts/deploy.py`
as above. The database app is never deployed from GitHub.

Set up once, in the repository's settings, **before** this workflow
reaches `main`. A run that names an environment that does not exist yet
creates it without a reviewer or branch rule; if `production` already
exists, open it and add both. Deploys also need the served weights in Git
LFS (`docs/BLOCKERS.md`, B8): without them every run stops at its check
step, before anything is deployed.

1. **Environment.** Settings, Environments, New environment, `production`.
   Add yourself as a required reviewer, so every deploy waits for your
   approval, and limit deployment branches to `main`.
2. **Deploy tokens**, one per app, each valid for one app only. Create
   each and pipe it straight into the environment secret, so it never
   appears on screen or in shell history:

   ```sh
   flyctl tokens create deploy --app hotel-curator-api --expiry 2160h \
     | gh secret set FLY_API_TOKEN_API --env production
   flyctl tokens create deploy --app hotel-curator --expiry 2160h \
     | gh secret set FLY_API_TOKEN_WEB --env production
   ```

   2160 hours is 90 days: repeat this before they expire. A failed deploy
   step that says "unauthorized" usually means an expired token.
3. **Variables**, only once the web app leaves its default mode (Settings,
   Environments, `production`, Environment variables):
   - after the Cloudflare cutover: `CURATOR_EDGE` = `cloudflare` and
     `CURATOR_DOMAIN` = the domain;
   - after a rollback to Fly mode on the domain: `CURATOR_EDGE` = `fly` and
     `CURATOR_DOMAIN` = the domain.

   With neither set, the workflow passes no mode and `scripts/deploy.py`
   keeps the one the machines run in, or stops if they run in Cloudflare
   mode or at a recorded domain. Set them only to the mode the web app
   really runs in: a value is applied as given. While the site is parked,
   a run without them stops at its check step; resume it first
   (`make fly-start`).

flyctl comes from its official image, pinned by tag and digest in
[`deploy/tools/flyctl/Dockerfile`](../../deploy/tools/flyctl/Dockerfile)
(never built; Dependabot proposes updates to the pin, like Trivy's in
`deploy/tools/trivy/`). The first step checks that the image reports the
pinned tag's version, so an image that does not behave as expected stops
the run before anything is deployed (ADR 011, A15).

If the smoke test fails, the new version is already live: read its
output, then fix forward with a new commit, or roll back by hand (the API:
"Deploying again" above; the web app: `scripts/deploy.py deploy web` from
a checkout of the previous commit).