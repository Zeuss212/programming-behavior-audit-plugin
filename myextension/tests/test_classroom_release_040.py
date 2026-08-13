from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib


ROOT = Path(__file__).resolve().parents[2]
RELEASE_ROOT = ROOT / "deploy" / "bluedot" / "release-0.4.0"
WHEEL_NAME = "myextension-0.4.0-py3-none-any.whl"


def _script(name: str) -> Path:
    script = RELEASE_ROOT / name
    assert script.is_file(), f"missing release script: {name}"
    return script


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_classroom_release_040_declares_student_runtime_without_secrets() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    values: dict[str, str] = {}
    for line in (RELEASE_ROOT / "runtime.env.example").read_text(
        encoding="utf-8"
    ).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, value = stripped.split("=", 1)
        values[key] = value

    assert package["version"] == "0.4.0"
    assert pyproject["tool"]["hatch"]["version"]["source"] == "nodejs"
    assert values == {
        "JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE": "student",
        "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL": "https://classroom-sync.example.invalid",
        "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR": "/workspace/result/behavior-audit",
        "JUPYTERLAB_BEHAVIOR_AUDIT_DEADLINE_POLL_SECONDS": "30",
        "JUPYTERLAB_BEHAVIOR_AUDIT_ANALYSIS_TIMEOUT_SEC": "180",
    }
    assert not any("KEY" in key or "SECRET" in key or "TOKEN" in key for key in values)


