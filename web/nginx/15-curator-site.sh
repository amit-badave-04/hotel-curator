#!/bin/sh
# Renders nginx's site for CURATOR_EDGE before nginx starts. The nginx image
# runs every /docker-entrypoint.d/*.sh in order and stops if one fails, so a
# missing or malformed setting fails the deploy instead of serving a
# half-configured site (ADR 011).
#   local       docker compose: the client is the TCP peer
#   fly         behind Fly's edge (hotel-curator.fly.dev): Fly-Client-IP
#   cloudflare  behind Cloudflare with TLS passed through: CF-Connecting-IP,
#               the origin certificate and the origin-pull CA come from
#               three base64 secrets (scripts/push_fly_secrets.py)
set -eu
conf_dir="${CURATOR_NGINX_DIR:-/etc/nginx}"
tls_dir="${CURATOR_TLS_DIR:-/tmp/curator-tls}"
edge="${CURATOR_EDGE:-local}"
upstream="${CURATOR_API_UPSTREAM:-}"
names="${CURATOR_SERVER_NAMES:-}"

fail() {
    echo "15-curator-site: $*" >&2
    exit 1
}

# CREDENTIALS: decodes a secret into a file only this user can read; the
# value is never echoed.
write_secret() {
    value=$(printenv "$1" || true)
    [ -n "$value" ] || fail "$1 is not set"
    printf '%s' "$value" | base64 -d > "$2" 2>/dev/null \
        || fail "$1 is not base64"
    grep -q -- '-----BEGIN ' "$2" || fail "$1 is not a PEM file"
}

case "$upstream" in
    "" | *[!A-Za-z0-9.:-]*) fail "CURATOR_API_UPSTREAM must be host:port" ;;
esac
case "$tls_dir" in
    / | //* | */ | *..* | *[!A-Za-z0-9/._-]*)
        fail "CURATOR_TLS_DIR must be a plain absolute path" ;;
    /*) ;;
    *) fail "CURATOR_TLS_DIR must be a plain absolute path" ;;
esac

case "$edge" in
    local | fly) ;;
    cloudflare)
        case "$names" in
            "" | *[!a-z0-9.\ -]*)
                fail "CURATOR_SERVER_NAMES must be lower-case host names" ;;
        esac
        # Each name must be a DNS name nginx accepts and fit
        # server_names_hash_bucket_size (http.conf).
        count=0
        for name in $names; do
            count=$((count + 1))
            case "$name" in
                .* | *. | *..* | -* | *-.* | *.-* | *-)
                    fail "CURATOR_SERVER_NAMES has a bad name" ;;
            esac
            [ "${#name}" -le 110 ] \
                || fail "CURATOR_SERVER_NAMES has a name over 110 characters"
        done
        [ "$count" -gt 0 ] || fail "CURATOR_SERVER_NAMES has no names"
        umask 077
        mkdir -p "$tls_dir"
        write_secret CURATOR_ORIGIN_CERT_B64 "$tls_dir/origin.pem"
        write_secret CURATOR_ORIGIN_KEY_B64 "$tls_dir/origin.key"
        write_secret CURATOR_ORIGIN_PULL_CA_B64 "$tls_dir/origin-pull-ca.pem"
        ;;
    *) fail "CURATOR_EDGE must be local, fly or cloudflare, not '$edge'" ;;
esac

variables='${CURATOR_API_UPSTREAM} ${CURATOR_SERVER_NAMES} ${CURATOR_TLS_DIR}'
CURATOR_API_UPSTREAM="$upstream" CURATOR_SERVER_NAMES="$names" \
    CURATOR_TLS_DIR="$tls_dir" envsubst "$variables" \
    < "$conf_dir/curator/site-$edge.conf.template" \
    > "$conf_dir/conf.d/default.conf"
echo "15-curator-site: edge mode $edge, API at $upstream"
