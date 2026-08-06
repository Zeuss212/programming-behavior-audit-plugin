from __future__ import annotations

import json
import os
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest

from myextension.analysis_job_store import (
    AnalysisJobConflictError,
    AnalysisJobIntegrityError,
    AnalysisJobStateError,
    AnalysisJobStore,
)
from myextension.analysis_worker import (
    AnalysisQueueFullError,
    AnalysisWorker,
    AnalysisWorkerStateError,
    compute_input_snapshot_hash,
)
from myextension.behavior_log_store import LOG_DIR_ENV_VAR
from myextension.canonical_json import canonical_json_bytes, sha256_json
from myextension.dimension_analyzer import analyze_session
from myextension.dimension_profile_store import DimensionProfileStore
from myextension.llm_transport import LlmTransportError
from myextension.review_store import ReviewConflictError, ReviewStore
from myextension.session_janitor import SessionJanitor
from myextension.session_store import SessionStore
from myextension.tests.test_dimension_analyzer import (
    events as analyzer_events,
    profile as analyzer_profile,
    truncated_provider_response,
)
from myextension.tests.test_assessment_profile import make_assessment_profile


SESSION_ID = "30000000-0000-4000-8000-000000000009"
JOB_KEYS = {
    "schema_version",
    "job_id",
    "session_id",
    "input_snapshot_hash",
    "status",
    "active_attempt_id",
    "attempt_ids",
    "analysis_id",
    "error_code",
    "created_at",
    "updated_at",
}


def session() -> dict[str, object]:
    return {
        "schema_version": 1,
        "session_id": SESSION_ID,
        "status": "finalized",
    }


