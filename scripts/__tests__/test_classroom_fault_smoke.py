"""Regression coverage for the deterministic classroom fault smoke runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_fault_smoke_module():
    path = Path(__file__).resolve().parents[1] / "classroom_fault_smoke.py"
    spec = importlib.util.spec_from_file_location("classroom_fault_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_observation_fields(observation) -> None:
    """Every scenario must remain comparable in a teacher-facing fault report."""

    assert isinstance(observation.before_session_id, str) and observation.before_session_id
    assert isinstance(observation.after_session_id, str) and observation.after_session_id
    assert isinstance(observation.last_sequence, int) and observation.last_sequence >= 0
    assert isinstance(observation.missing_ranges, tuple)
    assert all(
        isinstance(item, tuple)
        and len(item) == 2
        and isinstance(item[0], int)
        and isinstance(item[1], int)
        for item in observation.missing_ranges
    )
    assert observation.submission_reason is None or isinstance(observation.submission_reason, str)
    assert observation.brief_revision is None or observation.brief_revision >= 1
    assert isinstance(observation.object_count, int) and observation.object_count >= 0


def test_fault_smoke_records_recovery_evidence_for_every_supported_scenario(tmp_path: Path):
    smoke = _load_fault_smoke_module()

    observations = {item.scenario: item for item in smoke.run_all(tmp_path)}

    assert set(observations) == {
        "browser_reload",
        "deadline_worker_restart",
        "evidence_network_partition",
        "submission_retry",
        "ticket_replay",
    }
    for observation in observations.values():
        _assert_observation_fields(observation)

    reload = observations["browser_reload"]
    assert reload.before_session_id == reload.after_session_id
    assert reload.last_sequence == 1
    assert reload.missing_ranges == ()
    assert reload.submission_reason is None
    assert reload.brief_revision is None
    assert reload.object_count == 1
    assert reload.outcome == "resumed_without_duplicate_evidence"

    deadline = observations["deadline_worker_restart"]
    assert deadline.before_session_id == deadline.after_session_id
    assert deadline.last_sequence == 0
    assert deadline.missing_ranges == ((1, 1),)
    assert deadline.submission_reason == "system_deadline"
    assert deadline.brief_revision == 1
    assert deadline.object_count == 0
    assert deadline.outcome == "reclaimed_and_closed"
    assert deadline.details == {
        "active_before_cutoff": True,
        "teacher_end_recorded": True,
        "worker_claim_attempts": 2,
    }

    partition = observations["evidence_network_partition"]
    assert partition.before_session_id == partition.after_session_id
    assert partition.last_sequence == 1
    assert partition.object_count == 1
    assert partition.outcome == "deferred_then_delivered"

    submission = observations["submission_retry"]
    assert submission.before_session_id == submission.after_session_id
    assert submission.submission_reason == "student_manual"
    assert submission.brief_revision == 1
    assert submission.outcome == "pending_upload_then_submitted_once"

    replay = observations["ticket_replay"]
    assert replay.before_session_id == replay.after_session_id
    assert replay.outcome == "ticket_already_consumed"
