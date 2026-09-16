#!/usr/bin/env sh

set -eu

fail=0
warn=0

pass() {
  printf '[PASS] %s\n' "$1"
}

warning() {
  warn=1
  printf '[WARN] %s\n' "$1"
}

error() {
  fail=1
  printf '[FAIL] %s\n' "$1"
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

printf 'Adhan & Bell Pager preflight check\n'
printf '==================================\n'

os_name="$(uname -s 2>/dev/null || printf 'unknown')"
case "$os_name" in
  Linux)
    pass "Linux host detected"
    ;;
  *)
    warning "Non-Linux host detected ($os_name). Docker may still run, but SIP host networking is most reliable on Linux."
    ;;
esac

if have_cmd docker; then
  pass "Docker CLI found"
else
  error "Docker is not installed"
fi

if [ "$fail" -eq 0 ]; then
  if docker info >/dev/null 2>&1; then
    pass "Docker daemon is running"
  else
    error "Docker daemon is not running or current user cannot access it"
  fi
fi

if docker compose version >/dev/null 2>&1; then
  pass "Docker Compose v2 is available"
else
  error "Docker Compose v2 is not available"
fi

avail_kb="$(df -Pk . 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -n "${avail_kb:-}" ]; then
  avail_gb=$((avail_kb / 1024 / 1024))
  if [ "$avail_gb" -ge 4 ]; then
    pass "Available disk space looks good (${avail_gb} GB free)"
  else
    warning "Low free disk space (${avail_gb} GB). Recommended: at least 4 GB free."
  fi
fi

mem_kb=""
if [ -r /proc/meminfo ]; then
  mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
fi
if [ -n "${mem_kb:-}" ]; then
  mem_mb=$((mem_kb / 1024))
  if [ "$mem_mb" -ge 2048 ]; then
    pass "Memory looks sufficient (${mem_mb} MB)"
  else
    warning "Low memory detected (${mem_mb} MB). Recommended: 2 GB or more."
  fi
fi

port_busy() {
  port="$1"
  if have_cmd ss; then
    ss -ltnu 2>/dev/null | awk '{print $5}' | grep -Eq "(^|[:.])${port}$"
    return $?
  fi
  if have_cmd netstat; then
    netstat -ltnu 2>/dev/null | awk '{print $4}' | grep -Eq "(^|[:.])${port}$"
    return $?
  fi
  return 1
}

web_port="${WEB_PORT:-}"
if [ -z "$web_port" ] && [ -f .env ]; then
  web_port="$(grep -E '^WEB_PORT=' .env | tail -1 | cut -d= -f2)"
fi
web_port="${web_port:-8080}"

if have_cmd curl; then
  pass "curl found"
else
  warning "curl is not installed (install and upgrade use it for health checks)"
fi

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx adhan-bell-pager; then
  pass "Adhan & Bell Pager is already running (its own ports are expected to be in use)"
else
  for port in "$web_port" 5060; do
    if port_busy "$port"; then
      if [ "$port" = "$web_port" ]; then
        error "Web port $port is already used by another program. Set WEB_PORT in .env to a free port."
      else
        warning "SIP port $port appears to be in use. Set another local SIP port in the web UI after installing."
      fi
    else
      pass "Port $port appears free"
    fi
  done
fi

if docker compose config >/dev/null 2>&1; then
  pass "docker-compose.yml is valid"
else
  error "docker-compose.yml validation failed"
fi

printf '\n'
if [ "$fail" -ne 0 ]; then
  printf 'Result: environment is not ready yet.\n'
  exit 1
fi

if [ "$warn" -ne 0 ]; then
  printf 'Result: environment can run, but review warnings above.\n'
  exit 0
fi

printf 'Result: environment looks ready for deployment.\n'
