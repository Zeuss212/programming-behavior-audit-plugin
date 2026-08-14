"""Run deterministic local fault scenarios for the classroom integration.

This runner intentionally uses injected clocks and in-memory adapters.  It is
for local verification only: production containers never expose a client-set
clock or a fault-control endpoint.  Each observation keeps the fields needed
to review recovery without persisting credentials or raw evidence.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SERVICE_SOURCE = _REPOSITORY_ROOT / "services" / "classroom-sync" / "src"
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))
if str(_SERVICE_SOURCE) not in sys.path:
    sys.path.insert(0, str(_SERVICE_SOURCE))

from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import AuthorizationError
from classroom_sync.models import (
    Base,
    ExperimentPlanBinding,
    MonitorSession,
    PlanVersion,
    StudentAssignment,
    StudentBrief,
)
from classroom_sync.models import (
    EvidenceChunk as RemoteEvidenceChunk,
)
from classroom_sync.services.briefs import BriefService
from classroom_sync.services.deadlines import DeadlineService
from classroom_sync.services.sessions import PluginSessionService
from classroom_sync.storage import StorageUnavailable
from classroom_sync.worker import run_due_deadlines
from sqlalchemy import create_engine as create_sqlalchemy_engine
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from myextension.analysis_job_store import AnalysisJobStore
from myextension.dimension_profile_store import DimensionProfileStore
from myextension.evidence_outbox import EvidenceChunk, EvidenceOutbox
from myextension.platform_client import (
    BriefSubmissionReceipt,
    EvidenceUploadReceipt,
    PlatformClientError,
)
from myextension.platform_context_store import PlatformContextStore
from myextension.review_store import ReviewStore
from myextension.session_log_service import SessionLogService
from myextension.session_store import SessionStore
from myextension.submission_coordinator import SubmissionCoordinator
from myextension.tests.test_assessment_profile import make_assessment_profile
from myextension.tests.test_platform_registration import context

BASE_TIME = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)


class FaultObservation:
    """One credential-free recovery record suitable for JSON output."""

    def __init__(
        self,
        *,
        scenario: str,
        before_session_id: str,
        after_session_id: str,
        last_sequence: int,
        missing_ranges: tuple[tuple[int, int], ...],
        submission_reason: str | None,
        brief_revision: int | None,
        object_count: int,
        outcome: str,
        details: dict[str, object],
    ) -> None:
        self.scenario = scenario
        self.before_session_id = before_session_id
        self.after_session_id = after_session_id
        self.last_sequence = last_sequence
        self.missing_ranges = missing_ranges
        self.submission_reason = submission_reason
        self.brief_revision = brief_revision
        self.object_count = object_count
        self.outcome = outcome
        self.details = details

    def to_dict(self) -> dict[str, object]:
        return {
            "after_session_id": self.after_session_id,
            "before_session_id": self.before_session_id,
            "brief_revision": self.brief_revision,
            "details": self.details,
            "last_sequence": self.last_sequence,
            "missing_ranges": [
                {"from": start, "to": end} for start, end in self.missing_ranges
            ],
            "object_count": self.object_count,
            "outcome": self.outcome,
            "scenario": self.scenario,
            "submission_reason": self.submission_reason,
        }


class ManualClock:
    """Clock advanced only by the local fault harness."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, duration: timedelta) -> None:
        self.now += duration


class MemoryStorage:
    """Minimal private object storage whose availability can be changed in tests."""

    def __init__(self) -> None:
        self.available = True
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, body: bytes, *, content_type: str) -> None:
        del content_type
        if not self.available:
            raise StorageUnavailable("synthetic_minio_unavailable")
        self.objects.setdefault(key, body)


