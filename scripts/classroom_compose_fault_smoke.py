"""Exercise real fault recovery against the local classroom Compose stack.

This runner is deliberately restricted to the repository's local test stack
at ``127.0.0.1:18080``.  It never accepts a remote host, a BAMS address, or
production credentials.  Run ``python scripts/classroom_compose_fault_smoke.py
--all`` to create one isolated Compose project. The runner restarts only
``sync-api`` and temporarily stops/starts only ``minio``, then removes its own
containers and volumes. It does not create or control Docker resources outside
that project.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent
COMPOSE_FILE = REPOSITORY_ROOT / "deploy" / "classroom" / "docker-compose.test.yml"
FAULT_PROJECT_NAME_PATTERN = re.compile(r"classroom-fault-[0-9a-f]{12}")

if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from classroom_contract_smoke import (
    DEFAULT_BASE_URL,
    HttpSmokeClient,
    SmokeFailure,
    _read_collecting_state,
    _require_string,
    _submit_and_read,
    _upload_evidence,
    _write_state,
    run_smoke,
)


class ComposeFaultFailure(RuntimeError):
    """A local Compose safety guard or recovery assertion failed."""


def require_local_base_url(base_url: str) -> str:
    """Reject every endpoint except the loopback test API before Docker changes."""

    if base_url != DEFAULT_BASE_URL:
        raise ComposeFaultFailure(
            "Compose fault runner only accepts the local http://127.0.0.1:18080 test API."
        )
    return base_url


def require_fault_project_name(project_name: str) -> str:
    """Limit Docker control and cleanup to one randomly named local test project."""

    if FAULT_PROJECT_NAME_PATTERN.fullmatch(project_name) is None:
        raise ComposeFaultFailure("Compose fault runner requires an isolated classroom-fault-* project.")
    return project_name


def _docker_binary() -> str:
    bundled = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
    return str(bundled) if bundled.exists() else os.environ.get("DOCKER_BIN", "docker")


def compose_command(*arguments: str, project_name: str) -> tuple[str, ...]:
    """Build a command which targets only the checked-out test Compose file."""

    if not COMPOSE_FILE.is_file():
        raise ComposeFaultFailure("The checked-out local Compose file is unavailable.")
    isolated_project = require_fault_project_name(project_name)
    return (
        _docker_binary(),
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "--project-name",
        isolated_project,
        *arguments,
    )


def _run_compose(*arguments: str, project_name: str) -> None:
    completed = subprocess.run(
        compose_command(*arguments, project_name=project_name),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Docker error"
        raise ComposeFaultFailure(f"Local Compose command {' '.join(arguments)} failed: {detail}")


def expect_http_status(action: Callable[[], object], expected_status: int) -> bool:
    """Confirm a contract client observed the specific retryable HTTP status."""

    try:
        action()
    except SmokeFailure as error:
        marker = f"returned HTTP {expected_status}"
        if marker in str(error):
            return True
        raise ComposeFaultFailure(
            f"expected HTTP {expected_status} during the injected fault, got: {error}"
        ) from error
    raise ComposeFaultFailure(f"expected HTTP {expected_status} during the injected fault, got success")


def _wait_for_ready(client: HttpSmokeClient, *, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: SmokeFailure | None = None
    while time.monotonic() < deadline:
        try:
            response = client.request_json("GET", "/health/ready")
            if response.get("status") == "ready":
                return
            last_error = SmokeFailure("GET /health/ready did not report ready")
        except SmokeFailure as error:
            last_error = error
        time.sleep(0.5)
    raise ComposeFaultFailure("Local sync-api did not become ready after 30 seconds.") from last_error


def _evidence_body_for_sequence(sequence: int) -> bytes:
    payload = json.dumps(
        {"events": [{"sequence": sequence, "type": "notebook_run", "source": "compose-fault"}]},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return gzip.compress(payload, mtime=0)


def _upload_until_storage_recovers(
    client: HttpSmokeClient,
    *,
    session_id: str,
    access_token: str,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    body = _evidence_body_for_sequence(2)
    last_error: SmokeFailure | None = None
    while time.monotonic() < deadline:
        try:
            return client.put_evidence(
                f"/v1/classroom/plugin/sessions/{session_id}/evidence/2",
                body,
                token=access_token,
                first_event_sequence=2,
                last_event_sequence=2,
            )
        except SmokeFailure as error:
            if "returned HTTP 503" not in str(error):
                raise
            last_error = error
            time.sleep(0.5)
    raise ComposeFaultFailure("Local MinIO did not recover within 30 seconds.") from last_error


def run_all(base_url: str, *, state_file: Path, project_name: str) -> dict[str, object]:
    """Run restart, ticket-replay and MinIO-503 recovery against real local HTTP services."""

    safe_base_url = require_local_base_url(base_url)
    require_fault_project_name(project_name)
    client = HttpSmokeClient(safe_base_url)
    _wait_for_ready(client)
    initial = run_smoke(client, state_file=state_file, now=datetime.now(UTC))
    expected_session_id = _require_string(initial, "session_id")

    _run_compose("restart", "sync-api", project_name=project_name)
    _wait_for_ready(client)

    state = _read_collecting_state(state_file)
    assignment_id = _require_string(state, "assignment_id")
    ticket = client.request_json(
        "POST",
        f"/v1/classroom/student/assignments/{assignment_id}/launch-ticket",
        token="student-token",
    )
    ticket_value = _require_string(ticket, "ticket")
    registration = client.request_json(
        "POST",
        "/v1/classroom/plugin/sessions/register",
        payload={"ticket": ticket_value, "plugin_instance_id": "local-compose-fault-smoke"},
    )
    resumed_session_id = _require_string(registration, "session_id")
    if resumed_session_id != expected_session_id:
        raise ComposeFaultFailure("sync-api restart did not restore the original monitor session.")
    access_token = _require_string(registration, "access_token")
    ticket_rejected = expect_http_status(
        lambda: client.request_json(
            "POST",
            "/v1/classroom/plugin/sessions/register",
            payload={"ticket": ticket_value, "plugin_instance_id": "ticket-replay"},
        ),
        403,
    )

    _run_compose("stop", "minio", project_name=project_name)
    minio_503 = expect_http_status(
        lambda: client.put_evidence(
            f"/v1/classroom/plugin/sessions/{resumed_session_id}/evidence/2",
            _evidence_body_for_sequence(2),
            token=access_token,
            first_event_sequence=2,
            last_event_sequence=2,
        ),
        503,
    )
    _run_compose("start", "minio", project_name=project_name)
    evidence_two = _upload_until_storage_recovers(
        client,
        session_id=resumed_session_id,
        access_token=access_token,
    )
    if evidence_two.get("sequence") != 2:
        raise ComposeFaultFailure("MinIO recovery did not persist evidence sequence 2.")

    resumed = _upload_evidence(
        client,
        state,
        session_id=resumed_session_id,
        access_token=access_token,
    )
    completed = _submit_and_read(
        client,
        resumed,
        session_id=resumed_session_id,
        access_token=access_token,
    )
    if completed.get("submission_reason") != "student_manual":
        raise ComposeFaultFailure("The completed brief does not record a manual student submission.")
    _write_state(state_file, completed)
    return {
        "minio_503_observed": minio_503,
        "phase": completed["phase"],
        "session_id": resumed_session_id,
        "status": "ok",
        "submission_reason": "student_manual",
        "sync_api_restarted": True,
        "ticket_replay_rejected": ticket_rejected,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run every real local Compose fault scenario.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CLASSROOM_SYNC_BASE_URL", DEFAULT_BASE_URL),
        help="Must be the local test API (default: %(default)s).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.all:
        raise SystemExit("Pass --all to run the real local Compose fault matrix.")
    safe_base_url = require_local_base_url(args.base_url)
    project_name = f"classroom-fault-{uuid4().hex[:12]}"
    stack_touched = False
    try:
        stack_touched = True
        _run_compose("build", project_name=project_name)
        _run_compose("up", "-d", "--no-build", "--wait", project_name=project_name)
        with TemporaryDirectory(prefix="classroom-compose-fault-") as temporary:
            result = run_all(
                safe_base_url,
                state_file=Path(temporary) / "state.json",
                project_name=project_name,
            )
    finally:
        if stack_touched:
            _run_compose("down", "--volumes", "--remove-orphans", project_name=project_name)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
