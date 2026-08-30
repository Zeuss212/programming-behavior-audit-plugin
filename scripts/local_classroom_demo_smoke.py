"""Run the loopback-only local classroom demo smoke sequence."""

from __future__ import annotations

import argparse
import json
from time import monotonic, sleep
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from classroom_contract_smoke import HttpSmokeClient, run_smoke


FACADE_BASE_URL = "http://127.0.0.1:18082"
SYNC_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_STATE_FILE = Path("/private/tmp/classroom-local-demo-smoke-state.json")
SAFE_AI_ANALYSIS_STATUSES = frozenset({"not_requested", "pending", "ready", "unavailable"})
EVIDENCE_REQUIRED_AI_STATUSES = frozenset({"observed", "partial", "teacher_review_required"})
SAFE_KNOWLEDGE_POINT_STATUSES = EVIDENCE_REQUIRED_AI_STATUSES | {"not_observed"}
SENSITIVE_MONITORING_FIELDS = frozenset({"api_key", "access_token", "object_key", "evidence_refs"})
AI_POLL_TIMEOUT_SECONDS = 180
AI_POLL_INTERVAL_SECONDS = 1


class LocalDemoSmokeFailure(RuntimeError):
    """A local façade or service preflight did not satisfy the demo contract."""


def _request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(f"{base_url.rstrip('/')}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:  # nosec B310 - caller uses the fixed local URLs.
            status = response.status
            raw = response.read()
    except HTTPError as error:
        status = error.code
        raw = error.read()
    except (OSError, URLError) as error:
        raise LocalDemoSmokeFailure(f"local endpoint unavailable: {path}") from error
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise LocalDemoSmokeFailure(f"local endpoint returned invalid JSON: {path}") from error
    if not isinstance(decoded, dict):
        raise LocalDemoSmokeFailure(f"local endpoint returned non-object JSON: {path}")
    return status, decoded


def _require_status(
    base_url: str,
    method: str,
    path: str,
    expected_status: int,
    *,
    token: str | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    status, body = _request_json(base_url, method, path, token=token, payload=payload)
    if status != expected_status:
        raise LocalDemoSmokeFailure(f"local endpoint returned unexpected status: {path}")
    return body


def _require_login(base_url: str, username: str, password: str, expected_token: str) -> None:
    body = _require_status(
        base_url,
        "POST",
        "/v1/login",
        200,
        payload={"username": username, "password": password},
    )
    if body.get("token") != expected_token:
        raise LocalDemoSmokeFailure(f"{username} login returned an unexpected token")


def _require_safe_monitoring_briefs(monitoring: dict[str, object]) -> None:
    """Reject teacher monitoring DTOs that expose unsupported AI state or private data."""

    encoded = json.dumps(monitoring, ensure_ascii=False, sort_keys=True)
    if any(f'"{field}"' in encoded for field in SENSITIVE_MONITORING_FIELDS):
        raise LocalDemoSmokeFailure("monitoring response exposed sensitive fields")
    students = monitoring.get("students")
    if not isinstance(students, list):
        raise LocalDemoSmokeFailure("monitoring response has invalid students")
    for student in students:
        if not isinstance(student, dict):
            raise LocalDemoSmokeFailure("monitoring response has invalid student")
        brief = student.get("brief")
        if brief is None:
            continue
        if not isinstance(brief, dict) or brief.get("ai_analysis_status") not in SAFE_AI_ANALYSIS_STATUSES:
            raise LocalDemoSmokeFailure("monitoring response has invalid AI analysis status")


def _require_ready_plan_suggestion(sync_base_url: str, teacher_token: str) -> None:
    """Queue and poll one bounded plan suggestion without printing model output."""

    queued = _require_status(
        sync_base_url,
        "POST",
        "/v1/classroom/plan-suggestions",
        202,
        token=teacher_token,
        payload={
            "space_id": "course-001",
            "parent_algorithm_id": "parent-experiment-001",
            "title": "AI 完整课堂闭环验收",
            "statement": "实现 analyze_scores，处理空列表、成绩范围和统计结果返回。",
        },
    )
    job_id = queued.get("job_id")
    if not isinstance(job_id, str) or not job_id or queued.get("status") != "pending":
        raise LocalDemoSmokeFailure("AI plan suggestion was not queued safely")
    deadline = monotonic() + AI_POLL_TIMEOUT_SECONDS
    while monotonic() < deadline:
        status = _require_status(
            sync_base_url,
            "GET",
            f"/v1/classroom/plan-suggestions/{job_id}",
            200,
            token=teacher_token,
        )
        if status.get("status") == "ready":
            suggestion = status.get("suggestion")
            if not isinstance(suggestion, dict) or not isinstance(suggestion.get("knowledge_points"), list):
                raise LocalDemoSmokeFailure("AI plan suggestion returned an invalid safe result")
            if not suggestion["knowledge_points"]:
                raise LocalDemoSmokeFailure("AI plan suggestion returned no knowledge points")
            encoded = json.dumps(suggestion, ensure_ascii=False, sort_keys=True)
            if any(f'"{field}"' in encoded for field in SENSITIVE_MONITORING_FIELDS):
                raise LocalDemoSmokeFailure("AI plan suggestion exposed sensitive fields")
            return
        if status.get("status") == "failed":
            raise LocalDemoSmokeFailure("AI plan suggestion failed")
        if status.get("status") != "pending":
            raise LocalDemoSmokeFailure("AI plan suggestion returned an unknown task status")
        sleep(AI_POLL_INTERVAL_SECONDS)
    raise LocalDemoSmokeFailure("AI plan suggestion did not finish before the local test deadline")


def _require_ready_ai_brief(
    monitoring_reader: Callable[[str], dict[str, object]],
    brief_reader: Callable[[str], dict[str, object]],
    *,
    plan_version_id: str,
    session_id: str,
) -> None:
    """Wait for one student brief whose analysis was completed by the local worker."""

    deadline = monotonic() + AI_POLL_TIMEOUT_SECONDS
    while monotonic() < deadline:
        monitoring = monitoring_reader(plan_version_id)
        _require_safe_monitoring_briefs(monitoring)
        students = monitoring["students"]
        if isinstance(students, list) and any(
            isinstance(student, dict)
            and isinstance(student.get("brief"), dict)
            and student["brief"].get("ai_analysis_status") == "ready"
            for student in students
        ):
            brief = brief_reader(session_id)
            analysis = brief.get("ai_analysis")
            if brief.get("ai_analysis_status") != "ready" or not isinstance(analysis, dict):
                raise LocalDemoSmokeFailure("student brief lacks a usable AI teaching analysis")
            _require_usable_ai_analysis(analysis)
            return
        sleep(AI_POLL_INTERVAL_SECONDS)
    raise LocalDemoSmokeFailure("student AI analysis did not finish before the local test deadline")


def _require_usable_ai_analysis(analysis: dict[str, object]) -> None:
    """Require teacher-facing conclusions to be bounded and evidence-backed."""

    rows = analysis.get("knowledge_point_analyses")
    teacher_note = analysis.get("teacher_note")
    if not isinstance(rows, list) or not rows or not isinstance(teacher_note, str) or not teacher_note.strip():
        raise LocalDemoSmokeFailure("student brief lacks a usable AI teaching analysis")
    seen_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise LocalDemoSmokeFailure("student AI analysis has invalid knowledge point rows")
        point_id = row.get("knowledge_point_id")
        status = row.get("status")
        evidence_ids = row.get("evidence_event_ids")
        suggestion = row.get("teaching_suggestion")
        if not isinstance(point_id, str) or not point_id or point_id in seen_ids:
            raise LocalDemoSmokeFailure("student AI analysis has invalid knowledge point identifiers")
        seen_ids.add(point_id)
        if status not in SAFE_KNOWLEDGE_POINT_STATUSES:
            raise LocalDemoSmokeFailure("student AI analysis has an invalid teaching status")
        if not isinstance(evidence_ids, list) or any(
            not isinstance(event_id, str) or not event_id for event_id in evidence_ids
        ):
            raise LocalDemoSmokeFailure("student AI analysis has invalid evidence references")
        if status in EVIDENCE_REQUIRED_AI_STATUSES and not evidence_ids:
            raise LocalDemoSmokeFailure("student AI analysis is not evidence-backed")
        if status == "not_observed" and evidence_ids:
            raise LocalDemoSmokeFailure("student AI analysis cites evidence for an unobserved conclusion")
        if not isinstance(suggestion, str) or not suggestion.strip():
            raise LocalDemoSmokeFailure("student AI analysis lacks an actionable teaching suggestion")
    encoded = json.dumps(analysis, ensure_ascii=False, sort_keys=True)
    if any(f'"{field}"' in encoded for field in SENSITIVE_MONITORING_FIELDS):
        raise LocalDemoSmokeFailure("student AI analysis exposed sensitive fields")


def run_local_demo_smoke(
    *,
    facade_base_url: str = FACADE_BASE_URL,
    sync_base_url: str = SYNC_BASE_URL,
    state_file: Path = DEFAULT_STATE_FILE,
    contract_runner: Callable[..., dict[str, object]] = run_smoke,
    expected_teacher_token: str = "teacher-token",
    monitoring_reader: Callable[[str], dict[str, object]] | None = None,
    require_ai: bool = False,
    ai_suggestion_runner: Callable[[str, str], None] | None = None,
    brief_reader: Callable[[str], dict[str, object]] | None = None,
) -> dict[str, object]:
    """Check the façade boundary then reuse the existing teacher/student contract flow."""

    health = _require_status(facade_base_url, "GET", "/health/live", 200)
    if health.get("status") != "live":
        raise LocalDemoSmokeFailure("local façade health payload is invalid")
    _require_login(facade_base_url, "1", "1", expected_teacher_token)
    _require_login(facade_base_url, "2", "2", "student001-token")
    denied = _require_status(
        facade_base_url,
        "GET",
        "/v1/spaces/course-001/algorithm_development",
        403,
        token="student002-token",
    )
    if denied.get("detail") != "demo_course_access_denied":
        raise LocalDemoSmokeFailure("cross-course student was not denied by local façade")
    _require_status(sync_base_url, "GET", "/health/ready", 200)
    if require_ai:
        (ai_suggestion_runner or _require_ready_plan_suggestion)(sync_base_url, expected_teacher_token)

    try:
        runner_args = {"ai_policy": "allowed"} if require_ai else {}
        contract_runner(
            HttpSmokeClient(sync_base_url),
            state_file=state_file,
            now=datetime.now(UTC),
            repeat_existing=False,
            **runner_args,
        )
        result = contract_runner(
            HttpSmokeClient(sync_base_url),
            state_file=state_file,
            now=datetime.now(UTC),
            repeat_existing=True,
            **runner_args,
        )
    finally:
        state_file.unlink(missing_ok=True)
    if result.get("phase") != "submitted":
        raise LocalDemoSmokeFailure("local classroom contract did not submit a brief")
    plan_version_id = result.get("plan_version_id")
    if not isinstance(plan_version_id, str) or not plan_version_id:
        raise LocalDemoSmokeFailure("local classroom contract did not return a plan version")
    reader = monitoring_reader or (
        lambda plan_version_id: _require_status(
            sync_base_url,
            "GET",
            f"/v1/classroom/teacher/plans/{plan_version_id}/monitoring",
            200,
            token=expected_teacher_token,
        )
    )
    if require_ai:
        session_id = result.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise LocalDemoSmokeFailure("local classroom contract did not return a session")
        read_brief = brief_reader or (
            lambda session_id: _require_status(
                sync_base_url,
                "GET",
                f"/v1/classroom/teacher/sessions/{session_id}/brief",
                200,
                token=expected_teacher_token,
            )
        )
        _require_ready_ai_brief(
            reader,
            read_brief,
            plan_version_id=plan_version_id,
            session_id=session_id,
        )
    else:
        _require_safe_monitoring_briefs(reader(plan_version_id))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-ai",
        action="store_true",
        help="Require queued AI plan suggestions and a ready student AI analysis.",
    )
    args = parser.parse_args()
    result = run_local_demo_smoke(require_ai=args.require_ai)
    print(
        json.dumps(
            {"status": "ok", "phase": result["phase"], "ai_verified": args.require_ai},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
