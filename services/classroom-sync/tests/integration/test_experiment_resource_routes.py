"""HTTP coverage for teacher-owned experiment resource uploads."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.config import Settings
from classroom_sync.main import create_app
from classroom_sync.models import Base
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.experiment_resources import ExperimentResourceService
from classroom_sync.services.plans import PlanService
from classroom_sync.storage import StoredObject


class IdentityGateway:
    def resolve_principal(self, bearer_token: str) -> Principal:
        assert bearer_token == "teacher-token"
        return Principal("teacher-1", "teacher-a", bearer_token)

    def require_teacher_owner(
        self, principal: Principal, space_id: str, experiment_id: str
    ) -> None:
        assert (principal.user_id, space_id, experiment_id) == (
            "teacher-1",
            "space-1",
            "parent-1",
        )

    def require_student_member(self, principal: Principal, space_id: str) -> None:
        raise AssertionError("student authorization is not expected")

    def list_student_children(
        self, principal: Principal, space_id: str, parent_algorithm_id: str
    ) -> tuple[StudentChildExperiment, ...]:
        raise AssertionError("roster lookup is not expected")


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, StoredObject] = {}

    def put_bytes(self, key: str, body: bytes, *, content_type: str) -> None:
        self.objects[key] = StoredObject(body=body, content_type=content_type)

    def get_bytes(self, key: str) -> StoredObject:
        return self.objects[key]

    def delete_bytes(self, key: str) -> None:
        self.objects.pop(key, None)


def request(application: object, method: str, path: str, **kwargs: object) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_teacher_uploads_resource_then_lists_safe_metadata() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    storage = MemoryStorage()
    services = ClassroomServices(
        identity_gateway=IdentityGateway(),
        plan_service=cast(PlanService, object()),
        assignment_service=cast(AssignmentService, object()),
        experiment_resource_service=ExperimentResourceService(
            factory,
            storage=storage,
            clock=lambda: datetime(2026, 9, 2, 8, 0, tzinfo=UTC),
        ),
    )
    application = create_app(Settings(database_url="sqlite://"), classroom_services=services)
    base = "/v1/classroom/experiments/space-1/parent-1/resources"

    uploaded = request(
        application,
        "POST",
        f"{base}/assignment_material",
        params={"filename": "labels.txt"},
        headers={"Authorization": "Bearer teacher-token"},
        content=b"label data",
    )
    listed = request(
        application,
        "GET",
        base,
        headers={"Authorization": "Bearer teacher-token"},
    )

    assert uploaded.status_code == 201
    assert listed.status_code == 200
    assert listed.json()["resources"][0]["filename"] == "labels.txt"
    assert "object_key" not in uploaded.json()["resource"]
    assert len(storage.objects) == 1


def test_resource_upload_rejects_wrong_extension_without_storage_write() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    storage = MemoryStorage()
    services = ClassroomServices(
        identity_gateway=IdentityGateway(),
        plan_service=cast(PlanService, object()),
        assignment_service=cast(AssignmentService, object()),
        experiment_resource_service=ExperimentResourceService(
            factory,
            storage=storage,
            clock=lambda: datetime(2026, 9, 2, 8, 0, tzinfo=UTC),
        ),
    )
    application = create_app(Settings(database_url="sqlite://"), classroom_services=services)

    response = request(
        application,
        "POST",
        "/v1/classroom/experiments/space-1/parent-1/resources/assignment_material",
        params={"filename": "unsafe.exe"},
        headers={"Authorization": "Bearer teacher-token"},
        content=b"MZ",
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "experiment_resource_extension_invalid"
    assert storage.objects == {}
