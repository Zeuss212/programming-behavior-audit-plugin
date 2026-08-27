import asyncio
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.config import Settings
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.main import create_app
from classroom_sync.models import Base, PlanAuthoringSession, PlanDraft
from classroom_sync.services.assessment_materials import AssessmentMaterialService
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plans import PlanDraftInput, PlanService
from classroom_sync.services.sessions import PluginSessionService
from tests.integration.test_plan_assignment_flow import profile_draft
from tests.unit.test_publication_gate import profile_for, real_bundle


class FakeIdentityGateway:
    def __init__(self) -> None:
        self.teacher_checks: list[tuple[str, str, str]] = []
        self.student_checks: list[tuple[str, str]] = []

    def resolve_principal(self, bearer_token: str) -> Principal:
        if bearer_token == "teacher-token":
            return Principal("teacher-1", "teacher-a", bearer_token)
        if bearer_token == "student-token":
            return Principal("student-1", "student-a", bearer_token)
        if bearer_token == "other-teacher-token":
            return Principal("teacher-2", "teacher-b", bearer_token)
        raise AssertionError(f"Unexpected token: {bearer_token}")

    def require_teacher_owner(
        self, principal: Principal, space_id: str, experiment_id: str
    ) -> None:
        self.teacher_checks.append((principal.user_id, space_id, experiment_id))

    def require_student_member(self, principal: Principal, space_id: str) -> None:
        self.student_checks.append((principal.user_id, space_id))

    def list_student_children(
        self, principal: Principal, space_id: str, parent_algorithm_id: str
    ) -> tuple[StudentChildExperiment, ...]:
        assert principal.user_id == "teacher-1"
        assert (space_id, parent_algorithm_id) == ("space-1", "parent-1")
        return (StudentChildExperiment("student-1", "student-a", "child-1", "workbench-1"),)


class RecordingStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, body: bytes, *, content_type: str) -> None:
        assert content_type == "application/gzip"
        self.objects[key] = body


