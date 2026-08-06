# Local Session Log Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a left-sidebar local session log entry, a unified main-area viewer for source snapshots, behavior events, AI analysis and teacher reviews, and a local normalized training record export.

**Architecture:** Keep the existing session directory, raw event stream, immutable analysis result and append-only review history as authoritative. Add a read-only Python projection service and three authenticated endpoints, then consume them through typed TypeScript services in one reusable JupyterLab main-area widget. The exported `training_record.json` is an atomic, reproducible derived artifact and never becomes an authoritative input.

**Tech Stack:** Python 3.10+, Jupyter Server 2.x, Tornado, JSON Schema 2020-12, TypeScript, JupyterLab 4 Lumino widgets, Jest/jsdom, pytest-jupyter, CSS.

## Global Constraints

- This is a local single-user Pilot; the existing monitoring consent also covers local log retention.
- Do not change the AI inference rules, candidate selection, knowledge-point decisions or assessment-test execution behavior.
- Do not migrate or rewrite `raw_events.jsonl`, `result.json`, profile snapshots or review history.
- Source code shown for a historical session must come from captured event snapshots, never from re-reading the current Notebook after the session.
- Never return or export API keys, authentication tokens, prompt bodies, provider raw responses, sensitive request headers or absolute local paths.
- Student source, comments, output and error text are untrusted text and must never be rendered through `innerHTML`.
- New directories use `0700` and new files use `0600` where the platform supports POSIX permissions.
- All derived-record writes use same-directory temporary files, `fsync` and atomic replacement through the existing canonical JSON writer.
- Keep `latest-analysis` and “高级数据” working as compatibility-only paths.
- Do not read, copy or commit real `log/` or configured data-root contents; all tests use temporary synthetic sessions.
- The repository has no Git metadata. Do not initialize Git. Each task ends with a recorded verification checkpoint instead of a commit.

---

## File Map

### Backend files

- Create `myextension/session_log_service.py`: session summaries, cursor handling, code-snapshot projection, AI/review aggregation, integrity warnings and training-record construction.
- Modify `myextension/session_store.py`: safe session enumeration and optional/derived artifact access without exposing private paths.
- Create `myextension/api_schemas/session-log-list-v1.json`: strict list response contract.
- Create `myextension/api_schemas/session-log-detail-v1.json`: strict aggregated detail contract.
- Create `myextension/api_schemas/training-record-v1.json`: persisted training-record contract.
- Create `myextension/api_schemas/training-record-response-v1.json`: export endpoint response contract.
- Modify `myextension/routes.py`: authenticated list, detail and export handlers.
- Create `myextension/tests/test_session_log_service.py`: projection, integrity and export tests.
- Modify `myextension/tests/test_session_store.py`: storage boundary tests.
- Modify `myextension/tests/test_pilot_api.py`: endpoint, authentication, schema and OpenAPI tests.
- Modify `docs/openapi/myextension-v1.yaml`: public endpoint and component contracts.

### Frontend files

- Create `src/models/sessionLog.ts`: shared list/detail/export types.
- Create `src/services/sessionLogApi.ts`: typed request wrappers.
- Create `src/ui/sessionLogViewer.ts`: reusable main-area viewer and evidence navigation.
- Create `src/ui/sessionLogCommand.ts`: one-widget command registration and activation.
- Modify `src/ui/behaviorAnalysisSidebar.ts`: “本地日志” list, refresh, open and export controls.
- Modify `src/index.ts`: command wiring and production dependencies.
- Create `src/__tests__/sessionLogApi.spec.ts`: endpoint encoding and HTTP method tests.
- Create `src/__tests__/sessionLogViewer.spec.ts`: rendering, security, evidence navigation and state tests.
- Modify `src/__tests__/behaviorAnalysisSidebar.spec.ts`: sidebar entry and lifecycle tests.
- Modify `src/__tests__/myextension.spec.ts`: single-widget command registration test.
- Modify `style/base.css`: responsive viewer and compact sidebar log styles.

### Documentation

- Modify `README.md`, `项目说明.md`, and `启动说明.md`: user flow, artifact meaning, export location semantics and privacy boundary.

---

### Task 1: Safe session discovery and derived-artifact storage

**Files:**

- Modify: `myextension/session_store.py`
- Modify: `myextension/tests/test_session_store.py`

**Interfaces:**

- Produces: `SessionStore.list_session_ids() -> list[str]`
- Produces: `SessionStore.read_events_if_present(session_id: str) -> list[dict[str, object]] | None`
- Produces: `SessionStore.read_training_record(session_id: str) -> dict[str, object] | None`
- Produces: `SessionStore.write_training_record(session_id: str, record: Mapping[str, object]) -> None`
- Consumes later: `SessionLogService` in Task 2 and Task 3.

- [ ] **Step 1: Add failing session enumeration and artifact tests**

Append tests using the existing `started_session()` helper:

```python
def test_list_session_ids_returns_only_valid_private_session_directories(
    tmp_path: Path,
):
    store, first = started_session(
        tmp_path,
        started_at="2026-07-30T08:00:00+08:00",
    )
    _, second = started_session(
        tmp_path,
        started_at="2026-07-30T09:00:00+08:00",
    )

    assert set(store.list_session_ids()) == {
        str(first["session_id"]),
        str(second["session_id"]),
    }


def test_list_session_ids_rejects_symlink_entries(
    tmp_path: Path,
):
    store, _ = started_session(tmp_path)
    target = tmp_path / "outside"
    target.mkdir()
    (tmp_path / "sessions" / "malicious").symlink_to(
        target,
        target_is_directory=True,
    )

    with pytest.raises(SessionIntegrityError):
        store.list_session_ids()


def test_training_record_round_trip_uses_private_file(
    tmp_path: Path,
):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    record = {
        "schema_version": 1,
        "session": {"session_id": session_id},
        "export": {"content_hash": "a" * 64},
    }

    assert store.read_training_record(session_id) is None
    store.write_training_record(session_id, record)

    path = tmp_path / "sessions" / session_id / "training_record.json"
    assert store.read_training_record(session_id) == record
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
```

- [ ] **Step 2: Run the focused tests and verify the new API is absent**

Run:

```bash
.venv/bin/python -m pytest \
  myextension/tests/test_session_store.py \
  -k "list_session_ids or training_record_round_trip" -q
```

Expected: failures report missing `list_session_ids`, `read_training_record` and
`write_training_record`.

- [ ] **Step 3: Implement the minimal safe storage methods**

Add these public methods to `SessionStore`, reusing `_assert_safe_path`,
`_lock_for`, `_read_json` and `_write_json`:

```python
def list_session_ids(self) -> list[str]:
    root = self._assert_safe_path(self._sessions_root)
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise SessionIntegrityError("Session root is not a safe directory.")
    session_ids: list[str] = []
    for entry in root.iterdir():
        safe_entry = self._assert_safe_path(entry)
        if safe_entry.is_symlink() or not safe_entry.is_dir():
            raise SessionIntegrityError(
                "Session root contains an unsafe entry."
            )
        try:
            canonical = _canonical_uuid(entry.name, field="session_id")
        except InvalidSessionIdError as error:
            raise SessionIntegrityError(
                "Session root contains an invalid session directory."
            ) from error
        session_ids.append(canonical)
    return sorted(session_ids)


def read_events_if_present(
    self,
    session_id: str,
) -> list[dict[str, object]] | None:
    session_dir = self._session_dir(session_id)
    with self._lock_for(session_id):
        self.read(session_id)
        path = self._assert_safe_path(session_dir / "raw_events.jsonl")
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise SessionIntegrityError(
                "Canonical raw event stream is not a safe file."
            )
        return self._read_events_locked(session_id, session_dir)


def read_training_record(
    self,
    session_id: str,
) -> dict[str, object] | None:
    session_dir = self._session_dir(session_id)
    with self._lock_for(session_id):
        self.read(session_id)
        path = self._assert_safe_path(session_dir / "training_record.json")
        if not path.exists():
            return None
        return self._read_json(path)


def write_training_record(
    self,
    session_id: str,
    record: Mapping[str, object],
) -> None:
    session_dir = self._session_dir(session_id)
    with self._lock_for(session_id):
        self.read(session_id)
        self._write_json(
            self._assert_safe_path(session_dir / "training_record.json"),
            record,
        )
```

`_canonical_uuid(..., field="session_id")` raises
`InvalidSessionIdError`, so keep the exact catch shown above and wrap it as
`SessionIntegrityError`.

- [ ] **Step 4: Add missing-event and symlink tests**

```python
def test_read_events_if_present_distinguishes_missing_from_unsafe(
    tmp_path: Path,
):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    raw_path = tmp_path / "sessions" / session_id / "raw_events.jsonl"
    raw_path.unlink()
    assert store.read_events_if_present(session_id) is None

    target = tmp_path / "outside-events.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    raw_path.symlink_to(target)
    with pytest.raises(SessionIntegrityError):
        store.read_events_if_present(session_id)
```

- [ ] **Step 5: Run the storage test file**

Run:

```bash
.venv/bin/python -m pytest myextension/tests/test_session_store.py -q
```

Expected: all `test_session_store.py` tests pass.

- [ ] **Step 6: Record Task 1 checkpoint**

Record the command and pass count in the implementation handoff. Do not create
a Git commit because this project has no Git metadata.

---

### Task 2: Session summary, code snapshot and event-detail projection

