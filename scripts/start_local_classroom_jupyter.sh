#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
wheel="$root/dist/myextension-0.4.0-py3-none-any.whl"
demo_root=/private/tmp/classroom-local-demo-jupyter

if lsof -nP -iTCP:8888 -sTCP:LISTEN; then
  echo "127.0.0.1:8888 is already in use" >&2
  exit 1
fi

test -f "$wheel"
curl -fsS http://127.0.0.1:18081/classroom-api/health/ready >/dev/null
mkdir -p "$demo_root/notebooks" "$demo_root/behavior-audit"

export LOCAL_CLASSROOM_DEMO=true
export JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE=student
export JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL=http://127.0.0.1:18081/classroom-api
export JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK=true
export JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR="$demo_root/behavior-audit"

exec uv run --refresh --no-project --with "$wheel" --with 'jupyterlab>=4,<5' jupyter lab \
  --ServerApp.ip=127.0.0.1 \
  --ServerApp.port=8888 \
  --ServerApp.open_browser=False \
  --ServerApp.token='' \
  --ServerApp.password='' \
  --ServerApp.root_dir="$demo_root/notebooks"
