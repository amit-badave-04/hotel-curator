#!/bin/sh
# Runs once, when Postgres initialises an empty volume. TLS for connections
# on Fly's private network: a self-signed server certificate kept on the
# volume. The API connects with sslmode=require (encrypted); to verify the
# server as well, give the API this certificate and use sslmode=verify-full
# (ADR 011, "TLS chain"). Rotate by replacing server.crt and server.key, then
# `SELECT pg_reload_conf();`.
set -eu
cd "$PGDATA"
openssl req -new -x509 -days 3650 -nodes -newkey rsa:2048 \
    -subj "/CN=${FLY_APP_NAME:-hotel-curator-db}.internal" \
    -keyout server.key -out server.crt
chmod 0600 server.key

psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres <<'SQL'
ALTER SYSTEM SET ssl = 'on';
ALTER SYSTEM SET ssl_cert_file = 'server.crt';
ALTER SYSTEM SET ssl_key_file = 'server.key';
ALTER SYSTEM SET ssl_min_protocol_version = 'TLSv1.2';
ALTER SYSTEM SET password_encryption = 'scram-sha-256';
ALTER SYSTEM SET listen_addresses = '*';
SQL
