from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
FACADE_PATH = ROOT / "deploy" / "classroom" / "local-demo" / "fincolab_demo.py"
DOCKERFILE_PATH = ROOT / "deploy" / "classroom" / "local-demo" / "Dockerfile"
DOCKERIGNORE_PATH = ROOT / ".dockerignore"
MATERIALS_ROOT = ROOT / "deploy" / "classroom" / "local-demo" / "materials"
CPP_MATERIALS = {
    "sequence-list-experiment-001": MATERIALS_ROOT / "sequence-list" / "bundle.json",
    "linked-list-experiment-002": MATERIALS_ROOT / "linked-list" / "bundle.json",
}


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


@pytest.mark.parametrize(
    "parent_algorithm_id",
    ["sequence-list-experiment-001", "linked-list-experiment-002"],
)
def test_teacher_can_resolve_each_cpp_parent_for_material_ownership(
    parent_algorithm_id: str,
) -> None:
    with demo_client() as client:
        status, parent = client.request(
            "GET",
            f"/v1/spaces/course-001/algorithm_development/{parent_algorithm_id}",
            token="teacher-token",
        )

    assert status == HTTPStatus.OK
    assert parent["id"] == parent_algorithm_id
    assert parent["username"] == "teacher001"


def test_project_listing_keeps_python_parent_and_adds_both_cpp_parents() -> None:
    with demo_client() as client:
        status, projects = client.request(
            "GET",
            "/v1/spaces/course-001/algorithm_development",
            token="teacher-token",
        )

    assert status == HTTPStatus.OK
    assert [project["id"] for project in projects["data"][:3]] == [
        "parent-experiment-001",
        "sequence-list-experiment-001",
        "linked-list-experiment-002",
    ]


def test_teacher_receives_not_found_for_an_unknown_parent_subresource() -> None:
    with demo_client() as client:
        status, payload = client.request(
            "GET",
            "/v1/spaces/course-001/algorithm_development/"
            "sequence-list-experiment-001/not-a-resource",
            token="teacher-token",
        )

    assert status == HTTPStatus.NOT_FOUND
    assert payload == {"detail": "demo_endpoint_not_found"}


@pytest.mark.parametrize(("parent_algorithm_id", "bundle_path"), CPP_MATERIALS.items())
def test_teacher_receives_each_exact_sealed_cpp_material_bundle(
    parent_algorithm_id: str,
    bundle_path: Path,
) -> None:
    sealed_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    with demo_client() as client:
        status, private_bundle = client.request(
            "GET",
            f"/v1/spaces/course-001/algorithm_development/"
            f"{parent_algorithm_id}/assessment_materials",
            token="teacher-token",
        )

    assert status == HTTPStatus.OK
    adapter_starter = private_bundle["starter_source"]
    assert isinstance(adapter_starter, dict)
    encoded_source = adapter_starter.pop("content_base64")
    assert isinstance(encoded_source, str)
    source_bytes = base64.b64decode(encoded_source, validate=True)
    assert len(source_bytes) == adapter_starter["size_bytes"]
    assert hashlib.sha256(source_bytes).hexdigest() == adapter_starter["sha256"]
    assert private_bundle == sealed_bundle

    serialized = json.dumps(private_bundle, ensure_ascii=False)
    assert str(ROOT) not in serialized
    assert "import-config.json" not in serialized
    assert ".txt" not in serialized.casefold()


@pytest.mark.parametrize("parent_algorithm_id", CPP_MATERIALS)
@pytest.mark.parametrize(
    ("token", "expected_detail"),
    [
        ("student001-token", "demo_resource_access_denied"),
        ("student002-token", "demo_course_access_denied"),
    ],
)
def test_students_cannot_read_cpp_material_bundles(
    parent_algorithm_id: str,
    token: str,
    expected_detail: str,
) -> None:
    with demo_client() as client:
        status, payload = client.request(
            "GET",
            f"/v1/spaces/course-001/algorithm_development/"
            f"{parent_algorithm_id}/assessment_materials",
            token=token,
        )

    assert status == HTTPStatus.FORBIDDEN
    assert payload == {"detail": expected_detail}


@pytest.mark.parametrize("parent_algorithm_id", CPP_MATERIALS)
def test_cpp_material_route_never_serves_a_requested_raw_source_path(
    parent_algorithm_id: str,
) -> None:
    with demo_client() as client:
        status, payload = client.request(
            "GET",
            f"/v1/spaces/course-001/algorithm_development/"
            f"{parent_algorithm_id}/assessment_materials/raw.cpp",
            token="teacher-token",
        )

    assert status == HTTPStatus.NOT_FOUND
    assert payload == {"detail": "demo_endpoint_not_found"}


def test_demo_image_packages_only_the_two_sealed_material_resources() -> None:
    copy_sources = [
        line.split()[1]
        for line in DOCKERFILE_PATH.read_text(encoding="utf-8").splitlines()
        if line.startswith("COPY ")
    ]
    material_sources = [source for source in copy_sources if "/materials/" in source]

    assert material_sources == [
        "deploy/classroom/local-demo/materials/sequence-list/bundle.json",
        "deploy/classroom/local-demo/materials/linked-list/bundle.json",
    ]
    assert all(Path(source).name == "bundle.json" for source in material_sources)
    assert not any(
        source.casefold().endswith((".cpp", ".txt")) or "import-config.json" in source
        for source in copy_sources
    )