**Files:**

- Create: `myextension/session_log_service.py`
- Create: `myextension/tests/test_session_log_service.py`

**Interfaces:**

- Consumes: the four `SessionStore` methods from Task 1.
- Produces: `SessionLogService.list_sessions(limit: int = 20, cursor: str | None = None) -> dict[str, object]`
- Produces: `SessionLogService.get_detail(session_id: str) -> dict[str, object]`
- Produces: `SessionLogIntegrityError`.
- Produces detail keys: `schema_version`, `session`, `problem_profile`,
  `code_snapshots`, `behavior_events`, `ai_analysis`, `teacher_reviews`,
  `integrity`, `training_record`.

- [ ] **Step 1: Create synthetic service fixtures and failing list tests**

Create `myextension/tests/test_session_log_service.py` with a helper that uses
the existing session/profile fixtures:

```python
from pathlib import Path

from myextension.analysis_job_store import AnalysisJobStore
from myextension.review_store import ReviewStore
from myextension.session_log_service import SessionLogService
from myextension.tests.test_session_store import (
    batch,
    started_session,
)


def service_for(store):
    return SessionLogService(
        root=Path(store.root),
        session_store=store,
        job_store=AnalysisJobStore(Path(store.root)),
        review_store=ReviewStore(Path(store.root)),
    )


def test_list_sessions_is_newest_first_and_uses_profile_snapshot(tmp_path):
    store, old = started_session(
        tmp_path,
        started_at="2026-07-30T08:00:00+08:00",
    )
    _, new = started_session(
        tmp_path,
        started_at="2026-07-30T09:00:00+08:00",
    )

    result = service_for(store).list_sessions(limit=20)

    assert [row["session_id"] for row in result["sessions"]] == [
        new["session_id"],
        old["session_id"],
    ]
    assert result["sessions"][0]["problem_title"] == "平均数调试"
    assert result["next_cursor"] is None
```

Add this pagination test:

```python
def test_list_sessions_uses_opaque_cursor_without_duplicates(tmp_path):
    store = None
    created: list[str] = []
    for minute in range(21):
        current_store, session = started_session(
            tmp_path,
            started_at=f"2026-07-30T08:{minute:02d}:00+08:00",
        )
        store = current_store
        created.append(str(session["session_id"]))
    assert store is not None
    service = service_for(store)

    first = service.list_sessions(limit=20)
    second = service.list_sessions(
        limit=20,
        cursor=str(first["next_cursor"]),
    )

    first_ids = [row["session_id"] for row in first["sessions"]]
    second_ids = [row["session_id"] for row in second["sessions"]]
    assert isinstance(first["next_cursor"], str)
    assert len(first_ids) == 20
    assert len(second_ids) == 1
    assert set(first_ids).isdisjoint(second_ids)
    assert set(first_ids + second_ids) == set(created)
    assert second["next_cursor"] is None
```

- [ ] **Step 2: Run the service test and verify import failure**

Run:

```bash
.venv/bin/python -m pytest \
  myextension/tests/test_session_log_service.py \
  -k "list_sessions" -q
```

Expected: collection fails because `myextension.session_log_service` does not
exist.

- [ ] **Step 3: Implement summary projection and opaque cursor helpers**

Create `SessionLogService` with strict constructor dependencies and these
helpers:

```python
class SessionLogIntegrityError(RuntimeError):
    pass


class SessionLogService:
    MAX_PAGE_SIZE = 50

    def __init__(
        self,
        *,
        root: Path,
        session_store,
        job_store,
        review_store,
    ) -> None:
        self.root = Path(root)
        self.session_store = session_store
        self.job_store = job_store
        self.review_store = review_store

    def list_sessions(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, object]:
        if isinstance(limit, bool) or not 1 <= limit <= self.MAX_PAGE_SIZE:
            raise ValueError("limit must be between 1 and 50.")
        rows = [self._summary(value) for value in self.session_store.list_session_ids()]
        rows.sort(
            key=lambda row: (str(row["started_at"]), str(row["session_id"])),
            reverse=True,
        )
        start = self._decode_cursor(cursor, rows) if cursor else 0
        page = rows[start : start + limit]
        next_cursor = (
            self._encode_cursor(start + limit)
            if start + limit < len(rows)
            else None
        )
        return {
            "schema_version": 1,
            "sessions": page,
            "next_cursor": next_cursor,
        }
```

Encode only `{"offset": integer}` as canonical UTF-8 JSON with URL-safe base64
and no padding. Decode with validation: reject invalid base64, non-object JSON,
unknown keys, booleans and negative offsets. Cursor errors become `ValueError`;
they never include a filesystem path.

`_summary()` must read `session_store.read()` and `read_profile()`, use
`profile["title"]`, obtain job status only from the attached
`analysis_job_id`, verify the job belongs to the same session, and calculate
`review_count` only when a terminal result exists.

- [ ] **Step 4: Add failing detail and snapshot-deduplication tests**

```python
def test_detail_deduplicates_adjacent_equal_source_without_losing_event_ids(
    tmp_path,
):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.append_batch(session_id, **batch(
        session_id,
        sequence=1,
        source="value = 1\n",
    ))
    second = batch(
        session_id,
        sequence=2,
        segment_id="20000000-0000-4000-8000-000000000002",
        source="value = 1\n",
    )
    store.append_batch(session_id, **second)

    detail = service_for(store).get_detail(session_id)

    assert len(detail["behavior_events"]) == 2
    assert len(detail["code_snapshots"]) == 1
    assert detail["code_snapshots"][0]["event_ids"] == [
        f"{session_id}:1",
        f"{session_id}:2",
    ]
    assert detail["code_snapshots"][0]["source"] == "value = 1\n"
```

Add the remaining boundary tests:

```python
def test_detail_keeps_non_adjacent_equal_sources_separate(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    for sequence, source in enumerate(
        ("value = 1\n", "value = 2\n", "value = 1\n"),
        start=1,
    ):
        store.append_batch(
            session_id,
            **batch(
                session_id,
                sequence=sequence,
                segment_id=f"20000000-0000-4000-8000-{sequence:012d}",
                source=source,
            ),
        )
    detail = service_for(store).get_detail(session_id)
    assert [row["source"] for row in detail["code_snapshots"]] == [
        "value = 1\n",
        "value = 2\n",
        "value = 1\n",
    ]


def test_detail_reports_missing_raw_events_as_incomplete(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    (tmp_path / "sessions" / session_id / "raw_events.jsonl").unlink()

    detail = service_for(store).get_detail(session_id)

    assert detail["behavior_events"] == []
    assert detail["code_snapshots"] == []
    assert detail["integrity"] == {
        "complete": False,
        "missing_artifacts": ["raw_events"],
        "warnings": [],
    }


def test_detail_rejects_symlink_raw_events_without_leaking_root(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    raw_path = tmp_path / "sessions" / session_id / "raw_events.jsonl"
    raw_path.unlink()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    raw_path.symlink_to(outside)

    with pytest.raises(SessionLogIntegrityError) as captured:
        service_for(store).get_detail(session_id)

    assert str(tmp_path) not in str(captured.value)
```

For the no-source case, construct a normal `event()` row, remove
`cell_source`, recalculate the batch content hash with `sha256_json`, append
the batch, and assert `behavior_events` has one row while `code_snapshots` is
empty. Recursively serialize every successful detail with
`json.dumps(detail, ensure_ascii=False)` and assert `str(tmp_path)` is absent.

- [ ] **Step 5: Implement detail and snapshot projection**

Implement `get_detail()` so that:

```python
def get_detail(self, session_id: str) -> dict[str, object]:
    session = self.session_store.read(session_id)
    profile = self.session_store.read_profile(session_id)
    missing: list[str] = []
    warnings: list[str] = []
    events = self.session_store.read_events_if_present(session_id)
    if events is None:
        events = []
        missing.append("raw_events")
    public_events = [self._public_event(event) for event in events]
    detail = {
        "schema_version": 1,
        "session": self._summary(session_id),
        "problem_profile": self._public_profile(profile),
        "code_snapshots": self._code_snapshots(public_events),
        "behavior_events": public_events,
        "ai_analysis": None,
        "teacher_reviews": [],
        "integrity": {
            "complete": not missing,
            "missing_artifacts": missing,
            "warnings": warnings,
        },
        "training_record": {
            "exists": False,
            "stale": False,
            "generated_at": None,
            "content_hash": None,
        },
    }
    detail = self._attach_analysis(detail, session, profile)
    detail["training_record"] = self._training_record_state(
        session_id,
        detail,
    )
    return detail
```

Use explicit allowlists:

```python
PUBLIC_EVENT_FIELDS = {
    "event_id", "session_seq", "segment_type", "started_at", "ended_at",
    "duration_ms", "inserted_char_count", "deleted_char_count",
    "paste_char_count", "cell_source", "execution_result", "error_type",
    "error_message", "deleted_content", "deleted_is_full_line", "had_paste",
    "document_type", "file_name", "notebook_id", "cell_id", "cell_index",
    "cell_type",
}
```

Do not return `file_path`, `notebook_path`, previous/next notebook paths or any
root-derived value. For human context, convert a captured path to a basename
only and expose it as `document_name`; never expose directory components.

Each code snapshot has this exact shape:

