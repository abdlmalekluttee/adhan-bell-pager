#!/usr/bin/env bash
# Fail if a release archive (or the working tree) contains likely secrets or local runtime data.
#   ./scripts/check_secrets.sh dist/adhan-bell-pager-2.0.0.tar.gz
set -euo pipefail
target="${1:-.}"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
if [ -f "$target" ]; then tar xzf "$target" -C "$tmp"; root="$tmp"; else root="$target"; fi
fail=0
# runtime data that must never ship
bad_files="$(cd "$root" && find . \( -name 'app.db*' -o -name 'secret.key' -o -name 'users.json*' -o -name '.env' \
  -o -path '*/config/config.yaml' -o -name '*.pem' -o -name 'id_rsa*' -o -name 'id_ed25519*' \) -not -path './.git/*' | grep -v '.env.example' || true)"
if [ -n "$bad_files" ]; then echo "Runtime or key files found:"; echo "$bad_files"; fail=1; fi
# password-like assignments with real values (placeholders and code are allowed)
hits="$(grep -rInE '(password|passwd|secret|token)["'"'"']?\s*[:=]\s*["'"'"'][^"'"'"' ]{6,}["'"'"']' "$root" \
  --include='*.py' --include='*.yml' --include='*.yaml' --include='*.env*' --include='*.json' --include='*.md' --include='*.sh' \
  | grep -vE 'tests/|example|placeholder|Demo-|Correct-Horse|change-me|<|\$\{|os\.environ|getenv|_PASSWORD\}|live_sip_test' || true)"
if [ -n "$hits" ]; then echo "Possible secrets:"; echo "$hits"; fail=1; fi
for needle in ${EXTRA_SECRET_PATTERNS:-}; do
  if grep -rIl -- "$needle" "$root" >/dev/null 2>&1; then echo "Found forbidden value matching an EXTRA_SECRET_PATTERNS entry"; fail=1; fi
done
[ "$fail" -eq 0 ] && echo "No secrets found in $target" || exit 1
