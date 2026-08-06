#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <image>" >&2
  exit 64
fi

docker run \
  --rm \
  --entrypoint /bin/sh \
  --tmpfs /workspace/result:rw,mode=1777 \
  "$1" \
  -c '
set -eu
python -c "from importlib.metadata import version; assert version('"'"'myextension'"'"') == '"'"'0.2.1'"'"'; import myextension; assert myextension.__version__ == '"'"'0.2.1'"'"'"
python -m jupyter server extension list 2>&1 | grep -F myextension
python -m jupyter labextension list 2>&1 | grep -F myextension
log_root=${JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR:-/workspace/result/behavior-audit}
mkdir -p "$log_root"
probe="$log_root/.write-check-$$"
(umask 077 && : > "$probe")
test -w "$probe"
rm -f "$probe"
'
