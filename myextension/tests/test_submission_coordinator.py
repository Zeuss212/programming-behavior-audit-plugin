from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from myextension.analysis_job_store import AnalysisJobStore
from myextension.dimension_profile_store import DimensionProfileStore
from myextension.platform_context_store import PlatformContextStore
from myextension.platform_client import BriefSubmissionReceipt
from myextension.platform_deadline_worker import PlatformDeadlineWorker
from myextension.review_store import ReviewStore
from myextension.session_log_service import SessionLogService
from myextension.session_store import SessionStore
from myextension.submission_coordinator import SubmissionCoordinator
from myextension.tests.test_assessment_profile import make_assessment_profile
from myextension.tests.test_platform_registration import context


def test_manual_and_deadline_submission_share_one_local_idempotent_result(
    tmp_path: Path,
) -> None:
    class Outbox:
        def __init__(self) -> None:
            self.flushes = 0

        def flush_once(self):
            self.flushes += 1

        def list_entries(self, _session_id):
            return []

    class Client:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def submit_brief(self, stored_context, payload):
            assert stored_context == context()
            self.payloads.append(payload)
            return BriefSubmissionReceipt(
                brief_id="8a15f505-5d7e-46fe-a0e0-75dd5c336493",
                session_id=stored_context.session_id,
                revision=1,
                status="completed",
            )

    profiles = DimensionProfileStore(tmp_path)
    draft = profiles.create_draft(make_assessment_profile())
    profile = profiles.publish(str(draft["profile_id"]))
    session_store = SessionStore(tmp_path)
    session_store.start(
        problem_id=str(profile["problem_id"]),
        profile=profile,
        session_id=context().session_id,
    )
    context_store = PlatformContextStore(tmp_path)
    context_store.save_registered_context(context())
    service = SessionLogService(
        root=tmp_path,
        session_store=session_store,
        job_store=AnalysisJobStore(tmp_path),
        review_store=ReviewStore(tmp_path),
    )
    outbox = Outbox()
    client = Client()
    coordinator = SubmissionCoordinator(
        tmp_path,
        session_store=session_store,
        session_log_service=service,
        outbox=outbox,
        client=client,
        context_store=context_store,
    )
    cutoff = datetime(2026, 8, 13, 10, 15, tzinfo=timezone.utc)

    manual = coordinator.submit(
        context().session_id,
        reason="student_manual",
        cutoff_at=cutoff,
    )
    deadline = coordinator.submit(
        context().session_id,
        reason="system_deadline",
        cutoff_at=cutoff,
    )

    assert manual == deadline
    assert manual.status == "submitted"
    assert manual.reason == "student_manual"
    assert outbox.flushes == 1
    assert len(client.payloads) == 1
    assert client.payloads[0]["reason"] == "student_manual"
    assert client.payloads[0]["knowledge_points"][0]["status"] == "not_demonstrated"
    assert "ai_analysis_status" not in client.payloads[0]
    assert session_store.read(context().session_id)["status"] == "finalized"
    assert service.get_classroom_brief(context().session_id) is not None


def test_deadline_worker_calls_the_same_coordinator_only_after_the_cutoff(
    tmp_path: Path,
) -> None:
    class Coordinator:
        def __init__(self) -> None:
            self.calls = []

        def submit(self, session_id, *, reason, cutoff_at):
            self.calls.append((session_id, reason, cutoff_at))
            return "submitted"

    context_store = PlatformContextStore(tmp_path)
    context_store.save_registered_context(context())
    coordinator = Coordinator()
    cutoff = datetime(2026, 8, 12, 8, 45, tzinfo=timezone.utc)
    worker = PlatformDeadlineWorker(
        context_store,
        coordinator,
        now=lambda: cutoff,
        interval_seconds=30,
    )

    assert worker.run_once() == ["submitted"]
    assert coordinator.calls == [
        (context().session_id, "system_deadline", cutoff)
    ]