def request(app, method: str, path: str, **kwargs: object) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_teacher_publish_sync_and_student_acceptance_use_trusted_router_boundaries():
    """The browser only supplies a bearer; role and roster checks stay server-side."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    identity_gateway = FakeIdentityGateway()
    application = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=identity_gateway,
            plan_service=PlanService(session_factory, schema_registry, clock=lambda: now),
            assignment_service=AssignmentService(session_factory, clock=lambda: now),
        ),
    )
    teacher_headers = {"Authorization": "Bearer teacher-token"}
    student_headers = {"Authorization": "Bearer student-token"}
    draft_response = request(
        application,
        "POST",
        "/v1/classroom/plans/drafts",
        headers=teacher_headers,
        json={
            "space_id": "space-1",
            "parent_algorithm_id": "parent-1",
            "title": "字典课堂练习",
            "profile": profile_draft("学生是否正确读取字典中的值？"),
            "scheduled_start_at": now.isoformat(),
            "scheduled_end_at": (now + timedelta(minutes=30)).isoformat(),
            "ai_policy": "prohibited",
        },
    )
    assert draft_response.status_code == 201
    draft_id = draft_response.json()["draft_id"]

    publish_response = request(
        application,
        "POST",
        f"/v1/classroom/plans/drafts/{draft_id}/publish",
        headers=teacher_headers,
    )
    assert publish_response.status_code == 200
    plan_version_id = publish_response.json()["plan_version_id"]

    sync_response = request(
        application,
        "POST",
        f"/v1/classroom/plans/{plan_version_id}/assignments/sync",
        headers=teacher_headers,
    )
    assert sync_response.status_code == 200
    assignment_id = sync_response.json()["assignments"][0]["assignment_id"]

    accept_response = request(
        application,
        "POST",
        f"/v1/classroom/student/assignments/{assignment_id}/accept",
        headers=student_headers,
    )
    assert accept_response.status_code == 200
    assert accept_response.json()["status"] == "ready"
    assert identity_gateway.teacher_checks == [
        ("teacher-1", "space-1", "parent-1"),
        ("teacher-1", "space-1", "parent-1"),
        ("teacher-1", "space-1", "parent-1"),
    ]
    assert identity_gateway.student_checks == [("student-1", "space-1")]


def test_student_launches_plugin_with_one_time_ticket_and_uploads_evidence():
    """The HTTP boundary preserves student and plugin authentication separation."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    identity_gateway = FakeIdentityGateway()
    plan_service = PlanService(session_factory, schema_registry, clock=lambda: now)
    assignment_service = AssignmentService(session_factory, clock=lambda: now)
    application = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=identity_gateway,
            plan_service=plan_service,
            assignment_service=assignment_service,
            plugin_session_service=PluginSessionService(
                session_factory,
                storage=RecordingStorage(),
                plugin_jwt_secret="test-plugin-secret-012345678901234567",
                clock=lambda: now,
                schema_registry=schema_registry,
            ),
        ),
    )
    draft = plan_service.create_draft(
        PlanDraftInput(
            space_id="space-1",
            parent_algorithm_id="parent-1",
            title="字典课堂练习",
            profile=profile_draft("学生是否正确读取字典中的值？"),
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )
    plan_version = plan_service.publish_draft(draft.id, teacher_id="teacher-1")
    assignment = assignment_service.sync_assignments(
        plan_version,
        (StudentChildExperiment("student-1", "student-a", "child-1", "workbench-1"),),
    )[0]
    assignment = assignment_service.accept_assignment(assignment.id, student_id="student-1")

    launch_response = request(
        application,
        "POST",
        f"/v1/classroom/student/assignments/{assignment.id}/launch-ticket",
        headers={"Authorization": "Bearer student-token"},
    )
    assert launch_response.status_code == 201
    ticket = launch_response.json()["ticket"]

    register_response = request(
        application,
        "POST",
        "/v1/classroom/plugin/sessions/register",
        json={"ticket": ticket, "plugin_instance_id": "plugin-instance-a"},
    )
    assert register_response.status_code == 201
    credentials = register_response.json()
    assert credentials["assignment_id"] == assignment.id
    assert credentials["plan_id"] == plan_version.plan_id
    assert credentials["plan_version"] == 1
    assert credentials["profile"] == plan_version.profile
    assert credentials["scheduled_end_at"] == (now + timedelta(minutes=30)).isoformat()
    assert credentials["last_sync_at"] == now.isoformat()
    assert credentials["evidence_cutoff_at"] == (now + timedelta(minutes=45)).isoformat()
    plugin_headers = {"Authorization": f"Bearer {credentials['access_token']}"}

    refresh_response = request(
        application,
        "POST",
        f"/v1/classroom/plugin/sessions/{credentials['session_id']}/context/refresh",
        headers=plugin_headers,
    )
    assert refresh_response.status_code == 200
    refreshed = refresh_response.json()
    assert refreshed["session_id"] == credentials["session_id"]
    assert refreshed["profile"] == credentials["profile"]
    assert refreshed["scheduled_end_at"] == credentials["scheduled_end_at"]
    assert refreshed["last_sync_at"] == credentials["last_sync_at"]

    heartbeat_response = request(
        application,
        "POST",
        f"/v1/classroom/plugin/sessions/{credentials['session_id']}/heartbeat",
        headers=plugin_headers,
    )
    assert heartbeat_response.status_code == 200
    evidence_response = request(
        application,
        "PUT",
        f"/v1/classroom/plugin/sessions/{credentials['session_id']}/evidence/1",
        headers={
            **plugin_headers,
            "Content-Type": "application/gzip",
            "X-First-Event-Sequence": "1",
            "X-Last-Event-Sequence": "1",
        },
        content=gzip.compress(b'{"events":[{"sequence":1}]}'),
    )
    assert evidence_response.status_code == 201
    assert evidence_response.json()["sequence"] == 1
    missing_token_response = request(
        application,
        "POST",
        f"/v1/classroom/plugin/sessions/{credentials['session_id']}/heartbeat",
    )
    assert missing_token_response.status_code == 401
    schema_registry.validate("error", missing_token_response.json())


def test_draft_get_and_atomic_revisioned_put_recover_the_latest_server_state():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 28, 8, 0, tzinfo=UTC)
    application = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=FakeIdentityGateway(),
            plan_service=PlanService(session_factory, schema_registry, clock=lambda: now),
            assignment_service=AssignmentService(session_factory, clock=lambda: now),
        ),
    )
    headers = {"Authorization": "Bearer teacher-token"}
    create_response = request(
        application,
        "POST",
        "/v1/classroom/plans/drafts",
        headers=headers,
        json={
            "space_id": "space-1",
            "parent_algorithm_id": "parent-1",
            "title": "initial title",
            "profile": profile_draft("initial question"),
            "scheduled_start_at": now.isoformat(),
            "scheduled_end_at": (now + timedelta(minutes=30)).isoformat(),
            "ai_policy": "prohibited",
        },
    )
    assert create_response.status_code == 201
    draft_id = create_response.json()["draft_id"]

    get_response = request(
        application,
        "GET",
        f"/v1/classroom/plans/drafts/{draft_id}",
        headers=headers,
    )
    assert get_response.status_code == 200
    assert get_response.json()["publication_gate"] == {
        "status": "ready",
        "blocking_count": 0,
        "warning_count": 0,
        "issues": [],
    }

    updated_profile = profile_draft("updated question")
    update_response = request(
        application,
        "PUT",
        f"/v1/classroom/plans/drafts/{draft_id}",
        headers=headers,
        json={
            "expected_revision": 0,
            "title": "updated title",
            "profile": updated_profile,
            "scheduled_start_at": (now + timedelta(hours=1)).isoformat(),
            "scheduled_end_at": (now + timedelta(hours=2)).isoformat(),
            "ai_policy": "allowed",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json() == {
        **get_response.json(),
        "title": "updated title",
        "profile": updated_profile,
        "scheduled_start_at": "2026-08-28T09:00:00Z",
        "scheduled_end_at": "2026-08-28T10:00:00Z",
        "ai_policy": "allowed",
        "revision": 1,
    }

    conflict_response = request(
        application,
        "PUT",
        f"/v1/classroom/plans/drafts/{draft_id}",
        headers=headers,
        json={
            "expected_revision": 0,
            "title": "must not overwrite",
            "profile": profile_draft("must not overwrite"),
            "scheduled_start_at": now.isoformat(),
            "scheduled_end_at": (now + timedelta(minutes=10)).isoformat(),
            "ai_policy": "prohibited",
        },
    )
    assert conflict_response.status_code == 409
    assert conflict_response.json()["error"]["code"] == "plan_draft_revision_conflict"
    assert "details" not in conflict_response.json()["error"]
    recovered = request(
        application,
        "GET",
        f"/v1/classroom/plans/drafts/{draft_id}",
        headers=headers,
    ).json()
    assert recovered == update_response.json()


def test_draft_routes_reject_a_non_owner_before_returning_or_overwriting_state():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 28, 8, 0, tzinfo=UTC)
    plan_service = PlanService(session_factory, schema_registry, clock=lambda: now)
    application = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=FakeIdentityGateway(),
            plan_service=plan_service,
            assignment_service=AssignmentService(session_factory, clock=lambda: now),
        ),
    )
    draft = plan_service.create_draft(
        PlanDraftInput(
            space_id="space-1",
            parent_algorithm_id="parent-1",
            title="private draft",
            profile=profile_draft("private question"),
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )
    other_headers = {"Authorization": "Bearer other-teacher-token"}

    get_response = request(
        application,
        "GET",
        f"/v1/classroom/plans/drafts/{draft.id}",
        headers=other_headers,
    )
    put_response = request(
        application,
        "PUT",
        f"/v1/classroom/plans/drafts/{draft.id}",
        headers=other_headers,
        json={
            "expected_revision": 0,
            "title": "stolen",
            "profile": profile_draft("stolen"),
            "scheduled_start_at": now.isoformat(),
            "scheduled_end_at": (now + timedelta(minutes=30)).isoformat(),
            "ai_policy": "prohibited",
        },
    )

    assert get_response.status_code == 403
    assert put_response.status_code == 403
    assert get_response.json()["error"]["code"] == "plan_draft_owner_mismatch"
    assert put_response.json()["error"]["code"] == "plan_draft_owner_mismatch"
    with session_factory() as session:
        persisted = session.get(PlanDraft, draft.id)
        assert persisted is not None
        assert persisted.title == "private draft"
        assert persisted.revision == 0


def test_v3_publish_fetches_latest_materials_after_ownership_and_returns_safe_gate_details():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 28, 8, 0, tzinfo=UTC)
    materials = real_bundle("sequence-list")
    profile = profile_for(
        materials,
        tuple(requirement.id for requirement in materials.requirements),
    )
    identity = FakeIdentityGateway()

    class LatestMaterialService:
        def __init__(self) -> None:
            self.calls = 0

        def get_bundle(
            self,
            principal: Principal,
            space_id: str,
            parent_algorithm_id: str,
        ):
            assert identity.teacher_checks[-1] == (
                principal.user_id,
                space_id,
                parent_algorithm_id,
            )
            self.calls += 1
            return materials

    latest_materials = LatestMaterialService()
    plan_service = PlanService(session_factory, schema_registry, clock=lambda: now)
    application = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=identity,
            plan_service=plan_service,
            assignment_service=AssignmentService(session_factory, clock=lambda: now),
            assessment_material_service=cast(
                AssessmentMaterialService,
                latest_materials,
            ),
        ),
    )
    with session_factory.begin() as session:
        session.add(
            PlanAuthoringSession(
                id="blocked-authoring",
                teacher_id="teacher-1",
                space_id=materials.space_id,
                parent_algorithm_id=materials.parent_algorithm_id,
                status="open",
                active_slot=1,
                suggestion_job_id=None,
                published_plan_id=None,
                created_at=now,
                updated_at=now,
                closed_at=None,
            )
        )
    draft = plan_service.create_draft(
        PlanDraftInput(
            authoring_session_id="blocked-authoring",
            space_id=materials.space_id,
            parent_algorithm_id=materials.parent_algorithm_id,
            title="blocked sequence lesson",
            profile=profile,
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )

    response = request(
        application,
        "POST",
        f"/v1/classroom/plans/drafts/{draft.id}/publish",
        headers={"Authorization": "Bearer teacher-token"},
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "publication_gate_blocked"
    assert error["retryable"] is False
    assert error["details"]["status"] == "blocked"
    assert error["details"]["blocking_count"] == 3
    serialized = json.dumps(error["details"], ensure_ascii=False)
    assert len(serialized.encode("utf-8")) <= 32_768
    assert "teacher prose is not a gate key" not in serialized
    assert "expected_stdout" not in serialized
    assert "input" not in serialized
    assert latest_materials.calls == 1
    with session_factory() as session:
        authoring = session.get(PlanAuthoringSession, "blocked-authoring")
        assert authoring is not None
        assert authoring.status == "open"
        assert authoring.active_slot == 1
        assert authoring.published_plan_id is None
        assert authoring.closed_at is None
