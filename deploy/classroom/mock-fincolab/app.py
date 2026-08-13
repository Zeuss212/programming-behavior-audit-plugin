"""Read-only FinColab contract double used only by local Docker Compose tests.

The identities and bearer values below are intentionally non-production test
fixtures. This process never accepts writes and never acts as an auth server
outside the private Compose network.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


USERS = {
    "teacher-token": {"id": "teacher001", "username": "teacher001"},
    "student-token": {"id": "student001", "username": "student001"},
    "student002-token": {"id": "student002", "username": "student002"},
}
SPACE_MEMBERS = [
    {"id": "teacher001", "username": "teacher001", "role_name": "teacher"},
    {"id": "student001", "username": "student001", "role_name": "student"},
]


class MockFincolabHandler(BaseHTTPRequestHandler):
    """Serve only the trusted read APIs exercised by the sync service."""

    server_version = "classroom-mock-fincolab"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
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
                {"data": SPACE_MEMBERS, "current_page": 1, "total_page": 1},
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
                    "data": [
                        {
                            "id": "child-experiment-001",
                            "username": "student001",
                            "description": "[FINCOLAB_PARENT_PROJECT_ID:parent-experiment-001]",
                            "workbench_id": "workbench-student-001",
                        }
                    ],
                    "current_page": 1,
                    "total_page": 1,
                },
            )
            return
        self._reply(HTTPStatus.NOT_FOUND, {"detail": "test endpoint not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, {"detail": "read-only test service"})

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, {"detail": "read-only test service"})

    def _authenticated_user(self) -> dict[str, str] | None:
        prefix = "Bearer "
        authorization = self.headers.get("Authorization", "")
        token = authorization.removeprefix(prefix) if authorization.startswith(prefix) else ""
        user = USERS.get(token)
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
