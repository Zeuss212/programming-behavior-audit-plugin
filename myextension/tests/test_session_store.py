from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from myextension.analysis_job_store import AnalysisJobStore
from myextension.canonical_json import sha256_json
from myextension.behavior_log_store import write_session_projection
from myextension.dimension_profile_store import DimensionProfileStore
from myextension.session_store import (
    InvalidSessionIdError,
    SegmentConflictError,
    SequenceGapError,
    SessionIntegrityError,
    SessionStateError,
    SessionStore,
)
from myextension.tests.test_assessment_profile import make_assessment_profile


PROFILE_ID = "10000000-0000-4000-8000-000000000001"
SEGMENT_ID = "20000000-0000-4000-8000-000000000001"
JOB_ID = "30000000-0000-4000-8000-000000000001"
ANALYSIS_ID = "40000000-0000-4000-8000-000000000001"
ATTEMPT_ID = "50000000-0000-4000-8000-000000000001"


def published_profile() -> dict[str, object]:
    content: dict[str, object] = {
        "schema_version": 1,
        "profile_id": PROFILE_ID,
        "version": 1,
        "problem_id": "average-debug",
        "title": "平均数调试",
        "dimensions": [
            {
                "code": "DEBUG_CHAIN",
                "name": "调试链",
                "question": "是否根据失败反馈修改并再次运行？",
                "evidence_criteria": {
                    "support": ["失败后修改并再次运行"],
                    "exclusion": ["仅发生一次运行"],
                },
                "levels": [
                    {
                        "code": "not_observed",
                        "label": "未观察到",
                        "description": "没有形成调试链。",
                    },
                    {
                        "code": "possible",
                        "label": "可能",
                        "description": "形成了一次调试链。",
                    },
                    {
                        "code": "clear",
                        "label": "明显",
                        "description": "形成多次有针对性的调试链。",
                    },
                ],
                "teaching_actions": ["追问修改依据"],
                "analysis_config": {
                    "mode": "guided",
                    "required_features": ["failure_edit_success_chain_count"],
                    "minimum_observation": {
                        "valid_observation_duration_ms": 1000,
                        "run_count": 1,
                        "edit_event_count": 1,
                    },
                },
                "examples": [],
            }
        ],
    }
    return {
        **content,
        "content_hash": sha256_json(content),
        "deployment_status": "pilot",
        "preview_status": "pending_real_samples",
    }


def event(
    session_id: str,
    sequence: int,
    *,
    source: str | None = None,
) -> dict[str, object]:
    return {
        "event_id": f"{session_id}:{sequence}",
        "session_seq": sequence,
        "segment_type": "code_writing",
        "started_at": f"2026-07-28T09:00:{sequence:02d}+08:00",
        "ended_at": f"2026-07-28T09:00:{sequence + 1:02d}+08:00",
        "duration_ms": 1000,
        "notebook_path": "synthetic.ipynb",
        "cell_id": "cell-1",
        "cell_index": 0,
        "cell_type": "code",
        "cell_source": source or f"value = {sequence}",
    }


def batch(
    session_id: str,
    *,
    sequence: int,
    segment_id: str = SEGMENT_ID,
    source: str | None = None,
) -> dict[str, object]:
    segments = [event(session_id, sequence, source=source)]
    return {
        "segment_id": segment_id,
        "first_sequence": sequence,
        "last_sequence": sequence,
        "content_hash": sha256_json(
            {
                "first_sequence": sequence,
                "last_sequence": sequence,
                "segments": segments,
            }
        ),
        "segments": segments,
    }


def started_session(
    tmp_path: Path,
    *,
    started_at: str | None = None,
) -> tuple[SessionStore, dict[str, object]]:
    store = SessionStore(tmp_path)
    session = store.start(
        problem_id="average-debug",
        profile=published_profile(),
    )
    if started_at is not None:
        session_path = (
            tmp_path
            / "sessions"
            / str(session["session_id"])
            / "session.json"
        )
        stored = json.loads(session_path.read_text(encoding="utf-8"))
        stored["started_at"] = started_at
        session_path.write_text(json.dumps(stored), encoding="utf-8")
        session = store.read(str(session["session_id"]))
    return store, session


def test_start_copies_profile_and_signal_dictionary_snapshots(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)

    session = store.start(
        problem_id="average-debug",
        profile=published_profile(),
    )

    session_dir = tmp_path / "sessions" / str(session["session_id"])
    profile_snapshot = json.loads(
        (session_dir / "profile.json").read_text(encoding="utf-8")
    )
    dictionary_snapshot = json.loads(
        (session_dir / "signal_dictionary.json").read_text(encoding="utf-8")
    )
    assert profile_snapshot["content_hash"] == session["profile_content_hash"]
    assert dictionary_snapshot["version"] == "pilot-v1"
    assert sha256_json(dictionary_snapshot) == session["signal_dictionary_hash"]
    assert session["status"] == "collecting"
    assert session["last_contiguous_sequence"] == 0
    if os.name == "posix":
        assert session_dir.stat().st_mode & 0o777 == 0o700


def test_start_accepts_a_published_v2_profile_snapshot(tmp_path: Path) -> None:
    profile_store = DimensionProfileStore(tmp_path)
    draft = profile_store.create_draft(make_assessment_profile())
    published = profile_store.publish(str(draft["profile_id"]))

    session = SessionStore(tmp_path).start(
        problem_id=str(published["problem_id"]),
        profile=published,
    )

    assert session["profile_content_hash"] == published["content_hash"]
    snapshot = json.loads(
        (
            tmp_path
            / "sessions"
            / str(session["session_id"])
            / "profile.json"
        ).read_text(encoding="utf-8")
    )
    assert snapshot["schema_version"] == 2
    assert snapshot["knowledge_points"] == published["knowledge_points"]


