"""Unit coverage for the local classroom API contract smoke runner."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path


def _load_smoke_module():
    path = Path(__file__).resolve().parents[1] / "classroom_contract_smoke.py"
    spec = importlib.util.spec_from_file_location("classroom_contract_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingClient:
    """Small deterministic API double that checks the public route sequence."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.evidence_bodies: list[bytes] = []
        self._ticket_count = 0
        self.session_id = "33333333-3333-4333-8333-333333333333"

    def request_json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.calls.append((method, path, token))
        if path == "/v1/classroom/plans/drafts":
            assert method == "POST"
            assert token == "teacher-token"
            assert payload is not None
            assert payload["space_id"] == "course-001"
            return {"draft_id": "11111111-1111-4111-8111-111111111111", "revision": 0}
        if path.endswith("/publish"):
            assert token == "teacher-token"
            return {
                "plan_version_id": "22222222-2222-4222-8222-222222222222",
                "plan_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "version": 1,
                "content_hash": "b" * 64,
            }
        if path.endswith("/assignments/sync"):
            assert token == "teacher-token"
            return {
                "assignments": [
                    {
                        "assignment_id": "44444444-4444-4444-8444-444444444444",
                        "student_id": "student001",
                        "plan_version": 1,
                        "status": "pending_acceptance",
                    }
                ]
            }
        if path.endswith("/accept"):
            assert token == "student-token"
            return {
                "assignment_id": "44444444-4444-4444-8444-444444444444",
                "status": "ready",
                "plan_version": 1,
            }
        if path.endswith("/launch-ticket"):
            assert token == "student-token"
            self._ticket_count += 1
            return {"ticket": f"test-ticket-{self._ticket_count}", "expires_at": "2026-08-13T09:01:00+00:00"}
        if path == "/v1/classroom/plugin/sessions/register":
            assert token is None
            assert payload is not None
            return {
                "session_id": self.session_id,
                "access_token": "plugin-token",
                "expires_at": "2026-08-13T09:30:00+00:00",
                "assignment_id": "44444444-4444-4444-8444-444444444444",
                "plan_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "plan_version": 1,
                "profile": {"schema_version": 2},
                "scheduled_end_at": "2026-08-13T10:00:00+00:00",
                "evidence_cutoff_at": "2026-08-13T10:15:00+00:00",
                "last_sync_at": "2026-08-13T09:00:00+00:00",
            }
        if path.endswith("/submit"):
            assert token == "plugin-token"
            assert payload is not None
            assert payload["reason"] == "student_manual"
            return {
                "brief_id": "55555555-5555-4555-8555-555555555555",
                "session_id": self.session_id,
                "revision": 1,
                "status": "completed",
            }
        if path.endswith("/brief"):
            assert token == "teacher-token"
            return {
                "brief_id": "55555555-5555-4555-8555-555555555555",
                "session_id": self.session_id,
                "assignment_id": "44444444-4444-4444-8444-444444444444",
                "plan_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "revision": 1,
                "status": "completed",
                "submission_reason": "student_manual",
            }
        raise AssertionError(f"Unexpected request: {method} {path}")

    def put_evidence(
        self,
        path: str,
        body: bytes,
        *,
        token: str,
        first_event_sequence: int,
        last_event_sequence: int,
    ) -> dict[str, object]:
        self.calls.append(("PUT", path, token))
        self.evidence_bodies.append(body)
        assert token == "plugin-token"
        assert first_event_sequence == 1
        assert last_event_sequence == 1
        return {
            "evidence_id": "66666666-6666-4666-8666-666666666666",
            "session_id": self.session_id,
            "sequence": 1,
            "content_sha256": __import__("hashlib").sha256(body).hexdigest(),
        }


def test_contract_smoke_creates_then_replays_one_logical_classroom_flow(tmp_path: Path):
    smoke = _load_smoke_module()
    client = RecordingClient()
    state_file = tmp_path / "classroom-contract-state.json"
    now = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)

    first = smoke.run_smoke(client, state_file=state_file, now=now)
    repeated = smoke.run_smoke(client, state_file=state_file, now=now, repeat_existing=True)

    assert first["session_id"] == repeated["session_id"] == client.session_id
    assert first["assignment_id"] == repeated["assignment_id"]
    assert first["phase"] == "collecting"
    assert repeated["phase"] == "submitted"
    assert repeated["submission_reason"] == "student_manual"
    assert "brief_id" not in first
    assert repeated["brief_id"] == "55555555-5555-4555-8555-555555555555"
    assert state_file.is_file()
    assert "plugin-token" not in state_file.read_text(encoding="utf-8")
    assert client.evidence_bodies[0] == client.evidence_bodies[1]
    assert [path for _method, path, _token in client.calls] == [
        "/v1/classroom/plans/drafts",
        "/v1/classroom/plans/drafts/11111111-1111-4111-8111-111111111111/publish",
        "/v1/classroom/plans/22222222-2222-4222-8222-222222222222/assignments/sync",
        "/v1/classroom/student/assignments/44444444-4444-4444-8444-444444444444/accept",
        "/v1/classroom/student/assignments/44444444-4444-4444-8444-444444444444/launch-ticket",
        "/v1/classroom/plugin/sessions/register",
        "/v1/classroom/plugin/sessions/33333333-3333-4333-8333-333333333333/evidence/1",
        "/v1/classroom/student/assignments/44444444-4444-4444-8444-444444444444/launch-ticket",
        "/v1/classroom/plugin/sessions/register",
        "/v1/classroom/plugin/sessions/33333333-3333-4333-8333-333333333333/evidence/1",
        "/v1/classroom/plugin/sessions/33333333-3333-4333-8333-333333333333/submit",
        "/v1/classroom/teacher/sessions/33333333-3333-4333-8333-333333333333/brief",
    ]
