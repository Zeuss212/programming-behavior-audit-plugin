from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from gzip import decompress
from pathlib import Path

import pytest

from myextension.analysis_worker import AnalysisWorker
from myextension.analysis_job_store import AnalysisJobStore
from myextension.dimension_profile_store import DimensionProfileStore
from myextension.review_store import ReviewStore
from myextension.session_log_artifacts import build_evidence_chunk
from myextension.session_log_service import SessionLogService
from myextension.session_store import SessionIntegrityError, SessionStore
from myextension.tests.test_assessment_profile import make_assessment_profile
from myextension.tests.test_session_log_service import (
    append_synthetic_event,
    attached_job_fixture,
    provider_response,
    terminal_session_fixture,
)


def _service(store, *, job_store=None) -> SessionLogService:
    root = Path(store.root)
    return SessionLogService(
        root=root,
        session_store=store,
        job_store=job_store or AnalysisJobStore(root),
        review_store=ReviewStore(root),
    )


def _finalized_behavior_session(tmp_path: Path):
    profiles = DimensionProfileStore(tmp_path)
    draft = profiles.create_draft(make_assessment_profile())
    profile = profiles.publish(str(draft["profile_id"]))
    store = SessionStore(tmp_path)
    session = store.start(
        problem_id=str(profile["problem_id"]),
        profile=profile,
    )
    session_id = str(session["session_id"])
    rows = [
        {
            "segment_id": "21000000-0000-4000-8000-000000000001",
            "segment_type": "code_writing",
            "inserted_char_count": 12,
            "cell_source": "score = 60",
        },
        {
            "segment_id": "21000000-0000-4000-8000-000000000002",
            "segment_type": "code_deletion",
            "deleted_char_count": 2,
            "deleted_content": "00",
            "cell_source": "score = 6",
        },
        {
            "segment_id": "21000000-0000-4000-8000-000000000003",
            "segment_type": "code_paste",
            "paste_char_count": 8,
            "had_paste": True,
            "cell_source": "score = 60",
        },
        {
            "segment_id": "21000000-0000-4000-8000-000000000004",
            "segment_type": "idle",
            "duration_ms": 3200,
            "cell_source": "score = 60",
        },
        {
            "segment_id": "21000000-0000-4000-8000-000000000005",
            "segment_type": "code_execution",
            "duration_ms": 400,
            "execution_result": "success",
            "cell_source": "score = 60",
        },
    ]
    for sequence, row in enumerate(rows, start=1):
        append_synthetic_event(
            store,
            session_id,
            sequence=sequence,
            **row,
        )
    store.finalize(session_id, last_sequence=len(rows))
    return store, session_id


def test_evidence_chunk_is_a_deterministic_gzip_projection_of_canonical_events():
    session_id = "39e65774-a89a-4f05-961e-3527b13a6dd2"
    events = [
        {"event_id": f"{session_id}:1", "session_seq": 1, "segment_type": "code_writing"},
        {"event_id": f"{session_id}:2", "session_seq": 2, "segment_type": "code_execution"},
    ]
    created_at = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)

    chunk = build_evidence_chunk(
        session_id,
        sequence=3,
        events=events,
        created_at=created_at,
    )

    assert chunk.sequence == 3
    assert chunk.first_event_sequence == 1
    assert chunk.last_event_sequence == 2
    assert json.loads(decompress(chunk.body)) == {
        "schema_version": 1,
        "session_id": session_id,
        "first_event_sequence": 1,
        "last_event_sequence": 2,
        "events": events,
    }
    assert build_evidence_chunk(
        session_id,
        sequence=3,
        events=events,
        created_at=created_at,
    ).body == chunk.body


def test_finalize_export_writes_human_readable_local_logs_without_ai(
    tmp_path: Path,
) -> None:
    """Catches delaying the deterministic logs until the AI job finishes."""

    store, session_id = _finalized_behavior_session(tmp_path)

    _service(store).export_training_record(session_id)

    logs_dir = tmp_path / "sessions" / session_id / "logs"
    operation_path = logs_dir / "operation_log.json"
    process_path = logs_dir / "process_log.md"
    analysis_path = logs_dir / "analysis_log.json"
    assert operation_path.is_file()
    assert process_path.is_file()
    assert not analysis_path.exists()

    operation_text = operation_path.read_text(encoding="utf-8")
    operation = json.loads(operation_text)
    assert operation_text.startswith("{\n  \"schema_version\": 1,")
    assert operation["session"]["session_id"] == session_id
    assert [row["segment_type"] for row in operation["events"]] == [
        "code_writing",
        "code_deletion",
        "code_paste",
        "idle",
        "code_execution",
    ]
    assert operation["events"][-1]["execution_result"] == "success"

    process_text = process_path.read_text(encoding="utf-8")
    assert "# 编程行为过程日志" in process_text
    assert "## 会话摘要" in process_text
    assert "## 时间线" in process_text
    assert "## 行为明细" in process_text
    assert "停顿（可能包含思考）" in process_text
    assert "运行成功" in process_text

    if os.name == "posix":
        assert operation_path.stat().st_mode & 0o777 == 0o600
        assert process_path.stat().st_mode & 0o777 == 0o600
    assert not list(logs_dir.glob("*.tmp"))


