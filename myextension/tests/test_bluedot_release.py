from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


RELEASE_ROOT = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "bluedot"
    / "release-0.2.1"
)
WHEEL_NAME = "myextension-0.2.1-py3-none-any.whl"


def _script(name: str) -> Path:
    script = RELEASE_ROOT / name
    assert script.is_file(), f"missing release script: {name}"
    return script


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("build_image.sh", []),
        ("build_image.sh", ["base-only"]),
        ("build_image.sh", ["base", "target", "extra"]),
        ("verify_image.sh", []),
        ("verify_image.sh", ["image", "extra"]),
    ],
)
def test_release_scripts_reject_invalid_arity(name, args, tmp_path):
    completed = subprocess.run(
        ["sh", str(_script(name)), *args],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 64
    assert "Usage:" in completed.stderr


def test_build_script_checks_wheel_before_invoking_docker(tmp_path):
    bundle = tmp_path / "bundle"
    artifacts = bundle / "artifacts"
    fake_bin = tmp_path / "bin"
    caller = tmp_path / "caller"
    artifacts.mkdir(parents=True)
    fake_bin.mkdir()
    caller.mkdir()
    shutil.copy2(_script("build_image.sh"), bundle / "build_image.sh")
    (artifacts / WHEEL_NAME).write_bytes(b"synthetic-wheel")
    (bundle / "SHA256SUMS").write_text(
        f"{'0' * 64}  artifacts/{WHEEL_NAME}\n",
        encoding="utf-8",
    )
    docker_marker = tmp_path / "docker-called"
    _write_executable(
        fake_bin / "docker",
        "#!/bin/sh\n: > \"$DOCKER_MARKER\"\n",
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "DOCKER_MARKER": str(docker_marker),
    }

    completed = subprocess.run(
        [
            "sh",
            str(bundle / "build_image.sh"),
            "registry.invalid/base:1",
            "registry.invalid/plugin:0.2.1",
        ],
        cwd=caller,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert not docker_marker.exists()


def test_build_script_is_cwd_independent_and_passes_exact_docker_args(
    tmp_path,
):
    bundle = tmp_path / "bundle"
    artifacts = bundle / "artifacts"
    fake_bin = tmp_path / "bin"
    caller = tmp_path / "caller"
    artifacts.mkdir(parents=True)
    fake_bin.mkdir()
    caller.mkdir()
    shutil.copy2(_script("build_image.sh"), bundle / "build_image.sh")
    wheel = artifacts / WHEEL_NAME
    wheel.write_bytes(b"synthetic-wheel")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (bundle / "SHA256SUMS").write_text(
        f"{digest}  artifacts/{WHEEL_NAME}\n",
        encoding="utf-8",
    )
    docker_args = tmp_path / "docker-args"
    _write_executable(
        fake_bin / "docker",
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$DOCKER_ARGS_FILE\"\n",
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "DOCKER_ARGS_FILE": str(docker_args),
    }

    completed = subprocess.run(
        [
            "sh",
            str(bundle / "build_image.sh"),
            "registry.invalid/base:1",
            "registry.invalid/plugin:0.2.1",
        ],
        cwd=caller,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert docker_args.read_text(encoding="utf-8").splitlines() == [
        "build",
        "--build-arg",
        "BLUEDOT_BASE_IMAGE=registry.invalid/base:1",
        "--tag",
        "registry.invalid/plugin:0.2.1",
        str(bundle),
    ]


def test_verify_script_uses_noninteractive_tmpfs_container(tmp_path):
    fake_bin = tmp_path / "bin"
    caller = tmp_path / "caller"
    fake_bin.mkdir()
    caller.mkdir()
    docker_args = tmp_path / "docker-args"
    _write_executable(
        fake_bin / "docker",
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$DOCKER_ARGS_FILE\"\n",
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "DOCKER_ARGS_FILE": str(docker_args),
    }

    completed = subprocess.run(
        [
            "sh",
            str(_script("verify_image.sh")),
            "registry.invalid/plugin:0.2.1",
        ],
        cwd=caller,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    args = docker_args.read_text(encoding="utf-8").splitlines()
    assert args[:7] == [
        "run",
        "--rm",
        "--entrypoint",
        "/bin/sh",
        "--tmpfs",
        "/workspace/result:rw,mode=1777",
        "registry.invalid/plugin:0.2.1",
    ]
    assert args[7] == "-c"
    container_check = "\n".join(args[8:])
    assert "python -m jupyter lab " not in container_check
    assert "jupyter server extension list" in container_check
