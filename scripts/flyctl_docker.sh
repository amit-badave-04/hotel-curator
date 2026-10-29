#!/bin/sh
# flyctl from its official image, pinned by digest in
# deploy/tools/flyctl/Dockerfile (FLYCTL_IMAGE overrides it), for the deploy
# workflow (.github/workflows/deploy.yml): no binary download, and the
# digest is the checksum. scripts/deploy.py calls it through FLYCTL.
#
# The repository is mounted at the same path, so the absolute paths
# deploy.py passes stay valid inside the container. Only FLY_API_TOKEN is
# passed in, by name, so its value never appears on a command line.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
pin=$(sed -n 's/^FROM //p' "$root/deploy/tools/flyctl/Dockerfile")
FLYCTL_IMAGE=${FLYCTL_IMAGE:-$pin}
case "$FLYCTL_IMAGE" in
  flyio/flyctl:*@sha256:*) ;;
  *) echo "FLYCTL_IMAGE is not pinned by digest: $FLYCTL_IMAGE" >&2; exit 2 ;;
esac
here=$(pwd)
case "$here/" in
  "$root"/*) ;;
  *) echo "run from inside the repository ($root)" >&2; exit 2 ;;
esac
exec docker run --rm --env FLY_API_TOKEN \
  --volume "$root:$root" --workdir "$here" \
  "$FLYCTL_IMAGE" "$@"
