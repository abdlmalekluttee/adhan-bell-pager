#!/usr/bin/env bash
# First-time installation in this directory (an extracted release bundle or a git checkout).
#   ./scripts/install.sh
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/common.sh"

./scripts/preflight.sh || die "Fix the preflight problems above, then run this again."
if [ ! -f .env ]; then
  cp .env.example .env
  log "Created .env from .env.example (web port $(env_get WEB_PORT 8080))."
fi
env_set APP_VERSION "$(source_version)"
mkdir -p config audio backups
chmod 700 backups
log "Building image adhan-bell-pager:$(env_get APP_VERSION local) (the first build compiles PJSIP; allow a few minutes)"
docker compose build
docker compose up -d
log "Waiting for the service to become healthy"
if ! wait_healthy 180; then
  docker compose logs --tail 80
  die "The service did not become healthy."
fi
ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
log "Installed. Open http://${ip:-<server-ip>}:$(env_get WEB_PORT 8080)"
echo "    First sign-in: username admin, password admin. You will be asked to choose a new password."
