#!/usr/bin/env bash
# Reclaim preinstalled SDKs on disposable GitHub-hosted Linux image-build VMs.
# Neither source compilation nor final assembly uses these host SDKs. Never run
# the cleanup on a developer machine or a self-hosted runner.
set -euo pipefail
if [ "${GITHUB_ACTIONS:-}" != true ] || [ "${RUNNER_ENVIRONMENT:-}" != github-hosted ] || [ "${RUNNER_OS:-}" != Linux ]; then
  echo 'Image runner cleanup requires a disposable GitHub-hosted Linux runner.' >&2
  exit 2
fi
dry_run=false
case "${1:-}" in
  '') ;;
  --dry-run) dry_run=true ;;
  *) echo 'Usage: prepare-image-runner.sh [--dry-run]' >&2; exit 2 ;;
esac
sdk_paths=(/usr/share/dotnet /usr/local/lib/android /opt/ghc /opt/hostedtoolcache/CodeQL /usr/local/share/powershell)
echo 'Disk space before image-runner preparation:'
df -h /
if [ "$dry_run" = true ]; then
  printf 'Would remove unused hosted SDK: %s\n' "${sdk_paths[@]}"
else
  sudo rm -rf -- "${sdk_paths[@]}"
fi
echo 'Disk space after image-runner preparation:'
df -h /
