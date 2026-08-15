from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Iterator

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
            assert "teacher001 login" in str(error)
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
    with pytest.raises(smoke.LocalDemoSmokeFailure, match="sensitive"):
        smoke._require_safe_monitoring_briefs(
            {
                "students": [
                    {"brief": {"ai_analysis_status": "not_requested", "object_key": "private"}}
                ]
            }
        )
