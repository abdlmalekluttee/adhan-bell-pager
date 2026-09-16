#!/usr/bin/env bash
# Restore a backup made by backup.sh. The current data is moved aside first, never deleted.
#   ./scripts/restore.sh backups/adhan-bell-pager-2.0.0-20260917-101500.tar.gz
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/common.sh"

archive="${1:-}"
[ -f "$archive" ] || die "Usage: $0 <backup.tar.gz>"
archive="$(cd "$(dirname "$archive")" && pwd)/$(basename "$archive")"
stamp="$(date +%Y%m%d-%H%M%S)"
version="$(env_get APP_VERSION "$(source_version)")"
image="adhan-bell-pager:${version}"
docker image inspect "$image" >/dev/null 2>&1 || die "Image $image is not available. Build it first: docker compose build"

log "Stopping the service"
docker compose stop "$SERVICE" >/dev/null 2>&1 || true

log "Moving current data to config.before-restore-$stamp and audio.before-restore-$stamp"
docker run --rm --entrypoint sh -v "$ROOT:/work" "$image" -c "
  set -e; cd /work
  if [ -d config ]; then mv config config.before-restore-$stamp; fi
  if [ -d audio ]; then mv audio audio.before-restore-$stamp; fi
  mkdir -p config audio"

log "Extracting $archive"
docker run --rm --entrypoint sh -v "$ROOT:/work" -v "$archive:/backup.tar.gz:ro" "$image" -c "
  set -e; cd /work
  tar xzf /backup.tar.gz config audio
  chown -R 10001:10001 config audio
  chmod 700 config"

log "Starting the service"
docker compose up -d
wait_healthy 120 || die "The service did not become healthy after the restore. The previous data is in *.before-restore-$stamp."
log "Restore complete."
