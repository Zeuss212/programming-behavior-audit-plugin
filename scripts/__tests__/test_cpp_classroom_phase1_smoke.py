from __future__ import annotations

import base64
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SMOKE_PATH = ROOT / "scripts" / "cpp_classroom_phase1_smoke.py"
MATERIALS = ROOT / "deploy" / "classroom" / "local-demo" / "materials"
SERVICE_SRC = ROOT / "services" / "classroom-sync" / "src"
sys.path.insert(0, str(SERVICE_SRC))

from classroom_sync.auth.fincolab import Principal  # noqa: E402
from classroom_sync.services.assessment_materials import (  # noqa: E402
    AssessmentMaterialBundle,
    AssessmentMaterialService,
)
from classroom_sync.services.publication_gate import (  # noqa: E402
    PublicationGate,
    PublicationGateResult,
)


def _load_smoke(name: str = "cpp_classroom_phase1_smoke"):
    spec = importlib.util.spec_from_file_location(name, SMOKE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _real_material(name: str) -> AssessmentMaterialBundle:
    payload = cast(
        dict[str, object],
        json.loads((MATERIALS / name / "bundle.json").read_text(encoding="utf-8")),
    )
    starter = cast(dict[str, object], payload["starter_source"])
    source_path = MATERIALS / name / cast(str, starter["file_name"])
    starter["content_base64"] = base64.b64encode(source_path.read_bytes()).decode("ascii")

    class StaticGateway:
        def get_bundle(
            self,
            principal: Principal,
            space_id: str,
            parent_algorithm_id: str,
        ) -> dict[str, object]:
            assert principal.user_id == "teacher-1"
            assert space_id == payload["space_id"]
            assert parent_algorithm_id == payload["parent_algorithm_id"]
            return deepcopy(payload)

    return AssessmentMaterialService(StaticGateway()).get_bundle(
        Principal("teacher-1", "teacher001", "teacher-token"),
        cast(str, payload["space_id"]),
        cast(str, payload["parent_algorithm_id"]),
    )


class FixtureBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.sequence_materials = _real_material("sequence-list")
        self.linked_materials = _real_material("linked-list")
        self.sequence = self.sequence_materials.model_dump(mode="json")
        self.linked = self.linked_materials.model_dump(mode="json")
        self.saved_profile: dict[str, object] | None = None
        self.saved_gate: PublicationGateResult | None = None
        self.draft_reload_calls = 0
        self.published = False

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
            "username": "1",
            "password": "1",
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
            candidate = cast(dict[str, object], deepcopy(payload["profile"]))
            gate = PublicationGate().evaluate(candidate, self.linked_materials)
            self.saved_gate = gate
            if gate.status != "ready":
                return 409, {
                    "detail": "publication_gate_blocked",
                    "publication_gate": gate.safe_projection(),
                }
            self.saved_profile = candidate
            return 201, {
                "draft_id": "draft-linked-v3",
                "authoring_session_id": "authoring-linked",
                "profile": deepcopy(self.saved_profile),
                "revision": 0,
                "publication_gate": gate.safe_projection(),
            }
        if path == "/v1/classroom/plans/drafts/draft-linked-v3":
            assert method == "GET" and payload is None
            assert self.saved_profile is not None
            assert self.saved_gate is not None
            self.draft_reload_calls += 1
            return 200, {
                "draft_id": "draft-linked-v3",
                "authoring_session_id": "authoring-linked",
                "profile": deepcopy(self.saved_profile),
                "revision": 0,
                "publication_gate": self.saved_gate.safe_projection(),
            }
        if path == "/v1/classroom/plans/drafts/draft-linked-v3/publish":
            assert method == "POST" and payload is None
            assert self.saved_profile is not None
            gate = PublicationGate().evaluate(self.saved_profile, self.linked_materials)
            assert gate.status == "ready"
            self.published = True
            return 200, {"plan_version_id": "linked-plan-v1", "version": 1}
        if path == "/v1/classroom/plan-authoring-sessions/authoring-linked/plan-suggestion":
            assert method == "GET" and payload is None
            assert self.published
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
    assert backend.saved_gate is not None
    assert backend.saved_gate.status == "ready"
    assert backend.saved_gate.blocking_count == 0
    assert backend.draft_reload_calls == 1
    assert backend.published is True
    assert backend.saved_profile["schema_version"] == 3
    assert [
        point["material_requirement_id"]
        for point in backend.saved_profile["knowledge_points"]  # type: ignore[index]
    ] == ["REQ_LINK_TAIL_INSERT", "REQ_LINK_REVERSE"]
    assert [
        test["id"]
        for test in backend.saved_profile["assessment_tests"]  # type: ignore[index]
    ] == ["TEST_LINK0001", "TEST_LINK0002"]
    assert (
        backend.saved_profile["confirmations"]["material_bundle_hash"]  # type: ignore[index]
        == backend.linked_materials.bundle_hash
    )


def test_phase_one_smoke_rejects_an_unvalidated_schema_only_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_smoke("cpp_classroom_phase1_smoke_invalid_profile")
    backend = FixtureBackend()
    monkeypatch.setattr(
        smoke,
        "_corrected_linked_profile",
        lambda _materials: {"schema_version": 3},
    )

    with pytest.raises(smoke.PhaseOneSmokeFailure, match="unexpected status"):
        smoke.run_phase_one_smoke(
            facade_request=backend.facade_request,
            sync_request=backend.sync_request,
        )

    assert backend.saved_gate is not None
    assert backend.saved_gate.status == "blocked"
    assert backend.saved_gate.blocking_count > 0
    assert backend.saved_profile is None
    assert backend.draft_reload_calls == 0
    assert backend.published is False
    assert not any(path.endswith("/publish") for _, path in backend.calls)
    assert not any(path.endswith("/assignments/sync") for _, path in backend.calls)
    assert not any("/student/" in path or "/plugin/" in path for _, path in backend.calls)


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