```python
{
    "snapshot_id": sha256_json({
        "first_event_id": event["event_id"],
        "source": source,
    }),
    "event_ids": [event["event_id"]],
    "first_session_seq": event["session_seq"],
    "last_session_seq": event["session_seq"],
    "started_at": event["started_at"],
    "ended_at": event["ended_at"],
    "source": source,
    "document_type": event.get("document_type"),
    "document_name": event.get("document_name"),
    "cell_id": event.get("cell_id"),
    "cell_index": event.get("cell_index"),
    "execution_result": event.get("execution_result"),
    "error_type": event.get("error_type"),
    "error_message": event.get("error_message"),
}
```

Merge only adjacent snapshots when `source`, `document_type`,
`document_name`, `cell_id` and `cell_index` all match. Extend `event_ids`,
`last_session_seq` and `ended_at`; retain a later non-null execution/error
outcome.

- [ ] **Step 6: Normalize storage exceptions**

Catch `SessionIntegrityError`, `AnalysisJobIntegrityError` and
`ReviewIntegrityError` only at the service boundary and raise
`SessionLogIntegrityError("Stored session log is incomplete or unsafe.")`
without embedding the original path or student content. Do not catch
`SessionNotFoundError`.

- [ ] **Step 7: Run Task 2 tests**

Run:

```bash
.venv/bin/python -m pytest \
  myextension/tests/test_session_log_service.py \
  -k "list_sessions or detail" -q
```

Expected: all Task 2 tests pass.

- [ ] **Step 8: Record Task 2 checkpoint**

Record the focused command and pass count. Do not create a Git commit.

---

### Task 3: AI/review linkage and reproducible training record

**Files:**

- Modify: `myextension/session_log_service.py`
- Modify: `myextension/tests/test_session_log_service.py`
- Create: `myextension/api_schemas/training-record-v1.json`

**Interfaces:**

- Consumes: `AnalysisJobStore.get()`,
  `AnalysisJobStore.load_public_result()` and `ReviewStore.list()`.
- Produces: `SessionLogService.export_training_record(session_id: str) -> dict[str, object]`.
- Produces export response keys: `schema_version`, `session_id`,
  `relative_path`, `generated_at`, `content_hash`, `stale`.

- [ ] **Step 1: Add failing terminal analysis and evidence-link tests**

Import `finalized_session` and `provider_response` from
`test_analysis_job_store.py`, then define these local fixtures:

```python
def attached_job_fixture(
    tmp_path: Path,
    *,
    status: str = "queued",
):
    session_store, finalized = finalized_session(tmp_path)
    session_id = str(finalized["session_id"])
    job_store = AnalysisJobStore(tmp_path)
    job = job_store.create(
        session=finalized,
        input_snapshot_hash=compute_input_snapshot_hash(
            session_store,
            session_id,
        ),
    )
    session_store.attach_job(session_id, str(job["job_id"]))
    if status == "running":
        job_store.begin_attempt(str(job["job_id"]))
    elif status == "error":
        attempt = job_store.begin_attempt(str(job["job_id"]))
        job_store.finish_attempt(
            str(job["job_id"]),
            str(attempt["attempt_id"]),
            status="error",
            analysis_id=None,
            error_code="synthetic_failure",
        )
    elif status != "queued":
        raise ValueError(status)
    return session_store, finalized, job_store


def terminal_session_fixture(
    tmp_path: Path,
    *,
    mutate_provider_response=None,
):
    session_store, finalized, job_store = attached_job_fixture(tmp_path)
    session_id = str(finalized["session_id"])
    job_id = str(finalized.get("analysis_job_id") or (
        session_store.read(session_id)["analysis_job_id"]
    ))

    def provider(request, *, timeout_sec):
        response = provider_response(session_id)
        if mutate_provider_response is not None:
            mutate_provider_response(response, session_id)
        return response

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        synchronous=True,
    )
    worker.enqueue(job_id)
    completed = job_store.get(job_id)
    result = job_store.load_public_result(
        job_id,
        session_store=session_store,
    )
    worker.shutdown()
    return session_store, finalized, job_store, result
```

Import `AnalysisWorker`, `compute_input_snapshot_hash`, `AnalysisJobStore`,
`finalized_session` and `provider_response` explicitly. Verify the actual
`AnalysisJobStore.create()` call does not attach the job implicitly; retain
the explicit `session_store.attach_job()` shown above.

Define this narrow test double to exercise the projection’s defensive
cross-reference check without weakening the production analysis validator:

```python
class ResultOverrideJobStore:
    def __init__(self, wrapped, result):
        self.wrapped = wrapped
        self.result = result

    def get(self, job_id):
        return self.wrapped.get(job_id)

    def load_public_result(self, job_id, *, session_store):
        return json.loads(json.dumps(self.result))
```

Create a terminal result, copy it into the test double and insert one unknown
evidence ID:

```python
def test_detail_links_analysis_evidence_and_warns_for_unknown_ids(
    tmp_path,
):
    store, session, job_store, result = terminal_session_fixture(tmp_path)
    session_id = str(session["session_id"])
    known = f"{session_id}:1"
    result["dimension_results"][0]["ai_result"]["evidence_claims"].append({
        "event_id": f"{session_id}:999",
        "criterion_id": "support-1",
        "direction": "support",
        "claim": "合成缺失证据",
    })

    detail = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=ResultOverrideJobStore(job_store, result),
        review_store=ReviewStore(tmp_path),
    ).get_detail(session_id)

    assert detail["ai_analysis"]["session_id"] == session_id
    assert detail["behavior_events"][0]["referenced_by_dimensions"] == [
        detail["ai_analysis"]["dimension_results"][0]["dimension_code"]
    ]
    assert detail["integrity"]["complete"] is False
    assert detail["integrity"]["warnings"] == [
        "AI 证据引用了 1 个不存在的事件。"
    ]
```

Add these state and privacy tests:

```python
def test_detail_without_job_has_no_fabricated_ai_result(tmp_path):
    store, session = started_session(tmp_path)
    detail = service_for(store).get_detail(str(session["session_id"]))
    assert detail["session"]["analysis_status"] is None
    assert detail["ai_analysis"] is None
    assert detail["teacher_reviews"] == []


@pytest.mark.parametrize("status", ["queued", "running", "error"])
def test_non_terminal_job_exposes_status_without_result(
    tmp_path,
    status,
):
    store, session, job_store = attached_job_fixture(tmp_path, status=status)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    detail = service.get_detail(str(session["session_id"]))
    assert detail["session"]["analysis_status"] == status
    assert detail["ai_analysis"] is None


def test_detail_keeps_ai_result_and_latest_review_separate(tmp_path):
    store, session, job_store, result = terminal_session_fixture(tmp_path)
    row = result["dimension_results"][0]
    original_decision = json.loads(json.dumps(row["decision"]))
    append_synthetic_review(
        ReviewStore(tmp_path),
        analysis_id=str(result["analysis_id"]),
        dimension_code=str(row["dimension_code"]),
        evidence_event_ids=[
            str(claim["event_id"])
            for claim in row["ai_result"]["evidence_claims"]
        ],
    )
    detail = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    ).get_detail(str(session["session_id"]))

    assert detail["ai_analysis"]["dimension_results"][0]["decision"] == (
        original_decision
    )
    assert detail["teacher_reviews"][-1]["reason_code"] == (
        "teacher_correction"
    )
    serialized = json.dumps(detail, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "provider_request_id" not in serialized
    assert "prompt_snapshot" not in serialized
    assert "raw_response" not in serialized
```

Add one identity-corruption test by rewriting the synthetic `job.json`
`session_id` to another canonical UUID, then calling `get_detail()` and
asserting `SessionLogIntegrityError` without matching the temporary root
string. Recompute no hashes: the store must fail closed while reading the
tampered job.

- [ ] **Step 2: Run the focused tests and verify analysis is not attached**

Run:

```bash
.venv/bin/python -m pytest \
  myextension/tests/test_session_log_service.py \
  -k "analysis or evidence or review" -q
```

Expected: failures show `ai_analysis` remains null or evidence linkage fields
are absent.

- [ ] **Step 3: Implement analysis, review and evidence linkage**

Add a strict provenance allowlist:

```python
PUBLIC_PROVENANCE_FIELDS = {
    "analysis_pipeline_version",
    "feature_extractor_version",
    "signal_dictionary_version",
    "signal_dictionary_hash",
    "model_name",
    "model_version",
    "model_parameters",
    "prompt_version",
    "prompt_content_hash",
    "raw_response_hash",
    "input_snapshot_hash",
}
```

`_attach_analysis()` must:

1. read the attached job and verify `job["session_id"]`;
2. set `session["analysis_status"]` for every job state;
3. call `load_public_result()` only for `ready` or `partial`;
4. copy only `_RESULT_PUBLIC_FIELDS` equivalent fields and the provenance
   allowlist;
5. collect each dimension’s evidence IDs from
   `ai_result.evidence_claims[*].event_id`;
6. annotate matching public events with sorted
   `referenced_by_dimensions`;
7. count unknown IDs and append the fixed Chinese warning;
8. call `review_store.list(analysis_id, dimension_code)` for every result
   dimension and return the full append-only history;
9. set `integrity.complete = False` whenever an evidence reference is unknown.

Do not apply teacher corrections to the immutable AI result in this service.
The UI displays AI judgment and review history as separate records.

- [ ] **Step 4: Add failing training-record tests**

