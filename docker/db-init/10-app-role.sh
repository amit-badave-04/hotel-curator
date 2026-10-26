#!/bin/sh
# Runs once, when Postgres initialises an empty data volume. The API's role
# owns the curator database (so it can migrate and seed) but is not a
# superuser, so it cannot run COPY ... PROGRAM or read server files. Only that
# role may connect over the shared socket; the superuser only from inside
# this container (`docker compose exec -u postgres db psql curator`).
set -eu
psql -v ON_ERROR_STOP=1 --username postgres --dbname curator <<'SQL'
CREATE ROLE curator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
    NOBYPASSRLS;
ALTER DATABASE curator OWNER TO curator;
ALTER SCHEMA public OWNER TO curator;
SQL
cat > "$PGDATA/pg_hba.conf" <<'HBA'
# TYPE  DATABASE  USER      METHOD
local   curator   curator   trust
local   all       postgres  peer
HBA
