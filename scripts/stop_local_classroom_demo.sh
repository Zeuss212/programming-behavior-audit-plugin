#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
compose="$root/deploy/classroom/local-demo/docker-compose.yml"

docker compose -p classroom-local-demo -f "$compose" stop
echo "Stopped local classroom demo containers; demo volumes were preserved."