```python
def test_export_training_record_is_reproducible_and_private(tmp_path):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    session_id = str(session["session_id"])

    first = service.export_training_record(session_id)
    stored_first = store.read_training_record(session_id)
    second = service.export_training_record(session_id)
    stored_second = store.read_training_record(session_id)

    assert first["content_hash"] == second["content_hash"]
    assert stored_first["export"]["content_hash"] == first["content_hash"]
    assert stored_second["export"]["content_hash"] == second["content_hash"]
    serialized = json.dumps(stored_second, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "provider_request_id" not in serialized
    assert "prompt_snapshot" not in serialized
    assert "raw_response" not in serialized
```

Add the review-staleness and lifecycle tests:

```python
def test_teacher_review_marks_export_stale_until_reexport(tmp_path):
    store, session, job_store, result = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    session_id = str(session["session_id"])
    first = service.export_training_record(session_id)
    dimension = result["dimension_results"][0]
    append_synthetic_review(
        ReviewStore(tmp_path),
        analysis_id=str(result["analysis_id"]),
        dimension_code=str(dimension["dimension_code"]),
        evidence_event_ids=[
            str(claim["event_id"])
            for claim in dimension["ai_result"]["evidence_claims"]
        ],
    )

    stale = service.get_detail(session_id)["training_record"]
    second = service.export_training_record(session_id)
    current = service.get_detail(session_id)["training_record"]

    assert stale["exists"] is True
    assert stale["stale"] is True
    assert second["content_hash"] != first["content_hash"]
    assert current["stale"] is False


def test_training_record_validates_and_is_deleted_with_session(tmp_path):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    session_id = str(session["session_id"])
    service.export_training_record(session_id)
    record = store.read_training_record(session_id)
    validate_schema("training-record-v1", record)
    session_dir = tmp_path / "sessions" / session_id
    assert not list(session_dir.glob("*.tmp"))

    store.delete_cascade(
        session_id,
        actor="local_teacher",
        reason="synthetic_test_cleanup",
    )

    assert not session_dir.exists()
```

Define this exact helper in the test file:

```python
def append_synthetic_review(
    store: ReviewStore,
    *,
    analysis_id: str,
    dimension_code: str,
    evidence_event_ids: list[str],
) -> dict[str, object]:
    return store.append(
        analysis_id,
        dimension_code,
        expected_revision=0,
        correction={
            "revision": 0,
            "decision_status": "resolved",
            "evidence_status": "observed",
            "level_code": "possible",
            "evidence_event_ids": evidence_event_ids,
            "reason_code": "teacher_correction",
            "comment": "合成复核记录",
        },
    )
```

- [ ] **Step 5: Define the strict training-record schema**

Create `training-record-v1.json` with:

- top-level `additionalProperties: false`;
- required keys `schema_version`, `session`, `problem_profile`,
  `code_snapshots`, `behavior_events`, `ai_analysis`, `teacher_reviews`,
  `integrity`, `export`;
- `schema_version: 1`;
- canonical UUID patterns for session and event owner IDs;
- `export` requiring `schema_version`, `generated_at`,
  `source_session_id`, `source_state_hash`, `content_hash`;
- no keys named `prompt_snapshot`, `provider_request_id`, `raw_response`,
  `api_key`, `file_path` or `notebook_path`;
- bounded strings and arrays using the existing event maximums rather than
  unbounded payloads.

Use reusable `$defs` for session summary, event, code snapshot, analysis,
review, integrity and export metadata. Allow `ai_analysis` to be `null`.

- [ ] **Step 6: Implement source-state hashing and export**

Build a record from `get_detail()` and compute:

```python
source_state = {
    "session_id": session_id,
    "profile_content_hash": session.get("profile_content_hash"),
    "last_contiguous_sequence": session.get("last_contiguous_sequence"),
    "analysis_job_id": session.get("analysis_job_id"),
    "analysis_status": detail["session"].get("analysis_status"),
    "analysis_id": (
        detail["ai_analysis"].get("analysis_id")
        if detail["ai_analysis"] is not None
        else None
    ),
    "teacher_reviews": detail["teacher_reviews"],
}
source_state_hash = sha256_json(source_state)
```

Create `record_without_content_hash` with the detail fields and:

```python
"export": {
    "schema_version": 1,
    "generated_at": _now_iso(),
    "source_session_id": session_id,
    "source_state_hash": source_state_hash,
}
```

Compute `content_hash = sha256_json(record_without_content_hash)`, append it
to `export`, validate with `validate_schema("training-record-v1", record)`,
then call `session_store.write_training_record()`.

The endpoint response uses relative path
`sessions/<session_id>/training_record.json`; never resolve it to an absolute
path. For reproducibility, the content hash excludes `generated_at`: compute
the content payload hash from all record fields except `export.generated_at`
and `export.content_hash`. Repeated export with unchanged source data must
return the same `content_hash`, even though `generated_at` advances.

`_training_record_state(session_id, detail)` computes the current source-state
hash from the already attached analysis and review data, compares it with the
stored `source_state_hash`, and returns:

```python
{
    "exists": True,
    "stale": stored_hash != current_hash,
    "generated_at": stored["export"]["generated_at"],
    "content_hash": stored["export"]["content_hash"],
}
```

For no file, return `exists: False`, `stale: False`, and null metadata.

- [ ] **Step 7: Run the service and schema tests**

Run:

```bash
.venv/bin/python -m pytest \
  myextension/tests/test_session_log_service.py \
  myextension/tests/test_schema_registry.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Record Task 3 checkpoint**

Record both pass counts and the synthetic training-record schema validation.
Do not create a Git commit.

---

### Task 4: Authenticated API routes and OpenAPI contract

**Files:**

- Modify: `myextension/routes.py`
- Create: `myextension/api_schemas/session-log-list-v1.json`
- Create: `myextension/api_schemas/session-log-detail-v1.json`
- Create: `myextension/api_schemas/training-record-response-v1.json`
- Modify: `myextension/tests/test_pilot_api.py`
- Modify: `docs/openapi/myextension-v1.yaml`

**Interfaces:**

- Produces: `GET /myextension/session-logs?limit=20&cursor=<opaque>`
- Produces: `GET /myextension/sessions/<session_id>/log-detail`
- Produces: `POST /myextension/sessions/<session_id>/training-record`
- Consumes: `SessionLogService` from Task 2 and Task 3.

- [ ] **Step 1: Add failing API route tests**

Add pytest-jupyter tests that build a synthetic session through existing APIs:

```python
async def test_session_log_routes_are_authenticated_and_session_scoped(
    jp_fetch,
):
    profile = await create_published_profile(jp_fetch)
    session = await start_pilot_session(jp_fetch, profile)
    session_id = session["session_id"]

    listing = await jp_fetch("myextension", "session-logs")
    detail = await jp_fetch(
        "myextension",
        "sessions",
        session_id,
        "log-detail",
    )
    exported = await jp_fetch(
        "myextension",
        "sessions",
        session_id,
        "training-record",
        method="POST",
        body="{}",
    )

    assert response_json(listing)["sessions"][0]["session_id"] == session_id
    assert response_json(detail)["session"]["session_id"] == session_id
    assert response_json(exported)["session_id"] == session_id
```

Add exact query and validation assertions:

```python
@pytest.mark.parametrize(
    "query",
    ("limit=0", "limit=51", "cursor=not-base64"),
)
async def test_session_log_list_rejects_invalid_query(jp_fetch, query):
    response = await jp_fetch(
        "myextension",
        f"session-logs?{query}",
        raise_error=False,
    )
    assert response.code == 400
    assert response_json(response)["code"] == "session_log_query_invalid"


async def test_session_log_routes_return_safe_resource_errors(jp_fetch):
    invalid = "NOT-A-CANONICAL-UUID"
    invalid_response = await jp_fetch(
        "myextension",
        "sessions",
        invalid,
        "log-detail",
        raise_error=False,
    )
    missing_id = "00000000-0000-4000-8000-000000000000"
    missing_response = await jp_fetch(
        "myextension",
        "sessions",
        missing_id,
        "log-detail",
        raise_error=False,
    )
    assert invalid_response.code == 400
    assert invalid not in invalid_response.body.decode("utf-8")
    assert missing_response.code == 404
    assert response_json(missing_response)["code"] == "session_not_found"


async def test_training_record_rejects_unknown_body_field(
    jp_fetch,
):
    profile = await create_published_profile(jp_fetch)
    session = await start_pilot_session(jp_fetch, profile)
    response = await jp_fetch(
        "myextension",
        "sessions",
        session["session_id"],
        "training-record",
        method="POST",
        body=json.dumps({"absolute_path": "/private/value"}),
        raise_error=False,
    )
    assert response.code == 422
    body = response_json(response)
    assert body["code"] == "training_record_validation_failed"
    assert "/private/value" not in response.body.decode("utf-8")
```

For the successful list, detail and export responses in the first test, call
`validate_schema()` with `session-log-list-v1`,
`session-log-detail-v1` and `training-record-response-v1`. Serialize each
response and assert the pytest temporary root is absent. Create an unsafe
synthetic raw-event symlink and assert detail returns
`409 session_log_incomplete`. Follow the existing unauthenticated handler test
fixture in this file to call each handler without the Jupyter auth cookie and
assert the response is not `200`.

- [ ] **Step 2: Run the new API tests and verify 404 route failures**

Run:

```bash
.venv/bin/python -m pytest \
  myextension/tests/test_pilot_api.py \
  -k "session_log or training_record" -q