def read_attempt(
    root: Path,
    job: dict[str, object],
    attempt: dict[str, object],
) -> dict[str, object]:
    path = (
        root
        / "jobs"
        / str(job["job_id"])
        / "attempts"
        / f"{attempt['attempt_id']}.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_same_idempotency_input_returns_same_job(tmp_path):
    store = AnalysisJobStore(tmp_path)
    first = store.create(
        session=session(), input_snapshot_hash="a" * 64
    )
    replay = store.create(
        session=session(), input_snapshot_hash="a" * 64
    )
    assert replay["job_id"] == first["job_id"]


def test_retry_appends_attempt_without_overwriting_first(tmp_path):
    store = AnalysisJobStore(tmp_path)
    job = store.create(
        session=session(), input_snapshot_hash="a" * 64
    )
    first = store.begin_attempt(job["job_id"])
    store.finish_attempt(
        job["job_id"],
        first["attempt_id"],
        status="error",
        analysis_id=None,
        error_code="model_timeout",
    )
    first_snapshot = read_attempt(tmp_path, job, first)
    store.retry(job["job_id"], reason="teacher_requested")
    second = store.begin_attempt(job["job_id"])
    assert first["attempt_id"] != second["attempt_id"]
    assert store.get(job["job_id"])["attempt_ids"] == [
        first["attempt_id"],
        second["attempt_id"],
    ]
    assert read_attempt(tmp_path, job, first) == first_snapshot


def test_retry_exact_replay_resumes_one_pending_retry_audit(tmp_path):
    store = AnalysisJobStore(tmp_path)
    job = store.create(
        session=session(), input_snapshot_hash="a" * 64
    )
    first = store.begin_attempt(job["job_id"])
    store.finish_attempt(
        job["job_id"],
        first["attempt_id"],
        status="error",
        analysis_id=None,
        error_code="model_timeout",
    )

    queued = store.retry(
        job["job_id"],
        reason="teacher_requested",
    )
    replay = store.retry(
        job["job_id"],
        reason="teacher_requested",
    )

    assert replay == queued
    audit_path = (
        tmp_path
        / "jobs"
        / str(job["job_id"])
        / "retry_history.jsonl"
    )
    assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 1
    with pytest.raises(AnalysisJobStateError):
        store.retry(job["job_id"], reason="different_reason")


def test_initial_queued_job_is_not_mistaken_for_pending_retry(tmp_path):
    store = AnalysisJobStore(tmp_path)
    job = store.create(
        session=session(), input_snapshot_hash="a" * 64
    )

    with pytest.raises(AnalysisJobStateError):
        store.retry(job["job_id"], reason="teacher_requested")


def test_recover_running_job_marks_attempt_interrupted_and_requeues(
    tmp_path,
):
    store = AnalysisJobStore(tmp_path)
    job = store.create(
        session=session(), input_snapshot_hash="a" * 64
    )
    attempt = store.begin_attempt(job["job_id"])

    assert store.recover_interrupted() == [job["job_id"]]

    updated = store.get(job["job_id"])
    assert updated["status"] == "queued"
    stored = read_attempt(tmp_path, job, attempt)
    assert stored["status"] == "error"
    assert stored["error_code"] == "interrupted_after_restart"


def test_create_normalizes_hash_and_rejects_conflicting_session_input(tmp_path):
    store = AnalysisJobStore(tmp_path)
    created = store.create(
        session=session(),
        input_snapshot_hash="A" * 64,
    )
    assert created["input_snapshot_hash"] == "a" * 64
    with pytest.raises(AnalysisJobConflictError):
        store.create(
            session=session(),
            input_snapshot_hash="b" * 64,
        )


@pytest.mark.parametrize(
    "value",
    ["../escape", "not-a-uuid", "30000000-0000-4000-8000-000000000009/"],
)
def test_get_rejects_unsafe_job_ids(tmp_path, value):
    with pytest.raises(ValueError):
        AnalysisJobStore(tmp_path).get(value)


def test_finish_is_idempotent_but_terminal_attempt_is_immutable(tmp_path):
    store = AnalysisJobStore(tmp_path)
    job = store.create(session=session(), input_snapshot_hash="a" * 64)
    attempt = store.begin_attempt(str(job["job_id"]))
    analysis_id = "70000000-0000-4000-8000-000000000009"
    first = store.finish_attempt(
        str(job["job_id"]),
        str(attempt["attempt_id"]),
        status="ready",
        analysis_id=analysis_id,
        error_code=None,
        prompt_snapshot_hash="b" * 64,
        raw_response_snapshot_hash="c" * 64,
    )
    replay = store.finish_attempt(
        str(job["job_id"]),
        str(attempt["attempt_id"]),
        status="ready",
        analysis_id=analysis_id,
        error_code=None,
        prompt_snapshot_hash="b" * 64,
        raw_response_snapshot_hash="c" * 64,
    )
    assert replay == first
    with pytest.raises(AnalysisJobStateError):
        store.finish_attempt(
            str(job["job_id"]),
            str(attempt["attempt_id"]),
            status="partial",
            analysis_id=analysis_id,
            error_code="different",
        )


def test_exact_public_keys_private_modes_and_identity_validation(tmp_path):
    store = AnalysisJobStore(tmp_path)
    job = store.create(session=session(), input_snapshot_hash="a" * 64)
    attempt = store.begin_attempt(str(job["job_id"]))
    job_path = tmp_path / "jobs" / str(job["job_id"]) / "job.json"
    attempt_path = (
        job_path.parent / "attempts" / f"{attempt['attempt_id']}.json"
    )
    assert set(store.get(str(job["job_id"]))) == JOB_KEYS
    assert stat.S_IMODE(job_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(attempt_path.stat().st_mode) == 0o600

    stored = json.loads(job_path.read_text(encoding="utf-8"))
    stored["job_id"] = str(UUID(int=10))
    job_path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(AnalysisJobIntegrityError):
        store.get(str(job["job_id"]))


def test_symlink_and_fifo_are_rejected_without_following(tmp_path):
    store = AnalysisJobStore(tmp_path)
    job = store.create(session=session(), input_snapshot_hash="a" * 64)
    job_path = tmp_path / "jobs" / str(job["job_id"]) / "job.json"
    target = tmp_path / "synthetic-target.json"
    target.write_text("{}", encoding="utf-8")
    job_path.unlink()
    job_path.symlink_to(target)
    with pytest.raises(AnalysisJobIntegrityError):
        store.get(str(job["job_id"]))

    fifo_id = "70000000-0000-4000-8000-000000000099"
    fifo_dir = tmp_path / "jobs" / fifo_id
    fifo_dir.mkdir(parents=True)
    os.mkfifo(fifo_dir / "job.json")
    with pytest.raises(AnalysisJobIntegrityError):
        store.get(fifo_id)


def test_concurrent_begin_has_exactly_one_winner(tmp_path):
    first = AnalysisJobStore(tmp_path)
    second = AnalysisJobStore(tmp_path)
    job = first.create(session=session(), input_snapshot_hash="a" * 64)
    barrier = threading.Barrier(2)
    winners: list[str] = []
    failures: list[type[BaseException]] = []

    def begin(store: AnalysisJobStore) -> None:
        barrier.wait()
        try:
            attempt = store.begin_attempt(str(job["job_id"]))
            winners.append(str(attempt["attempt_id"]))
        except BaseException as error:  # captured for deterministic assertion
            failures.append(type(error))

    threads = [
        threading.Thread(target=begin, args=(first,)),
        threading.Thread(target=begin, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(winners) == 1
    assert failures == [AnalysisJobStateError]


def test_recovery_fails_closed_on_inconsistent_running_projection(tmp_path):
    store = AnalysisJobStore(tmp_path)
    job = store.create(session=session(), input_snapshot_hash="a" * 64)
    attempt = store.begin_attempt(str(job["job_id"]))
    attempt_path = (
        tmp_path
        / "jobs"
        / str(job["job_id"])
        / "attempts"
        / f"{attempt['attempt_id']}.json"
    )
    broken = json.loads(attempt_path.read_text(encoding="utf-8"))
    broken["status"] = "error"
    broken["error_code"] = "synthetic"
    broken["finished_at"] = "2026-07-28T00:00:00+00:00"
    attempt_path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(AnalysisJobIntegrityError):
        store.recover_interrupted()


def finalized_session(
    tmp_path: Path,
    *,
    source_events: list[dict[str, object]] | None = None,
) -> tuple[SessionStore, dict[str, object]]:
    profile_value = analyzer_profile()
    content = {
        key: profile_value[key]
        for key in (
            "schema_version",
            "profile_id",
            "version",
            "problem_id",
            "title",
            "dimensions",
        )
    }
    profile_value["content_hash"] = sha256_json(content)
    session_store = SessionStore(tmp_path)
    started = session_store.start(
        problem_id=str(profile_value["problem_id"]),
        profile=profile_value,
    )
    raw_events = source_events or analyzer_events()
    session_id = str(started["session_id"])
    normalized: list[dict[str, object]] = []
    for index, event in enumerate(raw_events, start=1):
        copied = dict(event)
        copied["event_id"] = f"{session_id}:{index}"
        copied["session_seq"] = index
        normalized.append(copied)
    segment_id = "60000000-0000-4000-8000-000000000009"
    batch_hash = sha256_json(
        {
            "first_sequence": 1,
            "last_sequence": len(normalized),
            "segments": normalized,
        }
    )
    session_store.append_batch(
        session_id,
        segment_id=segment_id,
        first_sequence=1,
        last_sequence=len(normalized),
        content_hash=batch_hash,
        segments=normalized,
    )
    finalized = session_store.finalize(
        session_id,
        last_sequence=len(normalized),
    )
    return session_store, finalized


def provider_response(session_id: str) -> dict[str, object]:
    return {
        "model": "synthetic-model",
        "id": "synthetic-request",
        "dimensions": [
            {
                "dimension_code": "DEBUG_CHAIN",
                "evidence_status": "observed",
                "level_code": "possible",
                "confidence": 0.8,
                "evidence_claims": [
                    {
                        "event_id": f"{session_id}:1",
                        "criterion_id": "support-1",
                        "direction": "support",
                        "claim": "合成失败后出现编辑与验证链",
                    }
                ],
                "explanation": "仅基于合成事件。",
            },
            {
                "dimension_code": "REPEATED_RUN_FAILURES",
                "evidence_status": "not_observed",
                "level_code": None,
                "confidence": 0.7,
                "evidence_claims": [],
                "explanation": "合成记录中未重复失败。",
            },
        ],
    }


def create_worker_job(
    tmp_path: Path,
) -> tuple[SessionStore, AnalysisJobStore, dict[str, object]]:
    session_store, finalized = finalized_session(tmp_path)
    snapshot_hash = compute_input_snapshot_hash(
        session_store,
        str(finalized["session_id"]),
    )
    job_store = AnalysisJobStore(tmp_path)
    job = job_store.create(
        session=finalized,
        input_snapshot_hash=snapshot_hash,
    )
    return session_store, job_store, job


def test_worker_ready_persists_private_artifacts_and_closed_public_result(
    tmp_path,
):
    session_store, job_store, job = create_worker_job(tmp_path)
    session_id = str(job["session_id"])
    calls: list[tuple[dict[str, object], int]] = []

    def provider(
        request: dict[str, object],
        *,
        timeout_sec: int,
    ) -> dict[str, object]:
        calls.append((request, timeout_sec))
        return provider_response(session_id)

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))
    updated = job_store.get(str(job["job_id"]))
    assert updated["status"] == "ready"
    assert calls and {timeout for _, timeout in calls} == {90}

    analysis_id = str(updated["analysis_id"])
    result_path = tmp_path / "analyses" / analysis_id / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert set(result) == {
        "schema_version",
        "analysis_id",
        "job_id",
        "attempt_id",
        "session_id",
        "profile_id",
        "profile_version",
        "profile_content_hash",
        "status",
        "dimension_results",
        "provenance",
    }
    assert not {
        "prompt_snapshot",
        "attempt_diagnostics",
        "error_code",
        "api_key",
        "Authorization",
    } & set(result)

    attempt_id = str(updated["active_attempt_id"])
    attempt_root = (
        tmp_path / "jobs" / str(job["job_id"]) / "attempts"
    )
    prompt_path = attempt_root / f"{attempt_id}.prompt.json"
    raw_path = attempt_root / f"{attempt_id}.raw_response.json"
    for path in (prompt_path, raw_path, result_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "prompt_snapshot" not in json.dumps(updated)
    assert "dimensions" in json.loads(raw_path.read_text())["responses"][0]
    worker.shutdown()


def test_worker_recovers_truncated_response_in_same_attempt(tmp_path):
    session_store, job_store, job = create_worker_job(tmp_path)
    requests: list[dict[str, object]] = []
    waits: list[float] = []

    def provider(request, *, timeout_sec):
        requests.append(dict(request))
        assert timeout_sec == 90
        if len(requests) == 1:
            return truncated_provider_response()
        return provider_response(str(job["session_id"]))

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        wait=waits.append,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))

    updated = job_store.get(str(job["job_id"]))
    assert updated["status"] == "ready"
    assert len(updated["attempt_ids"]) == 1
    assert waits == []
    assert [request["max_tokens"] for request in requests] == [
        8192,
        16384,
    ]

    attempt_id = str(updated["active_attempt_id"])
    raw_path = (
        tmp_path
        / "jobs"
        / str(job["job_id"])
        / "attempts"
        / f"{attempt_id}.raw_response.json"
    )
    assert len(json.loads(raw_path.read_text())["responses"]) == 2

    result_path = (
        tmp_path
        / "analyses"
        / str(updated["analysis_id"])
        / "result.json"
    )
    analysis = json.loads(result_path.read_text())
    assert analysis["dimension_results"][0]["ai_result"][
        "evidence_claims"
    ]
    worker.shutdown()


def test_worker_notifies_terminal_callback_after_ready_commit(tmp_path):
    session_store, job_store, job = create_worker_job(tmp_path)
    session_id = str(job["session_id"])
    observed: list[tuple[str, object]] = []

    def callback(value):
        observed.append(
            (value, job_store.get(str(job["job_id"]))["status"])
        )

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=lambda request, *, timeout_sec: provider_response(
            session_id
        ),
        terminal_callback=callback,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))

    assert observed == [(session_id, "ready")]
    worker.shutdown()


