# hotel-curator's Postgres 16 on Fly.io: the same pinned image as docker
# compose, plus first-start scripts that add TLS, two non-superuser roles
# and a pg_hba.conf that admits only those roles, only over TLS (ADR 011).
FROM postgres:16-alpine@sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36
# openssl generates the server certificate on first start (init/20-tls.sh).
RUN apk add --no-cache openssl
COPY --chmod=0755 init/ /docker-entrypoint-initdb.d/
# The entrypoint appends "host all all all <method>" to pg_hba.conf before
# the init scripts run. If they fail part-way (a missing secret), later
# starts skip initialisation; with "reject" that half-built database
# refuses every network connection instead of accepting the superuser.
# initdb's own defaults would trust every local and loopback connection
# until 10-roles.sh replaces pg_hba.conf; peer and scram close that window.
ENV POSTGRES_HOST_AUTH_METHOD=reject \
    POSTGRES_INITDB_ARGS="--auth-local=peer --auth-host=scram-sha-256"
