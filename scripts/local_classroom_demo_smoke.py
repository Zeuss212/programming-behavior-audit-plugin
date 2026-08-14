"""Run the loopback-only local classroom demo smoke sequence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from classroom_contract_smoke import HttpSmokeClient, run_smoke


FACADE_BASE_URL = "http://127.0.0.1:18082"
SYNC_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_STATE_FILE = Path("/private/tmp/classroom-local-demo-smoke-state.json")


class LocalDemoSmokeFailure(RuntimeError):
    """A local façade or service preflight did not satisfy the demo contract."""


def _request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(f"{base_url.rstrip('/')}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:  # nosec B310 - caller uses the fixed local URLs.
            status = response.status
            raw = response.read()
    except HTTPError as error:
        status = error.code
        raw = error.read()
    except (OSError, URLError) as error:
        raise LocalDemoSmokeFailure(f"local endpoint unavailable: {path}") from error
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise LocalDemoSmokeFailure(f"local endpoint returned invalid JSON: {path}") from error
    if not isinstance(decoded, dict):
        raise LocalDemoSmokeFailure(f"local endpoint returned non-object JSON: {path}")
    return status, decoded


def _require_status(
    base_url: str,
    method: str,
    path: str,
    expected_status: int,
    *,
    token: str | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    status, body = _request_json(base_url, method, path, token=token, payload=payload)
    if status != expected_status:
        raise LocalDemoSmokeFailure(f"local endpoint returned unexpected status: {path}")
    return body


def _require_login(base_url: str, username: str, password: str, expected_token: str) -> None:
    body = _require_status(
        base_url,
        "POST",
        "/v1/login",
        200,
        payload={"username": username, "password": password},
    )
    if body.get("token") != expected_token:
        raise LocalDemoSmokeFailure(f"{username} login returned an unexpected token")


def run_local_demo_smoke(
    *,
    facade_base_url: str = FACADE_BASE_URL,
    sync_base_url: str = SYNC_BASE_URL,
    state_file: Path = DEFAULT_STATE_FILE,
    contract_runner: Callable[..., dict[str, object]] = run_smoke,
    expected_teacher_token: str = "teacher-token",
) -> dict[str, object]:
    """Check the façade boundary then reuse the existing teacher/student contract flow."""

    health = _require_status(facade_base_url, "GET", "/health/live", 200)
    if health.get("status") != "live":
        raise LocalDemoSmokeFailure("local façade health payload is invalid")
    _require_login(facade_base_url, "teacher001", "local-demo-teacher", expected_teacher_token)
    _require_login(facade_base_url, "student001", "local-demo-student", "student001-token")
    denied = _require_status(
        facade_base_url,
        "GET",
        "/v1/spaces/course-001/algorithm_development",
        403,
        token="student002-token",
    )
    if denied.get("detail") != "demo_course_access_denied":
        raise LocalDemoSmokeFailure("cross-course student was not denied by local façade")
    _require_status(sync_base_url, "GET", "/health/ready", 200)

    try:
        contract_runner(
            HttpSmokeClient(sync_base_url),
            state_file=state_file,
            now=datetime.now(UTC),
            repeat_existing=False,
        )
        result = contract_runner(
            HttpSmokeClient(sync_base_url),
            state_file=state_file,
            now=datetime.now(UTC),
            repeat_existing=True,
        )
    finally:
        state_file.unlink(missing_ok=True)
    if result.get("phase") != "submitted":
        raise LocalDemoSmokeFailure("local classroom contract did not submit a brief")
    return result


def main() -> int:
    result = run_local_demo_smoke()
    print(json.dumps({"status": "ok", "phase": result["phase"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