def test_worker_notifies_terminal_callback_after_partial_commit(tmp_path):
    session_store, job_store, job = create_worker_job(tmp_path)
    session_id = str(job["session_id"])
    observed: list[tuple[str, object]] = []

    def provider(request, *, timeout_sec):
        raise LlmTransportError("provider_http_error", http_status=400)

    def callback(value):
        observed.append(
            (value, job_store.get(str(job["job_id"]))["status"])
        )

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        terminal_callback=callback,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))

    assert observed == [(session_id, "partial")]
    worker.shutdown()


@pytest.mark.parametrize("expected_status", ["ready", "partial"])
def test_worker_callback_failure_preserves_terminal_state(
    tmp_path,
    expected_status,
):
    session_store, job_store, job = create_worker_job(tmp_path)
    session_id = str(job["session_id"])

    def provider(request, *, timeout_sec):
        if expected_status == "partial":
            raise LlmTransportError("provider_http_error", http_status=400)
        return provider_response(session_id)

    def callback(value):
        raise RuntimeError("/private/synthetic-secret-path")

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        terminal_callback=callback,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))

    assert job_store.get(str(job["job_id"]))["status"] == expected_status
    worker.shutdown()


def test_worker_accepts_v2_without_sending_or_executing_assessment_tests(
    tmp_path,
):
    private_test_marker = "ASSESSMENT_TEST_MUST_STAY_OUT_OF_PROMPT"
    draft_payload = make_assessment_profile()
    draft_payload["dimensions"][0]["code"] = "DEBUG_CHAIN"
    draft_payload["assessment_tests"][0]["expected"] = private_test_marker
    draft_payload["confirmations"]["tests_hash"] = sha256_json(
        {
            "problem_context": draft_payload["problem_context"],
            "knowledge_points_hash": draft_payload["confirmations"][
                "knowledge_points_hash"
            ],
            "assessment_tests": draft_payload["assessment_tests"],
        }
    )
    profile_store = DimensionProfileStore(tmp_path)
    draft = profile_store.create_draft(draft_payload)
    published = profile_store.publish(str(draft["profile_id"]))

    session_store = SessionStore(tmp_path)
    started = session_store.start(
        problem_id=str(published["problem_id"]),
        profile=published,
    )
    session_id = str(started["session_id"])
    normalized_events = []
    for index, source_event in enumerate(analyzer_events(), start=1):
        copied = dict(source_event)
        copied["event_id"] = f"{session_id}:{index}"
        copied["session_seq"] = index
        normalized_events.append(copied)
    session_store.append_batch(
        session_id,
        segment_id="60000000-0000-4000-8000-000000000010",
        first_sequence=1,
        last_sequence=len(normalized_events),
        content_hash=sha256_json(
            {
                "first_sequence": 1,
                "last_sequence": len(normalized_events),
                "segments": normalized_events,
            }
        ),
        segments=normalized_events,
    )
    finalized = session_store.finalize(
        session_id,
        last_sequence=len(normalized_events),
    )
    job_store = AnalysisJobStore(tmp_path)
    job = job_store.create(
        session=finalized,
        input_snapshot_hash=compute_input_snapshot_hash(
            session_store,
            session_id,
        ),
    )

    def provider(
        request: dict[str, object],
        *,
        timeout_sec: int,
    ) -> dict[str, object]:
        assert private_test_marker not in json.dumps(request)
        return {
            "model": "synthetic-model",
            "id": "synthetic-request",
            "dimensions": [provider_response(session_id)["dimensions"][0]],
        }

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))

    completed = job_store.get(str(job["job_id"]))
    assert completed["status"] == "ready"
    analysis_id = str(completed["analysis_id"])
    public_result = (
        tmp_path / "analyses" / analysis_id / "result.json"
    ).read_text(encoding="utf-8")
    assert private_test_marker not in public_result
    assert '"dimension_code":"DEBUG_CHAIN"' in public_result
    worker.shutdown()


def test_persisted_prompt_snapshot_excludes_synthetic_private_and_instruction_markers(
    tmp_path,
):
    source_events = analyzer_events()
    source_events[0]["notebook_path"] = (
        "/Users/synthetic-learner/private-course/answer.ipynb"
    )
    source_events[0]["error_message"] = (
        "synthetic failure; student_name=Synthetic Learner 731; "
        "api_key=test-key-synthetic-only-731"
    )
    session_store, finalized = finalized_session(
        tmp_path,
        source_events=source_events,
    )
    session_id = str(finalized["session_id"])
    job_store = AnalysisJobStore(tmp_path)
    job = job_store.create(
        session=finalized,
        input_snapshot_hash=compute_input_snapshot_hash(
            session_store,
            session_id,
        ),
    )
    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=lambda request, *, timeout_sec: provider_response(
            session_id
        ),
        synchronous=True,
    )

    worker.enqueue(str(job["job_id"]))

    completed = job_store.get(str(job["job_id"]))
    prompt_path = (
        tmp_path
        / "jobs"
        / str(job["job_id"])
        / "attempts"
        / f"{completed['active_attempt_id']}.prompt.json"
    )
    persisted_prompt = prompt_path.read_text(encoding="utf-8")
    for forbidden in (
        "/Users/synthetic-learner/private-course/answer.ipynb",
        "Synthetic Learner 731",
        "ignore previous instructions",
        "test-key-synthetic-only-731",
    ):
        assert forbidden not in persisted_prompt
    worker.shutdown()


