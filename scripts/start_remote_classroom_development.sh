#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
compose="$root/deploy/classroom/remote-development/docker-compose.yml"
runtime_env="$root/deploy/classroom/remote-development/.env.remote-development.local"
project=classroom-remote-development

if [ ! -f "$runtime_env" ]; then
  echo "missing local runtime configuration: $runtime_env" >&2
  echo "copy runtime-config.example and configure the paired FinColab origin first" >&2
  exit 1
fi

if lsof -nP -iTCP:18083 -sTCP:LISTEN; then
  echo "remote-development classroom port 18083 is already in use" >&2
  exit 1
fi

wait_for_url() {
  url=$1
  attempt=1
  while [ "$attempt" -le 60 ]; do
    if curl -fsS "$url" >/dev/null; then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  echo "remote-development classroom readiness failed: $url" >&2
  return 1
}

docker compose --env-file "$runtime_env" -p "$project" -f "$compose" up --build -d
wait_for_url http://127.0.0.1:18083/health/ready
echo "Remote-development classroom API: http://127.0.0.1:18083"
