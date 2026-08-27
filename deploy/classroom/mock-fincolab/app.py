"""Read-only FinColab contract double used only by local Docker Compose tests.

The identities and bearer values below are intentionally non-production test
fixtures. This process never accepts writes and never acts as an auth server
outside the private Compose network.
"""

from __future__ import annotations

import json
import base64
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


def student_count() -> int:
    """Return a bounded local-only roster size for the Compose test double."""

    raw_value = os.environ.get("CLASSROOM_MOCK_STUDENT_COUNT", "1")
    try:
        count = int(raw_value)
    except ValueError:
        return 1
    return count if 1 <= count <= 100 else 1


def student_id(index: int) -> str:
    return f"student{index:03d}"


def student_binding_description(student: str) -> str:
    binding = {"parent_algorithm_id": "parent-experiment-001", "space_id": "course-001", "student_id": student, "student_username": student}
    payload = base64.urlsafe_b64encode(json.dumps(binding, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")).rstrip(b"=").decode("ascii")
    return f"[FINCOLAB_PARENT_PROJECT_ID:parent-experiment-001][FINCOLAB_STUDENT_BINDING_V1:{payload}]"


def users() -> dict[str, dict[str, str]]:
    roster = {"teacher-token": {"id": "teacher001", "username": "teacher001"}}
    for index in range(1, student_count() + 1):
        identifier = student_id(index)
        roster[f"{identifier}-token"] = {"id": identifier, "username": identifier}
    # Keep the original single-student fixture valid for the contract smoke.
    roster["student-token"] = roster["student001-token"]
    return roster


def space_members() -> list[dict[str, str]]:
    return [
        {"id": "teacher001", "username": "teacher001", "role_name": "teacher"},
        *[
            {"id": student_id(index), "username": student_id(index), "role_name": "student"}
            for index in range(1, student_count() + 1)
        ],
    ]


def student_children() -> list[dict[str, str]]:
    return [
        {
            "id": f"child-experiment-{index:03d}",
            "username": student_id(index),
            "description": student_binding_description(student_id(index)),
            "workbench_id": f"workbench-{student_id(index)}",
        }
        for index in range(1, student_count() + 1)
    ]


class MockFincolabHandler(BaseHTTPRequestHandler):
    """Serve only the trusted read APIs exercised by the sync service."""

    server_version = "classroom-mock-fincolab"
    sys_version = ""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health/live":
            self._reply(HTTPStatus.OK, {"status": "live"})
            return
        user = self._authenticated_user()
        if user is None:
            return
        if parsed.path == "/v1/user/info":
            self._reply(HTTPStatus.OK, user)
            return
        if parsed.path == "/v1/organizations/local-org/spaces/course-001/users":
            self._reply(
                HTTPStatus.OK,
                {"data": space_members(), "current_page": 1, "total_page": 1},
            )
            return
        if parsed.path == "/v1/spaces/course-001/algorithm_development/parent-experiment-001":
            self._reply(
                HTTPStatus.OK,
                {"id": "parent-experiment-001", "username": "teacher001"},
            )
            return
        if parsed.path == "/v1/spaces/course-001/algorithm_development":
            self._reply(
                HTTPStatus.OK,
                {
                    "data": student_children(),
                    "current_page": 1,
                    "total_page": 1,
                },
            )
            return
        self._reply(HTTPStatus.NOT_FOUND, {"detail": "test endpoint not found"})

    def do_POST(self) -> None:
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, {"detail": "read-only test service"})

    def do_PUT(self) -> None:
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, {"detail": "read-only test service"})

    def _authenticated_user(self) -> dict[str, str] | None:
        prefix = "Bearer "
        authorization = self.headers.get("Authorization", "")
        token = authorization.removeprefix(prefix) if authorization.startswith(prefix) else ""
        user = users().get(token)
        if user is None:
            self._reply(HTTPStatus.UNAUTHORIZED, {"detail": "test token rejected"})
            return None
        return user

    def _reply(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        """Avoid emitting request headers (and therefore test bearer values)."""


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), MockFincolabHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
