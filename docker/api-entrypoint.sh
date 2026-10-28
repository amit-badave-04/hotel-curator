#!/bin/sh
# API container entrypoint.
# With arguments, run them: Fly's release_command ("curator db release")
# arrives this way. Without: migrate and load the demo data unless
# CURATOR_MIGRATE_ON_START is false (on Fly the release command does it once
# per deploy), then serve.
set -eu
if [ "$#" -gt 0 ]; then
    exec "$@"
fi
curator db release --if-enabled
# CREDENTIALS: Fly gives every secret to every machine of the app, but the
# owner role's URL is for migrations only; the server never inherits it.
# Settings match names in any case, so every spelling goes.
for name in $(env | sed -n 's/^\([A-Za-z_]*\)=.*/\1/p' \
    | grep -ix 'curator_migration_database_url'); do
    unset "$name"
done
exec curator serve --host 0.0.0.0 --port 8000 --workers "${CURATOR_WORKERS:-1}"