def test_injected_worker_provider_never_loads_local_ai_config(
    tmp_path,
    monkeypatch,
):
    session_store, job_store, job = create_worker_job(tmp_path)
    monkeypatch.setattr(
        "myextension.llm_transport._read_ai_config",
        lambda: pytest.fail("injected calls must not read local AI config"),
    )
    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=lambda request, *, timeout_sec: provider_response(
            str(job["session_id"])
        ),
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))
    assert job_store.get(str(job["job_id"]))["status"] == "ready"


def test_input_hash_and_worker_are_stable_after_job_is_attached(tmp_path):
    session_store, finalized = finalized_session(tmp_path)
    session_id = str(finalized["session_id"])
    before = compute_input_snapshot_hash(session_store, session_id)
    job_store = AnalysisJobStore(tmp_path)
    job = job_store.create(
        session=finalized,
        input_snapshot_hash=before,
    )
    attached = session_store.attach_job(
        session_id,
        str(job["job_id"]),
    )
    assert attached["analysis_job_id"] == job["job_id"]
    assert compute_input_snapshot_hash(session_store, session_id) == before

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=lambda request, *, timeout_sec: provider_response(
            session_id
        ),
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))
    assert job_store.get(str(job["job_id"]))["status"] == "ready"


def test_input_snapshot_reads_all_four_sources_under_one_session_lock(
    tmp_path,
):
    class LockProbeStore:
        def __init__(self):
            self.depth = 0
            self.calls: list[str] = []

        @contextmanager
        def _lock_for(self, session_id):
            self.depth += 1
            try:
                yield
            finally:
                self.depth -= 1

        def _assert_locked(self, name):
            assert self.depth > 0
            self.calls.append(name)

        def read(self, session_id):
            self._assert_locked("session")
            return {"session_id": session_id, "status": "finalized"}

        def _session_dir(self, session_id):
            self._assert_locked("profile_path")
            return tmp_path / "sessions" / session_id

        def _read_json(self, path):
            self._assert_locked("profile")
            return {
                "profile_id":
                    "40000000-0000-4000-8000-000000000009"
            }

        def read_events(self, session_id):
            self._assert_locked("events")
            return []

        def read_signal_dictionary(self, session_id):
            self._assert_locked("dictionary")
            return {"version": "pilot-v1"}

    probe = LockProbeStore()
    digest = compute_input_snapshot_hash(  # type: ignore[arg-type]
        probe,
        SESSION_ID,
    )
    assert len(digest) == 64
    assert probe.depth == 0
    assert probe.calls == [
        "session",
        "profile_path",
        "profile",
        "events",
        "dictionary",
    ]


def test_worker_ai_not_configured_is_partial_with_no_private_leak(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    session_store, job_store, job = create_worker_job(tmp_path)
    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))
    updated = job_store.get(str(job["job_id"]))
    assert updated["status"] == "partial"
    assert updated["error_code"] == "ai_not_configured"
    result = json.loads(
        (
            tmp_path
            / "analyses"
            / str(updated["analysis_id"])
            / "result.json"
        ).read_text()
    )
    assert result["status"] == "partial"
    assert "error_code" not in result
    assert all(
        row["decision"]["final_level_code"] is None
        for row in result["dimension_results"]
    )


@pytest.mark.parametrize(
    ("failure", "expected_delays"),
    [
        (LlmTransportError("provider_network_error"), [2.0, 8.0]),
        (LlmTransportError("provider_timeout"), [2.0, 8.0]),
        (LlmTransportError("provider_http_error", http_status=429), [2.0, 8.0]),
        (LlmTransportError("provider_http_error", http_status=503), [2.0, 8.0]),
    ],
)
def test_worker_retries_transient_provider_calls_only(
    tmp_path,
    failure,
    expected_delays,
):
    session_store, job_store, job = create_worker_job(tmp_path)
    calls = 0
    waits: list[float] = []

    def provider(request, *, timeout_sec):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise failure
        return provider_response(str(job["session_id"]))

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        wait=waits.append,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))
    assert calls == 3
    assert waits == expected_delays
    assert job_store.get(str(job["job_id"]))["status"] == "ready"


@pytest.mark.parametrize("status", [400, 401, 403])
def test_worker_does_not_retry_nonretryable_http_status(
    tmp_path,
    status,
):
    session_store, job_store, job = create_worker_job(tmp_path)
    calls = 0
    waits: list[float] = []

    def provider(request, *, timeout_sec):
        nonlocal calls
        calls += 1
        raise LlmTransportError(
            "provider_http_error",
            http_status=status,
        )

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        wait=waits.append,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))
    assert calls == 1
    assert waits == []
    assert job_store.get(str(job["job_id"]))["status"] == "partial"


def test_one_logical_repair_keeps_independent_transport_retry_budgets(
    tmp_path,
):
    session_store, job_store, job = create_worker_job(tmp_path)
    complete = provider_response(str(job["session_id"]))
    initial = {
        "model": "synthetic-model",
        "dimensions": [complete["dimensions"][0]],  # type: ignore[index]
    }
    repair = {
        "model": "synthetic-model",
        "dimensions": [complete["dimensions"][1]],  # type: ignore[index]
    }
    calls = 0
    waits: list[float] = []

    def provider(request, *, timeout_sec):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LlmTransportError("provider_network_error")
        if calls == 2:
            return initial
        if calls in {3, 4}:
            raise LlmTransportError("provider_timeout")
        return repair

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        wait=waits.append,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))
    updated = job_store.get(str(job["job_id"]))
    assert updated["status"] == "ready"
    assert calls == 5
    assert waits == [2.0, 2.0, 8.0]
    raw_path = (
        tmp_path
        / "jobs"
        / str(job["job_id"])
        / "attempts"
        / f"{updated['active_attempt_id']}.raw_response.json"
    )
    recorded = json.loads(raw_path.read_text())["responses"]
    assert recorded == [initial, repair]


def test_invalid_model_output_gets_one_repair_but_no_transport_retry(
    tmp_path,
):
    session_store, job_store, job = create_worker_job(tmp_path)
    calls = 0
    waits: list[float] = []

    def provider(request, *, timeout_sec):
        nonlocal calls
        calls += 1
        return {"dimensions": []}

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        wait=waits.append,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))
    assert calls == 2
    assert waits == []
    assert job_store.get(str(job["job_id"]))["status"] == "partial"


def test_duplicate_simultaneous_enqueue_runs_only_one_attempt(tmp_path):
    session_store, job_store, job = create_worker_job(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def provider(request, *, timeout_sec):
        entered.set()
        assert release.wait(timeout=2)
        return provider_response(str(job["session_id"]))

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
    )
    worker.enqueue(str(job["job_id"]))
    assert entered.wait(timeout=2)
    worker.enqueue(str(job["job_id"]))
    release.set()
    worker._queue.join()
    assert len(job_store.get(str(job["job_id"]))["attempt_ids"]) == 1
    worker.shutdown()
    worker.shutdown()
    with pytest.raises(AnalysisWorkerStateError):
        worker.enqueue(str(job["job_id"]))


