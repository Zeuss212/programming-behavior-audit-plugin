#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <image> <output-tar>" >&2
  exit 64
fi

case "$2" in
  *.tar) ;;
  *)
    echo "Output path must end with .tar." >&2
    exit 64
    ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

"$script_dir/verify_image.sh" "$1"

platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$1")
if [ "$platform" != "linux/amd64" ]; then
  echo "Refusing to export non-linux/amd64 image: $platform" >&2
  exit 65
fi

docker save --output "$2" "$1"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$2" > "$2.sha256"
elif command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$2" > "$2.sha256"
else
  echo "No SHA-256 verification tool found (sha256sum or shasum)." >&2
  exit 69
fi
