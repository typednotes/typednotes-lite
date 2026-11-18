#!/usr/bin/env bash
# Run one self-contained Local instance against a dedicated persistent directory.
set -euo pipefail
usage() {
  echo "Usage: $0 DIRECTORY [--engine podman|docker] [--port PORT] [--image IMAGE] [--env-file FILE] [--pull always|missing|never]"
}
if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then usage; exit 0; fi
if [ $# -eq 0 ]; then usage >&2; exit 2; fi
directory=$1; shift
engine=${CONTAINER_ENGINE:-}
port=8080
image=${TYPEDNOTES_LOCAL_IMAGE:-ghcr.io/typednotes/typednotes-local:latest}
pull=always
env_file=
while [ $# -gt 0 ]; do
  if [ $# -lt 2 ]; then usage >&2; exit 2; fi
  case "$1" in
    --engine) engine=$2 ;;
    --port) port=$2 ;;
    --image) image=$2 ;;
    --env-file) env_file=$2 ;;
    --pull) pull=$2 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift 2
done
case "$port" in ''|*[!0-9]*) echo 'PORT must be an integer between 1 and 65535' >&2; exit 2 ;; esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then echo 'PORT must be between 1 and 65535' >&2; exit 2; fi
case "$pull" in always|missing|never) ;; *) echo 'Invalid pull policy' >&2; exit 2 ;; esac
if [ -z "$engine" ]; then
  if command -v podman >/dev/null; then engine=podman; else engine=docker; fi
fi
case "$engine" in podman|docker) ;; *) echo 'Engine must be podman or docker' >&2; exit 2 ;; esac
command -v "$engine" >/dev/null || { echo "$engine is not installed" >&2; exit 1; }
if ! "$engine" info >/dev/null 2>&1; then
  echo "$engine is not ready." >&2
  if [ "$engine" = podman ]; then echo 'On macOS, start your VM with: podman machine start' >&2; fi
  exit 1
fi
umask 077
mkdir -p "$directory"
directory=$(cd "$directory" && pwd -P)
if [ -z "$env_file" ] && [ -f "$directory/config.env" ]; then env_file="$directory/config.env"; fi
env_args=()
if [ -n "$env_file" ]; then
  [ -f "$env_file" ] || { echo "Environment file does not exist: $env_file" >&2; exit 2; }
  env_args=(--env-file "$env_file")
fi
read -r identifier _ < <(printf '%s' "$directory" | cksum)
echo "Typednotes Local: http://localhost:$port"
echo "Persistent directory: $directory"
exec "$engine" run --rm --name "typednotes-local-$identifier" --pull "$pull" \
  --publish "127.0.0.1:$port:8080" --volume "$directory:/data:Z" \
  "${env_args[@]}" --env "PUBLIC_URL=http://localhost:$port" "$image"