def test_worker_queue_is_bounded_to_one_hundred(tmp_path):
    class QueuedJobs:
        @staticmethod
        def get(job_id):
            return {"status": "queued"}

    worker = AnalysisWorker(
        tmp_path,
        job_store=QueuedJobs(),  # type: ignore[arg-type]
        session_store=FakeSessionStore(),  # type: ignore[arg-type]
        synchronous=True,
    )
    worker._synchronous = False
    ids = [str(UUID(int=index + 1)) for index in range(101)]
    for job_id in ids[:100]:
        worker.enqueue(job_id)
    with pytest.raises(AnalysisQueueFullError):
        worker.enqueue(ids[100])
    assert worker._queue.qsize() == 100
    worker.shutdown()


def test_worker_input_mismatch_becomes_stable_error_without_exception_text(
    tmp_path,
):
    session_store, finalized = finalized_session(tmp_path)
    store = AnalysisJobStore(tmp_path)
    job = store.create(
        session=finalized,
        input_snapshot_hash="a" * 64,
    )
    observed: list[str] = []
    worker = AnalysisWorker(
        tmp_path,
        job_store=store,
        session_store=session_store,
        provider_client=lambda *args, **kwargs: pytest.fail(
            "provider must not be called"
        ),
        terminal_callback=observed.append,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))
    updated = store.get(str(job["job_id"]))
    assert updated["status"] == "error"
    assert updated["error_code"] == "input_snapshot_mismatch"
    assert "mismatch" not in json.dumps(updated).replace(
        "input_snapshot_mismatch", ""
    )
    assert observed == []


def correction(*, revision: int = 0) -> dict[str, object]:
    return {
        "revision": revision,
        "decision_status": "resolved",
        "evidence_status": "observed",
        "level_code": "possible",
        "evidence_event_ids": [f"{SESSION_ID}:3"],
        "reason_code": "teacher_correction",
        "comment": "该次修改属于有效调试",
    }


