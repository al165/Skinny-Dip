#!/usr/bin/env bash
# Build the dip-args demo here and run it on a server that doesn't build anything.
#
#   ./ship.sh user@host                 # images + config, then (re)start the stack
#   ./ship.sh user@host --data          # also upload the build (first deploy); keeps existing server data
#   ./ship.sh user@host --data --replace-data   # overwrite the server's data (backed up there first)
#   ./ship.sh user@host --no-build      # ship the images already built here
#   ./ship.sh user@host --images        # new images from the sheet: picture files, their wiki pages
#                                       # and pre-computed vectors (no models needed on the server)
#   ./ship.sh user@host --dir /srv/dip-args     # where it lives on the server (default ~/dip-args)
#
# Needs: deploy.env here (copy example.env; its DIPA_DATA / DIPA_IMAGES are the LOCAL folders to
# upload), ssh access to the server, and docker + the compose plugin on the server.
#
# On the server it creates <dir>/compose.yaml, <dir>/deploy.env (pointing at <dir>/data and
# <dir>/images), <dir>/data (the database + wiki, read-write) and <dir>/images (read-only).
#
# Collaborators' wiki edits live in <dir>/data on the server. Re-shipping never touches them unless
# you pass --replace-data, which first saves <dir>/data-backup-<time>.tar.gz. To bring edits
# back here: rsync -a user@host:<dir>/data/wiki/ <local build>/wiki/ && python -m dipa.wiki apply
#
# --images: after `python -m dipa.imagelib import <sheet>` into the local build (DIPA_BUILD in
# deploy.env, default ./data-full), this sends the picture files, the image pages (a page edited more
# recently on the server is kept) and the library's vectors, and loads them in the running API.
set -euo pipefail
# never fail silently: say which line and command stopped the script
trap 'echo "ship.sh: failed at line $LINENO: $BASH_COMMAND" >&2' ERR
cd "$(dirname "$0")"

HOST=""; DIR="~/dip-args"; DATA=0; REPLACE=0; BUILD=1; IMAGES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --data) DATA=1 ;;
    --replace-data) REPLACE=1 ;;
    --no-build) BUILD=0 ;;
    --images) IMAGES=1 ;;
    --dir) DIR="$2"; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) HOST="$1" ;;
  esac
  shift
done
[ -n "$HOST" ] || { echo "usage: ./ship.sh user@host [--data [--replace-data]] [--no-build] [--dir DIR]" >&2; exit 2; }
[ -f deploy.env ] || { echo "deploy.env missing: cp example.env deploy.env and edit it" >&2; exit 1; }

step() { printf '\n== %s\n' "$*"; }
remote() { ssh "$HOST" "$@"; }

# local data/image folders to upload (from deploy.env)
# a key missing from deploy.env reads as empty (the default applies), not as a failure
env_get() { { grep -E "^$1=" deploy.env || true; } | tail -1 | cut -d= -f2-; }
LOCAL_DATA="$(env_get DIPA_DATA)"; LOCAL_DATA="${LOCAL_DATA:-./data-demo}"
LOCAL_IMAGES="$(env_get DIPA_IMAGES)"; LOCAL_IMAGES="${LOCAL_IMAGES:-../../00_Data/SD-Corpus-v1-small/ingest/images}"
LOCAL_BUILD="$(env_get DIPA_BUILD)"; LOCAL_BUILD="${LOCAL_BUILD:-./data-full}"   # where images are imported
if [ -z "$(env_get DIP_EDIT_TOKEN)" ] || [ "$(env_get DIP_EDIT_TOKEN)" = "change-me" ]; then
  echo "warning: DIP_EDIT_TOKEN is not set in deploy.env: anyone with the link can edit the wiki" >&2
fi

step "checking the server ($HOST)"
remote true || { echo "can't reach $HOST over ssh (check the host name, your key, or Tailscale)" >&2; exit 1; }
remote 'docker compose version >/dev/null' || { echo "the server needs docker and the compose plugin" >&2; exit 1; }
ARCH="$(remote uname -m)"
case "$ARCH" in
  x86_64|amd64) PLATFORM=linux/amd64 ;;
  aarch64|arm64) PLATFORM=linux/arm64 ;;
  *) echo "unsupported server architecture: $ARCH" >&2; exit 1 ;;
