#!/usr/bin/env bash
# Upgrade in place from a release bundle, rolling back automatically if the new version is not healthy.
#   ./scripts/upgrade.sh /path/to/adhan-bell-pager-2.1.0.tar.gz
# Keeps config/, audio/, backups/ and .env. Always takes a backup first.
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/common.sh"

bundle="${1:-}"
[ -f "$bundle" ] || die "Usage: $0 <release-bundle.tar.gz>"
bundle="$(cd "$(dirname "$bundle")" && pwd)/$(basename "$bundle")"

old_version="$(env_get APP_VERSION "$(source_version)")"
log "Installed version: $old_version"
backup_file="$(./scripts/backup.sh | tail -1)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
tar xzf "$bundle" -C "$tmp"
src="$(find "$tmp" -maxdepth 2 -name docker-compose.yml -printf '%h\n' | head -1)"
[ -n "$src" ] && [ -f "$src/app/__init__.py" ] || die "This file does not look like an Adhan & Bell Pager release bundle."
new_version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$src/app/__init__.py")"
log "Upgrading to: $new_version"

excludes=(--exclude=./config --exclude=./audio --exclude=./backups --exclude=./.env --exclude=./.previous-source
          --exclude='./config.before-restore-*' --exclude='./audio.before-restore-*')
rm -rf .previous-source
mkdir .previous-source
tar cf - "${excludes[@]}" . | tar xf - -C .previous-source
(cd "$src" && tar cf - "${excludes[@]}" .) | tar xf - -C "$ROOT"
env_set APP_VERSION "$new_version"

log "Building and starting $new_version"
if docker compose build && docker compose up -d && wait_healthy 180; then
  log "Upgrade to $new_version complete. Backup of the previous state: $backup_file"
  exit 0
fi

log "Version $new_version is not healthy. Rolling back to $old_version."
docker compose logs --tail 60 || true
(cd .previous-source && tar cf - .) | tar xf - -C "$ROOT"
env_set APP_VERSION "$old_version"
./scripts/restore.sh "$backup_file"
die "The upgrade failed and was rolled back to $old_version (settings restored from $backup_file)."
