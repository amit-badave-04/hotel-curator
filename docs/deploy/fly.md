# Deploying to Fly.io

How to create and deploy hotel-curator's three Fly apps. The design and
the reasons behind it are in
[ADR 011](../decisions/011-deployment-fly-cloudflare.md). The Cloudflare
cutover comes later; until then the site is `https://hotel-curator.fly.dev`.

| App | Config | Reachable at |
|---|---|---|
| `hotel-curator-db` | [`deploy/postgres/fly.toml`](../../deploy/postgres/fly.toml) | `hotel-curator-db.internal:5432`, private network only |
| `hotel-curator-api` | [`fly.toml`](../../fly.toml) | `hotel-curator-api.flycast:8000`, private network only |
| `hotel-curator` | [`web/fly.toml`](../../web/fly.toml) | `https://hotel-curator.fly.dev` |

## Before the first deploy

- Install `flyctl` and log in: `flyctl auth login`. A CI job uses a
  `FLY_API_TOKEN` instead.
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
deployed API should answer the same.

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

## Deploying again

`make fly-deploy` redeploys all three apps.
`uv run python scripts/deploy.py deploy api` (or `db`, `web`) deploys
one. A database redeploy restarts Postgres on the same volume; the data
stays.
