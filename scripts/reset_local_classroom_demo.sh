#!/bin/sh
set -eu

if [ "$#" -ne 1 ] || [ "$1" != "--yes-reset-local-demo" ]; then
  echo "Usage: $0 --yes-reset-local-demo" >&2
  exit 2
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
compose="$root/deploy/classroom/local-demo/docker-compose.yml"

docker compose -p classroom-local-demo -f "$compose" down --remove-orphans
docker volume rm classroom-local-demo-postgres classroom-local-demo-minio
echo "Reset only classroom-local-demo volumes."
