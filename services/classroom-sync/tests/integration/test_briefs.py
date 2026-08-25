import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.config import Settings
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import UpstreamUnavailableError, ValidationError
from classroom_sync.main import create_app
from classroom_sync.models import (
    Base,
    ClassroomBriefAnalysisJob,
    EvidenceChunk,
    ExperimentPlanBinding,
    MonitorSession,
    PlanVersion,
    StudentAssignment,
    StudentBrief,
)
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.brief_analysis import (
    BriefAiAnalysis,
    BriefAnalysisJobService,
)
from classroom_sync.services.briefs import BriefContent, BriefService, TeacherReviewInput
from classroom_sync.services.deadlines import DeadlineService
from classroom_sync.services.plans import PlanService
from classroom_sync.services.sessions import PLUGIN_TOKEN_AUDIENCE, PluginSessionService

IDS = {
    "binding": "c65f6e60-ecc1-4e36-9c0d-5ed2d0f3b67d",
    "assignment": "d7647a1a-89c3-4c6d-9b5f-7e803918aa9d",
    "session": "23d7d803-524a-4d9f-b8bd-152a540dba12",
    "plan": "2b16b5c0-4e58-48f9-9448-9067de005e4a",
}


def seeded_brief_service(
    now: datetime,
    *,
    ai_policy: str = "allowed",
    enforce_foreign_keys: bool = False,
):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    if enforce_foreign_keys:
        event.listen(
            engine,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
        )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(
            PlanVersion(
                id="plan-version-1",
                plan_id=IDS["plan"],
                profile_id="profile-1",
                version=1,
                source_draft_revision=1,
                space_id="space-1",
                parent_algorithm_id="parent-1",
                profile={"title": "字典课堂练习", "knowledge_points": []},
                content_hash="a" * 64,
                scheduled_start_at=now,
                scheduled_end_at=now + timedelta(minutes=30),
                ai_policy=ai_policy,
                published_at=now,
                teacher_id="teacher-1",
            )
        )
        session.add(
            ExperimentPlanBinding(
                id=IDS["binding"],
                space_id="space-1",
                parent_algorithm_id="parent-1",
                plan_id=IDS["plan"],
                plan_version=1,
                teacher_id="teacher-1",
                created_at=now,
                updated_at=None,
            )
        )
        session.flush()
        session.add(
            StudentAssignment(
                id=IDS["assignment"],
                binding_id=IDS["binding"],
                space_id="space-1",
                parent_algorithm_id="parent-1",
                child_algorithm_id="child-1",
                workbench_id="workbench-1",
                student_id="student-1",
                plan_id=IDS["plan"],
                plan_version=1,
                status="active",
                scheduled_start_at=now,
                scheduled_end_at=now + timedelta(minutes=30),
                accepted_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            MonitorSession(
                id=IDS["session"],
                assignment_id=IDS["assignment"],
                plan_id=IDS["plan"],
                plan_version=1,
                status="collecting",
                scheduled_end_at=now + timedelta(minutes=30),
                actual_end_at=now + timedelta(minutes=30),
                evidence_cutoff_at=now + timedelta(minutes=45),
                last_activity_at=now + timedelta(minutes=20),
                last_heartbeat_at=now + timedelta(minutes=20),
                last_contiguous_sequence=1,
                missing_ranges=[],
                completeness="complete",
                submission_reason=None,
                active_slot=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            EvidenceChunk(
                id="3e72ccff-32ca-48e0-acff-a4a10cf51b0b",
                session_id=IDS["session"],
                sequence=1,
                content_sha256="a" * 64,
                content_encoding="gzip",
                media_type="application/json",
                compressed_bytes=100,
                uncompressed_bytes=200,
                first_event_sequence=1,
                last_event_sequence=3,
                object_key="classrooms/class-1/sessions/session-1/chunks/00000001.json.gz",
                created_at=now,
            )
        )
    registry = ClassroomSchemaRegistry(
        Path(__file__).resolve().parents[4] / "contracts" / "classroom" / "v1"
    )
    return BriefService(factory, registry, clock=lambda: now), factory, registry


def valid_content(summary: str = "完成主要功能并验证运行结果。") -> BriefContent:
    return BriefContent(
        summary=summary,
        knowledge_points=(
            {
                "knowledge_point_id": "KP_DICT0001",
                "name": "字典读取",
                "status": "partial",
                "evidence_refs": ["chunk-1#event-1"],
                "demonstrated": "完成了读取代码并运行。",
                "gap": "尚未覆盖空键。",
                "teacher_suggestion": "追问空键输入。",
            },
        ),
        process_overview=("完成两次运行并修正一次键错误。",),
        issues=("缺少空键测试。",),
    )


def valid_analysis_input() -> dict[str, object]:
    return {
        "lesson": {"title": "Python 字典课堂练习"},
        "knowledge_points": [
            {
                "knowledge_point_id": "KP_DICT0001",
                "name": "字典读取",
                "description": "使用 get 处理键不存在。",
                "question": "学生是否选择了恰当的查询方式？",
                "evidence_criteria": [],
            }
        ],
        "evidence_events": [
            {
                "event_id": "chunk-1#event-1",
                "sequence": 1,
                "kind": "edit",
                "description": "编辑了代码。",
            }
        ],
        "code_snapshots": [
            {"event_id": "chunk-1#event-1", "source": "print(records.get('B', 0))"}
        ],
    }


def test_one_logical_brief_keeps_first_submission_time_and_revisions():
    """Late evidence produces a revision, not multiple student-facing reports."""
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    service, factory, registry = seeded_brief_service(now)

    first = service.submit(IDS["session"], valid_content(), reason="student_manual")
    revised = service.submit(
        IDS["session"], valid_content("补充证据后仍建议追问边界输入。"), reason="student_manual"
    )

    registry.validate("student-brief", first.payload)
    registry.validate("student-brief", revised.payload)
    assert first.revision == 1
    assert revised.revision == 2
    assert first.payload["brief_id"] == revised.payload["brief_id"]
    assert first.payload["submitted_at"] == revised.payload["submitted_at"]
    with factory() as session:
        assert len(session.scalars(select(StudentBrief)).all()) == 2
        monitor_session = session.get(MonitorSession, IDS["session"])
        assert monitor_session is not None
        assert monitor_session.status == "completed"
        assert monitor_session.active_slot is None


def test_server_requested_analysis_writes_pending_brief_and_durable_job():
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    service, factory, _registry = seeded_brief_service(now, enforce_foreign_keys=True)

    brief = service.submit(
        IDS["session"], valid_content(), reason="student_manual",
        request_ai_analysis=True, analysis_input=valid_analysis_input()
    )

    assert brief.payload["ai_analysis_status"] == "pending"
    assert brief.payload["ai_analysis"] is None
    with factory() as session:
        jobs = list(session.scalars(select(ClassroomBriefAnalysisJob)))
    assert [(job.source_brief_id, job.status, job.attempts) for job in jobs] == [
        (brief.id, "pending", 0)
    ]
    assert jobs[0].analysis_input == valid_analysis_input()
    assert "analysis_input" not in brief.payload


def test_plan_prohibited_forces_not_requested_and_never_queues_an_ai_job():
    """A student opt-in cannot override the immutable teacher plan policy."""
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    service, factory, _registry = seeded_brief_service(now, ai_policy="prohibited")

    brief = service.submit(
        IDS["session"],
        valid_content(),
        reason="student_manual",
        request_ai_analysis=True,
        analysis_input=valid_analysis_input(),
    )

    assert brief.payload["ai_analysis_status"] == "not_requested"
    with factory() as session:
        assert list(session.scalars(select(ClassroomBriefAnalysisJob))) == []


def test_analysis_worker_appends_a_ready_revision_without_overwriting_base_brief():
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    brief_service, factory, _registry = seeded_brief_service(now)
    base = brief_service.submit(
        IDS["session"], valid_content(), reason="student_manual",
        request_ai_analysis=True, analysis_input=valid_analysis_input()
    )

    class AnalysisService:
        def generate(self, _source):
            return BriefAiAnalysis(
                knowledge_point_analyses=[{
                    "knowledge_point_id": "KP_DICT0001",
                    "status": "observed",
                    "evidence_event_ids": ["chunk-1#event-1"],
                    "teaching_suggestion": "追问不存在键时的默认值处理。",
                }],
                teacher_note="仅反映本次过程证据，仍需教师复核。",
            )

    worker = BriefAnalysisJobService(
        factory,
        brief_service,
        AnalysisService(),
        clock=lambda: now,
    )

    assert worker.run_due_jobs("worker-a") == 1

    latest = brief_service.get_latest_brief(IDS["session"])
    assert latest.revision == 2
    assert latest.payload["summary"] == base.payload["summary"]
    assert latest.payload["ai_analysis_status"] == "ready"
    assert latest.payload["ai_analysis"] == {
        "knowledge_point_analyses": [{
            "knowledge_point_id": "KP_DICT0001",
            "status": "observed",
            "evidence_event_ids": ["chunk-1#event-1"],
            "teaching_suggestion": "追问不存在键时的默认值处理。",
        }],
        "teacher_note": "仅反映本次过程证据，仍需教师复核。",
    }
    with factory() as session:
        job = session.scalar(select(ClassroomBriefAnalysisJob))
    assert job is not None
    assert job.status == "completed"
    assert job.lease_owner is None


def test_analysis_worker_retries_from_the_actual_failure_time_then_marks_unavailable():
    """A slow or retried provider must not shorten its next retry delay."""
    start = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    current_time = [start]
    brief_service, factory, _registry = seeded_brief_service(start)
    base = brief_service.submit(
        IDS["session"], valid_content(), reason="student_manual",
        request_ai_analysis=True, analysis_input=valid_analysis_input()
    )

    class UnavailableAnalysisService:
        def generate(self, _source):
            raise UpstreamUnavailableError("ai_brief_analysis_upstream_unavailable")

    worker = BriefAnalysisJobService(
        factory,
        brief_service,
        UnavailableAnalysisService(),
        clock=lambda: current_time[0],
    )

    assert worker.run_due_jobs("worker-a") == 1
    with factory() as session:
        first_retry = session.scalar(select(ClassroomBriefAnalysisJob))
    assert first_retry is not None
    assert (first_retry.status, first_retry.attempts, first_retry.run_at.replace(tzinfo=UTC)) == (
        "pending",
        1,
        start + timedelta(seconds=5),
    )

    current_time[0] = start + timedelta(seconds=5)
    assert worker.run_due_jobs("worker-a") == 1
    with factory() as session:
        second_retry = session.scalar(select(ClassroomBriefAnalysisJob))
    assert second_retry is not None
    assert (second_retry.status, second_retry.attempts, second_retry.run_at.replace(tzinfo=UTC)) == (
        "pending",
        2,
        start + timedelta(seconds=35),
    )

    current_time[0] = start + timedelta(seconds=35)
    assert worker.run_due_jobs("worker-a") == 1

    latest = brief_service.get_latest_brief(IDS["session"])
    assert latest.revision == 2
    assert latest.payload["summary"] == base.payload["summary"]
    assert latest.payload["ai_analysis_status"] == "unavailable"
    assert latest.payload["ai_analysis"] is None
    with factory() as session:
        completed = session.scalar(select(ClassroomBriefAnalysisJob))
    assert completed is not None
    assert (completed.status, completed.attempts, completed.failure_code) == (
        "completed",
        3,
        "ai_brief_analysis_upstream_unavailable",
    )


def test_analysis_worker_stops_after_one_local_attempt_when_configured():
    """A controlled local provider probe must not spend the normal retry budget."""
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    brief_service, factory, _registry = seeded_brief_service(now)
    brief_service.submit(
        IDS["session"], valid_content(), reason="student_manual",
        request_ai_analysis=True, analysis_input=valid_analysis_input()
    )
    calls = [0]

    class UnavailableAnalysisService:
        def generate(self, _source):
            calls[0] += 1
            raise UpstreamUnavailableError("ai_provider_timeout")

    worker = BriefAnalysisJobService(
        factory,
        brief_service,
        UnavailableAnalysisService(),
        clock=lambda: now,
        max_attempts=1,
    )

    assert worker.run_due_jobs("worker-a") == 1
    with factory() as session:
        completed = session.scalar(select(ClassroomBriefAnalysisJob))
    assert completed is not None
    assert calls == [1]
    assert (completed.status, completed.attempts, completed.failure_code) == (
        "completed",
        1,
        "ai_provider_timeout",
    )


def test_invalid_ai_result_keeps_public_fixed_brief_and_hides_private_input():
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    brief_service, factory, _registry = seeded_brief_service(now)
    base = brief_service.submit(
        IDS["session"], valid_content(), reason="student_manual",
        request_ai_analysis=True, analysis_input=valid_analysis_input()
    )

    class UnsafeAnalysisService:
        def generate(self, _source):
            raise UpstreamUnavailableError(
                "ai_brief_analysis_response_invalid", retryable=False
            )

    worker = BriefAnalysisJobService(
        factory,
        brief_service,
        UnsafeAnalysisService(),
        clock=lambda: now,
    )

    assert worker.run_due_jobs("worker-a") == 1

    latest = brief_service.get_latest_brief(IDS["session"])
    encoded = str(latest.payload)
    assert latest.payload["summary"] == base.payload["summary"]
    assert latest.payload["ai_analysis_status"] == "unavailable"
    assert latest.payload["ai_analysis"] is None
    assert "analysis_input" not in encoded
    assert "code_snapshots" not in encoded
    assert "print(records.get" not in encoded


def test_analysis_worker_does_not_retry_coding_plan_authorization_or_policy_rejections():
    """A deterministic provider denial should reach the teacher as unavailable once."""
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    brief_service, factory, _registry = seeded_brief_service(now)
    brief_service.submit(
        IDS["session"], valid_content(), reason="student_manual",
        request_ai_analysis=True, analysis_input=valid_analysis_input()
    )

    class RejectedAnalysisService:
        def generate(self, _source):
            raise UpstreamUnavailableError(
                "ai_provider_authorization_or_policy_rejected", retryable=False
            )

    worker = BriefAnalysisJobService(
        factory,
        brief_service,
        RejectedAnalysisService(),
        clock=lambda: now,
    )

    assert worker.run_due_jobs("worker-a") == 1
    with factory() as session:
        completed = session.scalar(select(ClassroomBriefAnalysisJob))
    assert completed is not None
    assert (completed.status, completed.attempts, completed.failure_code) == (
        "completed",
        1,
        "ai_provider_authorization_or_policy_rejected",
    )


def test_teacher_review_is_an_auditable_overlay_not_a_mutation_of_student_brief():
    """Teacher judgment remains separately attributable from automatic results."""
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    service, _, registry = seeded_brief_service(now)
    original = service.submit(IDS["session"], valid_content(), reason="student_manual")

    review = service.review(
        IDS["session"],
        teacher_id="teacher-1",
        review_input=TeacherReviewInput(
            knowledge_point_reviews=(
                {
                    "knowledge_point_id": "KP_DICT0001",
                    "status": "mastered",
                    "reason": "课堂追问通过。",
                },
            ),
            comment="可进入下一节练习。",
        ),
    )

    registry.validate("teacher-review", review.payload)
    latest = service.get_latest_brief(IDS["session"])
    assert latest.payload == original.payload
    assert review.payload["knowledge_point_reviews"][0]["status"] == "mastered"


def test_brief_rejects_evidence_references_not_owned_by_its_session():
    """Teacher-facing conclusions cannot point to nonexistent raw evidence."""
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    service, _, _ = seeded_brief_service(now)
    invalid = valid_content()
    invalid_point = invalid.knowledge_points[0]
    invalid_point["evidence_refs"] = ["chunk-2#event-1"]

    with pytest.raises(ValidationError, match="brief_evidence_reference_invalid"):
        service.submit(IDS["session"], invalid, reason="student_manual")


class TeacherIdentityGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def resolve_principal(self, bearer_token: str) -> Principal:
        assert bearer_token == "teacher-token"
        return Principal("teacher-1", "teacher-a", bearer_token)

    def require_teacher_owner(
        self, principal: Principal, space_id: str, experiment_id: str
    ) -> None:
        self.calls.append((principal.user_id, space_id, experiment_id))

    def require_student_member(self, principal: Principal, space_id: str) -> None:
        raise AssertionError((principal, space_id))

    def list_student_children(
        self, principal: Principal, space_id: str, parent_algorithm_id: str
    ) -> tuple[StudentChildExperiment, ...]:
        raise AssertionError((principal, space_id, parent_algorithm_id))


def request(app, method: str, path: str, **kwargs: object) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_teacher_routes_can_read_brief_and_append_review_only_after_owner_check():
    """The teacher endpoint reads a final brief without exposing raw storage keys."""
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    brief_service, factory, registry = seeded_brief_service(now)
    brief_service.submit(IDS["session"], valid_content(), reason="student_manual")
    identity = TeacherIdentityGateway()
    app = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=identity,
            plan_service=PlanService(factory, registry, clock=lambda: now),
            assignment_service=AssignmentService(factory, clock=lambda: now),
            brief_service=brief_service,
        ),
    )
    headers = {"Authorization": "Bearer teacher-token"}

    brief_response = request(
        app,
        "GET",
        f"/v1/classroom/teacher/sessions/{IDS['session']}/brief",
        headers=headers,
    )
    review_response = request(
        app,
        "POST",
        f"/v1/classroom/teacher/sessions/{IDS['session']}/reviews",
        headers=headers,
        json={
            "knowledge_point_reviews": [
                {
                    "knowledge_point_id": "KP_DICT0001",
                    "status": "mastered",
                    "reason": "课堂追问通过。",
                }
            ],
            "comment": "可进入下一节练习。",
        },
    )

    assert brief_response.status_code == 200
    assert brief_response.json()["summary"] == "完成主要功能并验证运行结果。"
    assert review_response.status_code == 201
    assert review_response.json()["teacher_id"] == "teacher-1"
    assert identity.calls == [("teacher-1", "space-1", "parent-1")] * 2


