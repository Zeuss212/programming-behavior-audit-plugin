from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from myextension.analysis_job_store import AnalysisJobStore
from myextension.canonical_json import sha256_json
from myextension.dimension_profile_store import DimensionProfileStore
from myextension.platform_client import BriefSubmissionReceipt
from myextension.platform_context_store import PlatformContextStore
from myextension.platform_deadline_worker import PlatformDeadlineWorker
from myextension.review_store import ReviewStore
from myextension.session_log_service import SessionLogService
from myextension.session_store import SessionStore
from myextension.submission_coordinator import SubmissionCoordinator
from myextension.tests.test_assessment_profile import make_assessment_profile
from myextension.tests.test_platform_registration import context
from myextension.tests.test_session_store import batch


def automatic_dictionary_profile() -> dict[str, object]:
    profile = make_assessment_profile(confirmed=False)
    profile["knowledge_points"][0]["automatic_evaluation"] = {
        "mode": "all",
        "summary": "创建字典、使用带默认值的安全查询并成功运行。",
        "requirements": [
            {"kind": "successful_execution"},
            {"kind": "dict_literal_assignment"},
            {"kind": "dict_get_with_default"},
        ],
    }
    knowledge_hash = sha256_json(
        {
            "problem_context": profile["problem_context"],
            "knowledge_points": profile["knowledge_points"],
        }
    )
    profile["confirmations"] = {
        "knowledge_points_hash": knowledge_hash,
        "tests_hash": sha256_json(
            {
                "problem_context": profile["problem_context"],
                "knowledge_points_hash": knowledge_hash,
                "assessment_tests": profile["assessment_tests"],
            }
        ),
    }
    return profile


def append_synthetic_event(
    store: SessionStore,
    session_id: str,
    *,
    sequence: int,
    segment_id: str,
    **fields: object,
) -> None:
    payload = batch(session_id, sequence=sequence, segment_id=segment_id)
    segments = payload["segments"]
    assert isinstance(segments, list)
    event = segments[0]
    assert isinstance(event, dict)
    event.update(fields)
    payload["content_hash"] = sha256_json(
        {
            "first_sequence": sequence,
            "last_sequence": sequence,
            "segments": segments,
        }
    )
    store.append_batch(session_id, **payload)


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
        request_ai_analysis=False,
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
    assert str(UUID(str(client.payloads[0]["submission_id"]))) == client.payloads[0][
        "submission_id"
    ]
    assert client.payloads[0]["knowledge_points"][0]["status"] == "not_demonstrated"
    assert client.payloads[0]["request_ai_analysis"] is False
    assert "analysis_input" not in client.payloads[0]
    assert "ai_analysis_status" not in client.payloads[0]
    assert session_store.read(context().session_id)["status"] == "finalized"
    assert service.get_classroom_brief(context().session_id) is not None


def test_authorized_submission_includes_one_private_bounded_analysis_input(
    tmp_path: Path,
) -> None:
    class Entry:
        sequence = 1
        first_event_sequence = 1
        last_event_sequence = 2
        state = "delivered"

    class Outbox:
        def flush_once(self):
            return None

        def list_entries(self, _session_id):
            return [Entry()]

    class Client:
        def __init__(self) -> None:
            self.payload: dict[str, object] | None = None

        def submit_brief(self, stored_context, payload):
            self.payload = payload
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
    append_synthetic_event(
        session_store,
        context().session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000411",
        segment_type="code_writing",
        cell_source='records = {"A": 1}\nprint(records.get("B", 0))',
    )
    append_synthetic_event(
        session_store,
        context().session_id,
        sequence=2,
        segment_id="20000000-0000-4000-8000-000000000412",
        segment_type="code_execution",
        execution_result="success",
    )
    context_store = PlatformContextStore(tmp_path)
    context_store.save_registered_context(context())
    service = SessionLogService(
        root=tmp_path,
        session_store=session_store,
        job_store=AnalysisJobStore(tmp_path),
        review_store=ReviewStore(tmp_path),
    )
    client = Client()
    coordinator = SubmissionCoordinator(
        tmp_path,
        session_store=session_store,
        session_log_service=service,
        outbox=Outbox(),
        client=client,
        context_store=context_store,
    )

    coordinator.submit(
        context().session_id,
        reason="student_manual",
        cutoff_at=datetime(2026, 8, 13, 10, 15, tzinfo=timezone.utc),
        request_ai_analysis=True,
    )

    assert client.payload is not None
    assert client.payload["request_ai_analysis"] is True
    analysis_input = client.payload["analysis_input"]
    assert analysis_input["code_snapshots"]
    assert analysis_input["evidence_events"][0]["event_id"] == "chunk-1#event-1"
    assert "records" in analysis_input["code_snapshots"][0]["source"]


def test_submission_requires_review_when_supporting_events_were_not_delivered(
    tmp_path: Path,
) -> None:
    class Outbox:
        def flush_once(self):
            return None

        def list_entries(self, _session_id):
            return []

    class Client:
        def __init__(self) -> None:
            self.payload: dict[str, object] | None = None

        def submit_brief(self, stored_context, payload):
            self.payload = payload
            return BriefSubmissionReceipt(
                brief_id="8a15f505-5d7e-46fe-a0e0-75dd5c336493",
                session_id=stored_context.session_id,
                revision=1,
                status="completed",
            )

    profiles = DimensionProfileStore(tmp_path)
    draft = profiles.create_draft(automatic_dictionary_profile())
    profile = profiles.publish(str(draft["profile_id"]))
    session_store = SessionStore(tmp_path)
    session_store.start(
        problem_id=str(profile["problem_id"]),
        profile=profile,
        session_id=context().session_id,
    )
    append_synthetic_event(
        session_store,
        context().session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000401",
        cell_source='records = {"甲": 91, "乙": 88}\nprint(records.get("丙", "未找到"))',
    )
    append_synthetic_event(
        session_store,
        context().session_id,
        sequence=2,
        segment_id="20000000-0000-4000-8000-000000000402",
        segment_type="code_execution",
        execution_result="success",
    )
    context_store = PlatformContextStore(tmp_path)
    context_store.save_registered_context(context())
    service = SessionLogService(
        root=tmp_path,
        session_store=session_store,
        job_store=AnalysisJobStore(tmp_path),
        review_store=ReviewStore(tmp_path),
    )
    client = Client()
    coordinator = SubmissionCoordinator(
        tmp_path,
        session_store=session_store,
        session_log_service=service,
        outbox=Outbox(),
        client=client,
        context_store=context_store,
    )

    coordinator.submit(
        context().session_id,
        reason="student_manual",
        cutoff_at=datetime(2026, 8, 13, 10, 15, tzinfo=timezone.utc),
    )

    assert client.payload is not None
    rows = client.payload["knowledge_points"]
    assert isinstance(rows, list)
    assert rows[0]["status"] == "review_required"
    assert rows[0]["evidence_refs"] == ["session#missing-evidence"]
    assert "records" not in str(rows[0])


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


def test_delivered_evidence_is_not_truncated_before_per_point_selection() -> None:
    class Entry:
        sequence = 1
        first_event_sequence = 1
        last_event_sequence = 11

    detail = {
        "behavior_events": [
            {"session_seq": sequence, "segment_type": "code_writing"}
            for sequence in range(1, 12)
        ]
    }

    assert SubmissionCoordinator._evidence_refs(detail, [Entry()]) == [
        f"chunk-1#event-{sequence}" for sequence in range(1, 12)
    ]