def test_read_profile_returns_the_validated_trusted_snapshot(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)

    profile = store.read_profile(str(session["session_id"]))

    assert profile == published_profile()
    profile["title"] = "调用方修改不应写回"
    assert store.read_profile(str(session["session_id"]))["title"] == "平均数调试"


def test_start_rejects_profile_problem_or_content_hash_mismatch(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    changed = published_profile()
    changed["title"] = "被修改"

    with pytest.raises(SessionIntegrityError):
        store.start(problem_id="average-debug", profile=changed)

    with pytest.raises(SessionIntegrityError):
        store.start(problem_id="different-problem", profile=published_profile())
    assert not (tmp_path / "sessions").exists()


def test_start_rejects_sessions_root_symlink_without_writing_outside(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "sessions").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SessionIntegrityError):
        SessionStore(tmp_path).start(
            problem_id="average-debug",
            profile=published_profile(),
        )
    assert list(outside.iterdir()) == []


def test_replaying_same_segment_batch_is_idempotent(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    payload = batch(str(session["session_id"]), sequence=1)

    first = store.append_batch(str(session["session_id"]), **payload)
    replay = store.append_batch(str(session["session_id"]), **payload)

    assert first == replay
    assert len(store.read_events(str(session["session_id"]))) == 1


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("schema_version", 2),
        ("session_id", "10000000-0000-4000-8000-000000000099"),
        ("segment_id", "20000000-0000-4000-8000-000000000099"),
        ("content_hash", "f" * 64),
        ("accepted_count", 999),
        ("last_contiguous_sequence", 0),
        ("received_at", "not-an-iso-time"),
    ],
)
def test_replay_rejects_every_tampered_receipt_field(
    tmp_path: Path,
    field: str,
    tampered_value: object,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    payload = batch(session_id, sequence=1)
    store.append_batch(session_id, **payload)
    receipt_path = (
        tmp_path / "sessions" / session_id / "receipts" / f"{SEGMENT_ID}.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = tampered_value
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(SessionIntegrityError):
        store.append_batch(session_id, **payload)


def test_same_segment_id_with_different_hash_conflicts(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.append_batch(session_id, **batch(session_id, sequence=1))

    with pytest.raises(SegmentConflictError):
        store.append_batch(
            session_id,
            **batch(session_id, sequence=1, source="print(2)"),
        )


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda session_id, payload: payload["segments"][0].__setitem__(
                "event_id", f"{session_id}:2"
            ),
            SessionIntegrityError,
        ),
        (
            lambda _session_id, payload: payload["segments"][0].__setitem__(
                "session_seq", 2
            ),
            SessionIntegrityError,
        ),
        (
            lambda _session_id, payload: payload.__setitem__(
                "content_hash", "0" * 64
            ),
            SessionIntegrityError,
        ),
    ],
)
def test_append_rejects_event_identity_and_hash_mismatch(
    tmp_path: Path,
    mutate,
    expected_error: type[Exception],
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    payload = batch(session_id, sequence=1)
    mutate(session_id, payload)

    with pytest.raises(expected_error):
        store.append_batch(session_id, **payload)
    assert store.read_events(session_id) == []


def test_append_rejects_boolean_event_sequence(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    segments = [event(session_id, 1)]
    segments[0]["session_seq"] = True
    payload = {
        "segment_id": SEGMENT_ID,
        "first_sequence": 1,
        "last_sequence": 1,
        "content_hash": sha256_json(
            {
                "first_sequence": 1,
                "last_sequence": 1,
                "segments": segments,
            }
        ),
        "segments": segments,
    }

    with pytest.raises(SessionIntegrityError):
        store.append_batch(session_id, **payload)
    assert store.read_events(session_id) == []


def test_append_rejects_noncanonical_ids_and_sequence_gap(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])

    with pytest.raises(InvalidSessionIdError):
        store.read(session_id.upper())
    with pytest.raises(ValueError):
        store.append_batch(
            session_id,
            **batch(
                session_id,
                sequence=1,
                segment_id="abcdefab-0000-4000-8000-000000000001".upper(),
            ),
        )
    with pytest.raises(SequenceGapError) as exc:
        store.append_batch(
            session_id,
            **batch(
                session_id,
                sequence=2,
                segment_id="20000000-0000-4000-8000-000000000002",
            ),
        )
    assert exc.value.missing_ranges == [(1, 1)]


def test_finalize_rejects_sequence_gap_and_restores_collecting(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.append_batch(session_id, **batch(session_id, sequence=1))

    with pytest.raises(SequenceGapError) as exc:
        store.finalize(session_id, last_sequence=3)

    assert exc.value.missing_ranges == [(2, 3)]
    state = store.read(session_id)
    assert state["status"] == "collecting"
    assert state["finalization_failure_reason"] == "sequence_gap"


def test_finalize_is_idempotent_and_blocks_future_appends(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.append_batch(session_id, **batch(session_id, sequence=1))

    first = store.finalize(session_id, last_sequence=1)
    projection = tmp_path / str(first["legacy_projection_path"])
    projection_mtime = projection.stat().st_mtime_ns
    replay = store.finalize(session_id, last_sequence=1)

    assert replay == first
    assert projection.stat().st_mtime_ns == projection_mtime
    with pytest.raises(SessionStateError):
        store.append_batch(
            session_id,
            **batch(
                session_id,
                sequence=2,
                segment_id="20000000-0000-4000-8000-000000000002",
            ),
        )


def test_read_truncates_only_an_incomplete_last_jsonl_line(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.append_batch(session_id, **batch(session_id, sequence=1))
    raw_path = tmp_path / "sessions" / session_id / "raw_events.jsonl"
    with raw_path.open("ab") as handle:
        handle.write(b'{"event_id":"partial')

    assert [row["session_seq"] for row in store.read_events(session_id)] == [1]
    assert raw_path.read_bytes().endswith(b"\n")
    audit = (
        tmp_path / "sessions" / session_id / "session_recovery.jsonl"
    ).read_text(encoding="utf-8")
    assert "truncated_incomplete_jsonl_tail" in audit


def test_read_rejects_invalid_earlier_line_or_broken_continuity(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    raw_path = tmp_path / "sessions" / session_id / "raw_events.jsonl"
    raw_path.write_text(
        '{"session_seq":1,"event_id":"' + session_id + ':1"}\n'
        "not-json\n"
        '{"session_seq":2,"event_id":"' + session_id + ':2"}\n',
        encoding="utf-8",
    )

    with pytest.raises(SessionIntegrityError):
        store.read_events(session_id)

    raw_path.write_text(
        '{"session_seq":2,"event_id":"' + session_id + ':2"}\n',
        encoding="utf-8",
    )
    with pytest.raises(SessionIntegrityError):
        store.finalize(session_id, last_sequence=2)
    assert store.read(session_id)["status"] == "collecting"


def test_replay_recovers_crash_after_jsonl_fsync_without_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import myextension.session_store as session_module

    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    payload = batch(session_id, sequence=1)
    real_atomic_write = session_module.atomic_write_json
    raised = False

    def fail_before_receipt(path: Path, value: object) -> None:
        nonlocal raised
        if path.parent.name == "receipts" and not raised:
            raised = True
            raise OSError("synthetic crash before receipt")
        real_atomic_write(path, value)

    monkeypatch.setattr(session_module, "atomic_write_json", fail_before_receipt)
    with pytest.raises(OSError, match="synthetic crash"):
        store.append_batch(session_id, **payload)
    monkeypatch.setattr(session_module, "atomic_write_json", real_atomic_write)

    recovered = SessionStore(tmp_path).append_batch(session_id, **payload)

    assert recovered["accepted_count"] == 1
    assert [
        row["event_id"]
        for row in SessionStore(tmp_path).read_events(session_id)
    ] == [f"{session_id}:1"]


def test_stale_collecting_session_uses_last_receipt_and_becomes_abandoned(
    tmp_path: Path,
) -> None:
    store, session = started_session(
        tmp_path,
        started_at="2026-07-28T09:00:00+08:00",
    )
    session_id = str(session["session_id"])

    changed = store.abandon_stale(
        now=datetime.fromisoformat("2026-07-28T09:31:00+08:00"),
        timeout=timedelta(minutes=30),
    )

    assert changed == [session_id]
    assert store.read(session_id)["status"] == "abandoned"


def test_recent_server_receipt_prevents_stale_abandonment(tmp_path: Path) -> None:
    store, session = started_session(
        tmp_path,
        started_at="2026-07-28T09:00:00+08:00",
    )
    session_id = str(session["session_id"])
    payload = batch(session_id, sequence=1)
    store.append_batch(session_id, **payload)
    receipt_path = (
        tmp_path / "sessions" / session_id / "receipts" / f"{SEGMENT_ID}.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["received_at"] = "2026-07-28T09:20:00+08:00"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    changed = store.abandon_stale(
        now=datetime.fromisoformat("2026-07-28T09:31:00+08:00"),
        timeout=timedelta(minutes=30),
    )

    assert changed == []
    assert store.read(session_id)["status"] == "collecting"


def test_matching_forged_journal_and_receipt_cannot_control_staleness(
    tmp_path: Path,
) -> None:
    store, session = started_session(
        tmp_path,
        started_at="2026-07-28T09:00:00+08:00",
    )
    session_id = str(session["session_id"])
    segments = [event(session_id, 1)]
    false_hash = "f" * 64
    session_dir = tmp_path / "sessions" / session_id
    (session_dir / "raw_events.jsonl").write_text(
        json.dumps(segments[0]) + "\n",
        encoding="utf-8",
    )
    (session_dir / "batches" / f"{SEGMENT_ID}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": session_id,
                "segment_id": SEGMENT_ID,
                "first_sequence": 1,
                "last_sequence": 1,
                "content_hash": false_hash,
                "segments": segments,
            }
        ),
        encoding="utf-8",
    )
    (session_dir / "receipts" / f"{SEGMENT_ID}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": session_id,
                "segment_id": SEGMENT_ID,
                "content_hash": false_hash,
                "accepted_count": 1,
                "last_contiguous_sequence": 1,
                "received_at": "2026-07-28T09:30:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SessionIntegrityError):
        store.abandon_stale(
            now=datetime.fromisoformat("2026-07-28T09:31:00+08:00"),
            timeout=timedelta(minutes=30),
        )


def test_receipt_without_its_canonical_raw_range_cannot_control_staleness(
    tmp_path: Path,
) -> None:
    store, session = started_session(
        tmp_path,
        started_at="2026-07-28T09:00:00+08:00",
    )
    session_id = str(session["session_id"])
    store.append_batch(session_id, **batch(session_id, sequence=1))
    raw_path = tmp_path / "sessions" / session_id / "raw_events.jsonl"
    raw_path.write_text("", encoding="utf-8")

    with pytest.raises(SessionIntegrityError):
        store.abandon_stale(
            now=datetime.fromisoformat("2026-07-28T09:31:00+08:00"),
            timeout=timedelta(minutes=30),
        )


def test_forged_receipt_is_not_accepted_as_successful_activity(
    tmp_path: Path,
) -> None:
    store, session = started_session(
        tmp_path,
        started_at="2026-07-28T09:00:00+08:00",
    )
    session_id = str(session["session_id"])
    forged_id = "20000000-0000-4000-8000-000000000099"
    forged_path = (
        tmp_path / "sessions" / session_id / "receipts" / f"{forged_id}.json"
    )
    forged_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "segment_id": forged_id,
                "received_at": "2026-07-28T09:30:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SessionIntegrityError):
        store.abandon_stale(
            now=datetime.fromisoformat("2026-07-28T09:31:00+08:00"),
            timeout=timedelta(minutes=30),
        )


def test_recover_requires_actor_and_reason_and_appends_audit(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.abandon(session_id, reason="browser_closed")

    with pytest.raises(ValueError):
        store.recover(session_id, actor="", reason="继续补传")
    recovered = store.recover(
        session_id,
        actor="local-teacher",
        reason="继续补传未完成记录",
    )

    assert recovered["status"] == "collecting"
    audit = (
        tmp_path / "sessions" / session_id / "session_recovery.jsonl"
    ).read_text(encoding="utf-8")
    assert "继续补传未完成记录" in audit
    assert "local-teacher" in audit


@pytest.mark.parametrize("initial_status", ["abandoned", "finalizing"])
def test_recovery_audit_failure_preserves_original_lifecycle_status(
    tmp_path: Path,
    initial_status: str,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    session_dir = tmp_path / "sessions" / session_id
    if initial_status == "abandoned":
        store.abandon(session_id, reason="browser_closed")
    else:
        session_path = session_dir / "session.json"
        stored = json.loads(session_path.read_text(encoding="utf-8"))
        stored["status"] = "finalizing"
        session_path.write_text(json.dumps(stored), encoding="utf-8")
    session_path = session_dir / "session.json"
    original_state = session_path.read_bytes()
    outside_audit = tmp_path / f"outside-{initial_status}.jsonl"
    outside_audit.write_text("sentinel\n", encoding="utf-8")
    (session_dir / "session_recovery.jsonl").symlink_to(outside_audit)

    with pytest.raises(SessionIntegrityError):
        store.recover(
            session_id,
            actor="local-teacher",
            reason="继续补传未完成记录",
        )

    assert session_path.read_bytes() == original_state
    assert store.read(session_id)["status"] == initial_status
    assert outside_audit.read_text(encoding="utf-8") == "sentinel\n"


def _run_fifo_audit_operation(
    root: Path,
    session_id: str,
    operation: str,
) -> subprocess.CompletedProcess[str]:
    script = """
import sys
from pathlib import Path
from myextension.session_store import SessionIntegrityError, SessionStore

root = Path(sys.argv[1])
session_id = sys.argv[2]
operation = sys.argv[3]
store = SessionStore(root)
try:
    if operation == "delete":
        store.delete_cascade(
            session_id,
            actor="local-teacher",
            reason="synthetic-delete",
        )
    else:
        store.recover(
            session_id,
            actor="local-teacher",
            reason="synthetic-recovery",
        )
except SessionIntegrityError:
    print("rejected")
    raise SystemExit(0)
except Exception as error:
    print(type(error).__name__, str(error))
    raise SystemExit(2)
raise SystemExit(3)
"""
    return subprocess.run(
        [sys.executable, "-c", script, str(root), session_id, operation],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        timeout=1.0,
        check=False,
    )


@pytest.mark.parametrize("initial_status", ["abandoned", "finalizing"])
def test_recovery_fifo_audit_is_rejected_promptly_without_state_change(
    tmp_path: Path,
    initial_status: str,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    session_dir = tmp_path / "sessions" / session_id
    if initial_status == "abandoned":
        store.abandon(session_id, reason="browser_closed")
    else:
        session_path = session_dir / "session.json"
        stored = json.loads(session_path.read_text(encoding="utf-8"))
        stored["status"] = "finalizing"
        session_path.write_text(json.dumps(stored), encoding="utf-8")
    session_path = session_dir / "session.json"
    original_state = session_path.read_bytes()
    os.mkfifo(session_dir / "session_recovery.jsonl", mode=0o600)

    result = _run_fifo_audit_operation(
        tmp_path,
        session_id,
        "recover",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "rejected"
    assert session_path.read_bytes() == original_state
    assert store.read(session_id)["status"] == initial_status


def test_attach_job_is_idempotent_but_rejects_replacement(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])

    first = store.attach_job(session_id, JOB_ID)
    assert store.attach_job(session_id, JOB_ID) == first
    with pytest.raises(SessionStateError):
        store.attach_job(
            session_id,
            "30000000-0000-4000-8000-000000000002",
        )


def _write_associated_job_and_analysis(
    tmp_path: Path,
    session_id: str,
) -> tuple[str, str]:
    store = SessionStore(tmp_path)
    session = store.read(session_id)
    if session["status"] != "finalized":
        session = store.finalize(
            session_id,
            last_sequence=int(session["last_contiguous_sequence"]),
        )
    jobs = AnalysisJobStore(tmp_path)
    job = jobs.create(session=session, input_snapshot_hash="e" * 64)
    store.attach_job(session_id, str(job["job_id"]))
    attempt = jobs.begin_attempt(str(job["job_id"]))
    result = _closed_result(
        session=session,
        job=jobs.get(str(job["job_id"])),
        job_id=str(job["job_id"]),
        attempt_id=str(attempt["attempt_id"]),
    )
    result_path = _write_private_result(tmp_path, result)
    jobs.finish_attempt(
        str(job["job_id"]),
        str(attempt["attempt_id"]),
        status="ready",
        analysis_id=str(result["analysis_id"]),
        error_code=None,
    )
    analysis_dir = result_path.parent
    (analysis_dir / "review_history.jsonl").write_text(
        '{"revision":1}\n',
        encoding="utf-8",
    )
    os.chmod(analysis_dir / "review_history.jsonl", 0o600)
    return str(job["job_id"]), str(result["analysis_id"])


def test_delete_cascade_removes_only_trusted_associated_artifacts(
    tmp_path: Path,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    job_id, analysis_id = _write_associated_job_and_analysis(
        tmp_path,
        session_id,
    )
    unrelated = tmp_path / "analyses" / "40000000-0000-4000-8000-000000000099"
    unrelated.mkdir(parents=True)
    (unrelated / "result.json").write_text("{}", encoding="utf-8")

    manifest = store.delete_cascade(
        session_id,
        actor="local-teacher",
        reason="试点数据删除",
    )

    assert manifest["deleted_session_id"] == session_id
    assert not (tmp_path / "sessions" / session_id).exists()
    assert not (tmp_path / "jobs" / job_id).exists()
    assert not (tmp_path / "analyses" / analysis_id).exists()
    assert unrelated.exists()
    deletion_text = (tmp_path / "audit" / "session_deletions.jsonl").read_text(
        encoding="utf-8"
    )
    assert "试点数据删除" not in deletion_text
    assert "cell_source" not in deletion_text


def _closed_result(
    *,
    session: dict[str, object],
    job: dict[str, object],
    job_id: str,
    attempt_id: str,
) -> dict[str, object]:
    analysis_id = str(
        uuid5(
            NAMESPACE_URL,
            f"{job_id}:{attempt_id}:{session['session_id']}",
        )
    )
    return {
        "schema_version": 1,
        "analysis_id": analysis_id,
        "job_id": job_id,
        "attempt_id": attempt_id,
        "session_id": session["session_id"],
        "profile_id": session["profile_id"],
        "profile_version": session["profile_version"],
        "profile_content_hash": session["profile_content_hash"],
        "status": "ready",
        "dimension_results": [
            {
                "schema_version": 1,
                "dimension_code": "DEBUG_CHAIN",
                "decision": {
                    "status": "resolved",
                    "final_evidence_status": "not_observed",
                    "final_level_code": None,
                    "display_label": "未发现明显证据",
                    "source": "coverage",
                },
                "data_quality": {
                    "missing_required_signals": [],
                    "observation_opportunities": 1,
                    "reason_code": None,
                    "reason": None,
                },
                "ai_result": None,
                "review": {"revision": 0, "status": "unreviewed"},
            }
        ],
        "provenance": {
            "analysis_pipeline_version": "pilot-v1",
            "feature_extractor_version": "pilot-v1",
            "signal_dictionary_version": "pilot-v1",
            "signal_dictionary_hash": session["signal_dictionary_hash"],
            "model_name": "synthetic",
            "model_version": "1",
            "model_parameters": {"temperature": 0},
            "prompt_version": "pilot-v1",
            "prompt_content_hash": "a" * 64,
            "provider_request_id": None,
            "raw_response_hash": "b" * 64,
            "input_snapshot_hash": job["input_snapshot_hash"],
        },
    }


def _write_private_result(root: Path, result: dict[str, object]) -> Path:
    result_path = (
        root
        / "analyses"
        / str(result["analysis_id"])
        / "result.json"
    )
    result_path.parent.mkdir(mode=0o700, parents=True)
    result_path.write_text(
        json.dumps(result, ensure_ascii=False),
        encoding="utf-8",
    )
    os.chmod(result_path, 0o600)
    return result_path


def _partial_session_job(
    tmp_path: Path,
) -> tuple[
    SessionStore,
    AnalysisJobStore,
    dict[str, object],
    dict[str, object],
]:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    finalized = store.finalize(session_id, last_sequence=0)
    jobs = AnalysisJobStore(tmp_path)
    job = jobs.create(
        session=finalized,
        input_snapshot_hash="d" * 64,
    )
    store.attach_job(session_id, str(job["job_id"]))
    attempt = jobs.begin_attempt(str(job["job_id"]))
    result = _closed_result(
        session=finalized,
        job=jobs.get(str(job["job_id"])),
        job_id=str(job["job_id"]),
        attempt_id=str(attempt["attempt_id"]),
    )
    result["status"] = "partial"
    _write_private_result(tmp_path, result)
    jobs.finish_attempt(
        str(job["job_id"]),
        str(attempt["attempt_id"]),
        status="partial",
        analysis_id=str(result["analysis_id"]),
        error_code="ai_analysis_failed",
    )
    return store, jobs, finalized, jobs.get(str(job["job_id"]))


def test_delete_cascade_discovers_every_attempt_analysis_candidate(
    tmp_path: Path,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    finalized = store.finalize(session_id, last_sequence=0)
    jobs = AnalysisJobStore(tmp_path)
    job = jobs.create(
        session=finalized,
        input_snapshot_hash="d" * 64,
    )
    store.attach_job(session_id, str(job["job_id"]))

    failed_attempt = jobs.begin_attempt(str(job["job_id"]))
    jobs.finish_attempt(
        str(job["job_id"]),
        str(failed_attempt["attempt_id"]),
        status="error",
        analysis_id=None,
        error_code="analysis_worker_failed",
    )
    failed_candidate = _closed_result(
        session=finalized,
        job=jobs.get(str(job["job_id"])),
        job_id=str(job["job_id"]),
        attempt_id=str(failed_attempt["attempt_id"]),
    )
    failed_result_path = _write_private_result(tmp_path, failed_candidate)

    jobs.retry(str(job["job_id"]), reason="teacher_requested")
    partial_attempt = jobs.begin_attempt(str(job["job_id"]))
    partial_result = _closed_result(
        session=finalized,
        job=jobs.get(str(job["job_id"])),
        job_id=str(job["job_id"]),
        attempt_id=str(partial_attempt["attempt_id"]),
    )
    partial_result["status"] = "partial"
    partial_result_path = _write_private_result(tmp_path, partial_result)
    jobs.finish_attempt(
        str(job["job_id"]),
        str(partial_attempt["attempt_id"]),
        status="partial",
        analysis_id=str(partial_result["analysis_id"]),
        error_code="ai_analysis_failed",
    )
    review_path = partial_result_path.with_name("review_history.jsonl")
    review_path.write_text("", encoding="utf-8")
    os.chmod(review_path, 0o600)

    manifest = store.delete_cascade(
        session_id,
        actor="local-teacher",
        reason="试点数据删除",
    )

    assert manifest["deleted_job_ids"] == [job["job_id"]]
    assert manifest["deleted_analysis_ids"] == sorted(
        [failed_candidate["analysis_id"], partial_result["analysis_id"]]
    )
    assert not failed_result_path.parent.exists()
    assert not partial_result_path.parent.exists()
    assert not (tmp_path / "jobs" / str(job["job_id"])).exists()
    assert not (tmp_path / "sessions" / session_id).exists()


def test_delete_cascade_rejects_malformed_jobs_root_before_any_mutation(
    tmp_path: Path,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    malformed = jobs_root / "not-a-job"
    malformed.write_text("synthetic", encoding="utf-8")

    with pytest.raises(SessionIntegrityError):
        store.delete_cascade(
            session_id,
            actor="local-teacher",
            reason="试点数据删除",
        )

    assert (tmp_path / "sessions" / session_id).exists()
    assert malformed.read_text(encoding="utf-8") == "synthetic"
    assert not (tmp_path / "audit" / "session_deletions.jsonl").exists()


def test_delete_reservation_blocks_concurrent_retry_until_removal_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, jobs, session, job = _partial_session_job(tmp_path)
    session_id = str(session["session_id"])
    job_id = str(job["job_id"])
    preflight_reached = threading.Event()
    release_delete = threading.Event()
    retry_entered = threading.Event()
    retry_finished = threading.Event()
    real_validate = store._validate_delete_target_types

    def paused_validate(directory_targets, file_targets):
        real_validate(directory_targets, file_targets)
        preflight_reached.set()
        assert release_delete.wait(timeout=2)

    monkeypatch.setattr(
        store,
        "_validate_delete_target_types",
        paused_validate,
    )

    def retry():
        retry_entered.set()
        try:
            jobs.retry(job_id, reason="teacher_requested")
            return "succeeded"
        except Exception as error:
            return type(error).__name__
        finally:
            retry_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        delete_future = executor.submit(
            store.delete_cascade,
            session_id,
            actor="local-teacher",
            reason="试点数据删除",
        )
        assert preflight_reached.wait(timeout=2)
        retry_future = executor.submit(retry)
        assert retry_entered.wait(timeout=2)
        assert not retry_finished.wait(timeout=0.2)
        release_delete.set()
        manifest = delete_future.result(timeout=2)
        retry_outcome = retry_future.result(timeout=2)

    assert manifest["deleted_session_id"] == session_id
    assert retry_outcome in {
        "AnalysisJobNotFoundError",
        "AnalysisJobIntegrityError",
    }
    assert not (tmp_path / "sessions" / session_id).exists()
    assert not (tmp_path / "jobs" / job_id).exists()


def test_public_result_loader_never_acquires_session_after_job_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, jobs, session, job = _partial_session_job(tmp_path)
    session_id = str(session["session_id"])
    job_id = str(job["job_id"])
    job_lock = jobs._lock_for(job_id)
    real_read = store.read

    def order_checked_read(candidate_session_id):
        assert not job_lock._is_owned()
        return real_read(candidate_session_id)

    monkeypatch.setattr(store, "read", order_checked_read)

    result = jobs.load_public_result(
        job_id,
        session_store=store,
    )

    assert result["session_id"] == session_id
    assert result["job_id"] == job_id


def test_delete_reservation_prevents_concurrent_create_from_reviving_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, jobs, session, job = _partial_session_job(tmp_path)
    session_id = str(session["session_id"])
    job_id = str(job["job_id"])
    preflight_reached = threading.Event()
    release_delete = threading.Event()
    create_entered = threading.Event()
    create_finished = threading.Event()
    real_validate = store._validate_delete_target_types

    def paused_validate(directory_targets, file_targets):
        real_validate(directory_targets, file_targets)
        preflight_reached.set()
        assert release_delete.wait(timeout=2)

    monkeypatch.setattr(
        store,
        "_validate_delete_target_types",
        paused_validate,
    )

    def create():
        create_entered.set()
        try:
            jobs.create(
                session=session,
                input_snapshot_hash=str(job["input_snapshot_hash"]),
            )
            return "succeeded"
        except Exception as error:
            return type(error).__name__
        finally:
            create_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        delete_future = executor.submit(
            store.delete_cascade,
            session_id,
            actor="local-teacher",
            reason="试点数据删除",
        )
        assert preflight_reached.wait(timeout=2)
        create_future = executor.submit(create)
        assert create_entered.wait(timeout=2)
        assert not create_finished.wait(timeout=0.2)
        release_delete.set()
        manifest = delete_future.result(timeout=2)
        create_outcome = create_future.result(timeout=2)

    assert manifest["deleted_session_id"] == session_id
    assert create_outcome in {
        "AnalysisJobIntegrityError",
        "AnalysisJobStateError",
    }
    assert not (tmp_path / "sessions" / session_id).exists()
    assert not (tmp_path / "jobs" / job_id).exists()
    assert not any(
        candidate.is_dir()
        for candidate in (tmp_path / "jobs").iterdir()
    )


def test_active_worker_finish_is_reserved_while_delete_rejects_running_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, jobs, session, job = _partial_session_job(tmp_path)
    session_id = str(session["session_id"])
    job_id = str(job["job_id"])
    jobs.retry(job_id, reason="teacher_requested")
    attempt = jobs.begin_attempt(job_id)
    running_seen = threading.Event()
    release_delete = threading.Event()
    finish_entered = threading.Event()
    finish_done = threading.Event()
    real_get = AnalysisJobStore.get

    def paused_get(candidate_store, candidate_job_id):
        value = real_get(candidate_store, candidate_job_id)
        if (
            candidate_store.root == tmp_path
            and value["status"] == "running"
            and not running_seen.is_set()
        ):
            running_seen.set()
            assert release_delete.wait(timeout=2)
        return value

    monkeypatch.setattr(AnalysisJobStore, "get", paused_get)

    def finish():
        finish_entered.set()
        try:
            return jobs.finish_attempt(
                job_id,
                str(attempt["attempt_id"]),
                status="error",
                analysis_id=None,
                error_code="analysis_worker_failed",
            )
        finally:
            finish_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        delete_future = executor.submit(
            store.delete_cascade,
            session_id,
            actor="local-teacher",
            reason="试点数据删除",
        )
        assert running_seen.wait(timeout=2)
        finish_future = executor.submit(finish)
        assert finish_entered.wait(timeout=2)
        assert not finish_done.wait(timeout=0.2)
        release_delete.set()
        with pytest.raises(SessionStateError):
            delete_future.result(timeout=2)
        finished = finish_future.result(timeout=2)

    assert finished["status"] == "error"
    assert (tmp_path / "sessions" / session_id).exists()
    assert (tmp_path / "jobs" / job_id).exists()
    assert not (tmp_path / "audit" / "session_deletions.jsonl").exists()


def test_delete_cascade_rejects_symlink_escape(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.attach_job(session_id, JOB_ID)
    outside = tmp_path / "outside-job"
    outside.mkdir()
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    (jobs_root / JOB_ID).symlink_to(outside, target_is_directory=True)

    with pytest.raises(SessionIntegrityError):
        store.delete_cascade(
            session_id,
            actor="local-teacher",
            reason="试点数据删除",
        )
    assert outside.exists()
    assert (tmp_path / "sessions" / session_id).exists()


@pytest.mark.parametrize("audit_symlink_kind", ["directory", "file"])
def test_delete_audit_symlink_causes_zero_mutations(
    tmp_path: Path,
    audit_symlink_kind: str,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.append_batch(session_id, **batch(session_id, sequence=1))
    finalized = store.finalize(session_id, last_sequence=1)
    job_id, analysis_id = _write_associated_job_and_analysis(
        tmp_path,
        session_id,
    )
    projection = tmp_path / str(finalized["legacy_projection_path"])
    index_path = projection.parent / ".session_index.json"
    original_index = index_path.read_bytes()
    outside = tmp_path / f"outside-audit-{audit_symlink_kind}"

    if audit_symlink_kind == "directory":
        outside.mkdir()
        (tmp_path / "audit").symlink_to(outside, target_is_directory=True)
    else:
        outside.write_text("sentinel\n", encoding="utf-8")
        (tmp_path / "audit").mkdir()
        (tmp_path / "audit" / "session_deletions.jsonl").symlink_to(outside)

    with pytest.raises(SessionIntegrityError):
        store.delete_cascade(
            session_id,
            actor="local-teacher",
            reason="试点数据删除",
        )

    assert (tmp_path / "sessions" / session_id).exists()
    assert (tmp_path / "jobs" / job_id).exists()
    assert (tmp_path / "analyses" / analysis_id).exists()
    assert projection.exists()
    assert index_path.read_bytes() == original_index
    if audit_symlink_kind == "directory":
        assert list(outside.iterdir()) == []
    else:
        assert outside.read_text(encoding="utf-8") == "sentinel\n"


def test_delete_fifo_audit_is_rejected_promptly_with_zero_mutations(
    tmp_path: Path,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.append_batch(session_id, **batch(session_id, sequence=1))
    finalized = store.finalize(session_id, last_sequence=1)
    projection = tmp_path / str(finalized["legacy_projection_path"])
    index_path = projection.parent / ".session_index.json"
    original_index = index_path.read_bytes()
    original_projection = projection.read_bytes()
    (tmp_path / "audit").mkdir()
    os.mkfifo(
        tmp_path / "audit" / "session_deletions.jsonl",
        mode=0o600,
    )

    result = _run_fifo_audit_operation(
        tmp_path,
        session_id,
        "delete",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "rejected"
    assert (tmp_path / "sessions" / session_id).exists()
    assert index_path.read_bytes() == original_index
    assert projection.read_bytes() == original_projection


def test_legacy_projection_rejects_date_symlink_without_writing_outside(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-projection"
    outside.mkdir()
    (tmp_path / "2026-07-28").symlink_to(outside, target_is_directory=True)
    session_id = "60000000-0000-4000-8000-000000000001"

    with pytest.raises(ValueError, match="symbolic link"):
        write_session_projection(
            session_id,
            [event(session_id, 1)],
            log_root=tmp_path,
        )
    assert list(outside.iterdir()) == []


def test_decomposed_unicode_batch_replays_in_process_and_after_reconstruction(
    tmp_path: Path,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    payload = batch(
        session_id,
        sequence=1,
        source="name_e\u0301 = 1",
    )

    first = store.append_batch(session_id, **payload)
    same_process = store.append_batch(session_id, **payload)
    reconstructed = SessionStore(tmp_path).append_batch(session_id, **payload)

    assert same_process == first
    assert reconstructed == first
    assert len(store.read_events(session_id)) == 1


def test_concurrent_same_timestamp_finalizations_reserve_distinct_projections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import myextension.behavior_log_store as behavior_module

    first_store, first_session = started_session(tmp_path)
    second_store, second_session = started_session(tmp_path)
    first_id = str(first_session["session_id"])
    second_id = str(second_session["session_id"])
    first_store.append_batch(
        first_id,
        **batch(
            first_id,
            sequence=1,
            segment_id="70000000-0000-4000-8000-000000000001",
            source="owner = 'first'",
        ),
    )
    second_store.append_batch(
        second_id,
        **batch(
            second_id,
            sequence=1,
            segment_id="70000000-0000-4000-8000-000000000002",
            source="owner = 'second'",
        ),
    )
    real_load_index = behavior_module._load_session_index
    load_barrier = threading.Barrier(2)

    def synchronized_load(path: Path) -> dict[str, str]:
        value = real_load_index(path)
        try:
            load_barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        return value

    monkeypatch.setattr(
        behavior_module,
        "_load_session_index",
        synchronized_load,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            first_store.finalize,
            first_id,
            last_sequence=1,
        )
        second_future = executor.submit(
            second_store.finalize,
            second_id,
            last_sequence=1,
        )
        finalized = [first_future.result(), second_future.result()]

    projection_paths = [
        tmp_path / str(session["legacy_projection_path"])
        for session in finalized
    ]
    assert projection_paths[0] != projection_paths[1]
    index = json.loads(
        (tmp_path / "2026-07-28" / ".session_index.json").read_text(
            encoding="utf-8"
        )
    )
    assert index[first_id] != index[second_id]
    projected_event_ids = {
        json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))[
            "segments"
        ][0]["event_id"]
        for path in projection_paths
    }
    assert projected_event_ids == {f"{first_id}:1", f"{second_id}:1"}


def test_list_session_ids_returns_only_valid_private_session_directories(
    tmp_path: Path,
) -> None:
    store, first = started_session(
        tmp_path,
        started_at="2026-07-30T08:00:00+08:00",
    )
    _, second = started_session(
        tmp_path,
        started_at="2026-07-30T09:00:00+08:00",
    )

    assert store.list_session_ids() == sorted(
        [str(first["session_id"]), str(second["session_id"])]
    )


def test_list_session_ids_rejects_symlink_entries(
    tmp_path: Path,
) -> None:
    store, _ = started_session(tmp_path)
    target = tmp_path / "outside"
    target.mkdir()
    (tmp_path / "sessions" / "malicious").symlink_to(
        target,
        target_is_directory=True,
    )

    with pytest.raises(SessionIntegrityError):
        store.list_session_ids()


@pytest.mark.parametrize("entry_kind", ["file", "malformed_directory"])
def test_list_session_ids_rejects_malformed_or_non_directory_entries(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    store, _ = started_session(tmp_path)
    entry = tmp_path / "sessions" / "not-a-canonical-session"
    if entry_kind == "file":
        entry.write_text("synthetic", encoding="utf-8")
    else:
        entry.mkdir()

    with pytest.raises(SessionIntegrityError):
        store.list_session_ids()


def test_training_record_round_trip_uses_private_file(
    tmp_path: Path,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    record = {
        "schema_version": 1,
        "session": {"session_id": session_id},
        "export": {"content_hash": "a" * 64},
    }

    assert store.read_training_record(session_id) is None
    store.write_training_record(session_id, record)

    path = tmp_path / "sessions" / session_id / "training_record.json"
    assert store.read_training_record(session_id) == record
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("entry_kind", ["directory", "symlink"])
def test_training_record_rejects_non_regular_files(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    path = tmp_path / "sessions" / session_id / "training_record.json"
    if entry_kind == "directory":
        path.mkdir()
    else:
        target = tmp_path / "outside-training-record.json"
        target.write_text("{}", encoding="utf-8")
        path.symlink_to(target)

    with pytest.raises(SessionIntegrityError):
        store.read_training_record(session_id)


def test_read_events_if_present_distinguishes_missing_from_unsafe(
    tmp_path: Path,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    raw_path = tmp_path / "sessions" / session_id / "raw_events.jsonl"
    raw_path.unlink()
    assert store.read_events_if_present(session_id) is None

    target = tmp_path / "outside-events.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    raw_path.symlink_to(target)
    with pytest.raises(SessionIntegrityError):
        store.read_events_if_present(session_id)


def test_classroom_brief_round_trip_uses_private_file(tmp_path: Path) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    brief = {
        "schema_version": 1,
        "session_id": session_id,
        "status": "complete",
        "data_completeness": "complete",
    }

    assert store.read_classroom_brief(session_id) is None
    store.write_classroom_brief(session_id, brief)

    path = tmp_path / "sessions" / session_id / "classroom_brief.json"
    assert store.read_classroom_brief(session_id) == brief
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_classroom_brief_rejects_non_canonical_session_ids(
    tmp_path: Path,
) -> None:
    store, _ = started_session(tmp_path)

    with pytest.raises(InvalidSessionIdError):
        store.read_classroom_brief("not-a-canonical-session")
    with pytest.raises(InvalidSessionIdError):
        store.write_classroom_brief("not-a-canonical-session", {})


@pytest.mark.parametrize("entry_kind", ["directory", "symlink"])
def test_classroom_brief_rejects_non_regular_files(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    path = tmp_path / "sessions" / session_id / "classroom_brief.json"
    outside = tmp_path / "outside-classroom-brief.json"
    outside.write_text('{"preserve": true}', encoding="utf-8")
    if entry_kind == "directory":
        path.mkdir()
    else:
        path.symlink_to(outside)

    with pytest.raises(SessionIntegrityError):
        store.read_classroom_brief(session_id)
    with pytest.raises(SessionIntegrityError):
        store.write_classroom_brief(session_id, {"schema_version": 1})
    assert outside.read_text(encoding="utf-8") == '{"preserve": true}'
