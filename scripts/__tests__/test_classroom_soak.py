"""Regression coverage for the local accelerated classroom soak harness."""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

import pytest


def _load_soak_module():
    path = Path(__file__).resolve().parents[1] / "classroom_soak.py"
    spec = importlib.util.spec_from_file_location("classroom_soak", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingSoakClient:
    """Concurrent-safe local API double that records the accelerated protocol."""

    def __init__(self, students: int) -> None:
        self._students = students
        self._lock = threading.Lock()
        self.calls: list[tuple[str, str, str | None]] = []

    def request_json(self, method, path, *, token=None, payload=None):
        with self._lock:
            self.calls.append((method, path, token))
        if path == "/v1/classroom/plans/drafts":
            assert token == "teacher-token"
            return {"draft_id": "11111111-1111-4111-8111-111111111111", "revision": 0}
        if path.endswith("/publish"):
            return {
                "plan_version_id": "22222222-2222-4222-8222-222222222222",
                "plan_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "version": 1,
            }
        if path.endswith("/assignments/sync"):
            return {
                "assignments": [
                    {
                        "assignment_id": f"assignment-{index:03d}",
                        "student_id": f"student{index:03d}",
                        "status": "pending_acceptance",
                    }
                    for index in range(1, self._students + 1)
                ]
            }
        if path.endswith("/accept"):
            assert token is not None and token.endswith("-token")
            return {"status": "ready"}
        if path.endswith("/launch-ticket"):
            assert token is not None
            return {"ticket": token.removesuffix("-token")}
        if path == "/v1/classroom/plugin/sessions/register":
            assert payload is not None
            student_id = payload["ticket"]
            return {"session_id": f"session-{student_id}", "access_token": f"plugin-{student_id}"}
        if path.endswith("/heartbeat"):
            return {"status": "collecting"}
        if path.endswith("/submit"):
            assert payload is not None and payload["reason"] == "student_manual"
            return {"status": "completed", "revision": 1}
        raise AssertionError(f"Unexpected request: {method} {path}")

    def put_evidence(
        self,
        path,
        body,
        *,
        token,
        first_event_sequence,
        last_event_sequence,
    ):
        with self._lock:
            self.calls.append(("PUT", path, token))
        assert body
        assert token.startswith("plugin-student")
        assert (first_event_sequence, last_event_sequence) == (1, 1)
        return {"sequence": 1}


def test_accelerated_soak_reports_parallel_student_lifecycle_without_claiming_acceptance():
    soak = _load_soak_module()
    client = RecordingSoakClient(students=3)

    report = soak.run_accelerated(
        "http://127.0.0.1:18080",
        students=3,
        client_factory=lambda _base_url: client,
    )

    assert report["mode"] == "accelerated"
    assert report["acceptance_valid"] is False
    assert report["students"] == 3
    assert report["heartbeat_latency_ms"]["count"] == 3
    assert report["evidence_chunks"] == {
        "attempted": 3,
        "accepted_receipts": 3,
        "stored": "not_observed",
    }
    assert report["duplicates"] == {"evidence": "not_observed", "briefs": "not_observed"}
    assert report["missing_ranges"] == "not_observed"
    assert report["outbox_peak"] == "not_observed"
    assert report["final_status"] == {"completed": 3, "partial": 0}
    assert report["brief_revision"] == {"minimum": 1, "maximum": 1}
    assert sum(path.endswith("/heartbeat") for _method, path, _token in client.calls) == 3
    assert sum(path.endswith("/submit") for _method, path, _token in client.calls) == 3


def test_soak_rejects_production_duration_below_45_minutes_and_remote_acceleration():
    soak = _load_soak_module()

    with pytest.raises(soak.SoakConfigurationError, match="45"):
        soak.validate_request(accelerated=False, duration_minutes=44, base_url="https://classroom.test")
    with pytest.raises(soak.SoakConfigurationError, match="127.0.0.1"):
        soak.validate_request(accelerated=True, duration_minutes=None, base_url="https://classroom.test")
    assert soak.validate_request(
        accelerated=False,
        duration_minutes=45,
        base_url="https://classroom.test",
    ) == "real"


def test_soak_uses_nearest_rank_for_tail_latency_percentiles():
    soak = _load_soak_module()

    assert soak._percentile([1.0, 2.0, 3.0], 0.50) == 2.0
    assert soak._percentile([1.0, 2.0, 3.0], 0.95) == 3.0


def test_blind_audit_and_acceptance_template_keep_required_roles_and_boundaries():
    root = Path(__file__).resolve().parents[2]
    blind_audit = (root / "scripts/classroom_blind_audit.md").read_text(encoding="utf-8")
    acceptance = (root / "docs/verification/classroom-acceptance-template.md").read_text(
        encoding="utf-8"
    )

    for role in ("教师", "学生 A", "学生 B", "观察员"):
        assert role in blind_audit
    assert "40037" in blind_audit
    assert "15 分钟" in blind_audit
    assert "不读取源代码" in blind_audit
    assert "acceptance_valid" in acceptance
    assert "heartbeat" in acceptance
    assert "p95" in acceptance
    assert "BAMS" in acceptance
    assert "--project-name classroom-soak-verify-" in acceptance