def _dockerignore_includes(relative_path: str, patterns: list[str]) -> bool:
    """Apply Docker's ordered ignore rules for this repository's closed pattern subset."""

    path_parts = Path(relative_path).parts
    candidates = ["/".join(path_parts[:index]) for index in range(1, len(path_parts) + 1)]
    for candidate in candidates:
        included = True
        for raw_pattern in patterns:
            pattern = raw_pattern.strip()
            if not pattern or pattern.startswith("#"):
                continue
            negated = pattern.startswith("!")
            if negated:
                pattern = pattern[1:]
            directory_pattern = pattern.endswith("/")
            normalized = pattern.strip("/")
            assert normalized == "*" or normalized.endswith("/**") or "*" not in normalized
            prefix = normalized.removesuffix("/**")
            matched = (
                (normalized == "*" and "/" not in candidate)
                or candidate == normalized
                or (normalized.endswith("/**") and candidate.startswith(f"{prefix}/"))
                or (directory_pattern and candidate.startswith(f"{normalized}/"))
            )
            if matched:
                included = negated
        if not included:
            return False
    return True


def _local_demo_context_from_ignore_rules() -> set[str]:
    patterns = DOCKERIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    return {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "deploy" / "classroom" / "local-demo").rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and _dockerignore_includes(path.relative_to(ROOT).as_posix(), patterns)
    }


def _local_demo_context_from_docker() -> set[str] | None:
    docker = shutil.which("docker")
    if docker is None:
        return None
    available = subprocess.run(
        [docker, "info", "--format", "{{.ServerVersion}}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if available.returncode != 0:
        return None
    with TemporaryDirectory() as temporary_directory:
        temporary = Path(temporary_directory)
        dockerfile = temporary / "Dockerfile.context-check"
        dockerfile.write_text(
            "FROM scratch\nCOPY deploy/classroom/local-demo /context\n",
            encoding="utf-8",
        )
        output = temporary / "output"
        environment = {**os.environ, "DOCKER_BUILDKIT": "1"}
        completed = subprocess.run(
            [
                docker,
                "build",
                "--network=none",
                "--pull=false",
                "--output",
                f"type=local,dest={output}",
                "--file",
                str(dockerfile),
                str(ROOT),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
        assert completed.returncode == 0, completed.stderr
        return {
            f"deploy/classroom/local-demo/{path.relative_to(output / 'context').as_posix()}"
            for path in (output / "context").rglob("*")
            if path.is_file()
        }


def test_repository_docker_context_includes_copy_sources_and_excludes_private_assets() -> None:
    copy_sources = {
        line.split()[1]
        for line in DOCKERFILE_PATH.read_text(encoding="utf-8").splitlines()
        if line.startswith("COPY ")
    }
    expected_context = {
        "deploy/classroom/local-demo/Dockerfile",
        "deploy/classroom/local-demo/fincolab_demo.py",
        "deploy/classroom/local-demo/materials/sequence-list/bundle.json",
        "deploy/classroom/local-demo/materials/linked-list/bundle.json",
    }
    patterns = DOCKERIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    context_files = _local_demo_context_from_ignore_rules()

    assert copy_sources <= context_files
    assert context_files == expected_context
    assert not any(
        path.casefold().endswith((".cpp", ".txt"))
        or path.endswith("import-config.json")
        or path.endswith("source-manifest.json")
        or path.endswith("README.md")
        or path.endswith("docker-compose.yml")
        or path.endswith(".env.ai.example")
        for path in context_files
    )

    docker_context_files = _local_demo_context_from_docker()
    if docker_context_files is not None:
        assert docker_context_files == context_files

    guard_cases = {
        "deploy/classroom/local-demo/**": "deploy/classroom/local-demo/README.md",
        "deploy/classroom/local-demo/materials/**": (
            "deploy/classroom/local-demo/materials/source-manifest.json"
        ),
        "deploy/classroom/local-demo/materials/sequence-list/**": (
            "deploy/classroom/local-demo/materials/sequence-list/顺序表操作练习01.cpp"
        ),
    }
    for guard, forbidden_path in guard_cases.items():
        assert guard in patterns
        without_guard = [pattern for pattern in patterns if pattern != guard]
        assert _dockerignore_includes(forbidden_path, without_guard)


def test_facade_keeps_request_logging_silent(capsys: pytest.CaptureFixture[str]) -> None:
    with demo_client() as client:
        status, _payload = client.request("GET", "/v1/user/info", token="teacher-token")

    assert status == HTTPStatus.OK
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_student_receives_parent_metadata_and_own_child_for_compatibility_matching():
    with demo_client() as client:
        status, projects = client.request(
            "GET",
            "/v1/spaces/course-001/algorithm_development",
            token="student001-token",
        )
        assert status == HTTPStatus.OK
        assert [project["id"] for project in projects["data"]] == [
            "parent-experiment-001",
            "sequence-list-experiment-001",
            "linked-list-experiment-002",
            "child-experiment-001",
        ]
        assert projects["data"][3]["name"] == "exp-student001-a1b2"

        status, parent = client.request(
            "GET",
            "/v1/spaces/course-001/algorithm_development/parent-experiment-001",
            token="student001-token",
        )
        assert status == HTTPStatus.FORBIDDEN
        assert parent == {"detail": "demo_resource_access_denied"}

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


def test_facade_can_supply_a_bounded_local_roster_for_concurrency_checks(monkeypatch):
    monkeypatch.setenv("CLASSROOM_MOCK_STUDENT_COUNT", "20")
    facade = _load_facade_module()

    course_students = [
        user
        for user in facade.USERS.values()
        if user.role_name == "student" and user.space_id == facade.COURSE_ID
    ]

    assert len(course_students) == 20
    assert facade.authenticate_bearer("student020-token") is not None
    assert facade.authenticate_bearer("student002-token").space_id == facade.NEGATIVE_COURSE_ID
    assert len([facade._student_project(student.username) for student in course_students]) == 20
