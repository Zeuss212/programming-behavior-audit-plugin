from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "classroom" / "local-demo" / "docker-compose.yml"
START = ROOT / "scripts" / "start_local_classroom_demo.sh"
STOP = ROOT / "scripts" / "stop_local_classroom_demo.sh"
RESET = ROOT / "scripts" / "reset_local_classroom_demo.sh"


def _resolved_compose() -> dict[str, object]:
    completed = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "config", "--format", "json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return payload


def _published_ports(service: dict[str, object]) -> set[tuple[str, str, int]]:
    result: set[tuple[str, str, int]] = set()
    for port in service.get("ports", []):
        assert isinstance(port, dict)
        result.add((str(port.get("host_ip")), str(port.get("published")), int(port.get("target"))))
    return result


def test_resolved_local_demo_topology_is_loopback_only_and_isolated():
    config = _resolved_compose()

    assert config["name"] == "classroom-local-demo"
    assert "14.103." not in json.dumps(config, sort_keys=True)
    services = config["services"]
    assert isinstance(services, dict)
    assert _published_ports(services["demo-fincolab"]) == {("127.0.0.1", "18082", 8080)}
    assert _published_ports(services["sync-api"]) == {("127.0.0.1", "18080", 8080)}
    assert _published_ports(services["classroom-nginx"]) == {("127.0.0.1", "18081", 8080)}
    assert services["minio"].get("ports", []) == []
    assert "frontend" not in services
    assert "jupyter-student" not in services

    volumes = config["volumes"]
    assert isinstance(volumes, dict)
    assert set(volumes) == {"classroom-local-demo-postgres", "classroom-local-demo-minio"}
    assert volumes["classroom-local-demo-postgres"]["name"] == "classroom-local-demo-postgres"
    assert volumes["classroom-local-demo-minio"]["name"] == "classroom-local-demo-minio"


def test_resolved_dependencies_wait_for_storage_and_sync_readiness():
    config = _resolved_compose()
    services = config["services"]
    assert isinstance(services, dict)

    sync_dependencies = services["sync-api"]["depends_on"]
    assert sync_dependencies["postgres"]["condition"] == "service_healthy"
    assert sync_dependencies["minio-init"]["condition"] == "service_completed_successfully"
    assert sync_dependencies["demo-fincolab"]["condition"] == "service_healthy"
    assert services["classroom-nginx"]["depends_on"]["sync-api"]["condition"] == "service_healthy"
    assert services["deadline-worker"]["depends_on"]["sync-api"]["condition"] == "service_healthy"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _script_environment(tmp_path: Path, *, port_busy: bool = False) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace = tmp_path / "trace"
    _write_executable(
        fake_bin / "lsof",
        "#!/bin/sh\n"
        f"exit {'0' if port_busy else '1'}\n",
    )
    _write_executable(
        fake_bin / "docker",
        "#!/bin/sh\n"
        "printf 'docker %s\\n' \"$*\" >> \"$LOCAL_DEMO_TRACE\"\n",
    )
    _write_executable(
        fake_bin / "curl",
        "#!/bin/sh\n"
        "printf 'curl %s\\n' \"$*\" >> \"$LOCAL_DEMO_TRACE\"\n",
    )
    return (
        {
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "LOCAL_DEMO_TRACE": str(trace),
        },
        trace,
    )


def test_start_runs_only_the_named_project_after_ports_are_free(tmp_path: Path):
    environment, trace = _script_environment(tmp_path)

    completed = subprocess.run(["sh", str(START)], cwd=ROOT, env=environment, check=False, text=True, capture_output=True)

    assert completed.returncode == 0, completed.stderr
    lines = trace.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"docker compose -p classroom-local-demo -f {COMPOSE} up --build -d"
    assert lines[1:] == [
        "curl -fsS http://127.0.0.1:18082/health/live",
        "curl -fsS http://127.0.0.1:18080/health/ready",
        "curl -fsS http://127.0.0.1:18081/classroom-api/health/ready",
    ]


def test_start_refuses_to_take_over_an_existing_port(tmp_path: Path):
    environment, trace = _script_environment(tmp_path, port_busy=True)

    completed = subprocess.run(["sh", str(START)], cwd=ROOT, env=environment, check=False, text=True, capture_output=True)

    assert completed.returncode != 0
    assert "already in use" in completed.stderr
    assert not trace.exists()


@pytest.mark.parametrize(
    ("script", "arguments", "expected"),
    [
        (STOP, [], "docker compose -p classroom-local-demo -f {compose} stop"),
        (RESET, ["--yes-reset-local-demo"], "docker compose -p classroom-local-demo -f {compose} down --remove-orphans"),
    ],
)
def test_lifecycle_scripts_target_only_the_local_demo_project(
    tmp_path: Path,
    script: Path,
    arguments: list[str],
    expected: str,
):
    environment, trace = _script_environment(tmp_path)

    completed = subprocess.run(["sh", str(script), *arguments], cwd=ROOT, env=environment, check=False, text=True, capture_output=True)

    assert completed.returncode == 0, completed.stderr
    lines = trace.read_text(encoding="utf-8").splitlines()
    assert lines[0] == expected.format(compose=COMPOSE)
    if script == RESET:
        assert lines[1] == "docker volume rm classroom-local-demo-postgres classroom-local-demo-minio"


def test_reset_rejects_anything_except_the_explicit_confirmation(tmp_path: Path):
    environment, trace = _script_environment(tmp_path)

    completed = subprocess.run(["sh", str(RESET), "--yes"], cwd=ROOT, env=environment, check=False, text=True, capture_output=True)

    assert completed.returncode != 0
    assert "--yes-reset-local-demo" in completed.stderr
    assert not trace.exists()