```

Expected: requests fail because the routes are not registered.

- [ ] **Step 3: Add strict response schemas**

Create:

- `session-log-list-v1.json`, reusing the session-summary definition from the
  design and allowing `next_cursor` to be string or null;
- `session-log-detail-v1.json`, matching the exact detail fields from Task 2;
- `training-record-response-v1.json`, requiring:

```json
{
  "schema_version": 1,
  "session_id": "canonical UUID",
  "relative_path": "sessions/<UUID>/training_record.json",
  "generated_at": "non-empty ISO timestamp string",
  "content_hash": "64 lowercase hex characters",
  "stale": false
}
```

All schemas use `additionalProperties: false` at the top level and closed
nested objects for metadata, integrity and export state.

- [ ] **Step 4: Implement route handlers and service construction**

Add to `PilotAPIHandler`:

```python
def _session_log_service(self) -> SessionLogService:
    _, session_store, job_store = self._services()
    root = Path(session_store.root)
    return SessionLogService(
        root=root,
        session_store=session_store,
        job_store=job_store,
        review_store=ReviewStore(root),
    )
```

Add handlers:

```python
class SessionLogsRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def get(self):
        try:
            limit_text = self.get_query_argument("limit", "20")
            if not limit_text.isdecimal():
                raise ValueError("invalid limit")
            result = self._session_log_service().list_sessions(
                limit=int(limit_text),
                cursor=self.get_query_argument("cursor", None),
            )
            validate_schema(
                "session-log-list-v1",
                {**result, "request_id": self.request_id()},
            )
            self.finish_json(result)
        except ValueError:
            self.finish_error(
                400,
                "session_log_query_invalid",
                "日志查询参数无效。",
            )
        except SessionLogIntegrityError:
            self._finish_conflict("session_log_incomplete")


class SessionLogDetailRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def get(self, session_id):
        try:
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            result = self._session_log_service().get_detail(canonical_id)
            validate_schema(
                "session-log-detail-v1",
                {**result, "request_id": self.request_id()},
            )
            self.finish_json(result)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except SessionNotFoundError:
            self._finish_not_found("session_not_found")
        except SessionLogIntegrityError:
            self._finish_conflict("session_log_incomplete")
        except Exception:
            self._finish_internal_error()


class TrainingRecordRouteHandler(PilotAPIHandler):
    @tornado.web.authenticated
    def post(self, session_id):
        try:
            canonical_id = _canonical_resource_uuid(
                session_id,
                field="session_id",
            )
            body = self.read_json_object()
            if body != {}:
                raise ApiRequestError(
                    422,
                    "training_record_validation_failed",
                    "请求内容未通过校验。",
                    details={
                        "field": "$",
                        "reason": "unknown_field",
                    },
                )
            result = self._session_log_service().export_training_record(
                canonical_id
            )
            validate_schema(
                "training-record-response-v1",
                {**result, "request_id": self.request_id()},
            )
            self.finish_json(result)
        except ApiRequestError as error:
            self._finish_request_error(error)
        except SessionNotFoundError:
            self._finish_not_found("session_not_found")
        except SessionLogIntegrityError:
            self._finish_conflict("session_log_incomplete")
        except Exception:
            self._finish_internal_error()
```

Do not include exception text in responses. Add `SessionIntegrityError`,
`AnalysisJobIntegrityError`, `ReviewIntegrityError`, `ValidationError` and
`OSError` to the safe internal-error branches where response validation or
storage can fail without matching a known incomplete-log condition.

Register patterns before the generic session route:

```python
session_logs_route_pattern = url_path_join(
    base_url, "myextension", "session-logs"
)
session_log_detail_route_pattern = url_path_join(
    base_url, "myextension", "sessions", r"([^/]+)", "log-detail"
)
training_record_route_pattern = url_path_join(
    base_url, "myextension", "sessions", r"([^/]+)", "training-record"
)
```

- [ ] **Step 5: Update OpenAPI**

Add the three paths with:

- Jupyter cookie/token authentication inherited from the API;
- query bounds `minimum: 1`, `maximum: 50`;
- response `$ref`s matching packaged JSON schemas;
- 400/404/409/422/500 error references;
- descriptions stating that historical source is captured event source and
  that export stays on the Jupyter Server machine.

Add component schemas that reference the packaged JSON schema files using the
same relative-reference convention already used in the document.

- [ ] **Step 6: Run API and OpenAPI tests**

Run:

```bash
.venv/bin/python -m pytest \
  myextension/tests/test_pilot_api.py \
  myextension/tests/test_routes.py \
  myextension/tests/test_schema_registry.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Record Task 4 checkpoint**

Record route tests, OpenAPI validation and pass counts. Do not create a Git
commit.

---

### Task 5: Typed frontend models and API service

**Files:**

- Create: `src/models/sessionLog.ts`
- Create: `src/services/sessionLogApi.ts`
- Create: `src/__tests__/sessionLogApi.spec.ts`

**Interfaces:**

- Produces: `listSessionLogs(settings, options?)`
- Produces: `getSessionLogDetail(settings, sessionId)`
- Produces: `exportTrainingRecord(settings, sessionId)`
- Consumes: the three Task 4 endpoints.
- Consumed later by: viewer, command and sidebar.

- [ ] **Step 1: Write failing request-wrapper tests**

Mock `requestAPI` and assert exact encoded endpoints:

```typescript
it('lists logs with bounded query values', async () => {
  await listSessionLogs(settings, { limit: 20, cursor: 'a+b/c=' });
  expect(requestAPI).toHaveBeenCalledWith(
    'session-logs?limit=20&cursor=a%2Bb%2Fc%3D',
    settings
  );
});

it('loads and exports one canonical session path', async () => {
  await getSessionLogDetail(settings, SESSION_ID);
  await exportTrainingRecord(settings, SESSION_ID);
  expect(requestAPI).toHaveBeenNthCalledWith(
    1,
    `sessions/${SESSION_ID}/log-detail`,
    settings
  );
  expect(requestAPI).toHaveBeenNthCalledWith(
    2,
    `sessions/${SESSION_ID}/training-record`,
    settings,
    {
      method: 'POST',
      body: '{}',
      headers: { 'Content-Type': 'application/json' }
    }
  );
});
```

- [ ] **Step 2: Run the focused Jest file and verify missing modules**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test \
  src/__tests__/sessionLogApi.spec.ts --runInBand
```

Expected: TypeScript/Jest fails because the model and service do not exist.

- [ ] **Step 3: Define exact TypeScript models**

Create `src/models/sessionLog.ts` with:

```typescript
export interface ILocalSessionLogSummary {
  session_id: string;
  problem_id: string;
  problem_title: string;
  profile_id: string;
  profile_version: number;
  status: string;
  analysis_status: string | null;
  started_at: string;
  ended_at: string | null;
  event_count: number;
  review_count: number;
}

export interface ICodeSnapshot {
  snapshot_id: string;
  event_ids: string[];
  first_session_seq: number;
  last_session_seq: number;
  started_at: string;
  ended_at: string;
  source: string;
  document_type: 'notebook_cell' | 'python_file' | null;
  document_name: string | null;
  cell_id: string | null;
  cell_index: number | null;
  execution_result: 'success' | 'failure' | null;
  error_type: string | null;
  error_message: string | null;
}

export interface IBehaviorLogEvent {
  event_id: string;
  session_seq: number;
  segment_type: string;
  started_at: string;
  ended_at: string;
  duration_ms: number;
  document_name?: string;
  cell_source?: string;
  execution_result?: 'success' | 'failure';
  error_type?: string;
  error_message?: string;
  referenced_by_dimensions: string[];
  [key: string]: unknown;
}

export interface ITrainingRecordState {
  exists: boolean;
  stale: boolean;
  generated_at: string | null;
  content_hash: string | null;
}
```

Also define `ISessionLogListResponse`, `ISessionLogIntegrity`,
`ITeacherReviewRecord`, `ISessionLogDetail` and
`ITrainingRecordExportResponse`. Reuse `IDimensionResult` but define a
session-log-specific result because normal analysis responses require
`provider_request_id`, while the log API deliberately excludes it:

```typescript
export interface ISessionLogAnalysis extends Pick<
  IAnalysisResult,
  | 'analysis_id'
  | 'job_id'
  | 'attempt_id'
  | 'session_id'
  | 'profile_id'
  | 'profile_version'
  | 'profile_content_hash'
  | 'status'
  | 'error_code'
> {
  dimension_results: IDimensionResult[];
  provenance: Omit<IAnalysisProvenance, 'provider_request_id'>;
}
```

`ISessionLogDetail.ai_analysis` is `ISessionLogAnalysis | null`.

- [ ] **Step 4: Implement API wrappers**

Build query parameters with `URLSearchParams`, append cursor only when present,
and pass all session IDs through `encodeURIComponent`. Keep the JSON header
constant private to `sessionLogApi.ts`.

- [ ] **Step 5: Run API service tests and type compilation**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test \
  src/__tests__/sessionLogApi.spec.ts --runInBand
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:lib:prod
```

Expected: both commands pass.

- [ ] **Step 6: Record Task 5 checkpoint**

Record Jest and TypeScript results. Do not create a Git commit.

---

### Task 6: Main-area session log viewer

**Files:**

- Create: `src/ui/sessionLogViewer.ts`
- Create: `src/__tests__/sessionLogViewer.spec.ts`
- Modify: `style/base.css`

