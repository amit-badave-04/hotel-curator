#!/bin/sh
# Run a build command trusting the CA bundle in the optional build secret
# "extra_ca" (for TLS-inspecting proxies); without the secret, run it as is.
set -eu
if [ -f /run/secrets/extra_ca ]; then
    export PIP_CERT=/run/secrets/extra_ca SSL_CERT_FILE=/run/secrets/extra_ca
fi
exec "$@"
