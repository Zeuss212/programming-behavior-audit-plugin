"""Exercise the local teacher-to-student classroom API contract.

The first invocation creates one isolated test classroom flow and stops after
the first evidence upload, while the monitor session is still collecting.  A
later ``--repeat-existing`` invocation intentionally does *not* create another
plan, assignment, monitor session, or evidence chunk.  It obtains a fresh
one-time launch ticket, restores the existing session, replays the same
idempotent evidence write, then submits one logical brief for the teacher.

The state file contains identifiers only.  Tickets and plugin access tokens
are intentionally kept in memory so this smoke runner cannot leave reusable
credentials on disk.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TEACHER_TOKEN = "teacher-token"
STUDENT_TOKEN = "student-token"
DEFAULT_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_STATE_FILE = Path("/tmp/classroom-contract-smoke-state.json")


class SmokeFailure(RuntimeError):
    """A contract assertion failed without exposing request credentials."""


class SmokeClient(Protocol):
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


class HttpSmokeClient:
    """Minimal HTTP adapter with deliberately credential-free failures."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def request_json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return self._send(method, path, body=body, headers=headers)

    def put_evidence(
        self,
        path: str,
        body: bytes,
        *,
        token: str,
        first_event_sequence: int,
        last_event_sequence: int,
    ) -> dict[str, object]:
        return self._send(
            "PUT",
            path,
            body=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/gzip",
                "X-First-Event-Sequence": str(first_event_sequence),
                "X-Last-Event-Sequence": str(last_event_sequence),
            },
        )

    def _send(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None,
        headers: dict[str, str],
    ) -> dict[str, object]:
        request = Request(f"{self._base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:  # nosec B310 - caller controls local base URL.
                raw = response.read()
        except HTTPError as error:
            raise SmokeFailure(f"{method} {path} returned HTTP {error.code}") from error
        except (OSError, URLError) as error:
            raise SmokeFailure(f"{method} {path} is unavailable") from error
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise SmokeFailure(f"{method} {path} returned invalid JSON") from error
        if not isinstance(decoded, dict):
            raise SmokeFailure(f"{method} {path} returned a non-object JSON payload")
        return decoded


def _require_string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise SmokeFailure(f"Response field {name} is missing or invalid")
    return value


def _require_positive_int(payload: dict[str, object], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SmokeFailure(f"Response field {name} is missing or invalid")
    return value


def _profile() -> dict[str, object]:
    """A compact Profile v2 snapshot accepted by the shared schema."""

    return {
        "schema_version": 2,
        "problem_id": "dictionary-basics",
        "title": "字典数据结构",
        "problem_context": {
            "statement": "实现一个字典读取函数。",
            "language": "python",
            "submission_contract": {"kind": "function", "entrypoint": "lookup"},
        },
        "knowledge_points": [
            {
                "id": "KP_DICT0001",
                "name": "字典读取",
                "description": "能根据键读取字典中的值。",
                "source": "teacher",
                "order": 0,
            }
        ],
        "assessment_tests": [
            {
                "id": "TEST_DICT0001",
                "name": "读取存在的键",
                "knowledge_point_ids": ["KP_DICT0001"],
                "kind": "function_call",
                "input": "{'data': {'name': 'Ada'}, 'key': 'name'}",
                "expected": "Ada",
                "enabled": True,
                "source": "teacher",
                "order": 0,
            }
        ],
        "confirmations": {"knowledge_points_hash": None, "tests_hash": None},
        "dimensions": [
            {
                "knowledge_point_id": "KP_DICT0001",
                "name": "字典读取",
                "question": "学生是否正确读取字典中的值？",
                "evidence_criteria": [
                    {
                        "id": "uses_lookup",
                        "direction": "support",
                        "statement": "代码使用键读取字典值。",
                    },
                    {
                        "id": "returns_literal",
                        "direction": "exclude",
                        "statement": "代码直接返回固定值。",
                    },
                ],
                "levels": [
                    {"code": "possible", "name": "可能掌握", "definition": "有一次正确读取。"},
                    {
                        "code": "clear",
                        "name": "明确掌握",
                        "definition": "通过运行验证读取逻辑。",
                    },
                ],
                "teaching_actions": {
                    "possible": "追问边界输入。",
                    "clear": "进入下一题。",
                    "not_observed": "安排补充练习。",
                },
                "analysis_config": {"mode": "llm_evidence", "minimum_observation": {"run_count": 1}},
            }
        ],
    }


def _evidence_body() -> bytes:
    payload = json.dumps(
        {"events": [{"sequence": 1, "type": "notebook_run", "source": "smoke"}]},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return gzip.compress(payload, mtime=0)


def _brief_payload() -> dict[str, object]:
    return {
        "summary": "学生完成字典读取函数并运行了验证。",
        "knowledge_points": [
            {
                "knowledge_point_id": "KP_DICT0001",
                "name": "字典读取",
                "status": "partial",
                "evidence_refs": ["chunk-1#event-1"],
                "demonstrated": "完成了字典读取并执行一次运行。",
                "gap": "尚未证明空键边界处理。",
                "teacher_suggestion": "追问空键输入的处理方式。",
            }
        ],
        "process_overview": ["完成一次运行验证。"],
        "issues": ["缺少空键测试。"],
        "ai_analysis_status": "not_requested",
        "reason": "student_manual",
    }


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read_collecting_state(path: Path) -> dict[str, object]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SmokeFailure("Repeat mode requires a readable first-run state file") from error
    if not isinstance(parsed, dict):
        raise SmokeFailure("Repeat state must be a JSON object")
    required = {
        "assignment_id",
        "evidence_id",
        "phase",
        "plan_id",
        "plan_version",
        "plan_version_id",
        "session_id",
    }
    if set(parsed) != required:
        raise SmokeFailure("Repeat state has an unexpected shape")
    if parsed.get("phase") != "collecting":
        raise SmokeFailure("Repeat mode requires a collecting monitor session")
    for name in required - {"phase", "plan_version"}:
        _require_string(parsed, name)
    _require_positive_int(parsed, "plan_version")
    return parsed


def _launch_session(
    client: SmokeClient,
    state: dict[str, object],
    *,
    expected_session_id: str | None,
) -> tuple[str, str]:
    assignment_id = _require_string(state, "assignment_id")
    ticket = client.request_json(
        "POST",
        f"/v1/classroom/student/assignments/{assignment_id}/launch-ticket",
        token=STUDENT_TOKEN,
    )
    registration = client.request_json(
        "POST",
        "/v1/classroom/plugin/sessions/register",
        payload={"ticket": _require_string(ticket, "ticket"), "plugin_instance_id": "local-contract-smoke"},
    )
    session_id = _require_string(registration, "session_id")
    if expected_session_id is not None and session_id != expected_session_id:
        raise SmokeFailure("Ticket registration did not restore the expected monitor session")
    if _require_string(registration, "assignment_id") != assignment_id:
        raise SmokeFailure("Registered session is bound to a different assignment")
    if _require_string(registration, "plan_id") != _require_string(state, "plan_id"):
        raise SmokeFailure("Registered session is bound to a different plan")
    if _require_positive_int(registration, "plan_version") != _require_positive_int(
        state, "plan_version"
    ):
        raise SmokeFailure("Registered session is bound to a different plan version")
    return session_id, _require_string(registration, "access_token")


def _upload_evidence(
    client: SmokeClient,
    state: dict[str, object],
    *,
    session_id: str,
    access_token: str,
) -> dict[str, object]:
    evidence = _evidence_body()
    receipt = client.put_evidence(
        f"/v1/classroom/plugin/sessions/{session_id}/evidence/1",
        evidence,
        token=access_token,
        first_event_sequence=1,
        last_event_sequence=1,
    )
    if _require_string(receipt, "session_id") != session_id:
        raise SmokeFailure("Evidence receipt is bound to a different session")
    if _require_positive_int(receipt, "sequence") != 1:
        raise SmokeFailure("Evidence receipt sequence is invalid")
    if _require_string(receipt, "content_sha256") != sha256(evidence).hexdigest():
        raise SmokeFailure("Evidence receipt hash is invalid")
    evidence_id = _require_string(receipt, "evidence_id")
    if "evidence_id" in state and evidence_id != _require_string(state, "evidence_id"):
        raise SmokeFailure("Replay created a second logical evidence chunk")
    return {**state, "evidence_id": evidence_id, "session_id": session_id}


def _submit_and_read(
    client: SmokeClient,
    state: dict[str, object],
    *,
    session_id: str,
    access_token: str,
) -> dict[str, object]:

    submission = client.request_json(
        "POST",
        f"/v1/classroom/plugin/sessions/{session_id}/submit",
        token=access_token,
        payload=_brief_payload(),
    )
    brief_id = _require_string(submission, "brief_id")
    revision = _require_positive_int(submission, "revision")
    if _require_string(submission, "session_id") != session_id:
        raise SmokeFailure("Brief receipt is bound to a different session")
    if submission.get("status") not in {"completed", "partial"}:
        raise SmokeFailure("Brief receipt has an invalid terminal status")
    teacher_brief = client.request_json(
        "GET",
        f"/v1/classroom/teacher/sessions/{session_id}/brief",
        token=TEACHER_TOKEN,
    )
    if _require_string(teacher_brief, "brief_id") != brief_id:
        raise SmokeFailure("Teacher received a different logical brief")
    if _require_string(teacher_brief, "session_id") != session_id:
        raise SmokeFailure("Teacher brief is bound to a different session")
    if _require_string(teacher_brief, "assignment_id") != _require_string(state, "assignment_id"):
        raise SmokeFailure("Teacher brief is bound to a different assignment")
    if _require_string(teacher_brief, "plan_id") != _require_string(state, "plan_id"):
        raise SmokeFailure("Teacher brief is bound to a different plan")
    if _require_positive_int(teacher_brief, "revision") != revision:
        raise SmokeFailure("Teacher brief revision differs from the plugin receipt")
    if teacher_brief.get("submission_reason") != "student_manual":
        raise SmokeFailure("Teacher brief did not preserve the manual student submission reason")
    return {
        **state,
        "brief_id": brief_id,
        "brief_revision": revision,
        "phase": "submitted",
        "submission_reason": "student_manual",
        "session_id": session_id,
    }


def run_smoke(
    client: SmokeClient,
    *,
    state_file: Path,
    now: datetime,
    repeat_existing: bool = False,
) -> dict[str, object]:
    """Run a new contract flow or replay the durable identity-only state."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if repeat_existing:
        state = _read_collecting_state(state_file)
        session_id, access_token = _launch_session(
            client,
            state,
            expected_session_id=_require_string(state, "session_id"),
        )
        resumed = _upload_evidence(
            client,
            state,
            session_id=session_id,
            access_token=access_token,
        )
        completed = _submit_and_read(
            client,
            resumed,
            session_id=session_id,
            access_token=access_token,
        )
        _write_state(state_file, completed)
        return completed

    clock = now.astimezone(UTC)
    draft = client.request_json(
        "POST",
        "/v1/classroom/plans/drafts",
        token=TEACHER_TOKEN,
        payload={
            "space_id": "course-001",
            "parent_algorithm_id": "parent-experiment-001",
            "title": "本地课堂契约冒烟",
            "profile": _profile(),
            "scheduled_start_at": clock.isoformat(),
            "scheduled_end_at": (clock + timedelta(minutes=45)).isoformat(),
            "ai_policy": "prohibited",
        },
    )
    draft_id = _require_string(draft, "draft_id")
    published = client.request_json(
        "POST",
        f"/v1/classroom/plans/drafts/{draft_id}/publish",
        token=TEACHER_TOKEN,
    )
    plan_version_id = _require_string(published, "plan_version_id")
    plan_id = _require_string(published, "plan_id")
    plan_version = _require_positive_int(published, "version")
    synchronized = client.request_json(
        "POST",
        f"/v1/classroom/plans/{plan_version_id}/assignments/sync",
        token=TEACHER_TOKEN,
    )
    assignments = synchronized.get("assignments")
    if not isinstance(assignments, list):
        raise SmokeFailure("Synchronized assignments are missing or invalid")
    target_assignments = [
        assignment
        for assignment in assignments
        if isinstance(assignment, dict) and assignment.get("student_id") == "student001"
    ]
    if len(target_assignments) != 1:
        raise SmokeFailure("Expected exactly one seeded student001 assignment")
    assignment = target_assignments[0]
    assignment_id = _require_string(assignment, "assignment_id")
    accepted = client.request_json(
        "POST",
        f"/v1/classroom/student/assignments/{assignment_id}/accept",
        token=STUDENT_TOKEN,
    )
    if _require_string(accepted, "assignment_id") != assignment_id or accepted.get("status") != "ready":
        raise SmokeFailure("Student did not accept the synchronized assignment")

    state: dict[str, object] = {
        "assignment_id": assignment_id,
        "phase": "collecting",
        "plan_id": plan_id,
        "plan_version": plan_version,
        "plan_version_id": plan_version_id,
    }
    session_id, access_token = _launch_session(client, state, expected_session_id=None)
    state["session_id"] = session_id
    collecting = _upload_evidence(
        client,
        state,
        session_id=session_id,
        access_token=access_token,
    )
    _write_state(state_file, collecting)
    return collecting


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CLASSROOM_SYNC_BASE_URL", DEFAULT_BASE_URL),
        help="Local sync API base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(os.environ.get("CLASSROOM_SMOKE_STATE_FILE", DEFAULT_STATE_FILE)),
        help="Identifier-only state file used by --repeat-existing",
    )
    parser.add_argument(
        "--repeat-existing",
        action="store_true",
        help="Restore and replay the first run instead of creating another class flow",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_smoke(
        HttpSmokeClient(args.base_url),
        state_file=args.state_file,
        now=datetime.now(UTC),
        repeat_existing=args.repeat_existing,
    )
    output: dict[str, object] = {
        "assignment_id": result["assignment_id"],
        "phase": result["phase"],
        "session_id": result["session_id"],
        "status": "ok",
    }
    if result["phase"] == "submitted":
        output["brief_id"] = result["brief_id"]
        output["brief_revision"] = result["brief_revision"]
    print(
        json.dumps(
            output,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
