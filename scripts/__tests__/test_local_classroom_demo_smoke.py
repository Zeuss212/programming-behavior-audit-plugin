from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[2]
FACADE_PATH = ROOT / "deploy" / "classroom" / "local-demo" / "fincolab_demo.py"
SMOKE_PATH = ROOT / "scripts" / "local_classroom_demo_smoke.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def demo_facade_url() -> Iterator[str]:
    facade = _load_module(FACADE_PATH, "local_classroom_demo_facade_for_smoke")
    server = ThreadingHTTPServer(("127.0.0.1", 0), facade.DemoFincolabHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_local_smoke_checks_real_facade_then_runs_contract_state_machine_twice(tmp_path: Path):
    smoke = _load_module(SMOKE_PATH, "local_classroom_demo_smoke")
    state_path = tmp_path / "identifier-only-state.json"
    calls: list[bool] = []

    def fake_contract_runner(_client, *, state_file: Path, now, repeat_existing: bool):
        calls.append(repeat_existing)
        assert state_file == state_path
        assert now.tzinfo is not None
        return {
            "phase": "submitted" if repeat_existing else "collecting",
            "session_id": "session-local",
            "plan_version_id": "plan-version-local",
        }

    with demo_facade_url() as facade_url:
        result = smoke.run_local_demo_smoke(
            facade_base_url=facade_url,
            sync_base_url="http://127.0.0.1:18080",
            state_file=state_path,
            contract_runner=fake_contract_runner,
            monitoring_reader=lambda _plan_version_id: {
                "students": [{"brief": {"ai_analysis_status": "not_requested"}}]
            },
        )

    assert result == {
        "phase": "submitted",
        "session_id": "session-local",
        "plan_version_id": "plan-version-local",
    }
    assert calls == [False, True]
    assert not state_path.exists()


def test_local_smoke_bypasses_system_proxy_for_loopback(monkeypatch):
    smoke = _load_module(SMOKE_PATH, "local_classroom_demo_smoke_proxy_bypass")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    with demo_facade_url() as facade_url:
        status, body = smoke._request_json(facade_url, "GET", "/health/live")

    assert status == 200
    assert body == {"status": "live"}


def test_local_smoke_rejects_an_unexpected_facade_login_token(tmp_path: Path):
    smoke = _load_module(SMOKE_PATH, "local_classroom_demo_smoke_bad_login")
    state_path = tmp_path / "state.json"

    with demo_facade_url() as facade_url:
        try:
            smoke.run_local_demo_smoke(
                facade_base_url=facade_url,
                sync_base_url="http://127.0.0.1:18080",
                state_file=state_path,
                contract_runner=lambda *_args, **_kwargs: {"phase": "submitted"},
                expected_teacher_token="wrong-token",
        )
        except smoke.LocalDemoSmokeFailure as error:
            assert "1 login" in str(error)
        else:
            raise AssertionError("the façade token mismatch must fail before contract smoke")


def test_local_smoke_rejects_unsafe_or_unknown_ai_monitoring_fields() -> None:
    smoke = _load_module(SMOKE_PATH, "local_classroom_demo_smoke_ai_monitoring")

    smoke._require_safe_monitoring_briefs(
        {"students": [{"brief": {"ai_analysis_status": "ready"}}]}
    )

    with pytest.raises(smoke.LocalDemoSmokeFailure, match="AI analysis status"):
        smoke._require_safe_monitoring_briefs(
            {"students": [{"brief": {"ai_analysis_status": "forged_ready"}}]}
        )


def test_full_ai_loop_requires_a_plan_suggestion_and_ready_student_analysis(tmp_path: Path):
    smoke = _load_module(SMOKE_PATH, "local_classroom_demo_smoke_full_ai_loop")
    calls: list[object] = []

    def fake_contract_runner(_client, *, state_file: Path, now, repeat_existing: bool, ai_policy: str):
        calls.append((repeat_existing, ai_policy))
        assert state_file == tmp_path / "state.json"
        return {
            "phase": "submitted" if repeat_existing else "collecting",
            "session_id": "session-local",
            "plan_version_id": "plan-version-local",
        }

    def fake_suggestion_runner(base_url: str, teacher_token: str) -> None:
        calls.append((base_url, teacher_token))

    with demo_facade_url() as facade_url:
        result = smoke.run_local_demo_smoke(
            facade_base_url=facade_url,
            sync_base_url="http://127.0.0.1:18080",
            state_file=tmp_path / "state.json",
            contract_runner=fake_contract_runner,
            monitoring_reader=lambda _plan_version_id: {
                "students": [{"brief": {"ai_analysis_status": "ready"}}]
            },
            require_ai=True,
            ai_suggestion_runner=fake_suggestion_runner,
            brief_reader=lambda _session_id: {
                "ai_analysis_status": "ready",
                "ai_analysis": {
                    "knowledge_point_analyses": [
                        {
                            "knowledge_point_id": "KP_SCORE",
                            "status": "partial",
                            "evidence_event_ids": ["chunk-1#event-1"],
                            "teaching_suggestion": "请追问学生如何验证边界输入。",
                        }
                    ],
                    "teacher_note": "根据运行证据组织下一步追问。",
                },
            },
        )

    assert result["phase"] == "submitted"
    assert calls == [
        ("http://127.0.0.1:18080", "teacher-token"),
        (False, "allowed"),
        (True, "allowed"),
    ]
    with pytest.raises(smoke.LocalDemoSmokeFailure, match="sensitive"):
        smoke._require_safe_monitoring_briefs(
            {
                "students": [
                    {"brief": {"ai_analysis_status": "not_requested", "object_key": "private"}}
                ]
            }
        )
    with pytest.raises(smoke.LocalDemoSmokeFailure, match="evidence-backed"):
        smoke._require_usable_ai_analysis(
            {
                "knowledge_point_analyses": [
                    {
                        "knowledge_point_id": "KP_SCORE",
                        "status": "observed",
                        "evidence_event_ids": [],
                        "teaching_suggestion": "请追问学生如何验证边界输入。",
                    }
                ],
                "teacher_note": "根据运行证据组织下一步追问。",
            }
        )
