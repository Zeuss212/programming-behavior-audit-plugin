from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SMOKE_PATH = ROOT / "scripts" / "cpp_classroom_phase1_smoke.py"
MATERIALS = ROOT / "deploy" / "classroom" / "local-demo" / "materials"


def _load_smoke(name: str = "cpp_classroom_phase1_smoke"):
    spec = importlib.util.spec_from_file_location(name, SMOKE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _public_material(name: str, public_hash: str) -> dict[str, object]:
    payload = json.loads((MATERIALS / name / "bundle.json").read_text(encoding="utf-8"))
    payload.pop("importer_version")
    payload.pop("toolchain_profile")
    payload["bundle_hash"] = public_hash
    return payload


class FixtureBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.sequence = _public_material("sequence-list", "a" * 64)
        self.linked = _public_material("linked-list", "b" * 64)
        self.saved_profile: dict[str, object] | None = None

    def facade_request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        self.calls.append((method, path))
        assert token is None
        assert (method, path) == ("POST", "/v1/login")
        assert payload == {
            "username": "teacher001",
            "password": "local-demo-teacher",
        }
        return 200, {"token": "teacher-token"}

    def sync_request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        self.calls.append((method, path))
        assert token == "teacher-token"
        if path.endswith("/sequence-list-experiment-001/assessment-materials"):
            assert method == "GET" and payload is None
            return 200, deepcopy(self.sequence)
        if path.endswith("/linked-list-experiment-002/assessment-materials"):
            assert method == "GET" and payload is None
            return 200, deepcopy(self.linked)
        if path == "/v1/classroom/plan-authoring-sessions":
            assert method == "POST"
            assert payload == {
                "space_id": "course-001",
                "parent_algorithm_id": "linked-list-experiment-002",
            }
            return 200, self._authoring("open")
        if path.startswith("/v1/classroom/plan-authoring-sessions/current?"):
            assert method == "GET" and payload is None
            return 200, self._authoring("open")
        if path == "/v1/classroom/plans/drafts":
            assert method == "POST" and payload is not None
            self.saved_profile = deepcopy(payload["profile"])  # type: ignore[assignment]
            return 201, {
                "draft_id": "draft-linked-v3",
                "authoring_session_id": "authoring-linked",
                "profile": deepcopy(self.saved_profile),
                "revision": 0,
                "publication_gate": {
                    "status": "ready",
                    "blocking_count": 0,
                    "warning_count": 1,
                    "issues": [{"code": "boundary_coverage_incomplete"}],
                },
            }
        if path == "/v1/classroom/plans/drafts/draft-linked-v3":
            assert method == "GET" and payload is None
            assert self.saved_profile is not None
            return 200, {
                "draft_id": "draft-linked-v3",
                "authoring_session_id": "authoring-linked",
                "profile": deepcopy(self.saved_profile),
                "revision": 0,
                "publication_gate": {
                    "status": "ready",
                    "blocking_count": 0,
                    "warning_count": 1,
                    "issues": [{"code": "boundary_coverage_incomplete"}],
                },
            }
        if path == "/v1/classroom/plans/drafts/draft-linked-v3/publish":
            assert method == "POST" and payload is None
            return 200, {"plan_version_id": "linked-plan-v1", "version": 1}
        if path == "/v1/classroom/plan-authoring-sessions/authoring-linked/plan-suggestion":
            assert method == "GET" and payload is None
            return 200, self._authoring("published")
        raise AssertionError(f"unexpected smoke call: {method} {path}")

    @staticmethod
    def _authoring(status: str) -> dict[str, object]:
        return {
            "authoring_session_id": "authoring-linked",
            "status": status,
            "space_id": "course-001",
            "parent_algorithm_id": "linked-list-experiment-002",
            "draft_id": "draft-linked-v3" if status == "published" else None,
            "suggestion": {
                "status": "not_requested",
                "job_id": None,
                "input_hash": None,
            },
        }


def test_phase_one_smoke_completes_seven_steps_without_student_side_effects() -> None:
    smoke = _load_smoke()
    backend = FixtureBackend()

    result = smoke.run_phase_one_smoke(
        facade_request=backend.facade_request,
        sync_request=backend.sync_request,
    )

    assert result == {
        "status": "ok",
        "verified_steps": 7,
        "authoring_session_id": "authoring-linked",
        "draft_id": "draft-linked-v3",
        "plan_version_id": "linked-plan-v1",
        "session_status": "published",
        "assignment_sync_calls": 0,
        "student_run_calls": 0,
        "ai_calls": 0,
    }
    assert backend.calls == [
        ("POST", "/v1/login"),
        (
            "GET",
            (
                "/v1/classroom/experiments/course-001/"
                "sequence-list-experiment-001/assessment-materials"
            ),
        ),
        (
            "GET",
            (
                "/v1/classroom/experiments/course-001/"
                "linked-list-experiment-002/assessment-materials"
            ),
        ),
        ("POST", "/v1/classroom/plan-authoring-sessions"),
        (
            "GET",
            (
                "/v1/classroom/plan-authoring-sessions/current?"
                "space_id=course-001&parent_algorithm_id=linked-list-experiment-002"
            ),
        ),
        ("POST", "/v1/classroom/plan-authoring-sessions"),
        ("POST", "/v1/classroom/plans/drafts"),
        ("GET", "/v1/classroom/plans/drafts/draft-linked-v3"),
        ("POST", "/v1/classroom/plans/drafts/draft-linked-v3/publish"),
        (
            "GET",
            ("/v1/classroom/plan-authoring-sessions/authoring-linked/plan-suggestion"),
        ),
    ]
    assert not any(path.endswith("/assignments/sync") for _, path in backend.calls)
    assert not any("/student/" in path or "/plugin/" in path for _, path in backend.calls)
    assert not any(
        method == "POST" and path.endswith("/plan-suggestion") for method, path in backend.calls
    )

    assert backend.saved_profile is not None
    assert backend.saved_profile["schema_version"] == 3
    assert [
        point["material_requirement_id"]
        for point in backend.saved_profile["knowledge_points"]  # type: ignore[index]
    ] == ["REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"]
    assert [
        test["id"]
        for test in backend.saved_profile["assessment_tests"]  # type: ignore[index]
    ] == ["TEST_LINK0001", "TEST_LINK0002"]
    assert backend.saved_profile["confirmations"]["material_bundle_hash"] == "b" * 64  # type: ignore[index]


@pytest.mark.parametrize(
    ("material_name", "issue_code"),
    [
        ("sequence", "detector_profile_unavailable"),
        ("linked", "required_student_dimension_missing"),
    ],
)
def test_phase_one_smoke_fails_closed_when_an_exact_material_issue_is_missing(
    material_name: str,
    issue_code: str,
) -> None:
    smoke = _load_smoke(f"cpp_classroom_phase1_smoke_{material_name}")
    backend = FixtureBackend()
    material = getattr(backend, material_name)
    material["issues"] = [issue for issue in material["issues"] if issue["code"] != issue_code]

    with pytest.raises(smoke.PhaseOneSmokeFailure, match="material issue codes mismatch"):
        smoke.run_phase_one_smoke(
            facade_request=backend.facade_request,
            sync_request=backend.sync_request,
        )

    assert not any(path == "/v1/classroom/plan-authoring-sessions" for _, path in backend.calls)


def test_smoke_summary_never_contains_provider_source_or_test_contents() -> None:
    smoke = _load_smoke("cpp_classroom_phase1_smoke_privacy")
    backend = FixtureBackend()
    result = smoke.run_phase_one_smoke(
        facade_request=backend.facade_request,
        sync_request=backend.sync_request,
    )

    rendered = smoke.render_safe_summary(result)

    assert json.loads(rendered) == result
    assert "content_base64" not in rendered
    assert "expected_stdout" not in rendered
    assert "provider" not in rendered.casefold()
    assert "TEST_LINK0001" not in rendered
    assert "REQ_LINK_TAIL_INSERT" not in rendered
