#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
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
PYTHON="$RUNTIME/venv/bin/python"
LOG_ROOT="$RUNTIME/logs"
EXPORT_ROOT="$RUNTIME/exports"

[ -x "$PYTHON" ] || die "Demo Python environment is missing"
[ -d "$LOG_ROOT" ] || die "Demo log directory is missing"
mkdir -p "$EXPORT_ROOT"

exec "$PYTHON" "$SCRIPT_DIR/verify_demo.py" \
  --log-root "$LOG_ROOT" \
  --export \
  --export-dir "$EXPORT_ROOT"
