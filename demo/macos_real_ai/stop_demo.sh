#!/usr/bin/env bash

set -euo pipefail

TMP_ROOT=${TMPDIR:-/tmp}
TMP_ROOT=${TMP_ROOT%/}
STATE_FILE=${DEMO_STATE_FILE:-"$TMP_ROOT/myextension-real-ai-demo-current"}

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

validated_runtime() {
  local runtime canonical_tmp canonical_runtime runtime_parent runtime_name
  [ -f "$STATE_FILE" ] || die "no deployed Demo state found"
  IFS= read -r runtime < "$STATE_FILE" || true
  [ -n "$runtime" ] && [ -d "$runtime" ] || die "unsafe Demo runtime"
  canonical_tmp=$(cd "$TMP_ROOT" && pwd -P)
  canonical_runtime=$(cd "$runtime" && pwd -P)
  runtime_parent=$(dirname "$canonical_runtime")
  runtime_name=$(basename "$canonical_runtime")
  case "$runtime_name" in
    myextension-real-ai-demo.?*) ;;
    *) die "unsafe Demo runtime" ;;
  esac
  [ "$runtime_parent" = "$canonical_tmp" ] || die "unsafe Demo runtime"
  printf '%s\n' "$canonical_runtime"
}

RUNTIME=$(validated_runtime)
[ -f "$RUNTIME/server.pid" ] || die "Demo server PID file is missing"
[ -f "$RUNTIME/server.port" ] || die "Demo server port file is missing"
IFS= read -r SERVER_PID < "$RUNTIME/server.pid" || true
IFS= read -r SERVER_PORT < "$RUNTIME/server.port" || true
case "$SERVER_PID" in
  '' | *[!0-9]*) die "invalid Demo server PID" ;;
esac
case "$SERVER_PORT" in
  '' | *[!0-9]*) die "invalid Demo server port" ;;
esac

if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
  printf 'Demo server is already stopped. Runtime retained at %s\n' "$RUNTIME"
  exit 0
fi

COMMAND=$(ps -p "$SERVER_PID" -o command= 2>/dev/null || true)
case "$COMMAND" in
  *"$RUNTIME/venv/bin/python"*"--ServerApp.port=$SERVER_PORT"*) ;;
  *) die "refusing to signal unverified PID" ;;
esac

kill -INT "$SERVER_PID"
ATTEMPT=0
while kill -0 "$SERVER_PID" >/dev/null 2>&1 && [ "$ATTEMPT" -lt 20 ]; do
  sleep 0.5
  ATTEMPT=$((ATTEMPT + 1))
done

if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
  die "Demo server did not stop after SIGINT; no stronger signal was sent"
fi
printf 'Demo server stopped. Runtime and logs retained at %s\n' "$RUNTIME"
