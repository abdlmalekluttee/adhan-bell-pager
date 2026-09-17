#!/bin/sh
# Prepare the persistent volumes, then run the service as an unprivileged user.
# Set RUN_AS_ROOT=1 only if you enable "set system clock" (needs CAP_SYS_TIME).
set -eu
mkdir -p /config /audio
if [ "$(id -u)" = "0" ]; then
  chown -R app:app /config /audio 2>/dev/null || echo "warning: could not change ownership of /config or /audio" >&2
  chmod 700 /config 2>/dev/null || true
  if [ "${RUN_AS_ROOT:-0}" != "1" ]; then
    # Only NET_BIND_SERVICE is kept, so HTTPS_PORT may be 443 while the service runs unprivileged.
    exec setpriv --reuid=app --regid=app --init-groups --inh-caps=+net_bind_service --ambient-caps=+net_bind_service "$@"
  fi
fi
exec "$@"
