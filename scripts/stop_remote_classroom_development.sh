#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
compose="$root/deploy/classroom/remote-development/docker-compose.yml"
runtime_env="$root/deploy/classroom/remote-development/.env.remote-development.local"

docker compose --env-file "$runtime_env" -p classroom-remote-development -f "$compose" stop
echo "Stopped remote-development classroom containers; local volumes were preserved."
