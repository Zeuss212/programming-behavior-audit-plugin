from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import contextmanager
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


ROOT = Path(__file__).resolve().parents[2]
FACADE_PATH = ROOT / "deploy" / "classroom" / "local-demo" / "fincolab_demo.py"


def _load_facade_module():
    spec = importlib.util.spec_from_file_location("local_classroom_demo_facade", FACADE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DemoClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    def request(
        self,
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
            body = json.dumps(payload).encode("utf-8")
        request = Request(f"{self._base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=3) as response:  # nosec B310 - localhost test server.
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))


@contextmanager
def demo_client() -> Iterator[DemoClient]:
    facade = _load_facade_module()
    server = ThreadingHTTPServer(("127.0.0.1", 0), facade.DemoFincolabHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield DemoClient(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_teacher_login_returns_usable_roster_and_parent_project():
    with demo_client() as client:
        status, login = client.request(
            "POST",
            "/v1/login",
            payload={"username": "teacher001", "password": "local-demo-teacher"},
        )
        assert status == HTTPStatus.OK
        assert login["token"] == "teacher-token"

        status, roster = client.request(
            "GET",
            "/v1/organizations/local-org/spaces/course-001/users",
            token="teacher-token",
        )
        assert status == HTTPStatus.OK
        assert [member["username"] for member in roster["data"]] == ["teacher001", "student001"]

        status, parent = client.request(
            "GET",
            "/v1/spaces/course-001/algorithm_development/parent-experiment-001",
            token="teacher-token",
        )
        assert status == HTTPStatus.OK
        assert parent["id"] == "parent-experiment-001"
        assert parent["username"] == "teacher001"


def test_student_receives_only_own_child_and_loopback_workbench():
    with demo_client() as client:
        status, projects = client.request(
            "GET",
            "/v1/spaces/course-001/algorithm_development",
            token="student001-token",
        )
        assert status == HTTPStatus.OK
        assert [project["id"] for project in projects["data"]] == ["child-experiment-001"]

        status, workbench = client.request(
            "GET",
            "/v1/spaces/course-001/algorithm_development/child-experiment-001/workbench/workbench-student001",
            token="student001-token",
        )
        assert status == HTTPStatus.OK
        assert workbench["workbench_status"] == "RUNNING"
        assert workbench["jupyter_url"] == "http://127.0.0.1:8888/lab"


@pytest.mark.parametrize(
    ("path", "token", "expected_status", "expected_detail"),
    [
        (
            "/v1/spaces/course-001/algorithm_development",
            "student002-token",
            HTTPStatus.FORBIDDEN,
            "demo_course_access_denied",
        ),
        (
            "/v1/user/info",
            "not-a-demo-token",
            HTTPStatus.UNAUTHORIZED,
            "demo_token_rejected",
        ),
    ],
)
def test_facade_rejects_cross_course_and_unknown_bearers(
    path: str,
    token: str,
    expected_status: int,
    expected_detail: str,
):
    with demo_client() as client:
        status, payload = client.request("GET", path, token=token)
        assert status == expected_status
        assert payload == {"detail": expected_detail}


def test_facade_rejects_invalid_login_and_preserves_legacy_student_token_alias():
    with demo_client() as client:
        status, payload = client.request(
            "POST",
            "/v1/login",
            payload={"username": "student001", "password": "incorrect"},
        )
        assert status == HTTPStatus.UNAUTHORIZED
        assert payload == {"detail": "demo_login_rejected"}

        status, user = client.request("GET", "/v1/user/info", token="student-token")
        assert status == HTTPStatus.OK
        assert user["username"] == "student001"
