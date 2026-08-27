"""Teacher-only assessment material route integration tests."""

from __future__ import annotations

import asyncio
import base64
import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import httpx
import pytest

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.config import Settings
from classroom_sync.errors import AuthorizationError, UpstreamUnavailableError
from classroom_sync.main import create_app
from classroom_sync.services.assessment_materials import AssessmentMaterialService
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plans import PlanService

ROOT = Path(__file__).resolve().parents[4]
MATERIALS = ROOT / "deploy" / "classroom" / "local-demo" / "materials"
MATERIAL_FIXTURES = (
    (
        MATERIALS / "sequence-list" / "bundle.json",
        MATERIALS / "sequence-list" / "顺序表操作练习01.cpp",
    ),
    (
        MATERIALS / "linked-list" / "bundle.json",
        MATERIALS / "linked-list" / "链表操作练习02.cpp",
    ),
)


def private_payload(bundle_path: Path, source_path: Path) -> dict[str, object]:
    payload = cast(
        dict[str, object],
        json.loads(bundle_path.read_text(encoding="utf-8")),
    )
    starter = cast(dict[str, object], payload["starter_source"])
    starter["content_base64"] = base64.b64encode(source_path.read_bytes()).decode("ascii")
    return payload


class RecordingIdentityGateway:
    def __init__(self, *, owns_experiment: bool = True) -> None:
        self.owns_experiment = owns_experiment
        self.owner_checks: list[tuple[str, str, str]] = []

    def resolve_principal(self, bearer_token: str) -> Principal:
        return Principal("teacher-1", "teacher-a", bearer_token)

    def require_teacher_owner(
        self,
        principal: Principal,
        space_id: str,
        experiment_id: str,
    ) -> None:
        self.owner_checks.append((principal.user_id, space_id, experiment_id))
        if not self.owns_experiment:
            raise AuthorizationError("teacher_not_experiment_owner")

    def require_student_member(self, principal: Principal, space_id: str) -> None:
        raise AssertionError("Student authorization is not expected")

    def list_student_children(
        self,
        principal: Principal,
        space_id: str,
        parent_algorithm_id: str,
    ) -> tuple[StudentChildExperiment, ...]:
        raise AssertionError("Roster lookup is not expected")


class RecordingMaterialGateway:
    def __init__(
        self,
        payload: dict[str, object] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    def get_bundle(
        self,
        principal: Principal,
        space_id: str,
        parent_algorithm_id: str,
    ) -> dict[str, object]:
        self.calls.append((principal.bearer_token, space_id, parent_algorithm_id))
        if self.error is not None:
            raise self.error
        if self.payload is None:
            raise AssertionError("No material payload configured")
        return deepcopy(self.payload)


def request(app: object, path: str, *, bearer: str = "teacher-token") -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, headers={"Authorization": f"Bearer {bearer}"})

    return asyncio.run(send())


def create_material_app(
    identity_gateway: RecordingIdentityGateway,
    material_gateway: RecordingMaterialGateway | None,
):
    material_service = (
        None if material_gateway is None else AssessmentMaterialService(material_gateway)
    )
    return create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=identity_gateway,
            plan_service=cast(PlanService, object()),
            assignment_service=cast(AssignmentService, object()),
            assessment_material_service=material_service,
        ),
    )


@pytest.mark.parametrize(("bundle_path", "source_path"), MATERIAL_FIXTURES)
def test_teacher_owner_receives_each_real_bundle_without_private_source(
    bundle_path: Path,
    source_path: Path,
) -> None:
    payload = private_payload(bundle_path, source_path)
    identity_gateway = RecordingIdentityGateway()
    material_gateway = RecordingMaterialGateway(payload)
    space_id = cast(str, payload["space_id"])
    parent_id = cast(str, payload["parent_algorithm_id"])

    response = request(
        create_material_app(identity_gateway, material_gateway),
        f"/v1/classroom/experiments/{space_id}/{parent_id}/assessment-materials",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert body["space_id"] == space_id
    assert body["parent_algorithm_id"] == parent_id
    assert "bundle_hash" in body
    serialized = json.dumps(body, ensure_ascii=False)
    assert "content_base64" not in serialized
    assert "importer_version" not in serialized
    assert "toolchain_profile" not in serialized
    assert identity_gateway.owner_checks == [("teacher-1", space_id, parent_id)]
    assert material_gateway.calls == [("teacher-token", space_id, parent_id)]


def test_different_owner_is_rejected_before_any_material_gateway_access() -> None:
    identity_gateway = RecordingIdentityGateway(owns_experiment=False)
    material_gateway = RecordingMaterialGateway({})

    response = request(
        create_material_app(identity_gateway, material_gateway),
        "/v1/classroom/experiments/space-1/parent-1/assessment-materials",
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "teacher_not_experiment_owner"
    assert identity_gateway.owner_checks == [("teacher-1", "space-1", "parent-1")]
    assert material_gateway.calls == []


def test_unconfigured_material_service_returns_stable_503_after_ownership() -> None:
    identity_gateway = RecordingIdentityGateway()

    response = request(
        create_material_app(identity_gateway, None),
        "/v1/classroom/experiments/space-1/parent-1/assessment-materials",
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "assessment_materials_not_configured",
        "message": "课堂服务请求未能完成。",
        "retryable": False,
        "request_id": response.json()["error"]["request_id"],
    }
    assert identity_gateway.owner_checks == [("teacher-1", "space-1", "parent-1")]


@pytest.mark.parametrize(
    ("material_gateway", "expected_code"),
    (
        (
            RecordingMaterialGateway(
                error=UpstreamUnavailableError("assessment_materials_upstream_unavailable")
            ),
            "assessment_materials_upstream_unavailable",
        ),
        (RecordingMaterialGateway({"schema_version": 1}), "assessment_materials_contract_invalid"),
    ),
)
def test_material_failures_return_safe_stable_503_codes(
    material_gateway: RecordingMaterialGateway,
    expected_code: str,
) -> None:
    response = request(
        create_material_app(RecordingIdentityGateway(), material_gateway),
        "/v1/classroom/experiments/space-1/parent-1/assessment-materials",
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == expected_code
    assert response.json()["error"]["message"] == "课堂服务请求未能完成。"
    assert material_gateway.calls == [("teacher-token", "space-1", "parent-1")]