class SyncFixture:
    """A real classroom-sync service graph backed by an isolated SQLite database."""

    def __init__(self) -> None:
        self.clock = ManualClock(BASE_TIME)
        engine = create_sqlalchemy_engine("sqlite://")

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys = ON")

        Base.metadata.create_all(engine)
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.storage = MemoryStorage()
        self.assignment_id = "11111111-1111-4111-8111-111111111111"
        self.plan_id = "22222222-2222-4222-8222-222222222222"
        self._seed()
        registry = ClassroomSchemaRegistry(_REPOSITORY_ROOT / "contracts" / "classroom" / "v1")
        self.briefs = BriefService(self.factory, registry, clock=self.clock)
        self.sessions = PluginSessionService(
            self.factory,
            storage=self.storage,
            plugin_jwt_secret="local-fault-smoke-plugin-secret-012345678901234567",
            clock=self.clock,
            schema_registry=registry,
        )
        self.deadlines = DeadlineService(self.factory, self.briefs, clock=self.clock)

    def _seed(self) -> None:
        scheduled_end = self.clock.now + timedelta(minutes=30)
        with self.factory.begin() as session:
            session.add(
                ExperimentPlanBinding(
                    id="binding-1",
                    space_id="course-001",
                    parent_algorithm_id="parent-experiment-001",
                    plan_id=self.plan_id,
                    plan_version=1,
                    teacher_id="teacher001",
                    created_at=self.clock.now,
                    updated_at=None,
                )
            )
            session.add(
                PlanVersion(
                    id="plan-version-1",
                    plan_id=self.plan_id,
                    profile_id="profile-1",
                    version=1,
                    source_draft_revision=0,
                    space_id="course-001",
                    parent_algorithm_id="parent-experiment-001",
                    profile={
                        "schema_version": 2,
                        "title": "故障恢复验证",
                        "knowledge_points": [{"id": "KP_1", "name": "恢复验证"}],
                    },
                    content_hash="a" * 64,
                    scheduled_start_at=self.clock.now,
                    scheduled_end_at=scheduled_end,
                    ai_policy="prohibited",
                    published_at=self.clock.now,
                    teacher_id="teacher001",
                )
            )
            session.add(
                StudentAssignment(
                    id=self.assignment_id,
                    binding_id="binding-1",
                    space_id="course-001",
                    parent_algorithm_id="parent-experiment-001",
                    child_algorithm_id="child-1",
                    workbench_id="workbench-1",
                    student_id="student001",
                    plan_id=self.plan_id,
                    plan_version=1,
                    status="ready",
                    scheduled_start_at=self.clock.now,
                    scheduled_end_at=scheduled_end,
                    accepted_at=self.clock.now,
                    created_at=self.clock.now,
                    updated_at=self.clock.now,
                )
            )

    def register(self) -> tuple[str, str]:
        ticket = self.sessions.issue_ticket(self.assignment_id)
        credentials = self.sessions.register(ticket.ticket, plugin_instance_id="local-fault-smoke")
        return credentials.session_id, credentials.access_token

    def monitor_session(self, session_id: str) -> MonitorSession:
        with self.factory() as session:
            monitor_session = session.get(MonitorSession, session_id)
            if monitor_session is None:
                raise AssertionError("Fault fixture did not persist its monitor session.")
            return monitor_session

    def latest_brief(self, session_id: str) -> StudentBrief | None:
        with self.factory() as session:
            return session.scalar(
                select(StudentBrief)
                .where(StudentBrief.session_id == session_id)
                .order_by(StudentBrief.revision.desc())
                .limit(1)
            )

    def evidence_count(self, session_id: str) -> int:
        with self.factory() as session:
            return len(
                session.scalars(
                    select(RemoteEvidenceChunk).where(RemoteEvidenceChunk.session_id == session_id)
                ).all()
            )