def test_review_history_is_append_only_revisioned_and_private(tmp_path):
    analysis_id = "70000000-0000-4000-8000-000000000009"
    store = ReviewStore(tmp_path)
    first = store.append(
        analysis_id,
        "DEBUG_CHAIN",
        expected_revision=0,
        correction=correction(),
    )
    assert first["revision"] == 1
    with pytest.raises(ReviewConflictError):
        store.append(
            analysis_id,
            "DEBUG_CHAIN",
            expected_revision=0,
            correction=correction(),
        )
    second_correction = correction(revision=1)
    second_correction["comment"] = "复核后保持原判断"
    second = store.append(
        analysis_id,
        "DEBUG_CHAIN",
        expected_revision=1,
        correction=second_correction,
    )
    assert second["revision"] == 2
    assert store.list(analysis_id, "DEBUG_CHAIN") == [first, second]
    path = (
        tmp_path
        / "analyses"
        / analysis_id
        / "review_history.jsonl"
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(path.read_bytes().splitlines()) == 2


def test_concurrent_review_compare_and_append_has_one_winner(tmp_path):
    analysis_id = "70000000-0000-4000-8000-000000000010"
    stores = [ReviewStore(tmp_path), ReviewStore(tmp_path)]
    barrier = threading.Barrier(2)
    winners: list[int] = []
    conflicts: list[type[BaseException]] = []

    def append(store: ReviewStore) -> None:
        barrier.wait()
        try:
            winners.append(
                int(
                    store.append(
                        analysis_id,
                        "DEBUG_CHAIN",
                        expected_revision=0,
                        correction=correction(),
                    )["revision"]
                )
            )
        except BaseException as error:
            conflicts.append(type(error))

    threads = [
        threading.Thread(target=append, args=(store,))
        for store in stores
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert winners == [1]
    assert conflicts == [ReviewConflictError]


def test_review_rejects_corrupt_tail_symlink_and_unsafe_dimension(tmp_path):
    analysis_id = "70000000-0000-4000-8000-000000000011"
    store = ReviewStore(tmp_path)
    store.append(
        analysis_id,
        "DEBUG_CHAIN",
        expected_revision=0,
        correction=correction(),
    )
    path = (
        tmp_path
        / "analyses"
        / analysis_id
        / "review_history.jsonl"
    )
    with path.open("ab") as handle:
        handle.write(b'{"incomplete":')
    with pytest.raises(Exception):
        store.list(analysis_id, "DEBUG_CHAIN")
    path.unlink()
    target = tmp_path / "synthetic-review.jsonl"
    target.write_text("", encoding="utf-8")
    path.symlink_to(target)
    with pytest.raises(Exception):
        store.list(analysis_id, "DEBUG_CHAIN")
    with pytest.raises(ValueError):
        store.list(analysis_id, "../escape")


class FakeSessionStore:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def abandon_stale(self, *, now, timeout):
        self.calls.append((now, timeout))
        return ["30000000-0000-4000-8000-000000000099"]


def test_janitor_runs_once_on_start_and_is_idempotent():
    from datetime import datetime, timedelta, timezone

    fake = FakeSessionStore()
    fixed = datetime(2026, 7, 28, tzinfo=timezone.utc)
    janitor = SessionJanitor(
        fake,  # type: ignore[arg-type]
        interval_seconds=60,
        timeout=timedelta(minutes=30),
        now=lambda: fixed,
    )
    assert janitor.run_once() == [
        "30000000-0000-4000-8000-000000000099"
    ]
    janitor.start()
    janitor.start()
    janitor.shutdown()
    janitor.shutdown()
    assert len(fake.calls) == 2
    assert all(call[0].tzinfo is not None for call in fake.calls)


def test_extension_lifecycle_reuses_services_and_enqueues_recovery_once(
    tmp_path,
    monkeypatch,
):
    import myextension

    recovered_id = "70000000-0000-4000-8000-000000000099"

    class FakeJobStore:
        def __init__(self):
            self.recover_calls = 0

        def recover_interrupted(self):
            self.recover_calls += 1
            return [recovered_id]

        def list_queued(self):
            return [recovered_id]

    class FakeWorker:
        def __init__(self, job_store):
            self.job_store = job_store
            self.session_store = FakeSessionStore()
            self.enqueued: list[str] = []
            self.shutdown_calls = 0

        def enqueue(self, job_id):
            self.enqueued.append(job_id)

        def shutdown(self):
            self.shutdown_calls += 1

    class FakeJanitor:
        def __init__(self):
            self.start_calls = 0
            self.shutdown_calls = 0

        def start(self):
            self.start_calls += 1

        def shutdown(self):
            self.shutdown_calls += 1

    class FakeWebApp:
        def __init__(self, settings):
            self.settings = settings

    class FakeLog:
        def info(self, message):
            return None

    class FakeServer:
        def __init__(self, settings):
            self.web_app = FakeWebApp(settings)
            self.log = FakeLog()

    job_store = FakeJobStore()
    worker = FakeWorker(job_store)
    janitor = FakeJanitor()
    settings = {
        "base_url": "/",
        "myextension_analysis_job_store": job_store,
        "myextension_analysis_worker": worker,
        "myextension_session_janitor": janitor,
    }
    registrations: list[object] = []
    monkeypatch.setattr(myextension, "setup_route_handlers", lambda app: None)
    monkeypatch.setattr(
        myextension,
        "resolve_log_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        myextension.atexit,
        "register",
        registrations.append,
    )
    monkeypatch.setattr(
        myextension.atexit,
        "unregister",
        lambda callback: (
            registrations.remove(callback)
            if callback in registrations
            else None
        ),
    )

    server = FakeServer(settings)
    myextension._load_jupyter_server_extension(server)
    myextension._load_jupyter_server_extension(server)

    assert job_store.recover_calls == 1
    assert worker.enqueued == [recovered_id]
    assert janitor.start_calls == 1
    assert registrations == [worker.shutdown, janitor.shutdown]
    assert settings["myextension_analysis_worker"] is worker
    assert settings["myextension_session_janitor"] is janitor
    assert settings["myextension_analysis_job_store"] is job_store


@pytest.mark.parametrize("transition", ["begin", "finish", "retry"])
def test_idempotent_create_waits_for_each_job_publication_window(
    tmp_path,
    transition,
):
    primary = AnalysisJobStore(tmp_path)
    observer = AnalysisJobStore(tmp_path)
    job = primary.create(
        session=session(),
        input_snapshot_hash="a" * 64,
    )
    attempt = None
    if transition in {"finish", "retry"}:
        attempt = primary.begin_attempt(str(job["job_id"]))
    if transition == "retry":
        primary.finish_attempt(
            str(job["job_id"]),
            str(attempt["attempt_id"]),  # type: ignore[index]
            status="error",
            analysis_id=None,
            error_code="model_timeout",
        )

    window_open = threading.Event()
    release = threading.Event()
    original_write = primary._write_json

    def pausing_write(path, value):
        original_write(path, value)
        should_pause = (
            transition == "begin"
            and path.name.endswith(".json")
            and path.parent.name == "attempts"
            or transition == "finish"
            and path.name == f"{attempt['attempt_id']}.json"  # type: ignore[index]
            and value.get("status") == "ready"
            or transition == "retry"
            and path.name == "job.json"
            and value.get("status") == "queued"
        )
        if should_pause:
            window_open.set()
            assert release.wait(timeout=2)

    primary._write_json = pausing_write  # type: ignore[method-assign]
    mutation_errors: list[BaseException] = []

    def mutate():
        try:
            if transition == "begin":
                primary.begin_attempt(str(job["job_id"]))
            elif transition == "finish":
                primary.finish_attempt(
                    str(job["job_id"]),
                    str(attempt["attempt_id"]),  # type: ignore[index]
                    status="ready",
                    analysis_id=(
                        "70000000-0000-4000-8000-000000000019"
                    ),
                    error_code=None,
                )
            else:
                primary.retry(
                    str(job["job_id"]),
                    reason="teacher_requested",
                )
        except BaseException as error:
            mutation_errors.append(error)

    mutation = threading.Thread(target=mutate)
    mutation.start()
    assert window_open.wait(timeout=2)
    observed: list[dict[str, object]] = []
    observation_errors: list[BaseException] = []

    def observe():
        try:
            observed.append(
                observer.create(
                    session=session(),
                    input_snapshot_hash="a" * 64,
                )
            )
        except BaseException as error:
            observation_errors.append(error)

    observation = threading.Thread(target=observe)
    observation.start()
    observation.join(timeout=0.05)
    was_blocked = observation.is_alive()
    release.set()
    mutation.join(timeout=2)
    observation.join(timeout=2)
    assert mutation_errors == []
    assert was_blocked
    assert observation_errors == []
    assert observed[0]["job_id"] == job["job_id"]


def trusted_analysis_context(tmp_path):
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
    attempt = job_store.begin_attempt(str(job["job_id"]))
    session_value = session_store.read(session_id)
    profile_value = session_store._read_json(
        session_store._session_dir(session_id) / "profile.json"
    )
    events_value = session_store.read_events(session_id)
    dictionary_value = session_store.read_signal_dictionary(session_id)
    analysis = analyze_session(
        job_id=str(job["job_id"]),
        attempt_id=str(attempt["attempt_id"]),
        session=session_value,
        profile=profile_value,
        events=events_value,
        signal_dictionary=dictionary_value,
        client=lambda request: provider_response(session_id),
    )
    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=lambda request, *, timeout_sec: provider_response(
            session_id
        ),
        synchronous=True,
    )
    return (
        worker,
        job,
        attempt,
        session_value,
        profile_value,
        dictionary_value,
        analysis,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_provenance",
        "nested_private",
        "wrong_profile",
        "missing_dimension",
        "wrong_dictionary",
        "wrong_analysis_id",
    ],
)
def test_public_result_is_deeply_closed_and_bound_to_trusted_inputs(
    tmp_path,
    mutation,
):
    (
        worker,
        job,
        attempt,
        session_value,
        profile_value,
        dictionary_value,
        analysis,
    ) = trusted_analysis_context(tmp_path)
    mutated = json.loads(json.dumps(analysis))
    if mutation == "extra_provenance":
        mutated["provenance"]["raw_provider_body"] = "synthetic-secret"
    elif mutation == "nested_private":
        mutated["provenance"]["attempt_diagnostics"] = {
            "prompt_snapshot": "synthetic-secret"
        }
    elif mutation == "wrong_profile":
        mutated["profile_id"] = (
            "40000000-0000-4000-8000-000000000099"
        )
    elif mutation == "missing_dimension":
        mutated["dimension_results"].pop()
    elif mutation == "wrong_dictionary":
        mutated["provenance"]["signal_dictionary_hash"] = "f" * 64
    else:
        mutated["analysis_id"] = (
            "70000000-0000-4000-8000-000000000099"
        )
    with pytest.raises(AnalysisJobIntegrityError):
        worker._public_result(
            mutated,
            job=job,
            attempt=attempt,
            session=session_value,
            profile=profile_value,
            signal_dictionary=dictionary_value,
        )


def test_immutable_json_create_once_replays_exact_content_without_replace(
    tmp_path,
):
    worker = AnalysisWorker(tmp_path, synchronous=True)
    path = tmp_path / "jobs" / str(UUID(int=50)) / "immutable.json"
    value = {"schema_version": 1, "responses": []}
    first_hash = worker._write_private_json(path, value)
    first_inode = path.stat().st_ino
    second_hash = worker._write_private_json(path, value)
    assert second_hash == first_hash
    assert path.stat().st_ino == first_inode


@pytest.mark.parametrize(
    "existing",
    [
        {"schema_version": 1, "responses": [{"different": True}]},
        {"schema_version": 1, "analysis_id": str(UUID(int=99))},
    ],
)
def test_immutable_json_rejects_mismatched_existing_artifact_or_result(
    tmp_path,
    existing,
):
    worker = AnalysisWorker(tmp_path, synchronous=True)
    path = tmp_path / "analyses" / str(UUID(int=51)) / "result.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(existing, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    before = path.read_bytes()
    with pytest.raises(AnalysisJobIntegrityError):
        worker._write_private_json(
            path,
            {"schema_version": 1, "expected": True},
        )
    assert path.read_bytes() == before


