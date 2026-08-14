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
  --env JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE=student \
  --env JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL=https://classroom-sync.example.invalid/classroom-api \
  --env JUPYTERLAB_BEHAVIOR_AUDIT_DEADLINE_POLL_SECONDS=30 \
  "$1" \
  -c '
set -eu
test -z "${ARK_API_KEY:-}"
test -z "${OPENAI_API_KEY:-}"
test -z "${AWS_ACCESS_KEY_ID:-}"
test -z "${AWS_SECRET_ACCESS_KEY:-}"
python -c "from importlib.metadata import version; assert version('"'"'jupyterlab'"'"').split('"'"'.'"'"', 1)[0] == '"'"'4'"'"'; assert version('"'"'jupyter-server'"'"').split('"'"'.'"'"', 1)[0] == '"'"'2'"'"'"
python -c "from importlib.metadata import version; assert version('"'"'myextension'"'"') == '"'"'0.4.0'"'"'; import myextension; assert myextension.__version__ == '"'"'0.4.0'"'"'"
python -c "from myextension.platform_config import PlatformConfig; config = PlatformConfig.from_env(); assert config.student_mode; capabilities = config.capabilities(); assert capabilities['"'"'canCapture'"'"'] and capabilities['"'"'canSubmit'"'"']; assert not capabilities['"'"'canAuthorPlan'"'"']"
python -m jupyter server extension list 2>&1 | grep -F myextension
python -m jupyter labextension list 2>&1 | grep -F myextension
log_root=${JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR:-/workspace/result/behavior-audit}
mkdir -p "$log_root"
probe="$log_root/.write-check-$$"
(umask 077 && : > "$probe")
test -w "$probe"
rm -f "$probe"
'