def _evidence_body() -> bytes:
    payload = json.dumps(
        {"events": [{"sequence": 1, "type": "notebook_run", "source": "fault-smoke"}]},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return gzip.compress(payload, mtime=0)


def _ranges(monitor_session: MonitorSession) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    for raw_range in monitor_session.missing_ranges:
        start = raw_range.get("from")
        end = raw_range.get("to")
        if isinstance(start, int) and isinstance(end, int):
            result.append((start, end))
    return tuple(result)


def _browser_reload() -> FaultObservation:
    fixture = SyncFixture()
    session_id, access_token = fixture.register()
    first = fixture.sessions.put_evidence_chunk(
        access_token,
        session_id=session_id,
        sequence=1,
        body=_evidence_body(),
        first_event_sequence=1,
        last_event_sequence=1,
    )
    resumed_session_id, resumed_token = fixture.register()
    repeated = fixture.sessions.put_evidence_chunk(
        resumed_token,
        session_id=resumed_session_id,
        sequence=1,
        body=_evidence_body(),
        first_event_sequence=1,
        last_event_sequence=1,
    )
    assert resumed_session_id == session_id
    assert repeated.id == first.id
    monitor_session = fixture.monitor_session(session_id)
    assert fixture.evidence_count(session_id) == 1
    return FaultObservation(
        scenario="browser_reload",
        before_session_id=session_id,
        after_session_id=resumed_session_id,
        last_sequence=monitor_session.last_contiguous_sequence,
        missing_ranges=_ranges(monitor_session),
        submission_reason=monitor_session.submission_reason,
        brief_revision=None,
        object_count=len(fixture.storage.objects),
        outcome="resumed_without_duplicate_evidence",
        details={"evidence_ids_equal": True},
    )


def _ticket_replay() -> FaultObservation:
    fixture = SyncFixture()
    ticket = fixture.sessions.issue_ticket(fixture.assignment_id)
    credentials = fixture.sessions.register(ticket.ticket, plugin_instance_id="local-fault-smoke")
    try:
        fixture.sessions.register(ticket.ticket, plugin_instance_id="replay")
    except AuthorizationError as error:
        if str(error) != "ticket_already_consumed":
            raise AssertionError("Ticket replay produced an unexpected authorization error.") from error
    else:
        raise AssertionError("A consumed launch ticket was accepted again.")
    monitor_session = fixture.monitor_session(credentials.session_id)
    return FaultObservation(
        scenario="ticket_replay",
        before_session_id=credentials.session_id,
        after_session_id=credentials.session_id,
        last_sequence=monitor_session.last_contiguous_sequence,
        missing_ranges=_ranges(monitor_session),
        submission_reason=monitor_session.submission_reason,
        brief_revision=None,
        object_count=len(fixture.storage.objects),
        outcome="ticket_already_consumed",
        details={"replay_rejected": True},
    )


def _deadline_worker_restart() -> FaultObservation:
    fixture = SyncFixture()
    session_id, _access_token = fixture.register()
    fixture.deadlines.record_teacher_end(session_id, BASE_TIME + timedelta(minutes=20))
    fixture.clock.advance(timedelta(minutes=34, seconds=59))
    assert fixture.deadlines.claim_due_jobs("worker-before-cutoff") == ()
    assert fixture.monitor_session(session_id).status == "collecting"
    fixture.clock.advance(timedelta(seconds=1))
    first_claim = fixture.deadlines.claim_due_jobs("worker-before-restart")
    assert len(first_claim) == 1
    fixture.clock.advance(timedelta(seconds=61))
    assert run_due_deadlines(fixture.deadlines, "worker-after-restart") == 1
    monitor_session = fixture.monitor_session(session_id)
    brief = fixture.latest_brief(session_id)
    if brief is None:
        raise AssertionError("Replacement worker did not create the deadline brief.")
    return FaultObservation(
        scenario="deadline_worker_restart",
        before_session_id=session_id,
        after_session_id=monitor_session.id,
        last_sequence=monitor_session.last_contiguous_sequence,
        missing_ranges=_ranges(monitor_session),
        submission_reason=monitor_session.submission_reason,
        brief_revision=brief.revision,
        object_count=len(fixture.storage.objects),
        outcome="reclaimed_and_closed",
        details={
            "active_before_cutoff": True,
            "teacher_end_recorded": True,
            "worker_claim_attempts": 2,
        },
    )


def _evidence_network_partition(root: Path) -> FaultObservation:
    registered = context()
    now = ManualClock(BASE_TIME)

    class ContextStore:
        def read_registered_context(self):
            return registered

        def save_registered_context(self, value):
            return value

    class UnavailableClient:
        def upload_evidence(self, *_args: object, **_kwargs: object) -> EvidenceUploadReceipt:
            raise PlatformClientError("platform_evidence_failed")

    class DeliveryClient:
        def upload_evidence(
            self,
            stored_context: object,
            *,
            sequence: int,
            body: bytes,
            first_event_sequence: int,
            last_event_sequence: int,
        ) -> EvidenceUploadReceipt:
            del first_event_sequence, last_event_sequence
            if stored_context != registered:
                raise AssertionError("Outbox used a different stored platform context.")
            return EvidenceUploadReceipt(
                evidence_id="33333333-3333-4333-8333-333333333333",
                session_id=registered.session_id,
                sequence=sequence,
                content_sha256=__import__("hashlib").sha256(body).hexdigest(),
            )

    outbox_root = root / "network-partition"
    outbox = EvidenceOutbox(
        outbox_root,
        client=UnavailableClient(),
        context_store=ContextStore(),
        clock=now,
        jitter=lambda: 0,
    )
    entry = outbox.enqueue(
        registered.session_id,
        EvidenceChunk(
            sequence=1,
            first_event_sequence=1,
            last_event_sequence=1,
            body=_evidence_body(),
            created_at=now(),
        ),
    )
    first_report = outbox.flush_once()
    assert first_report.deferred == 1
    now.advance(timedelta(seconds=1))
    resumed = EvidenceOutbox(
        outbox_root,
        client=DeliveryClient(),
        context_store=ContextStore(),
        clock=now,
        jitter=lambda: 0,
    )
    second_report = resumed.flush_once()
    assert second_report.delivered == 1
    entries = resumed.list_entries(registered.session_id)
    assert len(entries) == 1
    assert entries[0].content_sha256 == entry.content_sha256
    assert entries[0].state == "delivered"
    return FaultObservation(
        scenario="evidence_network_partition",
        before_session_id=registered.session_id,
        after_session_id=registered.session_id,
        last_sequence=entries[0].sequence,
        missing_ranges=(),
        submission_reason=None,
        brief_revision=None,
        object_count=len(entries),
        outcome="deferred_then_delivered",
        details={"deferred_attempts": 1, "delivery_attempts": 1},
    )


def _submission_retry(root: Path) -> FaultObservation:
    registered = context()
    profiles = DimensionProfileStore(root)
    draft = profiles.create_draft(make_assessment_profile())
    profile = profiles.publish(str(draft["profile_id"]))
    session_store = SessionStore(root)
    session_store.start(
        problem_id=str(profile["problem_id"]),
        profile=profile,
        session_id=registered.session_id,
    )
    context_store = PlatformContextStore(root)
    context_store.save_registered_context(registered)
    log_service = SessionLogService(
        root=root,
        session_store=session_store,
        job_store=AnalysisJobStore(root),
        review_store=ReviewStore(root),
    )

    class EmptyOutbox:
        def flush_once(self) -> None:
            return None

        def list_entries(self, _session_id: str) -> list[object]:
            return []

    class FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        def submit_brief(self, stored_context: object, _payload: dict[str, object]) -> BriefSubmissionReceipt:
            if stored_context != registered:
                raise AssertionError("Submission used a different stored platform context.")
            self.calls += 1
            if self.calls == 1:
                raise PlatformClientError("platform_submission_failed")
            return BriefSubmissionReceipt(
                brief_id="44444444-4444-4444-8444-444444444444",
                session_id=registered.session_id,
                revision=1,
                status="completed",
            )

    client = FlakyClient()
    coordinator = SubmissionCoordinator(
        root,
        session_store=session_store,
        session_log_service=log_service,
        outbox=EmptyOutbox(),
        client=client,
        context_store=context_store,
    )
    cutoff = BASE_TIME + timedelta(minutes=45)
    pending = coordinator.submit(
        registered.session_id,
        reason="student_manual",
        cutoff_at=cutoff,
    )
    submitted = coordinator.submit(
        registered.session_id,
        reason="student_manual",
        cutoff_at=cutoff,
    )
    repeated = coordinator.submit(
        registered.session_id,
        reason="student_manual",
        cutoff_at=cutoff,
    )
    assert pending.status == "pending_upload"
    assert submitted == repeated
    assert submitted.status == "submitted"
    assert client.calls == 2
    session = session_store.read(registered.session_id)
    return FaultObservation(
        scenario="submission_retry",
        before_session_id=registered.session_id,
        after_session_id=registered.session_id,
        last_sequence=int(session["last_contiguous_sequence"]),
        missing_ranges=(),
        submission_reason=submitted.reason,
        brief_revision=submitted.revision,
        object_count=0,
        outcome="pending_upload_then_submitted_once",
        details={"remote_submit_attempts": client.calls},
    )


def run_all(root: Path) -> tuple[FaultObservation, ...]:
    """Execute every deterministic fault scenario in separate local roots."""

    root.mkdir(parents=True, exist_ok=True)
    return (
        _browser_reload(),
        _deadline_worker_restart(),
        _evidence_network_partition(root),
        _submission_retry(root / "submission-retry"),
        _ticket_replay(),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run the complete deterministic local fault matrix.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.all:
        raise SystemExit("Pass --all to run the local fault matrix.")
    with TemporaryDirectory(prefix="classroom-fault-smoke-") as temporary:
        observations = run_all(Path(temporary))
    print(json.dumps([item.to_dict() for item in observations], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
