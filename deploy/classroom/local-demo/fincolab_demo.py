"""Local-only FinColab façade for the classroom interactive demo.

It deliberately exposes only the browser and classroom-sync reads used by the
demo.  All identities, passwords and tokens are disposable local fixtures.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


LOCAL_ORGANIZATION_ID = "local-org"
NEGATIVE_ORGANIZATION_ID = "local-org-negative"
COURSE_ID = "course-001"
NEGATIVE_COURSE_ID = "course-002"
PARENT_ALGORITHM_ID = "parent-experiment-001"
CHILD_ALGORITHM_ID = "child-experiment-001"
WORKBENCH_ID = "workbench-student001"


@dataclass(frozen=True)
class DemoUser:
    user_id: str
    username: str
    password: str
    token: str
    role_name: str
    organization_id: str
    space_id: str


USERS = {
    "teacher001": DemoUser(
        "teacher001",
        "teacher001",
        "local-demo-teacher",
        "teacher-token",
        "teacher",
        LOCAL_ORGANIZATION_ID,
        COURSE_ID,
    ),
    "student001": DemoUser(
        "student001",
        "student001",
        "local-demo-student",
        "student001-token",
        "student",
        LOCAL_ORGANIZATION_ID,
        COURSE_ID,
    ),
    "student002": DemoUser(
        "student002",
        "student002",
        "local-demo-student2",
        "student002-token",
        "student",
        NEGATIVE_ORGANIZATION_ID,
        NEGATIVE_COURSE_ID,
    ),
}
TOKEN_ALIASES = {"student-token": USERS["student001"]}


def authenticate_bearer(token: str) -> DemoUser | None:
    """Resolve only a fixed local bearer value; no remote auth is involved."""

    for user in USERS.values():
        if user.token == token:
            return user
    return TOKEN_ALIASES.get(token)


def visible_spaces(user: DemoUser) -> list[dict[str, object]]:
    """Return the one course this local identity is allowed to see."""

    return [
        {
            "id": user.organization_id,
            "short_name": "本地课堂演示" if user.space_id == COURSE_ID else "本地课堂负例",
            "spaces": [
                {
                    "id": user.space_id,
                    "short_name": "Python 字典课堂" if user.space_id == COURSE_ID else "隔离课程",
                }
            ],
        }
    ]


def _user_info(user: DemoUser) -> dict[str, object]:
    return {
        "company_name": "本地课堂演示",
        "is_frozen": False,
        "timezone": "Asia/Shanghai",
        "agreement": True,
        "phone_number": "",
        "username": user.username,
        "locale": "zh-CN",
        "created_at": 0,
        "updated_at": 0,
        "email": f"{user.username}@local.demo.invalid",
        "platform_role": user.role_name,
        "oauth2_info_completed": True,
        "enabled": True,
        "id": user.user_id,
    }


def _space_members(space_id: str) -> list[dict[str, str]]:
    members = [user for user in USERS.values() if user.space_id == space_id]
    return [
        {
            "id": user.user_id,
            "username": user.username,
            "role_name": user.role_name,
            "role_id": 2 if user.role_name == "teacher" else 3,
        }
        for user in members
    ]


def _parent_project() -> dict[str, object]:
    return {
        "id": PARENT_ALGORITHM_ID,
        "name": "字典读取课堂练习",
        "username": "teacher001",
        "description": "教师本地课堂实验",
        "project_type": "notebook",
        "workbench_status": "NOT_STARTED",
    }


def _student_project() -> dict[str, object]:
    return {
        "id": CHILD_ALGORITHM_ID,
        "name": "exp-student001-a1b2",
        "username": "student001",
        "description": f"[FINCOLAB_PARENT_PROJECT_ID:{PARENT_ALGORITHM_ID}] 本地课堂学生任务",
        "project_type": "notebook",
        "workbench_id": WORKBENCH_ID,
        "workbench_status": "RUNNING",
        "jupyter_url": "http://127.0.0.1:8888/lab",
    }


def _pagination(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"data": rows, "current_page": 1, "total_page": 1, "total_count": len(rows)}


class DemoFincolabHandler(BaseHTTPRequestHandler):
    """Route only local fixture data and never log request headers."""

    server_version = "local-classroom-demo-fincolab"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health/live":
            self._reply(HTTPStatus.OK, {"status": "live"})
            return

        user = self._require_user()
        if user is None:
            return

        if parsed.path == "/v1/user/info":
            self._reply(HTTPStatus.OK, _user_info(user))
            return
        if parsed.path == "/v1/organizations/spaces":
            self._reply(HTTPStatus.OK, visible_spaces(user))
            return
        if parsed.path == "/v1/quota/spec/all":
            self._reply(
                HTTPStatus.OK,
                {"data": [{"id": 1, "name": "本地 CPU", "desc": "本地课堂演示资源", "cpu": 2, "memory": 4, "gpu": 0}]},
            )
            return

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 6 and parts[:3] == ["v1", "organizations", user.organization_id]:
            if parts[3:5] == ["spaces", user.space_id] and parts[5] == "users":
                self._reply(HTTPStatus.OK, _pagination(_space_members(user.space_id)))
                return
        if len(parts) >= 5 and parts[:3] == ["v1", "organizations", "local-org"]:
            self._reply(HTTPStatus.FORBIDDEN, {"detail": "demo_course_access_denied"})
            return

        if len(parts) >= 4 and parts[:3] == ["v1", "spaces", COURSE_ID] and parts[3] == "algorithm_development":
            if not self._require_course_access(user):
                return
            self._handle_course_algorithm_get(user, parts[4:])
            return
        if len(parts) >= 3 and parts[:3] == ["v1", "spaces", COURSE_ID]:
            self._reply(HTTPStatus.FORBIDDEN, {"detail": "demo_course_access_denied"})
            return
        if len(parts) >= 3 and parts[:3] == ["v1", "spaces", NEGATIVE_COURSE_ID]:
            if user.space_id != NEGATIVE_COURSE_ID:
                self._reply(HTTPStatus.FORBIDDEN, {"detail": "demo_course_access_denied"})
            else:
                self._reply(HTTPStatus.OK, _pagination([]))
            return

        self._reply(HTTPStatus.NOT_FOUND, {"detail": "demo_endpoint_not_found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/v1/login":
            self._login()
            return
        if parsed.path == "/v1/logout":
            if self._require_user() is not None:
                self._reply(HTTPStatus.OK, {})
            return
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, {"detail": "demo_method_not_allowed"})

    def do_PUT(self) -> None:  # noqa: N802
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, {"detail": "demo_method_not_allowed"})

    def do_PATCH(self) -> None:  # noqa: N802
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, {"detail": "demo_method_not_allowed"})

    def do_DELETE(self) -> None:  # noqa: N802
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, {"detail": "demo_method_not_allowed"})

    def _login(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._reply(HTTPStatus.BAD_REQUEST, {"detail": "demo_login_payload_invalid"})
            return
        if not isinstance(payload, dict):
            self._reply(HTTPStatus.BAD_REQUEST, {"detail": "demo_login_payload_invalid"})
            return
        username = payload.get("username")
        password = payload.get("password")
        user = USERS.get(username) if isinstance(username, str) else None
        if user is None or password != user.password:
            self._reply(HTTPStatus.UNAUTHORIZED, {"detail": "demo_login_rejected"})
            return
        self._reply(
            HTTPStatus.OK,
            {
                "token": user.token,
                "refresh_token": f"{user.username}-refresh-token",
                "username": user.username,
                "real_name": user.username,
                "role": user.role_name,
            },
        )

    def _handle_course_algorithm_get(self, user: DemoUser, tail: list[str]) -> None:
        if not tail:
            # The student UI matches its private child against the teacher's
            # parent metadata.  Listing the parent permits that local
            # association; its detail endpoint remains teacher-only below.
            rows = [_parent_project(), _student_project()]
            self._reply(HTTPStatus.OK, _pagination(rows))
            return
        algorithm_id = tail[0]
        if algorithm_id == PARENT_ALGORITHM_ID and user.role_name == "teacher" and len(tail) == 1:
            self._reply(HTTPStatus.OK, _parent_project())
            return
        if algorithm_id != CHILD_ALGORITHM_ID or user.username != "student001":
            self._reply(HTTPStatus.FORBIDDEN, {"detail": "demo_resource_access_denied"})
            return
        if len(tail) == 1:
            self._reply(HTTPStatus.OK, _student_project())
            return
        if tail == [CHILD_ALGORITHM_ID, "workbench", WORKBENCH_ID]:
            self._reply(
                HTTPStatus.OK,
                {
                    "id": WORKBENCH_ID,
                    "username": "student001",
                    "workbench_status": "RUNNING",
                    "jupyter_url": "http://127.0.0.1:8888/lab",
                    "container_resource_json": {"cpu": 2, "memory": 4, "gpu": 0},
                },
            )
            return
        self._reply(HTTPStatus.NOT_FOUND, {"detail": "demo_endpoint_not_found"})

    def _require_user(self) -> DemoUser | None:
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        token = authorization.removeprefix(prefix) if authorization.startswith(prefix) else ""
        user = authenticate_bearer(token)
        if user is None:
            self._reply(HTTPStatus.UNAUTHORIZED, {"detail": "demo_token_rejected"})
        return user

    def _require_course_access(self, user: DemoUser) -> bool:
        if user.space_id == COURSE_ID:
            return True
        self._reply(HTTPStatus.FORBIDDEN, {"detail": "demo_course_access_denied"})
        return False

    def _reply(self, status: HTTPStatus, payload: dict[str, Any] | list[dict[str, object]]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        """Do not leak fixture bearer/password values through HTTP request logs."""


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", 8080), DemoFincolabHandler).serve_forever()


if __name__ == "__main__":
    main()
