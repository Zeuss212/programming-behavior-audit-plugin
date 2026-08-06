#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd -P)

DEMO_PORT=18994
DEMO_BASE_URL="https://ark.cn-beijing.volces.com/api/coding/v3"
DEMO_MODEL="glm-5-2-260617"
DEMO_WHEEL="$PROJECT_ROOT/dist/myextension-0.2.1-py3-none-any.whl"
EXPECTED_WHEEL_SHA256=${DEMO_EXPECTED_WHEEL_SHA256:-"7138965244a5f71b9307ca89c5585cdb58aa1206a2ba1a0c13e57147bbeecf98"}
ENV_FILE="$SCRIPT_DIR/.env"
PREFLIGHT_ONLY=false
TMP_ROOT=${TMPDIR:-/tmp}
TMP_ROOT=${TMP_ROOT%/}
STATE_FILE=${DEMO_STATE_FILE:-"$TMP_ROOT/myextension-real-ai-demo-current"}

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

usage() {
  printf 'Usage: %s [--preflight] [--env-file PATH]\n' "$0"
}

trim_space() {
  printf '%s' "$1" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

load_env_file() {
  local file=$1
  local line key value
  [ -f "$file" ] || die "Demo env file does not exist: $file"

  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    case "$line" in
      '' | [[:space:]]'#'*) continue ;;
    esac
    case "$line" in
      *=*) ;;
      *) die "invalid Demo env line (expected NAME=value)" ;;
    esac
    key=$(trim_space "${line%%=*}")
    value=$(trim_space "${line#*=}")
    case "$value" in
      *'$('* | *'`'*) die "shell expressions are not allowed in Demo env values" ;;
    esac
    case "$key" in
      DEMO_PORT) DEMO_PORT=$value ;;
      DEMO_BASE_URL) DEMO_BASE_URL=$value ;;
      DEMO_MODEL) DEMO_MODEL=$value ;;
      DEMO_WHEEL) DEMO_WHEEL=$value ;;
      DEMO_EXPECTED_WHEEL_SHA256) EXPECTED_WHEEL_SHA256=$value ;;
      DEMO_API_KEY | ARK_API_KEY)
        die "API Key must be entered in the JupyterLab plug-in UI"
        ;;
      *) die "unsupported Demo env setting: $key" ;;
    esac
  done < "$file"
}

validate_settings() {
  local actual_sha
  [ "$(uname -s)" = "Darwin" ] || die "this Demo supports macOS only"
  command -v uv >/dev/null 2>&1 || die "uv is required (https://docs.astral.sh/uv/)"
  command -v shasum >/dev/null 2>&1 || die "shasum is required"
  command -v lsof >/dev/null 2>&1 || die "lsof is required"

  case "$DEMO_PORT" in
    '' | *[!0-9]*) die "DEMO_PORT must be an integer" ;;
  esac
  [ "$DEMO_PORT" -ge 1024 ] && [ "$DEMO_PORT" -le 65535 ] || \
    die "DEMO_PORT must be between 1024 and 65535"
  [ -n "$DEMO_MODEL" ] || die "DEMO_MODEL must not be empty"
  [ "${#EXPECTED_WHEEL_SHA256}" -eq 64 ] || \
    die "DEMO_EXPECTED_WHEEL_SHA256 must be 64 lowercase hexadecimal characters"
  case "$EXPECTED_WHEEL_SHA256" in
    *[!0-9a-f]*)
      die "DEMO_EXPECTED_WHEEL_SHA256 must be 64 lowercase hexadecimal characters"
      ;;
  esac
  case "$DEMO_BASE_URL" in
    https://*) ;;
    http://127.0.0.1/* | http://localhost/*) ;;
    *) die "DEMO_BASE_URL must use HTTPS (HTTP is allowed only for loopback)" ;;
  esac
  case "$DEMO_BASE_URL" in
    *://*@*) die "credentials are not allowed in DEMO_BASE_URL" ;;
  esac
  [ -f "$DEMO_WHEEL" ] || die "Demo wheel does not exist: $DEMO_WHEEL"
  actual_sha=$(shasum -a 256 "$DEMO_WHEEL")
  actual_sha=${actual_sha%% *}
  [ "$actual_sha" = "$EXPECTED_WHEEL_SHA256" ] || \
    die "wheel SHA-256 mismatch"

  if lsof -nP -iTCP:"$DEMO_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    die "port $DEMO_PORT is already in use"
  fi
}

