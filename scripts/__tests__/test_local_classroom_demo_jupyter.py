from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from myextension.platform_config import PlatformConfig


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "start_local_classroom_jupyter.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _launcher_environment(tmp_path: Path, *, port_busy: bool = False) -> tuple[dict[str, str], Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace = tmp_path / "trace"
    environment = tmp_path / "environment"
    _write_executable(fake_bin / "lsof", f"#!/bin/sh\nexit {'0' if port_busy else '1'}\n")
    _write_executable(fake_bin / "curl", "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "uv",
        "#!/bin/sh\n"
        "printf 'uv %s\\n' \"$*\" > \"$LOCAL_JUPYTER_TRACE\"\n"
        "env | grep '^JUPYTERLAB_BEHAVIOR_AUDIT_' | sort > \"$LOCAL_JUPYTER_ENVIRONMENT\"\n",
    )
    return (
        {
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "LOCAL_JUPYTER_TRACE": str(trace),
            "LOCAL_JUPYTER_ENVIRONMENT": str(environment),
        },
        trace,
        environment,
    )


def test_jupyter_launcher_starts_only_loopback_student_mode(tmp_path: Path):
    environment, trace, exported = _launcher_environment(tmp_path)

    completed = subprocess.run(["sh", str(LAUNCHER)], cwd=ROOT, env=environment, check=False, text=True, capture_output=True)

    assert completed.returncode == 0, completed.stderr
    invocation = trace.read_text(encoding="utf-8")
    assert "--refresh --no-project --with " in invocation
    assert "--with " + str(ROOT / "dist" / "myextension-0.4.0-py3-none-any.whl") in invocation
    assert "jupyter lab --ServerApp.ip=127.0.0.1 --ServerApp.port=8888" in invocation
    assert "--ServerApp.open_browser=False --ServerApp.token= --ServerApp.password=" in invocation
    assert exported.read_text(encoding="utf-8").splitlines() == [
        "JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK=true",
        "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR=/private/tmp/classroom-local-demo-jupyter/behavior-audit",
        "JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE=student",
        "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL=http://127.0.0.1:18081/classroom-api",
    ]


def test_jupyter_launcher_does_not_take_over_an_existing_port(tmp_path: Path):
    environment, trace, _exported = _launcher_environment(tmp_path, port_busy=True)

    completed = subprocess.run(["sh", str(LAUNCHER)], cwd=ROOT, env=environment, check=False, text=True, capture_output=True)

    assert completed.returncode != 0
    assert "already in use" in completed.stderr
    assert not trace.exists()


def test_platform_config_accepts_only_explicit_loopback_exception_for_local_proxy(tmp_path: Path):
    config = PlatformConfig.from_env(
        {
            "JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE": "student",
            "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL": "http://127.0.0.1:18081/classroom-api",
            "JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK": "true",
            "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR": str(tmp_path),
        }
    )

    assert config.student_mode is True
    assert config.sync_base_url == "http://127.0.0.1:18081/classroom-api"


@pytest.mark.parametrize(
    "value",
    ["http://classroom.example", "http://192.168.1.20:18081/classroom-api"],
)
def test_platform_config_keeps_non_loopback_plaintext_blocked(value: str):
    with pytest.raises(RuntimeError, match="HTTPS"):
        PlatformConfig.from_env(
            {
                "JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE": "student",
                "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL": value,
                "JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK": "true",
            }
        )