@pytest.mark.parametrize("status", ["queued", "running", "error"])
def test_non_ready_analysis_never_creates_a_success_log(
    tmp_path: Path,
    status: str,
) -> None:
    """Catches publishing an empty or failed analysis as a successful file."""

    store, session, job_store = attached_job_fixture(tmp_path, status=status)
    session_id = str(session["session_id"])

    _service(store, job_store=job_store).export_training_record(session_id)

    assert not (
        tmp_path / "sessions" / session_id / "logs" / "analysis_log.json"
    ).exists()


def test_ready_analysis_writes_only_the_validated_public_projection(
    tmp_path: Path,
) -> None:
    """Catches copying provider secrets or raw responses into the analysis log."""

    store, session, job_store, result = terminal_session_fixture(tmp_path)
    session_id = str(session["session_id"])

    _service(store, job_store=job_store).export_training_record(session_id)

    path = tmp_path / "sessions" / session_id / "logs" / "analysis_log.json"
    text = path.read_text(encoding="utf-8")
    artifact = json.loads(text)
    assert text.startswith("{\n  \"schema_version\": 1,")
    assert artifact["session"]["analysis_status"] == "ready"
    assert artifact["ai_analysis"]["analysis_id"] == result["analysis_id"]
    assert artifact["teacher_reviews"] == []
    assert artifact["integrity"]["complete"] is True
    serialized = json.dumps(artifact, ensure_ascii=False)
    assert "api_key" not in serialized
    assert "raw_response\"" not in serialized
    assert str(tmp_path) not in serialized


def test_ready_job_without_callback_artifact_remains_generating_until_refresh(
    tmp_path: Path,
) -> None:
    """Catches exposing a transient ready/job-to-log handoff as a permanent error."""

    store, session, job_store = attached_job_fixture(tmp_path)
    session_id = str(session["session_id"])
    service = _service(store, job_store=job_store)
    service.export_training_record(session_id)
    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=store,
        provider_client=lambda request, *, timeout_sec: provider_response(
            session_id
        ),
        synchronous=True,
    )

    worker.enqueue(str(store.read(session_id)["analysis_job_id"]))
    worker.shutdown()

    analysis_row = service.list_log_artifacts(session_id)[2]
    assert analysis_row["status"] == "generating"
    assert analysis_row["error_code"] is None

    service.export_training_record(session_id)
    assert service.list_log_artifacts(session_id)[2]["status"] == "ready"


def test_operation_and_process_logs_are_frozen_across_ai_completion(
    tmp_path: Path,
) -> None:
    """Catches later AI state changing the two logs promised at finalization."""

    store, session, job_store = attached_job_fixture(tmp_path)
    session_id = str(session["session_id"])
    service = _service(store, job_store=job_store)
    service.export_training_record(session_id)
    logs_dir = tmp_path / "sessions" / session_id / "logs"
    before = {
        name: (logs_dir / name).read_bytes()
        for name in ("operation_log.json", "process_log.md")
    }
    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=store,
        provider_client=lambda request, *, timeout_sec: provider_response(
            session_id
        ),
        terminal_callback=lambda value: service.export_training_record(value),
        synchronous=True,
    )

    worker.enqueue(str(store.read(session_id)["analysis_job_id"]))
    worker.shutdown()

    assert (logs_dir / "analysis_log.json").is_file()
    assert {
        name: (logs_dir / name).read_bytes()
        for name in ("operation_log.json", "process_log.md")
    } == before


def test_artifact_reads_reject_unknown_names_symlinks_and_oversized_views(
    tmp_path: Path,
) -> None:
    """Catches turning the log viewer into an arbitrary local-file reader."""

    store, session_id = _finalized_behavior_session(tmp_path)
    _service(store).export_training_record(session_id)

    with pytest.raises(ValueError):
        store.read_log_artifact(session_id, "../session.json")

    logs_dir = tmp_path / "sessions" / session_id / "logs"
    operation_path = logs_dir / "operation_log.json"
    operation_path.unlink()
    operation_path.symlink_to(tmp_path / "sessions" / session_id / "session.json")
    with pytest.raises(SessionIntegrityError):
        store.read_log_artifact(session_id, "operation_log.json")

    operation_path.unlink()
    store.write_log_artifact(session_id, "operation_log.json", b"12345")
    with pytest.raises(SessionIntegrityError, match="approved view limit"):
        store.read_log_artifact(
            session_id,
            "operation_log.json",
            max_bytes=4,
        )
    assert store.read_log_artifact(
        session_id,
        "operation_log.json",
        max_bytes=None,
    ) == b"12345"


def test_descriptor_open_rejects_path_replaced_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a list/read race being used to swap in a symbolic link."""

    store, session_id = _finalized_behavior_session(tmp_path)
    _service(store).export_training_record(session_id)
    logs_dir = tmp_path / "sessions" / session_id / "logs"
    operation_path = logs_dir / "operation_log.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    original_open = os.open
    replaced = False

    def replace_then_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if Path(path) == operation_path and not replaced:
            replaced = True
            operation_path.unlink()
            operation_path.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_then_open)

    with pytest.raises(SessionIntegrityError):
        with store.open_log_artifact(session_id, "operation_log.json"):
            pass