write_state() {
  local runtime=$1
  local state_parent state_temp
  state_parent=$(dirname "$STATE_FILE")
  mkdir -p "$state_parent"
  state_temp="$STATE_FILE.new.$$"
  printf '%s\n' "$runtime" > "$state_temp"
  mv "$state_temp" "$STATE_FILE"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --preflight)
      PREFLIGHT_ONLY=true
      shift
      ;;
    --env-file)
      [ "$#" -ge 2 ] || die "--env-file requires a path"
      ENV_FILE=$2
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown argument: $1"
      ;;
  esac
done

if [ -f "$ENV_FILE" ]; then
  load_env_file "$ENV_FILE"
elif [ "$ENV_FILE" != "$SCRIPT_DIR/.env" ]; then
  die "Demo env file does not exist: $ENV_FILE"
fi

validate_settings

printf 'Demo port: %s\n' "$DEMO_PORT"
printf 'AI Base URL: %s\n' "$DEMO_BASE_URL"
printf 'AI model: %s\n' "$DEMO_MODEL"
printf 'Delivery wheel: %s\n' "$DEMO_WHEEL"

if [ "$PREFLIGHT_ONLY" = true ]; then
  printf 'Preflight passed\n'
  exit 0
fi

RUNTIME=$(mktemp -d "$TMP_ROOT/myextension-real-ai-demo.XXXXXX")
VENV="$RUNTIME/venv"
WORKSPACE="$RUNTIME/workspace"
LOG_ROOT="$RUNTIME/logs"
EXPORT_ROOT="$RUNTIME/exports"
JUPYTER_CONFIG_DIR="$RUNTIME/jupyter-config"
JUPYTER_DATA_DIR="$RUNTIME/jupyter-data"
JUPYTER_RUNTIME_DIR="$RUNTIME/jupyter-runtime"
IPYTHONDIR="$RUNTIME/ipython"

mkdir -p \
  "$WORKSPACE" \
  "$LOG_ROOT" \
  "$EXPORT_ROOT" \
  "$JUPYTER_CONFIG_DIR" \
  "$JUPYTER_DATA_DIR" \
  "$JUPYTER_RUNTIME_DIR" \
  "$IPYTHONDIR"

printf 'Creating isolated Python 3.12 environment...\n'
uv venv --python 3.12 "$VENV"
uv pip install \
  --python "$VENV/bin/python" \
  "jupyterlab==4.6.1" \
  "jupyter-server==2.20.0" \
  "$DEMO_WHEEL"

cp "$SCRIPT_DIR/demo_notebook.ipynb" "$WORKSPACE/score_analysis_demo.ipynb"
printf '%s\n' "$DEMO_PORT" > "$RUNTIME/server.port"
printf '%s\n' "$DEMO_BASE_URL" > "$RUNTIME/ai-base-url.txt"
printf '%s\n' "$DEMO_MODEL" > "$RUNTIME/ai-model.txt"
write_state "$RUNTIME"

export JUPYTER_CONFIG_DIR
export JUPYTER_DATA_DIR
export JUPYTER_RUNTIME_DIR
export IPYTHONDIR
export JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR="$LOG_ROOT"

printf 'Starting isolated JupyterLab at http://127.0.0.1:%s/lab\n' "$DEMO_PORT"
printf 'Runtime: %s\n' "$RUNTIME"
printf 'Follow README.md to configure the API Key in the plug-in UI.\n'

"$VENV/bin/python" -m jupyter lab \
  --ServerApp.root_dir="$WORKSPACE" \
  --ServerApp.ip=127.0.0.1 \
  --ServerApp.port="$DEMO_PORT" \
  --ServerApp.port_retries=0 \
  --ServerApp.open_browser=True &
SERVER_PID=$!
printf '%s\n' "$SERVER_PID" > "$RUNTIME/server.pid"

forward_interrupt() {
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill -INT "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}

trap forward_interrupt INT TERM
set +e
wait "$SERVER_PID"
SERVER_STATUS=$?
set -e
printf 'JupyterLab stopped (status %s). Logs remain at %s\n' \
  "$SERVER_STATUS" "$LOG_ROOT"
exit "$SERVER_STATUS"
