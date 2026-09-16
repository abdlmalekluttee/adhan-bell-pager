#!/usr/bin/env bash
# Back up settings, database, secret key and audio into ./backups. Safe while the service is running.
#   ./scripts/backup.sh        # prints the archive path on the last line
# The archive contains secrets (SIP password, secret key, password hashes): keep it private.
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/common.sh"

mkdir -p backups
chmod 700 backups
stamp="$(date +%Y%m%d-%H%M%S)"
version="$(env_get APP_VERSION "$(source_version)")"
out="backups/adhan-bell-pager-${version}-${stamp}.tar.gz"
umask 077

if running; then
  docker exec "$SERVICE" python -m app.cli backup --out /tmp/backup.tar.gz >/dev/null
  docker exec "$SERVICE" cat /tmp/backup.tar.gz > "$out"
  docker exec "$SERVICE" rm -f /tmp/backup.tar.gz
else
  image="adhan-bell-pager:${version}"
  docker image inspect "$image" >/dev/null 2>&1 || die "The service is stopped and image $image is not available."
  docker run --rm --entrypoint sh -e CONFIG_PATH=/config/config.yaml -e AUDIO_DIR=/audio \
    -v "$ROOT/config:/config" -v "$ROOT/audio:/audio" "$image" \
    -c 'python -m app.cli backup --out /tmp/b.tar.gz >/dev/null && cat /tmp/b.tar.gz' > "$out"
fi
[ -s "$out" ] || die "Backup failed (the archive is empty)."
if [ -f .env ]; then cp -f .env "backups/env-${stamp}"; fi
log "Backup written: $out"
echo "$out"