def test_teacher_can_read_the_latest_review_or_an_explicit_empty_result():
    """Teacher review history is readable without changing the automatic brief."""
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    _, factory, registry = seeded_brief_service(now)
    review_times = iter((now, now + timedelta(minutes=1)))
    brief_service = BriefService(factory, registry, clock=lambda: next(review_times))
    identity = TeacherIdentityGateway()
    app = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=identity,
            plan_service=PlanService(factory, registry, clock=lambda: now),
            assignment_service=AssignmentService(factory, clock=lambda: now),
            brief_service=brief_service,
        ),
    )
    headers = {"Authorization": "Bearer teacher-token"}
    path = f"/v1/classroom/teacher/sessions/{IDS['session']}/reviews/latest"

    empty_response = request(app, "GET", path, headers=headers)

    first = brief_service.review(
        IDS["session"],
        teacher_id="teacher-1",
        review_input=TeacherReviewInput(
            knowledge_point_reviews=(
                {
                    "knowledge_point_id": "KP_DICT0001",
                    "status": "partial",
                    "reason": "首次复核仍需补充解释。",
                },
            ),
            comment="第一次复核。",
        ),
    )
    second = brief_service.review(
        IDS["session"],
        teacher_id="teacher-1",
        review_input=TeacherReviewInput(
            knowledge_point_reviews=(
                {
                    "knowledge_point_id": "KP_DICT0001",
                    "status": "mastered",
                    "reason": "代码与运行结果均已验证。",
                },
            ),
            comment="第二次复核：证据充分。",
        ),
    )
    latest = brief_service.get_latest_teacher_review(IDS["session"])
    latest_response = request(app, "GET", path, headers=headers)

    assert empty_response.status_code == 200
    assert empty_response.json() == {"review": None}
    assert latest is not None
    assert latest.id == second.id
    assert latest.id != first.id
    assert latest_response.status_code == 200
    assert latest_response.json()["review"]["comment"] == "第二次复核：证据充分。"
    assert latest_response.json()["review"]["knowledge_point_reviews"][0]["status"] == "mastered"
    assert identity.calls == [("teacher-1", "space-1", "parent-1")] * 2


