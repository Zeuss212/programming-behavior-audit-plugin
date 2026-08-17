"""Create a deterministic, checksum-verified classroom image build kit."""

from __future__ import annotations

import argparse
import gzip
import hashlib
from io import BytesIO
from pathlib import Path, PurePosixPath
import tarfile


ARCHIVE_ROOT = "behavior-audit-classroom-0.4.0"
WHEEL_NAME = "myextension-0.4.0-py3-none-any.whl"
PAYLOAD_PATHS = (
    PurePosixPath(".dockerignore"),
    PurePosixPath("Dockerfile"),
    PurePosixPath("INSTALL.md"),
    PurePosixPath("README.md"),
    PurePosixPath("build_image.sh"),
    PurePosixPath("export_image.sh"),
    PurePosixPath("runtime.env.example"),
    PurePosixPath("verify_image.sh"),
    PurePosixPath("artifacts") / WHEEL_NAME,
)
SOURCE_CHECKSUM_PATH = PurePosixPath("SHA256SUMS")
ARCHIVE_CHECKSUM_PATH = PurePosixPath("SHA256SUMS")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_payloads(source: Path) -> dict[PurePosixPath, bytes]:
    payloads: dict[PurePosixPath, bytes] = {}
    for relative_path in PAYLOAD_PATHS:
        file_path = source.joinpath(*relative_path.parts)
        if not file_path.is_file():
            raise FileNotFoundError(f"Missing required handoff payload: {relative_path}")
        payloads[relative_path] = file_path.read_bytes()
    return payloads


def verify_source_wheel(source: Path, wheel: bytes) -> None:
    checksum_path = source.joinpath(*SOURCE_CHECKSUM_PATH.parts)
    if not checksum_path.is_file():
        raise FileNotFoundError("Missing source wheel checksum manifest.")

    expected_line = f"{sha256(wheel)}  artifacts/{WHEEL_NAME}"
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    if lines != [expected_line]:
        raise ValueError("Source wheel checksum manifest does not match the candidate wheel.")


def render_manifest(payloads: dict[PurePosixPath, bytes]) -> bytes:
    lines = [f"{sha256(payloads[path])}  {path.as_posix()}" for path in sorted(payloads)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def add_file(bundle: tarfile.TarFile, name: PurePosixPath, data: bytes) -> None:
    entry = tarfile.TarInfo(name.as_posix())
    entry.size = len(data)
    entry.mode = 0o755 if name.suffix == ".sh" else 0o644
    entry.mtime = 0
    entry.uid = 0
    entry.gid = 0
    entry.uname = "root"
    entry.gname = "root"
    bundle.addfile(entry, BytesIO(data))


def write_archive(output: Path, payloads: dict[PurePosixPath, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_name(f".{output.name}.tmp")
    manifest = render_manifest(payloads)

    with temporary_output.open("wb") as raw_file:
        with gzip.GzipFile(
            filename="", fileobj=raw_file, mode="wb", mtime=0
        ) as compressed_file:
            with tarfile.open(fileobj=compressed_file, mode="w") as bundle:
                for relative_path in sorted(payloads):
                    add_file(
                        bundle,
                        PurePosixPath(ARCHIVE_ROOT) / relative_path,
                        payloads[relative_path],
                    )
                add_file(
                    bundle,
                    PurePosixPath(ARCHIVE_ROOT) / ARCHIVE_CHECKSUM_PATH,
                    manifest,
                )
    temporary_output.replace(output)

    output_checksum = output.with_name(f"{output.name}.sha256")
    output_checksum.write_text(
        f"{sha256(output.read_bytes())}  {output.name}\n", encoding="utf-8"
    )


def main() -> None:
    arguments = parse_arguments()
    source = arguments.source.resolve()
    output = arguments.output.resolve()
    if output.suffixes[-2:] != [".tar", ".gz"]:
        raise ValueError("Output archive must end with .tar.gz.")
    if not source.is_dir():
        raise NotADirectoryError(f"Handoff source is not a directory: {source}")

    payloads = read_payloads(source)
    verify_source_wheel(source, payloads[PurePosixPath("artifacts") / WHEEL_NAME])
    write_archive(output, payloads)


if __name__ == "__main__":
    main()
