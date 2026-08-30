"""Local-only FinColab façade for the classroom interactive demo.

It deliberately exposes only the browser and classroom-sync reads used by the
demo.  All identities, passwords and tokens are disposable local fixtures.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

LOCAL_ORGANIZATION_ID = "local-org"
NEGATIVE_ORGANIZATION_ID = "local-org-negative"
COURSE_ID = "course-001"
NEGATIVE_COURSE_ID = "course-002"
PARENT_ALGORITHM_ID = "parent-experiment-001"
SEQUENCE_LIST_PARENT_ALGORITHM_ID = "sequence-list-experiment-001"
LINKED_LIST_PARENT_ALGORITHM_ID = "linked-list-experiment-002"
CHILD_ALGORITHM_ID = "child-experiment-001"
WORKBENCH_ID = "workbench-student001"

PARENT_PROJECT_NAMES = {
    PARENT_ALGORITHM_ID: "字典读取课堂练习",
    SEQUENCE_LIST_PARENT_ALGORITHM_ID: "顺序表基本操作",
    LINKED_LIST_PARENT_ALGORITHM_ID: "链表尾插与逆置",
}

AI_FRAMEWORKS = [
    {
        "id": "framework-behavior",
        "name": "PyTorch-2.5.1-JupyterLab4-BehaviorAudit-0.2.2",
        "frame_type": "PyTorch",
    }
]

CODE_TEMPLATES_BY_FRAMEWORK = {
    "framework-behavior": [
        {
            "id": "template-behavior",
            "name": "BehaviorAudit starter",
            "version": "0.2.2",
        }
    ]
}

MATERIAL_BUNDLE_RESOURCES = {
    SEQUENCE_LIST_PARENT_ALGORITHM_ID: ("materials", "sequence-list", "bundle.json"),
    LINKED_LIST_PARENT_ALGORITHM_ID: ("materials", "linked-list", "bundle.json"),
}

# The private adapter validates these artifact bytes against the sealed hashes.
# They are intentionally embedded so the demo image never contains raw C++ paths.
MATERIAL_SOURCE_BASE64 = {
    SEQUENCE_LIST_PARENT_ALGORITHM_ID: (
        "Lyq/zrrzz7DM4g0KDQoJYS4JzerJxrTmtKLV+9DNyv2+3bXEy7PQ8rHttcTA4Lao0uWjrM3q"
        "yca7+bG+tcSzydSxuq/K/aOssqK4+LP20tTPwrmmxNy6r8r9tcS+38zlyrXP1qGjDQoJCWku"
        "CbTTy7PQ8rHt1tDJvrP9vt/T0Nfu0KHWtbXE1KrL2LKi08m6r8r9t7W72LG7yb7UqsvYtcTW"
        "taOsv9Wz9rXEzrvWw9PJ1+6689K7uPbUqsvYzO6yuQ0KCQkJaW50IGRlbGV0ZW1pbigpOw0K"
        "DQoJCWlpLgm008uz0PKx7dbQyb6z/dPruPi2qHjP4LXItcTL+dPQ1KrL2A0KCQkJdm9pZCBk"
        "ZWxldGVTYW1lKGludCB4KTsNCg0KCQlpaWkutNPLs9Dyse3W0Mm+s/3G5Na11Nq4+LaoIHPT"
        "63TWrrzko6hzIDwgdKOptcTL+dPQ1KrL2Cyyu7D8wKhzus10DQoJCQl2b2lkIGRlbGV0ZVNv"
        "bWUoaW50IHMsIGludCB0KTsNCiovDQojaW5jbHVkZSA8aW9zdHJlYW0+DQp1c2luZyBuYW1l"
        "c3BhY2Ugc3RkOw0KY2xhc3MgIFNlcUFycmF5ICAvL8uz0PKx7Q0Kew0KcHJpdmF0ZToNCiAg"
        "ICBpbnQqIGFycjsgLy/K/dfptcTG8Mq8tdjWtw0KICAgIGludCBOOy8vyv3X6bnmxKMNCiAg"
        "ICBpbnQgbjsvL8r91+m1scew1KrL2Lj2yv0NCnB1YmxpYzoNCiAgICBTZXFBcnJheShpbnQg"
        "Tk49MTApOw0KICAgIH5TZXFBcnJheSgpOw0KICAgIGJvb2wgaW5zZXJ0RWxlbWVudChpbnQg"
        "dmFsdWUpOy8vz/LLs9Dyse3W0LLlyOt2YWx1ZSzI57n7s8m5pre1u9h0cnVlo6y38dTyt7W7"
        "2GZhbHNlDQogICAgaW50IGRlbGV0ZW1pbigpOw0KICAgIHZvaWQgZGVsZXRlU2FtZShpbnQg"
        "eCk7DQogICAgdm9pZCBkZWxldGVTb21lKGludCBzLCBpbnQgdCk7DQogICAgdm9pZCBwcmlu"
        "dCgpOyAvL8rks/bLs9Dyse21xMr9vt0NCn07DQovL8fruPiz9rj3uPazydSxuq/K/bXEvt/M"
        "5cq1z9YNClNlcUFycmF5OjpTZXFBcnJheShpbnQgTk4pDQp7DQogICAgLy90b2RvLcfruPiz"
        "9r7fzOXKtc/WtPrC6w0KDQp9DQpTZXFBcnJheTo6flNlcUFycmF5KCkNCnsNCiAgIC8vdG9k"
        "by3H67j4s/a+38zlyrXP1rT6wusNCn0NCmJvb2wgU2VxQXJyYXk6Omluc2VydEVsZW1lbnQo"
        "aW50IHZhbHVlKQ0Kew0KICAgIC8vdG9kby3H67j4s/a+38zlyrXP1rT6wusNCn0NCmludCBT"
        "ZXFBcnJheTo6ZGVsZXRlbWluKCkNCnsNCiAgLy90b2RvLcfruPiz9r7fzOXKtc/WtPrC6w0K"
        "fQ0Kdm9pZCAgU2VxQXJyYXk6OmRlbGV0ZVNhbWUoaW50IHgpDQp7DQogICAgLy90b2RvLcfr"
        "uPiz9r7fzOXKtc/WtPrC6w0KfQ0Kdm9pZCAgU2VxQXJyYXk6OmRlbGV0ZVNvbWUoaW50IHMs"
        "aW50IHQpDQp7DQogICAgLy90b2RvLcfruPiz9r7fzOXKtc/WtPrC6w0KfQ0Kdm9pZCBTZXFB"
        "cnJheTo6cHJpbnQoKQ0Kew0KLy90b2RvLcfruPiz9r7fzOXKtc/WtPrC6w0KDQp9DQoNCi8v"
        "x+uyu9Kq0N64xM/Cw+ZtYWluuq/K/bXEuq/K/czlDQppbnQgbWFpbigpDQp7DQogICAgLy8g"
        "biCx7cq+0qrK5MjrtcTK/b7d1KrL2Lj2yv2jrCBtaW52YWy8x8K8yb6z/bXE1+7Qoda1o6wN"
        "CiAgICAvL3NhbWV2YWx1ZbHtyr7WuLaoyb6z/bXEyv2+3aOsc6GidLHtyr7Sqsm+s/21xMr9"
        "vt21xLe2zqdzPHQNCiAgICBpbnQgbixtaW52YWwsc2FtZXZhbHVlLCBzLCB0Ow0KDQogICAg"
        "U2VxQXJyYXkgYSgyMCk7DQogICAgY2luPj5uOw0KICAgIGZvciAoaW50IGkgPSAwOyBpIDwg"
        "bjsgaSsrKSB7DQogICAgICAgIGNpbiA+PiB2YWx1ZTsNCiAgICAgICAgYS5pbnNlcnRFbGVt"
        "ZW50KHZhbHVlKTsNCiAgICB9DQoNCiAgICBjb3V0IDw8ICLLs9Dyse3K/b7dzqo6IjsNCiAg"
        "ICBhLnByaW50KCk7DQogICAgbWludmFsID0gYS5kZWxldGVtaW4oKTsNCiAgICBjb3V0IDw8"
        "ICLJvrP91+7Qoda1uvPOqjoiOw0KICAgIGEucHJpbnQoKTsNCiAgICBjb3V0IDw8ICLX7tCh"
        "1rU6IiA8PCBtaW52YWwgPDwgZW5kbDsNCiAgICBjaW4gPj4gc2FtZXZhbHVlOw0KICAgIGEu"
        "ZGVsZXRlU2FtZShzYW1ldmFsdWUpOw0KICAgIGNvdXQgPDwgIsm+s/3P4M2s1rW6886qOiI7"
        "DQogICAgYS5wcmludCgpOw0KICAgIGNpbiA+PiBzID4+IHQ7DQogICAgYS5kZWxldGVTb21l"
        "KHMsIHQpOw0KICAgIGNvdXQgPDwgIsm+s/3WuLaot7bOp8r91rW6886qOiI7DQogICAgYS5w"
        "cmludCgpOw0KICAgIHJldHVybiAwOw0KfQ0K"
    ),
    LINKED_LIST_PARENT_ALGORITHM_ID: (
        "Lyror77lkI7kuaDpopgNCgnljZXpk77ooajmk43kvZzvvJoNCgnlrozlloTkuIvpnaLnmoTl"
        "uKblpLTnu5PngrnnmoTljZXlkJHpk77ooajnsbvnmoTnm7jlhbPmiJDlkZjlh73mlbDvvIwN"
        "CgkoMSnlkJHpk77ooajlsL7pg6jmj5LlhaXnmoTmiJDlkZjlh73mlbANCgkgICB2b2lkIGlu"
        "c2VydFRvVGFpbChpbnQgdmFsKTsNCgkoMinlhpnlh7rlsIbpk77ooajlgJLnva7nmoTmiJDl"
        "kZjlh73mlbANCgkJdm9pZCBSZXZlcnNlKCk7DQoqLw0KI2luY2x1ZGUgPGlvc3RyZWFtPg0K"
        "dXNpbmcgbmFtZXNwYWNlIHN0ZDsNCmNsYXNzIE5vZGUgLy/pk77ooajnmoTnu5Pngrnlrprk"
        "uYkNCnsNCnB1YmxpYzoNCiAgICBOb2RlKGludCB4KQ0KICAgIHsNCiAgICAgICAgZGF0YSA9"
        "IHg7DQogICAgICAgIG5leHQgPSBOVUxMOw0KICAgIH0NCiAgICBpbnQgZGF0YTsNCiAgICBO"
        "b2RlKiBuZXh0Ow0KfTsNCmNsYXNzIE1MaXN0IC8v5bim5pyJ5aS057uT54K555qE5Y2V5ZCR"
        "6ZO+6KGo57G75a6a5LmJDQp7DQpwcml2YXRlOg0KICAgIE5vZGUqIGhlYWQ7Ly/mjIflkJHl"
        "pLTnu5PngrnvvIzkuI3mmK/lrp7pmYXnmoTmlbDmja7nu5PngrkNCg0KcHVibGljOg0KICAg"
        "IE1MaXN0KCk7DQogICAgfk1MaXN0KCk7DQogICAgdm9pZCBpbnNlcnRUb1RhaWwoaW50IHZh"
        "bCk7Ly9UT0RPMTrlnKjnsbvlpJbnu5nlh7ror6Xlh73mlbDlrp7njrDigJTigJTlkJHlsL7p"
        "g6jmj5LlhaXmlbDmja52YWwNCiAgICB2b2lkIFJldmVyc2UoKTsvL1RPRE8yOuWcqOexu+Wk"
        "lue7meWHuuivpeWHveaVsOWunueOsOKAlOKAlOe/u+i9rOmTvuihqA0KICAgIHZvaWQgcHJp"
        "bnQoKTsNCn07DQovL+S4jemcgOimgeaUueWPmOS4i+mdoueahOaehOmAoOWHveaVsA0KTUxp"
        "c3Q6Ok1MaXN0KCkNCnsNCiAgICBoZWFkID0gbmV3IE5vZGUoMCk7Ly9oZWFkIOaMh+WQkee"
        "ahOaYr+WktOe7k+eCuQ0KfQ0KLy/kuI3pnIDopoHmlLnlj5jkuIvpnaLnmoTmnpDmnoTlh73"
        "mlbANCk1MaXN0Ojp+TUxpc3QoKQ0Kew0KICAgIE5vZGUqIHRlbXAgPSBoZWFkOw0KICAgIHdo"
        "aWxlKHRlbXApIC8v6YCQ5Liq6YeK5pS+57uT54K556m66Ze0DQogICAgew0KICAgICAgICBo"
        "ZWFkID0gaGVhZCAtPm5leHQ7DQogICAgICAgIGRlbGV0ZSB0ZW1wOw0KICAgICAgICB0ZW1w"
        "ID0gaGVhZDsNCiAgICB9DQogICAgaGVhZCA9IE5VTEw7DQp9DQoNCi8v5LiN6KaB5pS55Y+"
        "Y5LiL6Z2i55qEcHJpbnTlh73mlbANCnZvaWQgTUxpc3Q6OnByaW50KCkNCnsNCiAgICBOb2Rl"
        "KiBwID0gaGVhZC0+bmV4dDsNCiAgICB3aGlsZSAocCkgew0KICAgICAgICBjb3V0IDw8IHAt"
        "PmRhdGE8PCAiICI7DQogICAgICAgIHAgPSBwIC0+bmV4dDsNCiAgICB9DQp9DQovL+S4jeim"
        "geaUueWPmOS4i+mdoueahG1haW7lh73mlbANCmludCBtYWluKCkNCnsNCiAgICBNTGlzdCBs"
        "dDsvL+WIm+W7uumTvuihqOWvueixoSBsdA0KICAgIGludCBOdW07Ly9OdW0g6KGo56S66KaB"
        "6L6T5YWl55qE5YWD57Sg55qE5Liq5pWwDQogICAgY2luID4+IE51bTsNCiAgICBmb3IgKGlud"
        "CBpID0gMDsgaSA8IE51bTsgaSsrKSB7DQogICAgICAgIGludCB2YWw7DQogICAgICAgIGNpbi"
        "A+PiB2YWw7DQogICAgICAgIGx0Lmluc2VydFRvVGFpbCh2YWwpOw0KICAgIH0NCiAgICBjb3"
        "V0IDw8ICLlgJLnva7liY3kuLrvvJoiOw0KICAgIGx0LnByaW50KCk7DQogICAgY291dCA8PC"
        "BlbmRsOw0KICAgIGx0LlJldmVyc2UoKTsNCiAgICBjb3V0IDw8ICLlgJLnva7lkI7kuLrvvJ"
        "oiOw0KICAgIGx0LnByaW50KCk7DQogICAgY291dCA8PCBlbmRsOw0KICAgIHJldHVybiAwOw0K"
        "fQ0K"
    ),
}


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
    "1": DemoUser(
        "teacher001",
        "teacher001",
        "1",
        "teacher-token",
        "teacher",
        LOCAL_ORGANIZATION_ID,
        COURSE_ID,
    ),
    "2": DemoUser(
        "student001",
        "student001",
        "2",
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


def _local_student_count() -> int:
    """Keep the local-only concurrency fixture bounded and deterministic."""

    try:
        requested = int(os.environ.get("CLASSROOM_MOCK_STUDENT_COUNT", "1"))
    except ValueError:
        return 1
    return min(100, max(1, requested))


for _index in range(3, _local_student_count() + 2):
    _student_id = f"student{_index:03d}"
    USERS[_student_id] = DemoUser(
        _student_id,
        _student_id,
        f"local-demo-{_student_id}",
        f"{_student_id}-token",
        "student",
        LOCAL_ORGANIZATION_ID,
        COURSE_ID,
    )

TOKEN_ALIASES = {"student-token": USERS["2"]}


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


def _parent_project(parent_algorithm_id: str = PARENT_ALGORITHM_ID) -> dict[str, object]:
    return {
        "id": parent_algorithm_id,
        "name": PARENT_PROJECT_NAMES[parent_algorithm_id],
        "username": "teacher001",
        "description": "教师本地课堂实验",
        "project_type": "notebook",
        "workbench_status": "NOT_STARTED",
    }


def _assessment_material_bundle(parent_algorithm_id: str) -> dict[str, object]:
    resource = MATERIAL_BUNDLE_RESOURCES[parent_algorithm_id]
    payload = json.loads(
        Path(__file__).resolve().parent.joinpath(*resource).read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("starter_source"), dict):
        raise TypeError("sealed material bundle is invalid")
    payload["starter_source"]["content_base64"] = MATERIAL_SOURCE_BASE64[parent_algorithm_id]
    return payload


def _child_algorithm_id(
    student_id: str,
    parent_algorithm_id: str = PARENT_ALGORITHM_ID,
) -> str:
    if parent_algorithm_id == PARENT_ALGORITHM_ID:
        return CHILD_ALGORITHM_ID if student_id == "student001" else f"child-experiment-{student_id}"
    return f"child-{parent_algorithm_id}-{student_id}"


def _workbench_id(
    student_id: str,
    parent_algorithm_id: str = PARENT_ALGORITHM_ID,
) -> str:
    if parent_algorithm_id == PARENT_ALGORITHM_ID:
        return WORKBENCH_ID if student_id == "student001" else f"workbench-{student_id}"
    return f"workbench-{parent_algorithm_id}-{student_id}"


def _course_students() -> list[DemoUser]:
    return [
        user
        for user in USERS.values()
        if user.role_name == "student" and user.space_id == COURSE_ID
    ]


def _student_project(
    student_id: str = "student001",
    parent_algorithm_id: str = PARENT_ALGORITHM_ID,
) -> dict[str, object]:
    child_algorithm_id = _child_algorithm_id(student_id, parent_algorithm_id)
    workbench_id = _workbench_id(student_id, parent_algorithm_id)
    return {
        "id": child_algorithm_id,
        "name": f"exp-{student_id}-a1b2",
        "username": student_id,
        "description": (
            f"[FINCOLAB_PARENT_PROJECT_ID:{parent_algorithm_id}] 本地课堂学生任务"
        ),
        "project_type": "notebook",
        "workbench_id": workbench_id,
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
        if parsed.path == "/v1/ai_framework":
            self._reply(HTTPStatus.OK, {"data": AI_FRAMEWORKS})
            return
        if parsed.path == "/v1/quota/spec/all":
            self._reply(
                HTTPStatus.OK,
                {
                    "data": [
                        {
                            "id": 1,
                            "name": "本地 CPU",
                            "desc": "本地课堂演示资源",
                            "cpu": 2,
                            "memory": 4,
                            "gpu": 0,
                        }
                    ]
                },
            )
            return

        parts = [part for part in parsed.path.split("/") if part]
        if parts == ["v1", "spaces", COURSE_ID, "template"]:
            if not self._require_course_access(user):
                return
            framework_id = parse_qs(parsed.query).get("framework_id", [""])[0]
            self._reply(
                HTTPStatus.OK,
                {"items": CODE_TEMPLATES_BY_FRAMEWORK.get(framework_id, [])},
            )
            return
        if len(parts) == 6 and parts[:3] == ["v1", "organizations", user.organization_id]:
            if parts[3:5] == ["spaces", user.space_id] and parts[5] == "users":
                self._reply(HTTPStatus.OK, _pagination(_space_members(user.space_id)))
                return
        if len(parts) >= 5 and parts[:3] == ["v1", "organizations", "local-org"]:
            self._reply(HTTPStatus.FORBIDDEN, {"detail": "demo_course_access_denied"})
            return

        if (
            len(parts) >= 4
            and parts[:3] == ["v1", "spaces", COURSE_ID]
            and parts[3] == "algorithm_development"
        ):
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
            visible_students = _course_students() if user.role_name == "teacher" else [user]
            rows = [
                *[_parent_project(parent_id) for parent_id in PARENT_PROJECT_NAMES],
                *[
                    _student_project(student.username, parent_id)
                    for student in visible_students
                    for parent_id in PARENT_PROJECT_NAMES
                ],
            ]
            self._reply(HTTPStatus.OK, _pagination(rows))
            return
        algorithm_id = tail[0]
        if algorithm_id in PARENT_PROJECT_NAMES:
            if user.role_name != "teacher":
                self._reply(HTTPStatus.FORBIDDEN, {"detail": "demo_resource_access_denied"})
                return
            if len(tail) == 1:
                self._reply(HTTPStatus.OK, _parent_project(algorithm_id))
                return
            if (
                tail == [algorithm_id, "assessment_materials"]
                and algorithm_id in MATERIAL_BUNDLE_RESOURCES
            ):
                self._reply(HTTPStatus.OK, _assessment_material_bundle(algorithm_id))
                return
            self._reply(HTTPStatus.NOT_FOUND, {"detail": "demo_endpoint_not_found"})
            return
        student_context = next(
            (
                (candidate, parent_id)
                for candidate in _course_students()
                for parent_id in PARENT_PROJECT_NAMES
                if _child_algorithm_id(candidate.username, parent_id) == algorithm_id
            ),
            None,
        )
        if student_context is None or user.username != student_context[0].username:
            self._reply(HTTPStatus.FORBIDDEN, {"detail": "demo_resource_access_denied"})
            return
        student, parent_id = student_context
        if len(tail) == 1:
            self._reply(HTTPStatus.OK, _student_project(student.username, parent_id))
            return
        workbench_id = _workbench_id(student.username, parent_id)
        if tail == [algorithm_id, "workbench", workbench_id]:
            self._reply(
                HTTPStatus.OK,
                {
                    "id": workbench_id,
                    "username": student.username,
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