**Interfaces:**

- Produces: `SessionLogViewer extends Widget`
- Constructor:
  `new SessionLogViewer({ loadDetail, exportRecord })`
- Produces: `showSession(sessionId: string) -> Promise<void>`
- Consumes: `ISessionLogDetail` and `ITrainingRecordExportResponse`.

- [ ] **Step 1: Add failing viewer state and source-security tests**

Define these test helpers before the cases:

```typescript
const SESSION_A = '10000000-0000-4000-8000-000000000001';
const SESSION_B = '10000000-0000-4000-8000-000000000002';
const SESSION_ID = SESSION_A;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, resolve, reject };
}

async function flush(): Promise<void> {
  for (let index = 0; index < 10; index += 1) await Promise.resolve();
}

function buttonByText(
  viewer: SessionLogViewer,
  label: string
): HTMLButtonElement {
  const value = Array.from(
    viewer.node.querySelectorAll<HTMLButtonElement>('button')
  ).find(item => item.textContent === label);
  if (!value) throw new Error(`Missing button: ${label}`);
  return value;
}

function clickTab(viewer: SessionLogViewer, label: string): void {
  buttonByText(viewer, label).click();
}

function analysisFixture(sessionId: string): ISessionLogAnalysis {
  return {
    analysis_id: '40000000-0000-4000-8000-000000000001',
    job_id: '30000000-0000-4000-8000-000000000001',
    attempt_id: '50000000-0000-4000-8000-000000000001',
    session_id: sessionId,
    profile_id: '20000000-0000-4000-8000-000000000001',
    profile_version: 1,
    profile_content_hash: 'a'.repeat(64),
    status: 'ready',
    error_code: null,
    dimension_results: [
      {
        schema_version: 1,
        dimension_code: 'LOOP_ACCUMULATION',
        decision: {
          status: 'resolved',
          final_evidence_status: 'observed',
          final_level_code: 'possible',
          display_label: '可能出现',
          source: 'llm_evidence'
        },
        ai_result: {
          confidence: 0.8,
          evidence_claims: [
            {
              event_id: `${sessionId}:1`,
              criterion_id: 'support-1',
              direction: 'support',
              claim: '出现循环累加代码'
            }
          ],
          explanation: '仅基于合成日志。'
        }
      }
    ],
    provenance: {
      analysis_pipeline_version: 'teacher-dimensions-pilot-v1',
      feature_extractor_version: 'v1',
      signal_dictionary_version: 'pilot-v1',
      signal_dictionary_hash: 'b'.repeat(64),
      model_name: 'synthetic-model',
      model_version: null,
      model_parameters: { temperature: 0 },
      prompt_version: 'teacher-dimensions-pilot-v1',
      prompt_content_hash: 'c'.repeat(64),
      raw_response_hash: 'd'.repeat(64),
      input_snapshot_hash: 'e'.repeat(64)
    }
  };
}

function detailFixture(
  options: {
    source?: string;
    sessionId?: string;
    aiAnalysis?: ISessionLogAnalysis | null;
    integrity?: ISessionLogIntegrity;
  } = {}
): ISessionLogDetail {
  const sessionId = options.sessionId ?? SESSION_ID;
  const source = options.source ?? 'value = 1\n';
  return {
    schema_version: 1,
    session: {
      session_id: sessionId,
      problem_id: 'average-debug',
      problem_title: '平均数练习',
      profile_id: '20000000-0000-4000-8000-000000000001',
      profile_version: 1,
      status: 'finalized',
      analysis_status: 'ready',
      started_at: '2026-07-30T15:00:00+08:00',
      ended_at: '2026-07-30T15:05:00+08:00',
      event_count: 2,
      review_count: 0
    },
    problem_profile: { title: '平均数练习', dimensions: [] },
    code_snapshots: [
      {
        snapshot_id: 'f'.repeat(64),
        event_ids: [`${sessionId}:1`],
        first_session_seq: 1,
        last_session_seq: 1,
        started_at: '2026-07-30T15:00:01+08:00',
        ended_at: '2026-07-30T15:00:02+08:00',
        source,
        document_type: 'notebook_cell',
        document_name: 'lesson.ipynb',
        cell_id: 'cell-1',
        cell_index: 0,
        execution_result: null,
        error_type: null,
        error_message: null
      }
    ],
    behavior_events: [
      {
        event_id: `${sessionId}:1`,
        session_seq: 1,
        segment_type: 'code_writing',
        started_at: '2026-07-30T15:00:01+08:00',
        ended_at: '2026-07-30T15:00:02+08:00',
        duration_ms: 1000,
        cell_source: source,
        referenced_by_dimensions: ['LOOP_ACCUMULATION']
      },
      {
        event_id: `${sessionId}:2`,
        session_seq: 2,
        segment_type: 'code_execution',
        started_at: '2026-07-30T15:00:03+08:00',
        ended_at: '2026-07-30T15:00:04+08:00',
        duration_ms: 1000,
        execution_result: 'success',
        referenced_by_dimensions: []
      }
    ],
    ai_analysis:
      options.aiAnalysis === undefined
        ? analysisFixture(sessionId)
        : options.aiAnalysis,
    teacher_reviews: [],
    integrity: options.integrity ?? {
      complete: true,
      missing_artifacts: [],
      warnings: []
    },
    training_record: {
      exists: false,
      stale: false,
      generated_at: null,
      content_hash: null
    }
  };
}
```

Use injected promises and jsdom:

```typescript
it('renders captured source as text and never as markup', async () => {
  const source = '<img src=x onerror="globalThis.pwned=true">';
  const viewer = new SessionLogViewer({
    loadDetail: jest.fn(async () => detailFixture({ source })),
    exportRecord: jest.fn()
  });

  await viewer.showSession(SESSION_ID);
  clickTab(viewer, '原始代码');

  expect(viewer.node.querySelector('pre')?.textContent).toBe(source);
  expect(viewer.node.querySelector('img')).toBeNull();
});

it('reuses the widget while ignoring stale session responses', async () => {
  const first = deferred<ISessionLogDetail>();
  const second = deferred<ISessionLogDetail>();
  const loadDetail = jest
    .fn()
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  const viewer = new SessionLogViewer({
    loadDetail,
    exportRecord: jest.fn()
  });

  const firstLoad = viewer.showSession(SESSION_A);
  const secondLoad = viewer.showSession(SESSION_B);
  second.resolve(detailFixture({ sessionId: SESSION_B }));
  await secondLoad;
  first.resolve(detailFixture({ sessionId: SESSION_A }));
  await firstLoad;

  expect(viewer.node.textContent).toContain(SESSION_B);
  expect(viewer.node.textContent).not.toContain(SESSION_A);
});
```

Add these viewer behavior tests:

```typescript
it('shows missing AI and incomplete artifacts without hiding source', async () => {
  const detail = detailFixture({
    aiAnalysis: null,
    integrity: {
      complete: false,
      missing_artifacts: ['analysis_result'],
      warnings: []
    }
  });
  const viewer = new SessionLogViewer({
    loadDetail: jest.fn(async () => detail),
    exportRecord: jest.fn()
  });
  await viewer.showSession(SESSION_ID);
  expect(viewer.node.textContent).toContain('日志不完整');
  clickTab(viewer, 'AI 分析');
  expect(viewer.node.textContent).toContain('尚无 AI 结论');
  clickTab(viewer, '原始代码');
  expect(viewer.node.querySelector('pre')).not.toBeNull();
});

it('filters events and supports keyboard tab activation', async () => {
  const viewer = new SessionLogViewer({
    loadDetail: jest.fn(async () => detailFixture()),
    exportRecord: jest.fn()
  });
  await viewer.showSession(SESSION_ID);
  const sourceTab = buttonByText(viewer, '原始代码');
  sourceTab.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })
  );
  expect(sourceTab.getAttribute('aria-selected')).toBe('true');
  clickTab(viewer, '行为日志');
  const filter = viewer.node.querySelector<HTMLSelectElement>(
    '[aria-label="筛选行为类型"]'
  )!;
  filter.value = 'code_execution';
  filter.dispatchEvent(new Event('change', { bubbles: true }));
  expect(
    viewer.node.querySelectorAll('[data-event-type="code_execution"]')
  ).toHaveLength(1);
  expect(
    viewer.node.querySelectorAll('[data-event-type="code_writing"]')
  ).toHaveLength(0);
});

it('moves from an AI evidence claim to the exact event', async () => {
  HTMLElement.prototype.scrollIntoView = jest.fn();
  const viewer = new SessionLogViewer({
    loadDetail: jest.fn(async () => detailFixture()),
    exportRecord: jest.fn()
  });
  await viewer.showSession(SESSION_ID);
  clickTab(viewer, 'AI 分析');
  buttonByText(viewer, `${SESSION_ID}:1`).click();
  const event = viewer.node.querySelector<HTMLElement>(
    `[data-event-id="${SESSION_ID}:1"]`
  );
  expect(event).not.toBeNull();
  expect(document.activeElement).toBe(event);
});

it('announces export failure and ignores completion after disposal', async () => {
  const pending = deferred<ITrainingRecordExportResponse>();
  const exportRecord = jest.fn(() => pending.promise);
  const viewer = new SessionLogViewer({
    loadDetail: jest.fn(async () => detailFixture()),
    exportRecord
  });
  await viewer.showSession(SESSION_ID);
  buttonByText(viewer, '导出训练记录').click();
  expect(buttonByText(viewer, '正在导出…').disabled).toBe(true);
  viewer.dispose();
  pending.reject(new Error('synthetic secret response'));
  await flush();
  expect(document.body.textContent).not.toContain('synthetic secret response');
});
```

