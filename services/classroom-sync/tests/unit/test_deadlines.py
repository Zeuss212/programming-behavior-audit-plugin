from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from classroom_sync.application import ClassroomServices
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.models import (
    Base,
    ExperimentPlanBinding,
    MonitorSession,
    PlanVersion,
    StudentAssignment,
)
from classroom_sync.services.briefs import BriefService
from classroom_sync.services.deadlines import DeadlineService
from classroom_sync.worker import run_due_classroom_jobs

IDS = {
    "binding": "c65f6e60-ecc1-4e36-9c0d-5ed2d0f3b67d",
    "assignment": "d7647a1a-89c3-4c6d-9b5f-7e803918aa9d",
    "session": "23d7d803-524a-4d9f-b8bd-152a540dba12",
    "plan": "2b16b5c0-4e58-48f9-9448-9067de005e4a",
    "plan_version": "fb248bd9-8b73-4f2f-80dd-e9365f373e13",
    "profile": "0997bbf3-4f1b-405f-81b3-47c17fd315a8",
}


def seeded_deadline_service(now: datetime):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(
            PlanVersion(
                id=IDS["plan_version"],
                plan_id=IDS["plan"],
                profile_id=IDS["profile"],
                version=1,
                source_draft_revision=0,
                space_id="space-1",
                parent_algorithm_id="parent-1",
                profile={
                    "knowledge_points": [
                        {"id": "KP_DICT0001", "name": "字典读取"},
                    ]
                },
                content_hash="a" * 64,
                scheduled_start_at=now,
                scheduled_end_at=now + timedelta(minutes=30),
                ai_policy="prohibited",
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
                last_activity_at=now + timedelta(minutes=10),
                last_heartbeat_at=now + timedelta(minutes=10),
                last_contiguous_sequence=0,
                missing_ranges=[],
                completeness="complete",
                submission_reason=None,
                active_slot=1,
                created_at=now,
                updated_at=now,
            )
        )
    registry = ClassroomSchemaRegistry(
        Path(__file__).resolve().parents[4] / "contracts" / "classroom" / "v1"
    )
    brief_service = BriefService(factory, registry, clock=lambda: now)
    return DeadlineService(factory, brief_service, clock=lambda: now), factory


def test_deadline_is_exactly_fifteen_minutes_after_actual_end_and_closes_to_partial_brief():
    """Absent manual submission still gives the teacher one truthful partial report."""
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    service, _factory = seeded_deadline_service(now)

    job = service.schedule_session_deadline(IDS["session"])
    assert job.run_at == now + timedelta(minutes=45)
    assert service.claim_due_jobs("worker-a", now + timedelta(minutes=44, seconds=59)) == ()
    claimed = service.claim_due_jobs("worker-a", now + timedelta(minutes=45))
    assert [item.id for item in claimed] == [job.id]

    brief = service.close_session(IDS["session"], worker_id="worker-a")

    assert brief.status == "partial"
    assert brief.payload["submission_reason"] == "system_deadline"
    assert brief.payload["knowledge_points"][0]["status"] == "not_demonstrated"
    assert brief.payload["knowledge_points"][0]["evidence_refs"] == ["session#missing-evidence"]


def test_early_teacher_end_moves_deadline_and_expired_worker_lease_can_be_reclaimed():
    """Only an earlier teacher end is accepted; a crashed worker does not lose the job."""
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    service, _factory = seeded_deadline_service(now)
    service.schedule_session_deadline(IDS["session"])

    updated = service.record_teacher_end(IDS["session"], now + timedelta(minutes=20))
    assert updated.actual_end_at == now + timedelta(minutes=20)
    assert updated.evidence_cutoff_at == now + timedelta(minutes=35)
    first_claim = service.claim_due_jobs("worker-a", now + timedelta(minutes=35))
    assert len(first_claim) == 1
    assert service.claim_due_jobs("worker-b", now + timedelta(minutes=35, seconds=59)) == ()
    recovered_claim = service.claim_due_jobs("worker-b", now + timedelta(minutes=36, seconds=1))

    assert len(recovered_claim) == 1
    assert recovered_claim[0].attempts == 2


def test_worker_claimed_job_closes_once_and_subsequent_ticks_are_noops():
    """Repeated worker ticks leave the single logical auto-submission unchanged."""
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    service, _factory = seeded_deadline_service(now)
    service.schedule_session_deadline(IDS["session"])
    original_claim = service.claim_due_jobs("worker-a", now + timedelta(minutes=45))

    assert len(original_claim) == 1
    assert service.close_session(IDS["session"], worker_id="worker-a").revision == 1
    assert service.claim_due_jobs("worker-a", now + timedelta(days=1)) == ()


def test_classroom_worker_runs_deadline_and_configured_analysis_families_in_one_tick():
    """Removing either durable job family from the worker loop breaks this total."""
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    deadline_service, _factory = seeded_deadline_service(now)
    deadline_service.schedule_session_deadline(IDS["session"])
    deadline_service.record_teacher_end(IDS["session"], now - timedelta(minutes=15))

    class AnalysisService:
        def __init__(self) -> None:
            self.worker_ids: list[str] = []

        def run_due_jobs(self, worker_id: str) -> int:
            self.worker_ids.append(worker_id)
            return 1

    analysis_service = AnalysisService()
    services = ClassroomServices(
        identity_gateway=object(),
        plan_service=object(),
        assignment_service=object(),
        deadline_service=deadline_service,
        brief_analysis_service=analysis_service,
    )

    assert run_due_classroom_jobs(services, "worker-a") == 2
    assert analysis_service.worker_ids == ["worker-a"]
