#!/bin/sh
# Prepare the persistent volumes, then run the service as an unprivileged user.
# Set RUN_AS_ROOT=1 only if you enable "set system clock" (needs CAP_SYS_TIME).
set -eu
mkdir -p /config /audio
if [ "$(id -u)" = "0" ]; then
  chown -R app:app /config /audio 2>/dev/null || echo "warning: could not change ownership of /config or /audio" >&2
  chmod 700 /config 2>/dev/null || true
  if [ "${RUN_AS_ROOT:-0}" != "1" ]; then
    exec setpriv --reuid=app --regid=app --init-groups "$@"
  fi
fi
exec "$@"
