#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
compose="$root/deploy/classroom/local-demo/docker-compose.yml"
project=classroom-local-demo

if lsof -nP -iTCP:18080 -iTCP:18081 -iTCP:18082 -sTCP:LISTEN; then
  echo "a local classroom demo port is already in use" >&2
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
  echo "local classroom demo readiness failed: $url" >&2
  return 1
}

docker compose -p "$project" -f "$compose" up --build -d
wait_for_url http://127.0.0.1:18082/health/live
wait_for_url http://127.0.0.1:18080/health/ready
wait_for_url http://127.0.0.1:18081/classroom-api/health/ready

echo "Local façade: http://127.0.0.1:18082"
echo "Local sync API: http://127.0.0.1:18080"
echo "Local classroom proxy: http://127.0.0.1:18081/classroom-api"