def test_immutable_json_rejects_noncanonical_semantic_replay(tmp_path):
    worker = AnalysisWorker(tmp_path, synchronous=True)
    path = tmp_path / "jobs" / str(UUID(int=52)) / "artifact.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version": 1, "responses": []}', encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(AnalysisJobIntegrityError):
        worker._write_private_json(
            path,
            {"schema_version": 1, "responses": []},
        )


def test_janitor_failed_first_pass_can_be_started_again():
    from datetime import datetime, timezone

    class FailOnceStore:
        def __init__(self):
            self.calls = 0

        def abandon_stale(self, *, now, timeout):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic janitor failure")
            return []

    store = FailOnceStore()
    janitor = SessionJanitor(
        store,  # type: ignore[arg-type]
        now=lambda: datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    with pytest.raises(RuntimeError):
        janitor.start()
    assert janitor._started is False
    assert janitor._thread is None
    janitor.start()
    janitor.shutdown()
    assert store.calls == 2


@pytest.mark.parametrize(
    "failure_stage",
    ["recover", "enqueue", "janitor", "worker_start"],
)
def test_extension_startup_failure_rolls_back_only_new_services(
    tmp_path,
    monkeypatch,
    failure_stage,
):
    import myextension

    queued_id = "70000000-0000-4000-8000-000000000088"
    registrations: list[object] = []

    class FakeJobStore:
        def recover_interrupted(self):
            if failure_stage == "recover":
                raise RuntimeError("synthetic recovery failure")
            return []

        def list_queued(self):
            return [queued_id]

    class FakeWorker:
        instances: list["FakeWorker"] = []

        def __init__(
            self,
            root,
            *,
            job_store,
            session_store,
            terminal_callback=None,
            autostart=True,
        ):
            self.job_store = job_store
            self.session_store = session_store
            self.terminal_callback = terminal_callback
            self.autostart = autostart
            self.start_calls = 0
            self.shutdown_calls = 0
            self.enqueued: list[str] = []
            self.instances.append(self)

        def enqueue(self, job_id):
            if failure_stage == "enqueue":
                raise RuntimeError("synthetic enqueue failure")
            self.enqueued.append(job_id)

        def start(self):
            self.start_calls += 1
            if failure_stage == "worker_start":
                raise RuntimeError("synthetic worker-start failure")

        def shutdown(self):
            self.shutdown_calls += 1

    class FakeJanitor:
        instances: list["FakeJanitor"] = []

        def __init__(self, session_store):
            self.shutdown_calls = 0
            self.start_calls = 0
            self.instances.append(self)

        def start(self):
            self.start_calls += 1
            if failure_stage == "janitor":
                raise RuntimeError("synthetic janitor failure")

        def shutdown(self):
            self.shutdown_calls += 1

    class FakeWebApp:
        def __init__(self):
            self.settings = {"base_url": "/"}

    class FakeServer:
        web_app = FakeWebApp()
        log = type("Log", (), {"info": lambda self, message: None})()

    monkeypatch.setattr(myextension, "setup_route_handlers", lambda app: None)
    monkeypatch.setattr(myextension, "resolve_log_root", lambda: tmp_path)
    monkeypatch.setattr(myextension, "AnalysisJobStore", lambda root: FakeJobStore())
    monkeypatch.setattr(myextension, "AnalysisWorker", FakeWorker)
    monkeypatch.setattr(myextension, "SessionJanitor", FakeJanitor)
    monkeypatch.setattr(
        myextension.atexit,
        "register",
        registrations.append,
    )
    monkeypatch.setattr(
        myextension.atexit,
        "unregister",
        lambda callback: (
            registrations.remove(callback)
            if callback in registrations
            else None
        ),
    )

    server = FakeServer()
    with pytest.raises(RuntimeError):
        myextension._load_jupyter_server_extension(server)
    assert "myextension_analysis_worker" not in server.web_app.settings
    assert "myextension_session_janitor" not in server.web_app.settings
    assert "myextension_analysis_job_store" not in server.web_app.settings
    assert not server.web_app.settings.get(
        "myextension_background_services_started"
    )
    assert registrations == []
    assert FakeWorker.instances[0].shutdown_calls == 1
    assert FakeWorker.instances[0].autostart is False
    assert callable(FakeWorker.instances[0].terminal_callback)
    assert FakeJanitor.instances[0].shutdown_calls == 1


def test_extension_does_not_shutdown_injected_services_on_startup_failure(
    tmp_path,
    monkeypatch,
):
    import myextension

    class ExistingJobStore:
        def recover_interrupted(self):
            raise RuntimeError("synthetic recovery failure")

        def list_queued(self):
            return []

    class ExistingWorker:
        def __init__(self, job_store):
            self.job_store = job_store
            self.session_store = FakeSessionStore()
            self.shutdown_calls = 0

        def shutdown(self):
            self.shutdown_calls += 1

    class ExistingJanitor:
        def __init__(self):
            self.shutdown_calls = 0

        def start(self):
            return None

        def shutdown(self):
            self.shutdown_calls += 1

    job_store = ExistingJobStore()
    worker = ExistingWorker(job_store)
    janitor = ExistingJanitor()
    settings = {
        "base_url": "/",
        "myextension_analysis_job_store": job_store,
        "myextension_analysis_worker": worker,
        "myextension_session_janitor": janitor,
    }
    server = type(
        "Server",
        (),
        {
            "web_app": type("Web", (), {"settings": settings})(),
            "log": type("Log", (), {"info": lambda self, message: None})(),
        },
    )()
    monkeypatch.setattr(myextension, "setup_route_handlers", lambda app: None)
    monkeypatch.setattr(myextension, "resolve_log_root", lambda: tmp_path)
    with pytest.raises(RuntimeError):
        myextension._load_jupyter_server_extension(server)
    assert worker.shutdown_calls == 0
    assert janitor.shutdown_calls == 0
    assert settings["myextension_analysis_worker"] is worker
    assert settings["myextension_session_janitor"] is janitor


def test_extension_enqueues_preexisting_queued_job_once(
    tmp_path,
    monkeypatch,
):
    import myextension

    queued_id = "70000000-0000-4000-8000-000000000077"

    class QueuedStore:
        def __init__(self):
            self.list_calls = 0

        def recover_interrupted(self):
            return []

        def list_queued(self):
            self.list_calls += 1
            return [queued_id]

    class Worker:
        def __init__(self, store):
            self.job_store = store
            self.session_store = FakeSessionStore()
            self.enqueued: list[str] = []

        def enqueue(self, job_id):
            self.enqueued.append(job_id)

        def shutdown(self):
            return None

    class Janitor:
        def start(self):
            return None

        def shutdown(self):
            return None

    store = QueuedStore()
    worker = Worker(store)
    settings = {
        "base_url": "/",
        "myextension_analysis_job_store": store,
        "myextension_analysis_worker": worker,
        "myextension_session_janitor": Janitor(),
    }
    server = type(
        "Server",
        (),
        {
            "web_app": type("Web", (), {"settings": settings})(),
            "log": type("Log", (), {"info": lambda self, message: None})(),
        },
    )()
    monkeypatch.setattr(myextension, "setup_route_handlers", lambda app: None)
    monkeypatch.setattr(myextension, "resolve_log_root", lambda: tmp_path)
    myextension._load_jupyter_server_extension(server)
    myextension._load_jupyter_server_extension(server)
    assert worker.enqueued == [queued_id]
    assert store.list_calls == 1


@pytest.mark.parametrize("corruption", ["mode", "number", "duplicate", "future"])
def test_retry_audit_corruption_fails_closed(tmp_path, corruption):
    store = AnalysisJobStore(tmp_path)
    job = store.create(session=session(), input_snapshot_hash="a" * 64)
    attempt = store.begin_attempt(str(job["job_id"]))
    store.finish_attempt(
        str(job["job_id"]),
        str(attempt["attempt_id"]),
        status="error",
        analysis_id=None,
        error_code="model_timeout",
    )
    store.retry(str(job["job_id"]), reason="teacher_requested")
    path = (
        tmp_path
        / "jobs"
        / str(job["job_id"])
        / "retry_history.jsonl"
    )
    if corruption == "mode":
        os.chmod(path, 0o644)
    else:
        record = json.loads(path.read_text().strip())
        if corruption == "number":
            record["next_attempt_number"] = True
        elif corruption == "future":
            record["next_attempt_number"] = 99
        path.write_bytes(
            (
                json.dumps(record, separators=(",", ":"))
                + "\n"
                + (
                    json.dumps(record, separators=(",", ":")) + "\n"
                    if corruption == "duplicate"
                    else ""
                )
            ).encode()
        )
        os.chmod(path, 0o600)
    with pytest.raises(AnalysisJobIntegrityError):
        store.get(str(job["job_id"]))


def test_list_queued_is_sorted_and_validates_complete_jobs(tmp_path):
    store = AnalysisJobStore(tmp_path)
    first_session = session()
    second_session = {
        **session(),
        "session_id": "30000000-0000-4000-8000-000000000010",
    }
    first = store.create(
        session=first_session,
        input_snapshot_hash="a" * 64,
    )
    second = store.create(
        session=second_session,
        input_snapshot_hash="b" * 64,
    )
    assert store.list_queued() == sorted(
        [str(first["job_id"]), str(second["job_id"])]
    )


def test_deferred_real_worker_cannot_execute_before_explicit_start(tmp_path):
    job_id = "70000000-0000-4000-8000-000000000066"
    calls: list[str] = []

    class QueuedStore:
        @staticmethod
        def get(requested_job_id):
            return {"status": "queued"}

    worker = AnalysisWorker(
        tmp_path,
        job_store=QueuedStore(),  # type: ignore[arg-type]
        session_store=FakeSessionStore(),  # type: ignore[arg-type]
        provider_client=lambda *args, **kwargs: calls.append("provider"),
        autostart=False,
    )
    worker.enqueue(job_id)
    assert worker._thread is None
    assert worker._queue.qsize() == 1
    assert calls == []
    worker.shutdown()
    assert worker._thread is None


def test_failed_loader_then_immediate_retry_has_one_execution_owner(
    tmp_path,
    monkeypatch,
):
    import myextension

    job_id = "70000000-0000-4000-8000-000000000055"
    owners: list[int] = []
    executed = threading.Event()

    class QueuedStore:
        def recover_interrupted(self):
            return []

        def list_queued(self):
            return [job_id]

        def get(self, requested_job_id):
            return {"status": "queued"}

    class CountingWorker(AnalysisWorker):
        instances: list["CountingWorker"] = []

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.instances.append(self)

        def _execute(self, requested_job_id):
            owners.append(id(self))
            executed.set()

    class FailFirstJanitor:
        starts = 0

        def __init__(self, session_store):
            self.shutdown_calls = 0

        def start(self):
            type(self).starts += 1
            if type(self).starts == 1:
                raise RuntimeError("synthetic first-start failure")

        def shutdown(self):
            self.shutdown_calls += 1

    class Web:
        def __init__(self):
            self.settings = {"base_url": "/"}

    server = type(
        "Server",
        (),
        {
            "web_app": Web(),
            "log": type("Log", (), {"info": lambda self, message: None})(),
        },
    )()
    monkeypatch.setattr(myextension, "setup_route_handlers", lambda app: None)
    monkeypatch.setattr(myextension, "resolve_log_root", lambda: tmp_path)
    monkeypatch.setattr(myextension, "AnalysisJobStore", lambda root: QueuedStore())
    monkeypatch.setattr(myextension, "AnalysisWorker", CountingWorker)
    monkeypatch.setattr(myextension, "SessionJanitor", FailFirstJanitor)
    monkeypatch.setattr(myextension.atexit, "register", lambda callback: None)
    monkeypatch.setattr(myextension.atexit, "unregister", lambda callback: None)

    with pytest.raises(RuntimeError):
        myextension._load_jupyter_server_extension(server)
    assert owners == []
    assert len(CountingWorker.instances) == 1
    first = CountingWorker.instances[0]
    assert first._thread is None or not first._thread.is_alive()

    myextension._load_jupyter_server_extension(server)
    assert executed.wait(timeout=2)
    assert len(CountingWorker.instances) == 2
    second = CountingWorker.instances[1]
    assert owners == [id(second)]
    second.shutdown()


@pytest.mark.parametrize("reader", ["get", "list", "recover"])
def test_retry_audit_gap_and_cleared_reverse_binding_fail_closed(
    tmp_path,
    reader,
):
    store = AnalysisJobStore(tmp_path)
    job = store.create(session=session(), input_snapshot_hash="a" * 64)
    first = store.begin_attempt(str(job["job_id"]))
    store.finish_attempt(
        str(job["job_id"]),
        str(first["attempt_id"]),
        status="error",
        analysis_id=None,
        error_code="model_timeout",
    )
    store.retry(str(job["job_id"]), reason="first_retry")
    second = store.begin_attempt(str(job["job_id"]))
    store.finish_attempt(
        str(job["job_id"]),
        str(second["attempt_id"]),
        status="error",
        analysis_id=None,
        error_code="model_timeout",
    )
    store.retry(str(job["job_id"]), reason="second_retry")

    audit_path = (
        tmp_path
        / "jobs"
        / str(job["job_id"])
        / "retry_history.jsonl"
    )
    records = audit_path.read_bytes().splitlines()
    assert len(records) == 2
    audit_path.write_bytes(records[1] + b"\n")
    os.chmod(audit_path, 0o600)

    attempt_path = (
        tmp_path
        / "jobs"
        / str(job["job_id"])
        / "attempts"
        / f"{second['attempt_id']}.json"
    )
    stored_attempt = json.loads(attempt_path.read_text())
    stored_attempt["retry_reason"] = None
    attempt_path.write_bytes(canonical_json_bytes(stored_attempt))
    os.chmod(attempt_path, 0o600)

    with pytest.raises(AnalysisJobIntegrityError):
        if reader == "get":
            store.get(str(job["job_id"]))
        elif reader == "list":
            store.list_queued()
        else:
            store.recover_interrupted()