Add this rejected-load assertion:

```typescript
it('uses a fixed safe load error', async () => {
  const viewer = new SessionLogViewer({
    loadDetail: jest.fn(async () => {
      throw new Error('synthetic private path /Users/example');
    }),
    exportRecord: jest.fn()
  });
  await viewer.showSession(SESSION_ID);
  expect(viewer.node.textContent).toContain('日志读取失败，请重试。');
  expect(viewer.node.textContent).not.toContain('/Users/example');
});
```

- [ ] **Step 2: Run the viewer tests and verify missing widget**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test \
  src/__tests__/sessionLogViewer.spec.ts --runInBand
```

Expected: module import fails.

- [ ] **Step 3: Implement viewer shell and async generation guard**

Create a single `Widget` with:

```typescript
export interface ISessionLogViewerDependencies {
  loadDetail: (sessionId: string) => Promise<ISessionLogDetail>;
  exportRecord: (sessionId: string) => Promise<ITrainingRecordExportResponse>;
}

export class SessionLogViewer extends Widget {
  private generation = 0;
  private detail: ISessionLogDetail | null = null;
  private selectedTab:
    | 'overview'
    | 'source'
    | 'analysis'
    | 'events'
    | 'reviews' = 'overview';

  async showSession(sessionId: string): Promise<void> {
    const generation = ++this.generation;
    this.renderLoading();
    try {
      const detail = await this.deps.loadDetail(sessionId);
      if (this.isDisposed || generation !== this.generation) return;
      this.detail = detail;
      this.render();
    } catch {
      if (this.isDisposed || generation !== this.generation) return;
      this.renderError();
    }
  }
}
```

Set:

- `id = "myextension-session-log-viewer"`;
- title label `会话日志`;
- title caption `查看本地代码、行为事件和分析记录`;
- `title.closable = true`.

Use `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls` and
`role="tabpanel"`. ArrowLeft/ArrowRight move focus; Enter/Space activates.

- [ ] **Step 4: Implement the five views**

Render each field through `textContent`:

- Overview: identity, profile version, dates, counts, status and integrity.
- Source: one `<article>` per `ICodeSnapshot`, metadata plus `<pre><code>`.
- Analysis: dimension label, AI decision, data quality, evidence buttons and
  safe provenance subset.
- Events: native `<select>` filter and one row/article per event.
- Reviews: append-only review history or empty-state text.

Use a `Map<string, HTMLElement>` while rendering events and a
`Map<string, HTMLElement>` while rendering snapshots.

Evidence button action:

```typescript
private revealEvidence(eventId: string): void {
  this.selectedTab = 'events';
  this.render();
  const event = this.eventNodes.get(eventId);
  event?.scrollIntoView({ block: 'center' });
  event?.focus();
}
```

Each event article has `tabIndex = -1` and `data-event-id`.
If an event belongs to a snapshot, render a secondary “查看当时代码” button
that switches to source and focuses that snapshot.

- [ ] **Step 5: Implement export behavior**

Disable the export button while running. On success, update visible
`training_record` state using the response and announce:

```text
训练记录已保存在本机：sessions/<session_id>/training_record.json
```

On failure announce “训练记录生成失败，请重试。” without exception content.
Show “需要重新生成” whenever `detail.training_record.stale` is true.

- [ ] **Step 6: Add responsive and accessible styles**

Add namespaced classes:

```css
.jp-BehaviorAudit-sessionLogViewer {
  box-sizing: border-box;
  height: 100%;
  overflow: auto;
  padding: 24px;
  color: var(--jp-ui-font-color1);
  background: var(--jp-layout-color0);
}

.jp-BehaviorAudit-logTabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  border-bottom: 1px solid var(--jp-border-color2);
}

.jp-BehaviorAudit-sourceCode {
  overflow: auto;
  white-space: pre;
  tab-size: 4;
}

.jp-BehaviorAudit-logEvent[data-evidence='true'] {
  border-left: 3px solid var(--jp-brand-color1);
}
```

Use only JupyterLab CSS variables. At widths below 700px, stack metadata
columns without horizontal page overflow; code blocks may scroll internally.
Do not use color alone to express error, incomplete or evidence state.

- [ ] **Step 7: Run viewer tests, lint and TypeScript build**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test \
  src/__tests__/sessionLogViewer.spec.ts --runInBand
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:lib:prod
```

Expected: all commands pass.

- [ ] **Step 8: Record Task 6 checkpoint**

Record viewer Jest, accessibility assertions, lint and build results. Do not
create a Git commit.

---

### Task 7: Left-sidebar log entry and single-widget command wiring

**Files:**

- Create: `src/ui/sessionLogCommand.ts`
- Modify: `src/ui/behaviorAnalysisSidebar.ts`
- Modify: `src/index.ts`
- Modify: `src/__tests__/behaviorAnalysisSidebar.spec.ts`
- Modify: `src/__tests__/myextension.spec.ts`
- Modify: `style/base.css`

**Interfaces:**

- Consumes: Task 5 API wrappers and Task 6 viewer.
- Adds sidebar dependencies:
  `listSessionLogs`, `exportTrainingRecord`, `openSessionLog`.
- Produces command: `myextension:open-session-log`.

- [ ] **Step 1: Add failing single-widget command tests**

Create tests around:

```typescript
const open = registerSessionLogCommand(app, {
  loadDetail,
  exportRecord
});

await open(SESSION_A);
await open(SESSION_B);

expect(app.shell.add).toHaveBeenCalledTimes(1);
expect(app.shell.activateById).toHaveBeenLastCalledWith(
  'myextension-session-log-viewer'
);
expect(loadDetail).toHaveBeenLastCalledWith(SESSION_B);
```

Add the invalid-argument assertion:

```typescript
await app.commands.execute(OPEN_SESSION_LOG_COMMAND, {});
await app.commands.execute(OPEN_SESSION_LOG_COMMAND, { sessionId: 17 });
expect(app.shell.add).not.toHaveBeenCalled();
expect(loadDetail).not.toHaveBeenCalled();
```

- [ ] **Step 2: Add failing sidebar log-flow tests**

Define this synthetic summary:

```typescript
const sessionLogSummary: ILocalSessionLogSummary = {
  session_id: '10000000-0000-4000-8000-000000000020',
  problem_id: 'average-debug',
  problem_title: '平均数练习',
  profile_id: profile.profile_id,
  profile_version: profile.version,
  status: 'finalized',
  analysis_status: 'ready',
  started_at: '2026-07-30T15:00:00+08:00',
  ended_at: '2026-07-30T15:05:00+08:00',
  event_count: 12,
  review_count: 1
};
```

Import `ILocalSessionLogSummary` and
`ITrainingRecordExportResponse` from `models/sessionLog`, then extend the
sidebar fixture dependencies:

```typescript
listSessionLogs: jest.fn(async () => ({
  schema_version: 1,
  sessions: [sessionLogSummary],
  next_cursor: null
})),
exportTrainingRecord: jest.fn(async sessionId => ({
  schema_version: 1,
  session_id: sessionId,
  relative_path: `sessions/${sessionId}/training_record.json`,
  generated_at: '2026-07-30T16:00:00+08:00',
  content_hash: 'a'.repeat(64),
  stale: false
})),
openSessionLog: jest.fn(async () => undefined),
```

Add these core sidebar assertions:

```typescript
it('shows local logs outside advanced data and opens the selected session', async () => {
  const deps = dependencies(createCapture(), [profile]);
  const sidebar = new BehaviorAnalysisSidebar(deps);
  await flush();

  const localHeading = Array.from(sidebar.node.querySelectorAll('h2')).find(
    value => value.textContent === '本地日志'
  );
  const advanced = Array.from(sidebar.node.querySelectorAll('details')).find(
    value => value.querySelector('summary')?.textContent === '高级数据'
  );
  expect(localHeading).toBeDefined();
  expect(advanced?.contains(localHeading ?? null)).toBe(false);
  expect(sidebar.node.textContent).toContain('分析完成');

  findButton(sidebar, '查看日志').click();
  await flush();
  expect(deps.openSessionLog).toHaveBeenCalledWith(
    sessionLogSummary.session_id
  );
});

it('shows an instructive empty log state', async () => {
  const deps = dependencies(createCapture(), [profile]);
  deps.listSessionLogs = jest.fn(async () => ({
    schema_version: 1,
    sessions: [],
    next_cursor: null
  }));
  const sidebar = new BehaviorAnalysisSidebar(deps);
  await flush();
  expect(sidebar.node.textContent).toContain(
    '完成一次监控后，可在这里查看本地日志'
  );
});

it('exports only a relative path and ignores a late response after dispose', async () => {
  const pending = deferred<ITrainingRecordExportResponse>();
  const deps = dependencies(createCapture(), [profile]);
  deps.exportTrainingRecord = jest.fn(() => pending.promise);
  const sidebar = new BehaviorAnalysisSidebar(deps);
  await flush();
  findButton(sidebar, '导出训练记录').click();
  expect(findButton(sidebar, '正在导出…').disabled).toBe(true);
  sidebar.dispose();
  pending.resolve({
    schema_version: 1,
    session_id: sessionLogSummary.session_id,
    relative_path: `sessions/${sessionLogSummary.session_id}/training_record.json`,
    generated_at: '2026-07-30T16:00:00+08:00',
    content_hash: 'a'.repeat(64),
    stale: false
  });
  await flush();
  expect(document.body.textContent).not.toContain('/Users/');
});
```

