#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <base-image> <target-image>" >&2
  exit 64
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$script_dir" && sha256sum -c SHA256SUMS)
elif command -v shasum >/dev/null 2>&1; then
  (cd "$script_dir" && shasum -a 256 -c SHA256SUMS)
else
  echo "No SHA-256 verification tool found (sha256sum or shasum)." >&2
  exit 69
fi

docker build \
  --build-arg "BLUEDOT_BASE_IMAGE=$1" \
  --tag "$2" \
  "$script_dir"
