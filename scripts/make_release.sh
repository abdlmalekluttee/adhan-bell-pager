#!/usr/bin/env bash
# Build release artifacts into dist/: a deployable source bundle, checksums and release notes.
#   ./scripts/make_release.sh v2.0.0
set -euo pipefail
cd "$(dirname "$0")/.."
tag="${1:?usage: make_release.sh vX.Y.Z}"
version="${tag#v}"
src_version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/__init__.py)"
[ "$version" = "$src_version" ] || { echo "Tag $tag does not match app version $src_version" >&2; exit 1; }
name="adhan-bell-pager-${version}"
rm -rf dist && mkdir -p "dist/$name"
git archive --format=tar HEAD | tar -x -C "dist/$name"
rm -rf "dist/$name/tests/live" "dist/$name/.github" "dist/$name/assets/screenshots-v1"
( cd dist && tar czf "$name.tar.gz" "$name" && rm -rf "$name" )
./scripts/check_secrets.sh "dist/$name.tar.gz"
awk -v h="## [$version]" 'index($0, h) == 1 {on = 1; next} on && index($0, "## [") == 1 {exit} on {print}' CHANGELOG.md   > dist/RELEASE_NOTES.md
[ -s dist/RELEASE_NOTES.md ] || echo "See CHANGELOG.md" > dist/RELEASE_NOTES.md
( cd dist && sha256sum "$name.tar.gz" > SHA256SUMS )
ls -l dist