Extend the existing successful stop test with:

```typescript
const callsBeforeStop = deps.listSessionLogs.mock.calls.length;
findButton(sidebar, '停止监控').click();
await flush();
expect(deps.listSessionLogs.mock.calls.length).toBeGreaterThan(callsBeforeStop);
```

Extend the existing successful delete test in the same way, recording
`callsBeforeDelete`, completing the precise-ID deletion flow, and asserting
the call count is greater afterward. Add a refresh-button test that records
the selected profile value and `capture.isEnabled()` before clicking
`刷新日志`, awaits `flush()`, then asserts both values are unchanged.

Use this refresh assertion:

```typescript
const selectedBefore = sidebar.node.querySelector<HTMLSelectElement>(
  '#behavior-analysis-profile'
)!.value;
const activeBefore = capture.isEnabled();
findButton(sidebar, '刷新日志').click();
await flush();
expect(
  sidebar.node.querySelector<HTMLSelectElement>('#behavior-analysis-profile')!
    .value
).toBe(selectedBefore);
expect(capture.isEnabled()).toBe(activeBefore);
```

- [ ] **Step 3: Run focused command/sidebar tests**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test \
  src/__tests__/myextension.spec.ts \
  src/__tests__/behaviorAnalysisSidebar.spec.ts \
  --runInBand
```

Expected: failures report missing dependencies, command and UI.

- [ ] **Step 4: Implement one-widget command registration**

Create response-envelope schemas that require both `schema_version: 1` and a
canonical UUID `request_id`. Then add:

```typescript
export const OPEN_SESSION_LOG_COMMAND = 'myextension:open-session-log';

export function registerSessionLogCommand(
  app: JupyterFrontEnd,
  deps: ISessionLogViewerDependencies
): (sessionId: string) => Promise<void> {
  let viewer: SessionLogViewer | null = null;
  app.commands.addCommand(OPEN_SESSION_LOG_COMMAND, {
    label: '查看本地会话日志',
    execute: async args => {
      const sessionId = args['sessionId'];
      if (typeof sessionId !== 'string' || sessionId.length === 0) return;
      if (viewer === null || viewer.isDisposed) {
        viewer = new SessionLogViewer(deps);
        app.shell.add(viewer, 'main');
      }
      app.shell.activateById(viewer.id);
      await viewer.showSession(sessionId);
    }
  });
  return sessionId =>
    app.commands
      .execute(OPEN_SESSION_LOG_COMMAND, { sessionId })
      .then(() => undefined);
}
```

- [ ] **Step 5: Implement sidebar state and section**

Add private state:

```typescript
private sessionLogs: ILocalSessionLogSummary[] = [];
private sessionLogsStatus: 'loading' | 'ready' | 'error' = 'loading';
private sessionLogsGeneration = 0;
private trainingExportSessionId: string | null = null;
private trainingExportMessage = '';
```

Add `refreshSessionLogs()` with a generation guard and a fixed page size 20.
Call it once after construction, after successful stop/finalization and after
successful deletion.

Insert `localLogsSection()` after the current-session result section and before
`advancedSection()`. Each log row uses heading text, a `<time>` element,
Chinese status and explicit buttons. The source/session ID is not used as the
primary visible title, but is available in a collapsed technical detail.

Use this exact status mapping:

```typescript
const SESSION_LOG_STATUS: Record<string, string> = {
  collecting: '采集中',
  finalized: '采集完成',
  queued: '等待分析',
  running: '分析中',
  ready: '分析完成',
  partial: '分析完成（部分结果）',
  error: '分析失败'
};
```

When `analysis_status` is non-null it takes display precedence over session
status.

- [ ] **Step 6: Wire production API dependencies**

In `index.ts`:

1. call `registerSessionLogCommand()` before constructing the sidebar;
2. inject `getSessionLogDetail()` and `exportTrainingRecord()` into the
   viewer dependencies;
3. inject `listSessionLogs`, `exportTrainingRecord` and the returned
   `openSessionLog` into `sidebarDependencies`;
4. keep the existing “高级数据” open-file behavior unchanged.

- [ ] **Step 7: Add compact sidebar styles**

Use a vertical card list with no fixed pixel width:

```css
.jp-BehaviorAudit-sessionLogList {
  display: grid;
  gap: 8px;
}

.jp-BehaviorAudit-sessionLogItem {
  min-width: 0;
  padding: 8px;
  border: 1px solid var(--jp-border-color2);
  border-radius: var(--jp-border-radius);
}

.jp-BehaviorAudit-sessionLogActions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
```

Long titles wrap; technical IDs use `overflow-wrap: anywhere`. Do not rotate
or vertically stack Chinese headings.

- [ ] **Step 8: Run frontend regression**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:lib:prod
```

Expected: the complete Jest suite, lint and TypeScript production build pass.

- [ ] **Step 9: Record Task 7 checkpoint**

Record Jest count, lint and build results. Do not create a Git commit.

---

### Task 8: Documentation, full regression, package and installed smoke test

**Files:**

- Modify: `README.md`
- Modify: `项目说明.md`
- Modify: `启动说明.md`
- Verify: `dist/myextension-0.2.0-py3-none-any.whl`

**Interfaces:**

- Consumes all previous tasks.
- Produces reproducible deployment and user test instructions.

- [ ] **Step 1: Update user documentation**

Document this exact normal flow:

1. Select a published assessment plan.
2. Confirm collection and start monitoring.
3. Stop monitoring and wait for collection/analysis status.
4. Open “本地日志” in the left sidebar.
5. Select “查看日志” to inspect overview, captured source, AI analysis,
   behavior events and teacher reviews.
6. Select “导出训练记录” to create
   `sessions/<session_id>/training_record.json` on the Jupyter Server machine.

State clearly:

- source snapshots are historical captured source, not the current Notebook;
- an AI result is an auxiliary judgment, while teacher review is separate;
- no AI configuration still permits code/event viewing;
- relative export location is intentionally shown instead of an absolute path;
- real log directories must not be committed or shared casually;
- this remains a single-user Pilot.

- [ ] **Step 2: Run full backend tests**

Run:

```bash
.venv/bin/python -m pytest myextension/tests -q
```

Expected: all backend tests pass. If pytest-jupyter loopback binding is blocked
by the sandbox, rerun the same command with the existing approved local
loopback permission and record that environment requirement.

- [ ] **Step 3: Run full frontend quality commands**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:lib:prod
```

Expected: all commands pass.

- [ ] **Step 4: Build the wheel**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m build --wheel
```

Expected:

```text
dist/myextension-0.2.0-py3-none-any.whl
```

Verify the wheel contains the new Python module, response schemas and rebuilt
labextension:

```bash
.venv/bin/python -m zipfile -l \
  dist/myextension-0.2.0-py3-none-any.whl
```

- [ ] **Step 5: Install and verify extension registration**

Run:

```bash
.venv/bin/python -m pip install --force-reinstall \
  dist/myextension-0.2.0-py3-none-any.whl
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jupyter labextension list
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jupyter server extension list
```

Expected: `myextension` is enabled and OK in both frontend and server lists.

- [ ] **Step 6: Run a synthetic local smoke session**

Use a fresh temporary data root, never the user’s existing data:

```bash
SMOKE_DATA_ROOT="$(mktemp -d)"
JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR="$SMOKE_DATA_ROOT" \
  PATH="$PWD/.venv/bin:$PATH" \
  .venv/bin/jupyter lab --no-browser
```

In the browser:

1. create or select a synthetic assessment plan;
2. start monitoring;
3. enter and run a small synthetic function;
4. stop monitoring;
5. verify the new session appears under “本地日志”;
6. verify source and event tabs work before any AI result;
7. if the configured synthetic AI path is available, verify evidence
   navigation; otherwise verify “尚无 AI 结论”;
8. export a training record and inspect only the synthetic record;
9. confirm the record contains source, events and analysis/review fields and
   excludes absolute paths and credentials;
10. delete the synthetic session and confirm it leaves the list.

Stop the temporary server with Ctrl+C. Keep the temporary path for failure
diagnosis; remove it only after verification is recorded and only with an
explicit path check.

- [ ] **Step 7: Compute artifact hash and record final evidence**

Run:

```bash
shasum -a 256 dist/myextension-0.2.0-py3-none-any.whl
```

Record:

- exact frontend and backend pass counts;
- lint and build results;
- wheel path and SHA-256;
- frontend/server extension registration status;
- smoke-test results for list, source, AI empty/result, evidence navigation,
  export and delete;
- any unverified item or remaining Pilot limitation.

Do not claim completion if any required command or smoke step remains
unverified.

---

## Execution Stop Points

- Stop after Task 4 if backend contracts or path-safety tests are not green;
  do not build UI against an unstable API.
- Stop after Task 6 if untrusted source can create DOM elements or evidence
  navigation points to the wrong session.
- Stop before wheel installation if any frontend test, backend test, lint or
  production build fails.
- Stop after installed smoke testing and report evidence. Deployment to any
  shared JupyterHub, remote server or external training system is outside this
  plan and requires separate authorization.