class UnusedStorage:
    def put_bytes(self, key: str, body: bytes, *, content_type: str) -> None:
        raise AssertionError((key, body, content_type))


def test_plugin_can_manually_submit_one_brief_for_its_own_monitor_session():
    """A manually submitted final report uses a plugin token, not browser role claims."""
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    brief_service, factory, registry = seeded_brief_service(now)
    secret = "test-plugin-secret-012345678901234567"
    plugin_service = PluginSessionService(
        factory,
        storage=UnusedStorage(),
        plugin_jwt_secret=secret,
        clock=lambda: now,
        schema_registry=registry,
    )
    app = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=TeacherIdentityGateway(),
            plan_service=PlanService(factory, registry, clock=lambda: now),
            assignment_service=AssignmentService(factory, clock=lambda: now),
            plugin_session_service=plugin_service,
            brief_service=brief_service,
        ),
    )
    token = jwt.encode(
        {
            "sub": IDS["session"],
            "aud": PLUGIN_TOKEN_AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=30)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )
    content = valid_content()

    response = request(
        app,
        "POST",
        f"/v1/classroom/plugin/sessions/{IDS['session']}/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "summary": content.summary,
            "knowledge_points": list(content.knowledge_points),
            "process_overview": list(content.process_overview),
            "issues": list(content.issues),
            "reason": "student_manual",
        },
    )

    assert response.status_code == 201
    assert response.json()["revision"] == 1
    assert response.json()["status"] == "completed"
    first = brief_service.get_latest_brief(IDS["session"])
    assert first.payload["ai_analysis_status"] == "not_requested"

    configured_app = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=TeacherIdentityGateway(),
            plan_service=PlanService(factory, registry, clock=lambda: now),
            assignment_service=AssignmentService(factory, clock=lambda: now),
            plugin_session_service=plugin_service,
            brief_service=brief_service,
            brief_analysis_service=object(),
        ),
    )
    configured_response = request(
        configured_app,
        "POST",
        f"/v1/classroom/plugin/sessions/{IDS['session']}/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "summary": content.summary,
            "knowledge_points": list(content.knowledge_points),
            "process_overview": list(content.process_overview),
            "issues": list(content.issues),
            "reason": "student_manual",
            "request_ai_analysis": True,
            "analysis_input": valid_analysis_input(),
        },
    )

    assert configured_response.status_code == 201
    configured = brief_service.get_latest_brief(IDS["session"])
    assert configured.payload["ai_analysis_status"] == "pending"
    with factory() as session:
        jobs = list(session.scalars(select(ClassroomBriefAnalysisJob)))
    assert len(jobs) == 1


