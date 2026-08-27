"""Run the deterministic C++ classroom phase-one backend smoke."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

FACADE_BASE_URL = "http://127.0.0.1:18082"
SYNC_BASE_URL = "http://127.0.0.1:18080"
SPACE_ID = "course-001"
SEQUENCE_PARENT_ID = "sequence-list-experiment-001"
LINKED_PARENT_ID = "linked-list-experiment-002"
SEQUENCE_BLOCKER_CODES = (
    "starter_source_protected_compile_error",
    "starter_source_non_utf8_confirmation_required",
    "detector_profile_unavailable",
)
LINKED_RAW_DIMENSION_CODES = (
    "teacher_dimension_not_student_responsibility",
    "teacher_dimension_outside_task",
    "required_student_dimension_missing",
)
CORRECTED_REQUIREMENTS = (
    ("REQ_LINK_TAIL_INSERT", "KP_LINKTAL1", "CRIT_LINKTAL1"),
    ("REQ_LINK_REVERSE", "KP_LINKREV1", "CRIT_LINKREV1"),
)
EXPECTED_LINKED_TEST_IDS = ("TEST_LINK0001", "TEST_LINK0002")


class PhaseOneSmokeFailure(RuntimeError):
    """A local phase-one boundary did not satisfy the approved contract."""


class JsonRequest(Protocol):
    def __call__(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]: ...


class LocalJsonClient:
    """Small loopback-only JSON client that bypasses host proxy settings."""

    def __init__(self, base_url: str) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("phase-one smoke accepts only a local HTTP origin")
        self._base_url = base_url.rstrip("/")
        self._opener = build_opener(ProxyHandler({}))

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        headers = {"Accept": "application/json"}
        body = None
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        request = Request(
            f"{self._base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=5) as response:  # nosec B310 - loopback only.
                status = response.status
                raw = response.read()
        except HTTPError as error:
            status = error.code
            raw = error.read()
        except (OSError, URLError) as error:
            raise PhaseOneSmokeFailure(f"local endpoint unavailable: {path}") from error
        try:
            decoded: object = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise PhaseOneSmokeFailure(f"local endpoint returned invalid JSON: {path}") from error
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise PhaseOneSmokeFailure(f"local endpoint returned a non-object: {path}")
        return status, cast(dict[str, object], decoded)


def _require_status(
    request: JsonRequest,
    method: str,
    path: str,
    expected_status: int,
    *,
    token: str | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    status, body = request(method, path, token=token, payload=payload)
    if status != expected_status:
        raise PhaseOneSmokeFailure(f"local endpoint returned unexpected status: {path}")
    return body


def _required_string(payload: Mapping[str, object], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PhaseOneSmokeFailure(f"{context} returned an invalid identifier")
    return value


def _objects(payload: Mapping[str, object], key: str, context: str) -> list[dict[str, object]]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, dict) and all(isinstance(item_key, str) for item_key in item)
        for item in value
    ):
        raise PhaseOneSmokeFailure(f"{context} returned an invalid {key} collection")
    return cast(list[dict[str, object]], value)


def _assert_exact_issue_codes(
    materials: Mapping[str, object],
    expected: tuple[str, ...],
) -> None:
    blocking_codes = tuple(
        issue.get("code")
        for issue in _objects(materials, "issues", "assessment materials")
        if issue.get("severity") == "blocking"
    )
    if blocking_codes != expected:
        raise PhaseOneSmokeFailure("material issue codes mismatch")


def _sha256_json(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _corrected_linked_profile(materials: Mapping[str, object]) -> dict[str, object]:
    if materials.get("parent_algorithm_id") != LINKED_PARENT_ID:
        raise PhaseOneSmokeFailure("linked-list material identity mismatch")
    bundle_hash = _required_string(materials, "bundle_hash", "linked-list materials")
    starter = materials.get("starter_source")
    if not isinstance(starter, dict):
        raise PhaseOneSmokeFailure("linked-list materials lack a starter artifact")
    starter_source = {
        key: starter.get(key)
        for key in (
            "artifact_id",
            "display_name",
            "file_name",
            "sha256",
            "size_bytes",
            "source",
        )
    }
    if any(value is None for value in starter_source.values()):
        raise PhaseOneSmokeFailure("linked-list starter artifact is incomplete")

    requirements = {
        _required_string(requirement, "id", "linked-list requirement"): requirement
        for requirement in _objects(materials, "requirements", "linked-list materials")
    }
    tests = _objects(materials, "assessment_tests", "linked-list materials")
    if tuple(test.get("id") for test in tests) != EXPECTED_LINKED_TEST_IDS:
        raise PhaseOneSmokeFailure("linked-list assessment test identifiers mismatch")

    knowledge_points: list[dict[str, object]] = []
    dimensions: list[dict[str, object]] = []
    linked_criteria: dict[str, list[tuple[str, str]]] = {
        test_id: [] for test_id in EXPECTED_LINKED_TEST_IDS
    }
    for order, (requirement_id, point_id, criterion_id) in enumerate(CORRECTED_REQUIREMENTS):
        requirement = requirements.get(requirement_id)
        if requirement is None or requirement.get("student_responsibility") is not True:
            raise PhaseOneSmokeFailure("corrected linked-list requirement is unavailable")
        test_ids = requirement.get("test_ids")
        if test_ids != list(EXPECTED_LINKED_TEST_IDS):
            raise PhaseOneSmokeFailure("linked-list requirement test bindings mismatch")
        knowledge_points.append(
            {
                "id": point_id,
                "material_requirement_id": requirement_id,
                "name": f"linked-list point {order + 1}",
                "description": f"verified linked-list requirement {order + 1}",
                "source": "teacher",
                "order": order,
            }
        )
        for test_id in EXPECTED_LINKED_TEST_IDS:
            linked_criteria[test_id].append((point_id, criterion_id))
        dimensions.append(
            {
                "knowledge_point_id": point_id,
                "name": f"linked-list dimension {order + 1}",
                "question": f"is linked-list requirement {order + 1} satisfied?",
                "evidence_criteria": [
                    {
                        "id": criterion_id,
                        "material_requirement_id": requirement_id,
                        "statement": f"linked-list criterion {order + 1}",
                        "required": True,
                    }
                ],
                "verification_bindings": [
                    {
                        "criterion_id": criterion_id,
                        "kind": "assessment_test",
                        "assessment_test_id": EXPECTED_LINKED_TEST_IDS[0],
                    }
                ],
                "analysis_config": {"mode": "evidence_binding"},
            }
        )

    assessment_tests: list[dict[str, object]] = []
    for material_test in tests:
        test_id = cast(str, material_test["id"])
        if material_test.get("enabled") is not True:
            raise PhaseOneSmokeFailure("linked-list assessment test is disabled")
        linked = linked_criteria[test_id]
        without_hash = {
            **{key: value for key, value in material_test.items() if key != "content_hash"},
            "knowledge_point_ids": list(dict.fromkeys(point for point, _ in linked)),
            "criterion_ids": list(dict.fromkeys(criterion for _, criterion in linked)),
        }
        assessment_tests.append({**without_hash, "content_hash": _sha256_json(without_hash)})

    knowledge_points_hash = _sha256_json({"knowledge_points": knowledge_points})
    tests_without_hash = [
        {key: value for key, value in assessment_test.items() if key != "content_hash"}
        for assessment_test in assessment_tests
    ]
    return {
        "schema_version": 3,
        "problem_id": "cpp-linked-list-phase1",
        "title": "Linked-list tail insertion and reverse",
        "problem_context": {
            "statement": "Complete the approved linked-list edit responsibilities.",
            "language": "cpp",
            "submission_contract": {"kind": "stdin_stdout"},
            "toolchain_profile": "cpp17_stdio_v1",
            "entry_file": starter_source["file_name"],
            "source_encoding": "utf-8",
        },
        "starter_source": starter_source,
        "knowledge_points": knowledge_points,
        "assessment_tests": assessment_tests,
        "dimensions": dimensions,
        "confirmations": {
            "material_bundle_hash": bundle_hash,
            "starter_source_hash": _sha256_json(starter_source),
            "knowledge_points_hash": knowledge_points_hash,
            "dimensions_hash": _sha256_json(
                {
                    "knowledge_points_hash": knowledge_points_hash,
                    "dimensions": dimensions,
                }
            ),
            "tests_hash": _sha256_json({"assessment_tests": tests_without_hash}),
        },
    }


def run_phase_one_smoke(
    *,
    facade_request: JsonRequest,
    sync_request: JsonRequest,
) -> dict[str, object]:
    """Prove the seven approved backend steps without student or sync side effects."""

    calls: list[tuple[str, str]] = []

    def sync(
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        calls.append((method, path))
        return sync_request(method, path, token=token, payload=payload)

    login = _require_status(
        facade_request,
        "POST",
        "/v1/login",
        200,
        payload={"username": "teacher001", "password": "local-demo-teacher"},
    )
    teacher_token = _required_string(login, "token", "teacher login")

    sequence_materials = _require_status(
        sync,
        "GET",
        f"/v1/classroom/experiments/{SPACE_ID}/{SEQUENCE_PARENT_ID}/assessment-materials",
        200,
        token=teacher_token,
    )
    _assert_exact_issue_codes(sequence_materials, SEQUENCE_BLOCKER_CODES)

    linked_materials = _require_status(
        sync,
        "GET",
        f"/v1/classroom/experiments/{SPACE_ID}/{LINKED_PARENT_ID}/assessment-materials",
        200,
        token=teacher_token,
    )
    _assert_exact_issue_codes(linked_materials, LINKED_RAW_DIMENSION_CODES)

    authoring_scope: dict[str, object] = {
        "space_id": SPACE_ID,
        "parent_algorithm_id": LINKED_PARENT_ID,
    }
    created = _require_status(
        sync,
        "POST",
        "/v1/classroom/plan-authoring-sessions",
        200,
        token=teacher_token,
        payload=authoring_scope,
    )
    session_id = _required_string(created, "authoring_session_id", "authoring session")
    recovered = _require_status(
        sync,
        "GET",
        "/v1/classroom/plan-authoring-sessions/current?"
        f"space_id={SPACE_ID}&parent_algorithm_id={LINKED_PARENT_ID}",
        200,
        token=teacher_token,
    )
    repeated = _require_status(
        sync,
        "POST",
        "/v1/classroom/plan-authoring-sessions",
        200,
        token=teacher_token,
        payload=authoring_scope,
    )
    recovered_ids = {
        session_id,
        _required_string(recovered, "authoring_session_id", "recovered authoring session"),
        _required_string(repeated, "authoring_session_id", "repeated authoring session"),
    }
    if len(recovered_ids) != 1:
        raise PhaseOneSmokeFailure("authoring session recovery returned a different identifier")

    profile = _corrected_linked_profile(linked_materials)
    draft = _require_status(
        sync,
        "POST",
        "/v1/classroom/plans/drafts",
        201,
        token=teacher_token,
        payload={
            "authoring_session_id": session_id,
            "space_id": SPACE_ID,
            "parent_algorithm_id": LINKED_PARENT_ID,
            "title": "Linked-list phase-one publication",
            "profile": profile,
            "scheduled_start_at": "2030-01-01T08:00:00Z",
            "scheduled_end_at": "2030-01-01T08:30:00Z",
            "ai_policy": "prohibited",
        },
    )
    draft_id = _required_string(draft, "draft_id", "linked-list draft")
    gate = draft.get("publication_gate")
    if (
        not isinstance(gate, dict)
        or gate.get("status") != "ready"
        or gate.get("blocking_count") != 0
    ):
        raise PhaseOneSmokeFailure("corrected linked-list profile did not pass publication gate")
    saved = _require_status(
        sync,
        "GET",
        f"/v1/classroom/plans/drafts/{draft_id}",
        200,
        token=teacher_token,
    )
    if saved.get("profile") != profile or saved.get("authoring_session_id") != session_id:
        raise PhaseOneSmokeFailure("corrected linked-list profile was not saved exactly")

    published = _require_status(
        sync,
        "POST",
        f"/v1/classroom/plans/drafts/{draft_id}/publish",
        200,
        token=teacher_token,
    )
    plan_version_id = _required_string(published, "plan_version_id", "published plan")
    closed = _require_status(
        sync,
        "GET",
        f"/v1/classroom/plan-authoring-sessions/{session_id}/plan-suggestion",
        200,
        token=teacher_token,
    )
    if closed.get("authoring_session_id") != session_id or closed.get("status") != "published":
        raise PhaseOneSmokeFailure("published authoring session did not close")

    assignment_sync_calls = sum(path.endswith("/assignments/sync") for _, path in calls)
    student_run_calls = sum(
        "/student/" in path or "/plugin/" in path or "/runs" in path for _, path in calls
    )
    ai_calls = sum(
        method == "POST"
        and (path.endswith("/plan-suggestion") or path == "/v1/classroom/plan-suggestions")
        for method, path in calls
    )
    if assignment_sync_calls or student_run_calls or ai_calls:
        raise PhaseOneSmokeFailure("phase-one smoke crossed a forbidden side-effect boundary")

    return {
        "status": "ok",
        "verified_steps": 7,
        "authoring_session_id": session_id,
        "draft_id": draft_id,
        "plan_version_id": plan_version_id,
        "session_status": "published",
        "assignment_sync_calls": assignment_sync_calls,
        "student_run_calls": student_run_calls,
        "ai_calls": ai_calls,
    }


def render_safe_summary(result: Mapping[str, object]) -> str:
    """Render identifiers and counters only, never material or provider payloads."""

    return json.dumps(dict(result), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facade-base-url", default=FACADE_BASE_URL)
    parser.add_argument("--sync-base-url", default=SYNC_BASE_URL)
    args = parser.parse_args(argv)
    facade_client = LocalJsonClient(args.facade_base_url)
    sync_client = LocalJsonClient(args.sync_base_url)
    result = run_phase_one_smoke(
        facade_request=facade_client.request,
        sync_request=sync_client.request,
    )
    print(render_safe_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
