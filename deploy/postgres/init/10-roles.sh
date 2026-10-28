#!/bin/sh
# Runs once, when Postgres initialises an empty volume (the first deploy).
# Two roles, neither a superuser:
#   curator_owner  owns the database and schema; the release command
#                  (migrations and seed) connects as this role.
#   curator_app    may only connect and read; the API serves with it.
# Passwords come from Fly secrets (scripts/push_fly_secrets.py) and are read
# inside psql with \getenv, so they never appear on a command line.
# pg_hba.conf admits only these roles, only over TLS, with scram-sha-256;
# the superuser only from inside this machine (peer). The superuser-only
# file goes first, so a failure anywhere below leaves a database that
# refuses every connection except the superuser's peer login.
set -eu
: "${PGDATA:?}"
printf 'local all postgres peer\n' > "$PGDATA/pg_hba.conf"
db="${POSTGRES_DB:-curator}"
# A plain name only: a pg_hba.conf keyword, a comma list or a space would
# change what the lines below admit, and Postgres cuts names at 63 bytes.
case "$db" in
    all | sameuser | samerole | samegroup | replication \
        | [!a-z_]* | *[!a-z0-9_]*)
        echo "10-roles.sh: POSTGRES_DB must be a plain lower-case name" >&2
        exit 1 ;;
esac
if [ "${#db}" -gt 63 ]; then
    echo "10-roles.sh: POSTGRES_DB is longer than 63 characters" >&2
    exit 1
fi
cat > "$PGDATA/pg_hba.conf" <<HBA
# TYPE   DATABASE  USER           ADDRESS  METHOD
local    all       postgres                peer
hostssl  $db       curator_owner  all      scram-sha-256
hostssl  $db       curator_app    all      scram-sha-256
HBA
: "${CURATOR_OWNER_PASSWORD:?set it with scripts/push_fly_secrets.py}"
: "${CURATOR_APP_PASSWORD:?set it with scripts/push_fly_secrets.py}"

psql -v ON_ERROR_STOP=1 --username postgres --dbname "$db" <<'SQL'
\getenv owner_password CURATOR_OWNER_PASSWORD
\getenv app_password CURATOR_APP_PASSWORD
SET password_encryption = 'scram-sha-256';
CREATE ROLE curator_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS PASSWORD :'owner_password';
CREATE ROLE curator_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS PASSWORD :'app_password';
SELECT format('ALTER DATABASE %I OWNER TO curator_owner', current_database())
\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database())
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO curator_owner, curator_app',
              current_database())
\gexec
ALTER SCHEMA public OWNER TO curator_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO curator_app;
ALTER DEFAULT PRIVILEGES FOR ROLE curator_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO curator_app;
SQL