def test_plugin_submit_rejects_malformed_private_input_without_echoing_it():
    """A malformed AI input must not become a job or leak through the HTTP error body."""
    now = datetime(2026, 8, 12, 8, 30, tzinfo=UTC)
    brief_service, factory, registry = seeded_brief_service(now)
    secret = "test-plugin-secret-012345678901234567"
    plugin_service = PluginSessionService(
        factory,
        storage=UnusedStorage(),
        plugin_jwt_secret=secret,
        clock=lambda: now,
        schema_registry=registry,
    )
    app = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=TeacherIdentityGateway(),
            plan_service=PlanService(factory, registry, clock=lambda: now),
            assignment_service=AssignmentService(factory, clock=lambda: now),
            plugin_session_service=plugin_service,
            brief_service=brief_service,
        ),
    )
    token = jwt.encode(
        {
            "sub": IDS["session"],
            "aud": PLUGIN_TOKEN_AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=30)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )
    content = valid_content()
    private_marker = "private-prompt-not-for-teacher"

    response = request(
        app,
        "POST",
        f"/v1/classroom/plugin/sessions/{IDS['session']}/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "summary": content.summary,
            "knowledge_points": list(content.knowledge_points),
            "process_overview": list(content.process_overview),
            "issues": list(content.issues),
            "reason": "student_manual",
            "request_ai_analysis": True,
            "analysis_input": {"lesson": {"title": private_marker}},
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["error"]["code"] == "classroom_request_validation_failed"
    assert payload["error"]["retryable"] is False
    assert "detail" not in payload
    assert private_marker not in response.text
    with factory() as session:
        assert list(session.scalars(select(ClassroomBriefAnalysisJob))) == []


def test_teacher_can_advance_the_hard_deadline_by_ending_class_early():
    """Teacher early-end is authorized against the same parent experiment as brief review."""
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    brief_service, factory, registry = seeded_brief_service(now)
    identity = TeacherIdentityGateway()
    app = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=identity,
            plan_service=PlanService(factory, registry, clock=lambda: now),
            assignment_service=AssignmentService(factory, clock=lambda: now),
            brief_service=brief_service,
            deadline_service=DeadlineService(factory, brief_service, clock=lambda: now),
        ),
    )
    actual_end_at = now + timedelta(minutes=20)

    response = request(
        app,
        "POST",
        f"/v1/classroom/teacher/sessions/{IDS['session']}/end",
        headers={"Authorization": "Bearer teacher-token"},
        json={"actual_end_at": actual_end_at.isoformat()},
    )

    assert response.status_code == 200
    assert response.json()["actual_end_at"] == actual_end_at.isoformat()
    assert response.json()["evidence_cutoff_at"] == (actual_end_at + timedelta(minutes=15)).isoformat()
    assert identity.calls == [("teacher-1", "space-1", "parent-1")]
from classroom_sync.auth.fincolab import Principal
