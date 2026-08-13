"""Safety and failure-contract tests for the real local Compose fault runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_compose_fault_module():
    path = Path(__file__).resolve().parents[1] / "classroom_compose_fault_smoke.py"
    spec = importlib.util.spec_from_file_location("classroom_compose_fault_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_compose_fault_runner_is_limited_to_local_test_services():
    smoke = _load_compose_fault_module()
    project_name = "classroom-fault-012345abcdef"

    assert smoke.require_local_base_url("http://127.0.0.1:18080") == "http://127.0.0.1:18080"
    for unsafe_url in (
        "http://127.0.0.1:18080/",
        "http://localhost:18080",
        "http://user@127.0.0.1:18080",
        "https://14.103.139.131:40037",
    ):
        with pytest.raises(smoke.ComposeFaultFailure, match="local"):
            smoke.require_local_base_url(unsafe_url)

    restart = smoke.compose_command("restart", "sync-api", project_name=project_name)
    minio_stop = smoke.compose_command("stop", "minio", project_name=project_name)
    cleanup = smoke.compose_command(
        "down", "--volumes", "--remove-orphans", project_name=project_name
    )
    assert restart[-2:] == ("restart", "sync-api")
    assert restart[-4:] == ("--project-name", project_name, "restart", "sync-api")
    assert minio_stop[-2:] == ("stop", "minio")
    assert cleanup[-3:] == ("down", "--volumes", "--remove-orphans")
    assert cleanup[cleanup.index("--project-name") + 1] == project_name
    assert "deploy/classroom/docker-compose.test.yml" in "/".join(restart)
    for unsafe_project_name in ("classroom-contract-test", "classroom-fault-short", "other-fault-012345abcdef"):
        with pytest.raises(smoke.ComposeFaultFailure, match="isolated"):
            smoke.compose_command("restart", "sync-api", project_name=unsafe_project_name)

    compose = (Path(__file__).resolve().parents[2] / "deploy/classroom/docker-compose.test.yml").read_text(
        encoding="utf-8"
    )
    assert "image: classroom-contract-test-mock-fincolab:latest" in compose
    assert "image: classroom-contract-test-sync-api:latest" in compose
    assert "image: classroom-contract-test-deadline-worker:latest" in compose
    dockerfile = (Path(__file__).resolve().parents[2] / "services/classroom-sync/Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "docker/dockerfile" not in dockerfile
    assert "--mount=type=cache" not in dockerfile


def test_real_compose_fault_runner_requires_the_expected_http_failure():
    smoke = _load_compose_fault_module()

    assert smoke.expect_http_status(
        lambda: (_ for _ in ()).throw(smoke.SmokeFailure("PUT /evidence returned HTTP 503")),
        503,
    )
    with pytest.raises(smoke.ComposeFaultFailure, match="expected HTTP 503"):
        smoke.expect_http_status(lambda: None, 503)


def test_real_compose_fault_runner_enforces_every_recovery_step(monkeypatch, tmp_path: Path):
    smoke = _load_compose_fault_module()
    project_name = "classroom-fault-012345abcdef"
    session_id = "33333333-3333-4333-8333-333333333333"
    state = {
        "assignment_id": "44444444-4444-4444-8444-444444444444",
        "evidence_id": "55555555-5555-4555-8555-555555555555",
        "phase": "collecting",
        "plan_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "plan_version": 1,
        "plan_version_id": "22222222-2222-4222-8222-222222222222",
        "session_id": session_id,
    }
    compose_calls: list[tuple[tuple[str, ...], str]] = []

    class Client:
        def __init__(self) -> None:
            self.evidence_two_attempts = 0

        def request_json(self, _method, path, *, token=None, payload=None):
            if path.endswith("/launch-ticket"):
                assert token == "student-token"
                return {"ticket": "one-time-ticket"}
            if path == "/v1/classroom/plugin/sessions/register":
                assert payload is not None
                if payload["plugin_instance_id"] == "ticket-replay":
                    raise smoke.SmokeFailure("POST /register returned HTTP 403")
                return {"session_id": session_id, "access_token": "plugin-token"}
            raise AssertionError(f"Unexpected request: {path}")

        def put_evidence(self, path, _body, *, token, first_event_sequence, last_event_sequence):
            assert path.endswith("/evidence/2")
            assert token == "plugin-token"
            assert (first_event_sequence, last_event_sequence) == (2, 2)
            self.evidence_two_attempts += 1
            if self.evidence_two_attempts == 1:
                raise smoke.SmokeFailure("PUT /evidence returned HTTP 503")
            return {"sequence": 2}

    client = Client()
    monkeypatch.setattr(smoke, "HttpSmokeClient", lambda _base_url: client)
    monkeypatch.setattr(smoke, "_wait_for_ready", lambda _client: None)
    monkeypatch.setattr(smoke, "run_smoke", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(smoke, "_read_collecting_state", lambda _path: state)
    monkeypatch.setattr(smoke, "_upload_evidence", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(
        smoke,
        "_submit_and_read",
        lambda *_args, **_kwargs: {**state, "phase": "submitted", "submission_reason": "student_manual"},
    )
    monkeypatch.setattr(smoke, "_write_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        smoke,
        "_run_compose",
        lambda *arguments, project_name: compose_calls.append((arguments, project_name)),
    )

    result = smoke.run_all(
        "http://127.0.0.1:18080",
        state_file=tmp_path / "state.json",
        project_name=project_name,
    )

    assert result == {
        "minio_503_observed": True,
        "phase": "submitted",
        "session_id": session_id,
        "status": "ok",
        "submission_reason": "student_manual",
        "sync_api_restarted": True,
        "ticket_replay_rejected": True,
    }
    assert client.evidence_two_attempts == 2
    assert compose_calls == [
        (("restart", "sync-api"), project_name),
        (("stop", "minio"), project_name),
        (("start", "minio"), project_name),
    ]
