#!/usr/bin/env bash
# Shared helpers for the host scripts. The scripts can be run from anywhere; they work in the install directory.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
SERVICE=adhan-bell-pager

env_get() {   # env_get KEY DEFAULT
  local v=""
  if [ -f .env ]; then v="$(grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true)"; fi
  echo "${v:-$2}"
}

env_set() {   # env_set KEY VALUE
  touch .env
  if grep -qE "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}

source_version() { sed -n 's/^__version__ = "\(.*\)"/\1/p' app/__init__.py; }

running() { [ "$(docker inspect -f '{{.State.Running}}' "$SERVICE" 2>/dev/null || echo false)" = "true" ]; }

wait_healthy() {   # wait_healthy SECONDS
  local port deadline
  port="$(env_get WEB_PORT 8080)"
  deadline=$((SECONDS + ${1:-120}))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if curl -fsS -m 3 "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

log() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
