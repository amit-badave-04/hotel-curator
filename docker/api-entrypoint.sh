#!/bin/sh
# Migrate, load the demo data (no-op when already loaded), then serve.
set -eu
curator db upgrade
curator db seed
exec curator serve --host 0.0.0.0 --port 8000 --workers "${CURATOR_WORKERS:-1}"