esac
RUID="$(remote id -u)"
echo "architecture $ARCH ($PLATFORM), user id $RUID"
[ "$RUID" = 1000 ] || echo "note: the API runs as uid 1000; $DIR/data must be writable by it (chown -R 1000:1000 $DIR/data)"
remote "mkdir -p $DIR/data $DIR/images"

if [ "$BUILD" = 1 ]; then
  step "building the images here for $PLATFORM"
  # Cross-building (e.g. arm64 on an x86 laptop) needs QEMU: docker run --privileged --rm tonistiigi/binfmt --install all
  DOCKER_DEFAULT_PLATFORM="$PLATFORM" docker compose --env-file deploy.env build
fi

step "sending the images (docker save | gzip | ssh docker load)"
docker image inspect dip-args-api:latest dip-args-site:latest >/dev/null
docker save dip-args-api:latest dip-args-site:latest | gzip -1 | remote 'gunzip | docker load'

step "sending compose.yaml and deploy.env (paths rewritten for the server)"
scp -q compose.yaml "$HOST:$DIR/compose.yaml"
sed -e 's#^DIPA_DATA=.*#DIPA_DATA=./data#' -e 's#^DIPA_IMAGES=.*#DIPA_IMAGES=./images#' deploy.env \
  | remote "cat > $DIR/deploy.env && chmod 600 $DIR/deploy.env"
remote "grep -q '^DIPA_DATA=' $DIR/deploy.env || echo DIPA_DATA=./data >> $DIR/deploy.env; \
        grep -q '^DIPA_IMAGES=' $DIR/deploy.env || echo DIPA_IMAGES=./images >> $DIR/deploy.env"

step "sending the image library (read-only on the server)"
rsync -az "$LOCAL_IMAGES"/ "$HOST:$DIR/images/"

if [ "$DATA" = 1 ]; then
  [ -s "$LOCAL_DATA/args.sqlite" ] || { echo "no database in $LOCAL_DATA (DIPA_DATA in deploy.env)" >&2; exit 1; }
  if remote "test -s $DIR/data/args.sqlite" && [ "$REPLACE" = 0 ]; then
    step "the server already has data: kept (it holds collaborators' edits; --replace-data overwrites it)"
  else
    if remote "test -s $DIR/data/args.sqlite"; then
      step "backing up the server's data before replacing it"
      remote "cd $DIR && docker compose --env-file deploy.env stop api >/dev/null 2>&1 || true; \
              tar czf data-backup-\$(date +%Y%m%d-%H%M%S).tar.gz data && ls -1 data-backup-*.tar.gz | tail -1"
    fi
    step "uploading the build ($LOCAL_DATA → $DIR/data)"
    # a consistent copy of the database even if something has it open here
    TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
    sqlite3 "$LOCAL_DATA/args.sqlite" ".timeout 30000" ".backup '$TMP/args.sqlite'"
    rsync -az "$TMP/args.sqlite" "$HOST:$DIR/data/args.sqlite"
    rsync -az --delete "$LOCAL_DATA/wiki/" "$HOST:$DIR/data/wiki/"
  fi
fi

remote "test -s $DIR/data/args.sqlite" || { echo "the server has no database yet: run again with --data" >&2; exit 1; }

step "starting the stack on the server (no build)"
remote "cd $DIR && docker compose --env-file deploy.env up -d --no-build --remove-orphans && docker compose --env-file deploy.env ps"
if [ "$IMAGES" = 1 ]; then
  step "image library: vectors from $LOCAL_BUILD (computed here, so the server needs no models)"
  VTMP="$(mktemp -d)"
  DIP_DATA_DIR="$LOCAL_BUILD" DIPA_IMAGE_DIR="$LOCAL_IMAGES" python -m dipa.imagelib export "$VTMP/image-vectors.json"
  step "sending image pages (pages edited more recently on the server are kept) and vectors"
  rsync -az --update "$LOCAL_BUILD/wiki/images/" "$HOST:$DIR/data/wiki/images/"
  rsync -az "$VTMP/image-vectors.json" "$HOST:$DIR/data/image-vectors.json"
  rm -rf "$VTMP"
  step "loading them in the running API"
  remote "cd $DIR && docker compose --env-file deploy.env exec -T api python -m dipa.imagelib load /dipa/data/image-vectors.json"
fi

PORT="$(env_get DIPA_PORT)"
echo
echo "done: the demo listens on the server at 127.0.0.1:${PORT:-3001} (point your reverse proxy at it)."
echo "check: ssh $HOST 'curl -s http://127.0.0.1:${PORT:-3001}/api/health'"
