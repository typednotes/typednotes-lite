#!/usr/bin/env bash
# Resolve each service's newest stable release, then build both image variants.
# Extra arguments are passed to Bake (e.g. --print, --push or target names).
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
if [ -z "${PLATFORM:-}" ]; then
  case "$(uname -m)" in
    arm64|aarch64) PLATFORM=linux/arm64 ;;
    *) PLATFORM=linux/amd64 ;;
  esac
fi
export PLATFORM
python3 -m images.releases --output .images
exec docker buildx bake -f lite/docker-bake.hcl -f .images/bake.json \
  --set '*.cache-from=' --set '*.cache-to=' \
  --set lite.output=type=docker --set local.output=type=docker "$@"
