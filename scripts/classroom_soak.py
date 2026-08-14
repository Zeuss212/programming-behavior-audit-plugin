"""Run the local accelerated classroom concurrency precheck.

``--accelerated`` addresses the isolated local Compose API only.  It creates a
single test plan, exercises the complete student lifecycle concurrently, and
prints a credential-free JSON report.  The mode intentionally marks
``acceptance_valid`` false: it does not keep a real class open for 45 minutes
and does not exercise the deployed teacher/student frontends.

The production command shape accepts exactly 45 minutes and no shorter value,
but it stops before sending requests. A real classroom run requires a separate
deployment authorization, a recorded target, and human acceptance witnesses.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
from time import perf_counter
from typing import Protocol

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from classroom_contract_smoke import (
    DEFAULT_BASE_URL,
    HttpSmokeClient,
    _brief_payload,
    _profile,
    _require_positive_int,
    _require_string,
)

MAX_ACCELERATED_STUDENTS = 100


class SoakConfigurationError(ValueError):
    """The requested soak mode is unsafe or cannot support valid acceptance."""


class SoakFailure(RuntimeError):
    """One accelerated student lifecycle did not meet its contract assertions."""


class SoakClient(Protocol):
    def request_json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]: ...

    def put_evidence(
        self,
        path: str,
        body: bytes,
        *,
        token: str,
        first_event_sequence: int,
        last_event_sequence: int,
    ) -> dict[str, object]: ...


def validate_request(*, accelerated: bool, duration_minutes: int | None, base_url: str) -> str:
    """Validate mode boundaries without making requests or accepting a remote target."""

    if accelerated:
        if duration_minutes is not None:
            raise SoakConfigurationError("Accelerated soak cannot be combined with a duration.")
        if base_url != DEFAULT_BASE_URL:
            raise SoakConfigurationError(
                "Accelerated soak only targets the local http://127.0.0.1:18080 API."
            )
        return "accelerated"
    if duration_minutes != 45:
        raise SoakConfigurationError("A production classroom acceptance run must last exactly 45 minutes.")
    return "real"


def _student_token(student_id: str) -> str:
    return f"{student_id}-token"


def _evidence_body(student_id: str) -> bytes:
    payload = json.dumps(
        {
            "events": [
                {"sequence": 1, "type": "notebook_run", "source": f"accelerated:{student_id}"}
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return gzip.compress(payload, mtime=0)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise SoakFailure("No heartbeat latency observations were recorded.")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 3)


def _create_assignments(client: SoakClient, *, students: int) -> tuple[dict[str, str], ...]:
    now = datetime.now(UTC)
    draft = client.request_json(
        "POST",
        "/v1/classroom/plans/drafts",
        token="teacher-token",
        payload={
            "space_id": "course-001",
            "parent_algorithm_id": "parent-experiment-001",
            "title": "本地加速课堂并发预检",
            "profile": _profile(),
            "scheduled_start_at": now.isoformat(),
            "scheduled_end_at": (now + timedelta(minutes=45)).isoformat(),
            "ai_policy": "prohibited",
        },
    )
    published = client.request_json(
        "POST",
        f"/v1/classroom/plans/drafts/{_require_string(draft, 'draft_id')}/publish",
        token="teacher-token",
    )
    synchronized = client.request_json(
        "POST",
        f"/v1/classroom/plans/{_require_string(published, 'plan_version_id')}/assignments/sync",
        token="teacher-token",
    )
    raw_assignments = synchronized.get("assignments")
    if not isinstance(raw_assignments, list):
        raise SoakFailure("Accelerated soak did not receive an assignment list.")
    assignments: list[dict[str, str]] = []
    for raw_assignment in raw_assignments:
        if not isinstance(raw_assignment, dict):
            continue
        assignment_id = raw_assignment.get("assignment_id")
        student_id = raw_assignment.get("student_id")
        if isinstance(assignment_id, str) and isinstance(student_id, str):
            assignments.append({"assignment_id": assignment_id, "student_id": student_id})
    if len(assignments) != students:
        raise SoakFailure(
            f"Expected {students} local mock assignments, received {len(assignments)}. "
            "Start Compose with CLASSROOM_MOCK_STUDENT_COUNT set to the requested value."
        )
    return tuple(sorted(assignments, key=lambda item: item["student_id"]))


def _run_student(client: SoakClient, assignment: dict[str, str]) -> dict[str, object]:
    student_id = assignment["student_id"]
    assignment_id = assignment["assignment_id"]
    token = _student_token(student_id)
    accepted = client.request_json(
        "POST",
        f"/v1/classroom/student/assignments/{assignment_id}/accept",
        token=token,
    )
    if accepted.get("status") != "ready":
        raise SoakFailure("Student assignment was not ready after acceptance.")
    ticket = client.request_json(
        "POST",
        f"/v1/classroom/student/assignments/{assignment_id}/launch-ticket",
        token=token,
    )
    registration = client.request_json(
        "POST",
        "/v1/classroom/plugin/sessions/register",
        payload={
            "ticket": _require_string(ticket, "ticket"),
            "plugin_instance_id": f"accelerated-{student_id}",
        },
    )
    session_id = _require_string(registration, "session_id")
    plugin_token = _require_string(registration, "access_token")

    heartbeat_started = perf_counter()
    heartbeat = client.request_json(
        "POST",
        f"/v1/classroom/plugin/sessions/{session_id}/heartbeat",
        token=plugin_token,
    )
    heartbeat_latency_ms = (perf_counter() - heartbeat_started) * 1000
    if heartbeat.get("status") != "collecting":
        raise SoakFailure("Student heartbeat did not keep the monitor session collecting.")
    evidence = client.put_evidence(
        f"/v1/classroom/plugin/sessions/{session_id}/evidence/1",
        _evidence_body(student_id),
        token=plugin_token,
        first_event_sequence=1,
        last_event_sequence=1,
    )
    if _require_positive_int(evidence, "sequence") != 1:
        raise SoakFailure("Evidence receipt did not preserve sequence 1.")
    submission = client.request_json(
        "POST",
        f"/v1/classroom/plugin/sessions/{session_id}/submit",
        token=plugin_token,
        payload=_brief_payload(),
    )
    status = submission.get("status")
    if status not in {"completed", "partial"}:
        raise SoakFailure("Student brief did not reach a terminal status.")
    return {
        "heartbeat_latency_ms": heartbeat_latency_ms,
        "revision": _require_positive_int(submission, "revision"),
        "session_id": session_id,
        "status": status,
    }


def run_accelerated(
    base_url: str,
    *,
    students: int,
    client_factory: Callable[[str], SoakClient] = HttpSmokeClient,
) -> dict[str, object]:
    """Run a local concurrent lifecycle precheck without claiming classroom acceptance."""

    validate_request(accelerated=True, duration_minutes=None, base_url=base_url)
    if not 1 <= students <= MAX_ACCELERATED_STUDENTS:
        raise SoakConfigurationError(
            f"Accelerated soak students must be between 1 and {MAX_ACCELERATED_STUDENTS}."
        )
    teacher_client = client_factory(base_url)
    assignments = _create_assignments(teacher_client, students=students)
    observations: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=students, thread_name_prefix="classroom-soak") as executor:
        futures = [executor.submit(_run_student, client_factory(base_url), assignment) for assignment in assignments]
        for future in as_completed(futures):
            observations.append(future.result())
    latencies = [float(observation["heartbeat_latency_ms"]) for observation in observations]
    statuses = [str(observation["status"]) for observation in observations]
    revisions = [int(observation["revision"]) for observation in observations]
    return {
        "schema_version": 1,
        "mode": "accelerated",
        "acceptance_valid": False,
        "students": students,
        "heartbeat_latency_ms": {
            "count": len(latencies),
            "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
        },
        "evidence_chunks": {
            "attempted": students,
            "accepted_receipts": students,
            "stored": "not_observed",
        },
        "duplicates": {
            "evidence": "not_observed",
            "briefs": "not_observed",
        },
        "missing_ranges": "not_observed",
        "outbox_peak": "not_observed",
        "final_status": {"completed": statuses.count("completed"), "partial": statuses.count("partial")},
        "brief_revision": {"minimum": min(revisions), "maximum": max(revisions)},
        "limitations": [
            "Accelerated mode does not keep a real class open for 45 minutes.",
            "Evidence receipt is observed, but object counts, duplicate chunks, and missing ranges are not read.",
            "This direct API harness does not exercise the Jupyter plugin outbox.",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--accelerated", action="store_true", help="Run local concurrent development precheck.")
    mode.add_argument("--duration-minutes", type=int, help="Validate a real classroom duration requirement.")
    parser.add_argument("--students", type=int, default=30, help="Local mock student count (default: %(default)s).")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CLASSROOM_SYNC_BASE_URL", DEFAULT_BASE_URL),
        help="Accelerated mode accepts only %(default)s.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mode = validate_request(
        accelerated=args.accelerated,
        duration_minutes=args.duration_minutes,
        base_url=args.base_url,
    )
    if mode == "real":
        raise SystemExit(
            "Real 45-minute acceptance requires deployment authorization and an assigned audit team; "
            "use docs/verification/classroom-acceptance-template.md."
        )
    print(json.dumps(run_accelerated(args.base_url, students=args.students), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