def test_build_script_checks_the_040_wheel_before_invoking_docker(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    artifacts = bundle / "artifacts"
    fake_bin = tmp_path / "bin"
    artifacts.mkdir(parents=True)
    fake_bin.mkdir()
    shutil.copy2(_script("build_image.sh"), bundle / "build_image.sh")
    (artifacts / WHEEL_NAME).write_bytes(b"synthetic-wheel")
    (bundle / "SHA256SUMS").write_text(
        f"{'0' * 64}  artifacts/{WHEEL_NAME}\n", encoding="utf-8"
    )
    docker_marker = tmp_path / "docker-called"
    _write_executable(fake_bin / "docker", '#!/bin/sh\n: > "$DOCKER_MARKER"\n')

    completed = subprocess.run(
        [
            "sh",
            str(bundle / "build_image.sh"),
            "registry.invalid/base:4",
            "registry.invalid/behavior-audit:0.4.0-classroom",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "DOCKER_MARKER": str(docker_marker),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert not docker_marker.exists()


def test_build_script_constructs_a_linux_amd64_image_after_checksum(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    artifacts = bundle / "artifacts"
    fake_bin = tmp_path / "bin"
    artifacts.mkdir(parents=True)
    fake_bin.mkdir()
    shutil.copy2(_script("build_image.sh"), bundle / "build_image.sh")
    wheel = artifacts / WHEEL_NAME
    wheel.write_bytes(b"synthetic-wheel")
    (bundle / "SHA256SUMS").write_text(
        f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  artifacts/{WHEEL_NAME}\n",
        encoding="utf-8",
    )
    docker_args = tmp_path / "docker-args"
    _write_executable(
        fake_bin / "docker",
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$DOCKER_ARGS_FILE"\n',
    )

    completed = subprocess.run(
        [
            "sh",
            str(bundle / "build_image.sh"),
            "registry.invalid/base@sha256:abc",
            "behavior-audit:0.4.0-classroom",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "DOCKER_ARGS_FILE": str(docker_args),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert docker_args.read_text(encoding="utf-8").splitlines()[:4] == [
        "build",
        "--platform",
        "linux/amd64",
        "--build-arg",
    ]


def test_export_script_refuses_non_amd64_image_before_docker_save(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    fake_bin = tmp_path / "bin"
    bundle.mkdir()
    fake_bin.mkdir()
    shutil.copy2(_script("export_image.sh"), bundle / "export_image.sh")
    _write_executable(bundle / "verify_image.sh", "#!/bin/sh\nexit 0\n")
    docker_args = tmp_path / "docker-args"
    _write_executable(
        fake_bin / "docker",
        "#!/bin/sh\nprintf '%s\\n' \"$@\" >> \"$DOCKER_ARGS_FILE\"\n"
        "if [ \"$1\" = image ]; then\n  printf 'linux/arm64\\n'\nfi\n",
    )

    completed = subprocess.run(
        [
            "sh",
            str(bundle / "export_image.sh"),
            "behavior-audit:0.4.0-classroom",
            str(tmp_path / "behavior-audit-0.4.0-linux-amd64.tar"),
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "DOCKER_ARGS_FILE": str(docker_args),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "save" not in docker_args.read_text(encoding="utf-8")


def test_verify_script_checks_classroom_capabilities_and_has_no_runtime_secret(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_args = tmp_path / "docker-args"
    _write_executable(
        fake_bin / "docker",
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$DOCKER_ARGS_FILE"\n',
    )

    completed = subprocess.run(
        [
            "sh",
            str(_script("verify_image.sh")),
            "registry.invalid/behavior-audit:0.4.0-classroom",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "DOCKER_ARGS_FILE": str(docker_args),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    args = docker_args.read_text(encoding="utf-8").splitlines()
    assert args[:14] == [
        "run",
        "--rm",
        "--entrypoint",
        "/bin/sh",
        "--tmpfs",
        "/workspace/result:rw,mode=1777",
        "--env",
        "JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE=student",
        "--env",
        "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL=https://classroom-sync.example.invalid",
        "--env",
        "JUPYTERLAB_BEHAVIOR_AUDIT_DEADLINE_POLL_SECONDS=30",
        "registry.invalid/behavior-audit:0.4.0-classroom",
        "-c",
    ]
    container_check = "\n".join(args[14:])
    assert "version('jupyterlab').split('.', 1)[0] == '4'" in container_check
    assert "version('jupyter-server').split('.', 1)[0] == '2'" in container_check
    assert "canSubmit" in container_check
    assert "test -z \"${ARK_API_KEY:-}\"" in container_check
    assert "/workspace/result/behavior-audit" in container_check


def test_release_bundle_checksum_is_for_the_exact_candidate_wheel() -> None:
    wheel = RELEASE_ROOT / "artifacts" / WHEEL_NAME
    checksum = (RELEASE_ROOT / "SHA256SUMS").read_text(encoding="utf-8")

    assert checksum == f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  artifacts/{WHEEL_NAME}\n"


def test_release_bundle_uses_the_current_built_wheel() -> None:
    built_wheel = ROOT / "dist" / WHEEL_NAME
    release_wheel = RELEASE_ROOT / "artifacts" / WHEEL_NAME

    assert built_wheel.is_file(), "Build the wheel before preparing the release bundle."
    assert release_wheel.read_bytes() == built_wheel.read_bytes()


def test_handoff_archive_contains_only_manifested_payloads_and_checksums(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "package_classroom_image_handoff.py"),
            "--source",
            str(RELEASE_ROOT),
            "--output",
            str(archive),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert archive.is_file()
    checksum_file = archive.with_name(f"{archive.name}.sha256")
    expected_archive_checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert checksum_file.read_text(encoding="utf-8") == (
        f"{expected_archive_checksum}  {archive.name}\n"
    )

    archive_root = "behavior-audit-classroom-0.4.0"
    expected_payloads = {
        ".dockerignore",
        "Dockerfile",
        "README.md",
        "INSTALL.md",
        "runtime.env.example",
        "build_image.sh",
        "verify_image.sh",
        "export_image.sh",
        f"artifacts/{WHEEL_NAME}",
    }
    with tarfile.open(archive, "r:gz") as bundle:
        members = {
            member.name: member
            for member in bundle.getmembers()
            if member.isfile()
        }
        assert set(members) == {
            *(f"{archive_root}/{path}" for path in expected_payloads),
            f"{archive_root}/SHA256SUMS",
        }
        manifest = bundle.extractfile(members[f"{archive_root}/SHA256SUMS"])
        assert manifest is not None
        entries = {
            line.split("  ", 1)[1]: line.split("  ", 1)[0]
            for line in manifest.read().decode("utf-8").splitlines()
        }
        assert set(entries) == expected_payloads
        for payload in expected_payloads:
            payload_file = bundle.extractfile(members[f"{archive_root}/{payload}"])
            assert payload_file is not None
            assert entries[payload] == hashlib.sha256(payload_file.read()).hexdigest()


def test_handoff_archive_is_byte_reproducible_for_the_same_source(
    tmp_path: Path,
) -> None:
    first_archive = tmp_path / "first.tar.gz"
    second_archive = tmp_path / "second.tar.gz"
    package_script = ROOT / "scripts" / "package_classroom_image_handoff.py"

    for archive in (first_archive, second_archive):
        completed = subprocess.run(
            [
                sys.executable,
                str(package_script),
                "--source",
                str(RELEASE_ROOT),
                "--output",
                str(archive),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    assert first_archive.read_bytes() == second_archive.read_bytes()
