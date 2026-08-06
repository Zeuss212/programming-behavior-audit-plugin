from __future__ import annotations

import copy
import json
import logging
import unicodedata
from pathlib import Path

import pytest
from jsonschema import ValidationError
import myextension.session_log_service as session_log_service_module

from myextension.analysis_job_store import (
    AnalysisJobIntegrityError,
    AnalysisJobStore,
)
from myextension.canonical_json import sha256_json
from myextension.analysis_worker import AnalysisWorker, compute_input_snapshot_hash
from myextension.dimension_profile_store import DimensionProfileStore
from myextension.review_store import ReviewStore
from myextension.session_log_service import (
    SessionLogIntegrityError,
    SessionLogService,
)
from myextension.schema_registry import validate_schema
from myextension.session_store import (
    SessionNotFoundError,
    SessionStore,
)
from myextension.tests.test_session_store import batch, event, started_session
from myextension.tests.test_analysis_job_store import (
    finalized_session,
    provider_response,
)
from myextension.tests.test_assessment_profile import make_assessment_profile
from myextension.training_record_automation import TrainingRecordRefresher


FORBIDDEN_PUBLIC_KEYS = {
    "raw_response",
    "provider_request_id",
    "prompt_snapshot",
    "api_key",
    "file_path",
    "notebook_path",
}


def assert_no_forbidden_public_keys(value: object) -> None:
    if isinstance(value, dict):
        assert not FORBIDDEN_PUBLIC_KEYS.intersection(value)
        for nested in value.values():
            assert_no_forbidden_public_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            assert_no_forbidden_public_keys(nested)


def service_for(store):
    return SessionLogService(
        root=Path(store.root),
        session_store=store,
        job_store=AnalysisJobStore(Path(store.root)),
        review_store=ReviewStore(Path(store.root)),
    )


def append_synthetic_event(
    store,
    session_id: str,
    *,
    sequence: int,
    segment_id: str,
    **fields: object,
) -> None:
    payload = batch(
        session_id,
        sequence=sequence,
        segment_id=segment_id,
    )
    segments = payload["segments"]
    assert isinstance(segments, list)
    row = segments[0]
    assert isinstance(row, dict)
    row.update(fields)
    payload["content_hash"] = sha256_json(
        {
            "first_sequence": sequence,
            "last_sequence": sequence,
            "segments": segments,
        }
    )
    store.append_batch(session_id, **payload)


def attached_job_fixture(tmp_path: Path, *, status: str = "queued"):
    session_store, finalized = finalized_session(tmp_path)
    session_id = str(finalized["session_id"])
    job_store = AnalysisJobStore(tmp_path)
    job = job_store.create(
        session=finalized,
        input_snapshot_hash=compute_input_snapshot_hash(session_store, session_id),
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


def terminal_session_fixture(tmp_path: Path, *, mutate_provider_response=None):
    session_store, finalized, job_store = attached_job_fixture(tmp_path)
    session_id = str(finalized["session_id"])
    job_id = str(
        finalized.get("analysis_job_id")
        or session_store.read(session_id)["analysis_job_id"]
    )

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
    result = job_store.load_public_result(job_id, session_store=session_store)
    worker.shutdown()
    return session_store, finalized, job_store, result


class ResultOverrideJobStore:
    def __init__(self, wrapped, result):
        self.wrapped = wrapped
        self.result = result

    def get(self, job_id):
        return self.wrapped.get(job_id)

    def load_public_result(self, job_id, *, session_store):
        return json.loads(json.dumps(self.result))


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


def test_detail_links_analysis_evidence_and_warns_for_unknown_ids(tmp_path):
    store, session, job_store, result = terminal_session_fixture(tmp_path)
    session_id = str(session["session_id"])
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
    assert detail["integrity"]["warnings"] == ["AI 证据引用了 1 个不存在的事件。"]


def test_detail_without_job_has_no_fabricated_ai_result(tmp_path):
    store, session = started_session(tmp_path)
    detail = service_for(store).get_detail(str(session["session_id"]))
    assert detail["session"]["analysis_status"] is None
    assert detail["ai_analysis"] is None
    assert detail["teacher_reviews"] == []


def test_detail_hides_partial_placeholder_when_ai_is_not_configured(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR",
        str(tmp_path),
    )
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    store, session, job_store = attached_job_fixture(tmp_path)
    session_id = str(session["session_id"])
    job_id = str(store.read(session_id)["analysis_job_id"])
    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=store,
        synchronous=True,
    )
    worker.enqueue(job_id)
    worker.shutdown()

    detail = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    ).get_detail(session_id)

    assert detail["session"]["analysis_status"] == "partial"
    assert detail["ai_analysis"] is None


def test_worker_callback_refreshes_missing_ai_status_without_fake_result(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR",
        str(tmp_path),
    )
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    store, session, job_store = attached_job_fixture(tmp_path)
    session_id = str(session["session_id"])
    job_id = str(store.read(session_id)["analysis_job_id"])
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    service.export_training_record(session_id)
    initial = store.read_training_record(session_id)
    record_path = (
        tmp_path / "sessions" / session_id / "training_record.json"
    )
    initial_bytes = record_path.read_bytes()

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=store,
        terminal_callback=TrainingRecordRefresher(
            service,
            logger=logging.getLogger(__name__),
        ).refresh,
        synchronous=True,
    )
    worker.enqueue(job_id)
    worker.shutdown()

    refreshed = store.read_training_record(session_id)
    assert job_store.get(job_id)["status"] == "partial"
    assert job_store.get(job_id)["error_code"] == "ai_not_configured"
    assert refreshed["session"]["analysis_status"] == "partial"
    assert refreshed["ai_analysis"] is None
    assert refreshed["export"]["source_state_hash"] != initial["export"][
        "source_state_hash"
    ]
    assert record_path.read_bytes() != initial_bytes


@pytest.mark.parametrize("status", ["queued", "running", "error"])
def test_non_terminal_job_exposes_status_without_result(tmp_path, status):
    store, session, job_store = attached_job_fixture(tmp_path, status=status)
    detail = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    ).get_detail(str(session["session_id"]))
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

    assert detail["ai_analysis"]["dimension_results"][0]["decision"] == original_decision
    assert detail["teacher_reviews"][-1]["reason_code"] == "teacher_correction"
    serialized = json.dumps(detail, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert_no_forbidden_public_keys(detail)


def test_detail_fails_closed_when_attached_job_identity_is_tampered(tmp_path):
    store, session, job_store = attached_job_fixture(tmp_path)
    job_id = str(store.read(str(session["session_id"]))["analysis_job_id"])
    job_path = tmp_path / "jobs" / job_id / "job.json"
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job["session_id"] = "30000000-0000-4000-8000-000000000099"
    job_path.write_text(json.dumps(job), encoding="utf-8")

    with pytest.raises(SessionLogIntegrityError) as captured:
        SessionLogService(
            root=tmp_path,
            session_store=store,
            job_store=job_store,
            review_store=ReviewStore(tmp_path),
        ).get_detail(str(session["session_id"]))

    assert str(tmp_path) not in str(captured.value)


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
    assert first["relative_path"] == f"sessions/{session_id}/training_record.json"
    serialized = json.dumps(stored_second, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert_no_forbidden_public_keys(stored_second)


def test_public_projection_allows_safe_raw_response_hash(tmp_path):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    session_id = str(session["session_id"])

    detail = service.get_detail(session_id)
    service.export_training_record(session_id)
    record = store.read_training_record(session_id)

    assert isinstance(
        detail["ai_analysis"]["provenance"]["raw_response_hash"], str
    )
    assert len(detail["ai_analysis"]["provenance"]["raw_response_hash"]) == 64
    assert record["ai_analysis"]["provenance"]["raw_response_hash"] == (
        detail["ai_analysis"]["provenance"]["raw_response_hash"]
    )
    assert_no_forbidden_public_keys(detail)
    assert_no_forbidden_public_keys(record)


def test_export_training_record_supports_real_published_v2_profile(tmp_path):
    profiles = DimensionProfileStore(tmp_path)
    draft = profiles.create_draft(make_assessment_profile())
    published = profiles.publish(str(draft["profile_id"]))
    store = SessionStore(tmp_path)
    session = store.start(
        problem_id=str(published["problem_id"]),
        profile=published,
    )
    session_id = str(session["session_id"])
    store.finalize(session_id, last_sequence=0)

    service_for(store).export_training_record(session_id)
    record = store.read_training_record(session_id)

    validate_schema("training-record-v1", record)
    assert record["problem_profile"]["knowledge_points"][0]["order"] == 0
    assert record["problem_profile"]["dimensions"][0]["knowledge_point_id"] == (
        "KP_A1B2C3D4"
    )


def test_export_training_record_accepts_durable_review_identifier(tmp_path):
    store, session, job_store, result = terminal_session_fixture(tmp_path)
    row = result["dimension_results"][0]
    reviews = ReviewStore(tmp_path)
    append_synthetic_review(
        reviews,
        analysis_id=str(result["analysis_id"]),
        dimension_code=str(row["dimension_code"]),
        evidence_event_ids=["event-1"],
    )

    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=reviews,
    )
    service.export_training_record(str(session["session_id"]))
    record = store.read_training_record(str(session["session_id"]))

    validate_schema("training-record-v1", record)
    assert record["teacher_reviews"][-1]["evidence_event_ids"] == ["event-1"]


def test_orphan_attached_job_normalizes_to_safe_integrity_error(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.finalize(session_id, last_sequence=0)
    store.attach_job(session_id, "50000000-0000-4000-8000-000000000001")
    service = service_for(store)

    with pytest.raises(SessionLogIntegrityError) as captured:
        service.get_detail(session_id)

    assert str(captured.value) == "Stored session log is incomplete or unsafe."


def test_training_schema_allows_safe_hash_and_rejects_sensitive_mutations(
    tmp_path,
):
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

    safe_hash = copy.deepcopy(record)
    safe_hash["ai_analysis"]["provenance"]["raw_response_hash"] = "a" * 64
    validate_schema("training-record-v1", safe_hash)

    mutations = [
        lambda value: value["ai_analysis"]["provenance"].update({"raw_response": {}}),
        lambda value: value["ai_analysis"]["provenance"].update({"provider_request_id": "x"}),
        lambda value: value["ai_analysis"]["provenance"].update({"prompt_snapshot": {}}),
        lambda value: value["ai_analysis"].update({"api_key": "x"}),
        lambda value: value["behavior_events"][0].update({"file_path": "/tmp/x"}),
        lambda value: value["behavior_events"][0].update({"notebook_path": "/tmp/x"}),
        lambda value: value["ai_analysis"]["dimension_results"][0]["decision"].update({"extra": True}),
    ]
    for mutate in mutations:
        candidate = copy.deepcopy(record)
        mutate(candidate)
        with pytest.raises(ValidationError):
            validate_schema("training-record-v1", candidate)


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


def test_training_record_rejects_schema_valid_mutation_with_old_content_hash(
    tmp_path,
):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    session_id = str(session["session_id"])
    service.export_training_record(session_id)
    stored = store.read_training_record(session_id)
    stored["problem_profile"]["title"] = "被篡改但仍符合 schema"
    store.write_training_record(session_id, stored)

    with pytest.raises(SessionLogIntegrityError) as captured:
        service.get_detail(session_id)

    assert str(captured.value) == "Stored session log is incomplete or unsafe."


def test_projected_event_change_marks_training_record_stale(tmp_path):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    session_id = str(session["session_id"])
    service.export_training_record(session_id)
    raw_path = tmp_path / "sessions" / session_id / "raw_events.jsonl"
    rows = [
        json.loads(line)
        for line in raw_path.read_text(encoding="utf-8").splitlines()
    ]
    rows[0]["cell_source"] = "changed = True\n"
    raw_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    detail = service.get_detail(session_id)

    assert detail["training_record"]["stale"] is True


def test_export_retries_when_review_arrives_during_write(tmp_path, monkeypatch):
    store, session, job_store, result = terminal_session_fixture(tmp_path)
    reviews = ReviewStore(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=reviews,
    )
    session_id = str(session["session_id"])
    dimension = result["dimension_results"][0]
    original_write = store.write_training_record
    calls = 0

    def write_with_one_review(
        session_id_arg,
        record,
        *,
        require_raw_events=False,
    ):
        nonlocal calls
        calls += 1
        if calls == 1:
            append_synthetic_review(
                reviews,
                analysis_id=str(result["analysis_id"]),
                dimension_code=str(dimension["dimension_code"]),
                evidence_event_ids=[
                    str(claim["event_id"])
                    for claim in dimension["ai_result"]["evidence_claims"]
                ],
            )
        if require_raw_events:
            original_write(
                session_id_arg,
                record,
                require_raw_events=True,
            )
        else:
            original_write(session_id_arg, record)

    monkeypatch.setattr(store, "write_training_record", write_with_one_review)

    response = service.export_training_record(session_id)
    stored = store.read_training_record(session_id)

    assert calls == 2
    assert response["stale"] is False
    assert response["content_hash"] == stored["export"]["content_hash"]
    assert service.get_detail(session_id)["training_record"]["stale"] is False


def test_detail_preserves_interleaved_review_append_order(tmp_path):
    store, session, job_store, result = terminal_session_fixture(tmp_path)
    reviews = ReviewStore(tmp_path)
    first = result["dimension_results"][0]
    second = result["dimension_results"][1]
    analysis_id = str(result["analysis_id"])
    event_ids = [
        str(claim["event_id"])
        for claim in first["ai_result"]["evidence_claims"]
    ]
    append_synthetic_review(
        reviews,
        analysis_id=analysis_id,
        dimension_code=str(first["dimension_code"]),
        evidence_event_ids=event_ids,
    )
    append_synthetic_review(
        reviews,
        analysis_id=analysis_id,
        dimension_code=str(second["dimension_code"]),
        evidence_event_ids=["event-1"],
    )
    reviews.append(
        analysis_id,
        str(first["dimension_code"]),
        expected_revision=1,
        correction={
            "revision": 1,
            "decision_status": "resolved",
            "evidence_status": "observed",
            "level_code": "possible",
            "evidence_event_ids": event_ids,
            "reason_code": "teacher_correction",
            "comment": "第二条 A 复核",
        },
    )

    detail = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=reviews,
    ).get_detail(str(session["session_id"]))

    assert [row["dimension_code"] for row in detail["teacher_reviews"]] == [
        first["dimension_code"],
        second["dimension_code"],
        first["dimension_code"],
    ]


def test_training_schema_rejects_deep_minimum_observation_without_recursion(
    tmp_path,
):
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
    nested: dict[str, object] = {"edit_event_count": 1}
    for _ in range(100):
        nested = {"minimum_observation": nested}
    record["problem_profile"]["dimensions"][0]["analysis_config"] = {
        "mode": "llm_evidence",
        "minimum_observation": nested,
    }

    with pytest.raises(ValidationError):
        validate_schema("training-record-v1", record)


def test_export_normalizes_only_schema_validation_errors(tmp_path, monkeypatch):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )

    def fail_validation(*_args, **_kwargs):
        raise ValidationError("synthetic schema failure")

    monkeypatch.setattr(session_log_service_module, "validate_schema", fail_validation)
    with pytest.raises(SessionLogIntegrityError):
        service.export_training_record(str(session["session_id"]))

    def fail_registry(*_args, **_kwargs):
        raise RuntimeError("synthetic registry failure")

    monkeypatch.setattr(session_log_service_module, "validate_schema", fail_registry)
    with pytest.raises(RuntimeError, match="synthetic registry failure"):
        service.export_training_record(str(session["session_id"]))


def test_export_over_approved_limit_fails_without_truncating(tmp_path, monkeypatch):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    session_id = str(session["session_id"])
    detail = service.get_detail(session_id)
    event = detail["behavior_events"][0]
    detail["behavior_events"] = [copy.deepcopy(event) for _ in range(10_001)]
    monkeypatch.setattr(service, "get_detail", lambda _session_id: copy.deepcopy(detail))

    with pytest.raises(SessionLogIntegrityError) as captured:
        service.export_training_record(session_id)

    assert str(captured.value) == "Training record exceeds the approved export limit."


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record["problem_profile"].update({"title": "已变更题目"}),
        lambda record: record["behavior_events"][0].update({"cell_source": "changed = 1\n"}),
        lambda record: record["ai_analysis"]["provenance"].update({"model_name": "other-model"}),
        lambda record: record["teacher_reviews"][0].update({"comment": "已变更复核"}),
        lambda record: record["integrity"].update({"complete": False}),
    ],
    ids=["profile", "event_source", "ai_provenance", "review", "integrity"],
)
def test_stored_public_fields_must_match_declared_source_state_hash(tmp_path, mutate):
    store, session, job_store, result = terminal_session_fixture(tmp_path)
    reviews = ReviewStore(tmp_path)
    dimension = result["dimension_results"][0]
    append_synthetic_review(
        reviews,
        analysis_id=str(result["analysis_id"]),
        dimension_code=str(dimension["dimension_code"]),
        evidence_event_ids=[
            str(claim["event_id"])
            for claim in dimension["ai_result"]["evidence_claims"]
        ],
    )
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=reviews,
    )
    session_id = str(session["session_id"])
    service.export_training_record(session_id)
    stored = store.read_training_record(session_id)
    old_source_state_hash = stored["export"]["source_state_hash"]
    mutate(stored)
    stored["export"]["content_hash"] = service._record_content_hash(stored)
    assert stored["export"]["source_state_hash"] == old_source_state_hash
    store.write_training_record(session_id, stored)

    with pytest.raises(SessionLogIntegrityError) as captured:
        service.get_detail(session_id)

    assert str(captured.value) == "Stored session log is incomplete or unsafe."


def test_export_response_uses_accepted_competing_stored_timestamp(
    tmp_path,
    monkeypatch,
):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    session_id = str(session["session_id"])
    original_write = store.write_training_record
    accepted_timestamp = "2030-01-01T00:00:00+00:00"

    def write_competing_same_source(
        session_id_arg,
        record,
        *,
        require_raw_events=False,
    ):
        competing = copy.deepcopy(record)
        competing["export"]["generated_at"] = accepted_timestamp
        competing["export"]["content_hash"] = service._record_content_hash(
            competing
        )
        if require_raw_events:
            original_write(
                session_id_arg,
                competing,
                require_raw_events=True,
            )
        else:
            original_write(session_id_arg, competing)

    monkeypatch.setattr(store, "write_training_record", write_competing_same_source)

    response = service.export_training_record(session_id)
    stored = store.read_training_record(session_id)

    assert stored["export"]["generated_at"] == accepted_timestamp
    assert response["generated_at"] == accepted_timestamp


def test_export_does_not_persist_when_raw_events_disappear_at_write_boundary(
    tmp_path,
    monkeypatch,
):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    session_id = str(session["session_id"])
    raw_path = tmp_path / "sessions" / session_id / "raw_events.jsonl"
    original_write = store.write_training_record
    original_write_json = store._write_json
    write_calls = 0

    def count_write(path, value):
        nonlocal write_calls
        write_calls += 1
        return original_write_json(path, value)

    def delete_then_write(
        session_id_arg,
        record,
        *,
        require_raw_events=False,
    ):
        raw_path.unlink()
        if require_raw_events:
            return original_write(
                session_id_arg,
                record,
                require_raw_events=True,
            )
        return original_write(session_id_arg, record)

    monkeypatch.setattr(
        store,
        "write_training_record",
        delete_then_write,
    )
    monkeypatch.setattr(store, "_write_json", count_write)

    with pytest.raises(SessionLogIntegrityError):
        service.export_training_record(session_id)

    assert not (
        tmp_path / "sessions" / session_id / "training_record.json"
    ).exists()
    assert write_calls == 0


def _detail_at_public_byte_budget(
    service: SessionLogService,
    session_id: str,
    *,
    target: int,
):
    detail = service.get_detail(session_id)
    event = copy.deepcopy(detail["behavior_events"][0])
    event["cell_source"] = "x" * 99_000
    detail["behavior_events"] = [copy.deepcopy(event) for _ in range(335)]
    detail["code_snapshots"] = []
    current = service._public_fields_byte_size(detail)
    assert current < target
    remaining = target - current
    for row in detail["behavior_events"]:
        source = row["cell_source"]
        capacity = 100_000 - len(source)
        increment = min(capacity, remaining)
        row["cell_source"] = source + ("x" * increment)
        remaining -= increment
        if remaining == 0:
            break
    assert remaining == 0
    assert service._public_fields_byte_size(detail) == target
    return detail


def test_aggregate_budget_allows_at_limit_and_short_circuits_over_limit(
    tmp_path,
    monkeypatch,
):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    session_id = str(session["session_id"])
    at_limit = _detail_at_public_byte_budget(
        service,
        session_id,
        target=32 * 1024 * 1024,
    )
    service._assert_export_bounds(at_limit)
    at_limit["behavior_events"][-1]["cell_source"] += "x"
    calls = 0

    def unexpected_write(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("over-budget export must not write")

    monkeypatch.setattr(service, "get_detail", lambda _session_id: at_limit)
    monkeypatch.setattr(store, "write_training_record", unexpected_write)

    with pytest.raises(SessionLogIntegrityError) as captured:
        service.export_training_record(session_id)

    assert str(captured.value) == "Training record exceeds the approved export limit."
    assert calls == 0


def test_aggregate_budget_rejects_one_giant_string_before_json_encoding(
    tmp_path,
    monkeypatch,
):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    detail = service.get_detail(str(session["session_id"]))
    detail["behavior_events"][0]["cell_source"] = "x" * (
        32 * 1024 * 1024 + 1
    )
    original_iterencode = session_log_service_module.json.JSONEncoder.iterencode

    def guarded_iterencode(encoder, value, *_args, **_kwargs):
        if value is detail["behavior_events"]:
            raise AssertionError("giant field must not reach JSON encoding")
        return original_iterencode(encoder, value, *_args, **_kwargs)

    monkeypatch.setattr(
        session_log_service_module.json.JSONEncoder,
        "iterencode",
        guarded_iterencode,
    )

    with pytest.raises(SessionLogIntegrityError) as captured:
        service._assert_export_bounds(detail)

    assert str(captured.value) == "Training record exceeds the approved export limit."


def test_aggregate_budget_rejects_nfc_expansion_before_writing(
    tmp_path,
    monkeypatch,
):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    session_id = str(session["session_id"])
    detail = service.get_detail(session_id)
    event = copy.deepcopy(detail["behavior_events"][0])
    event["cell_source"] = "\u0344" * 50_000
    assert len(event["cell_source"]) == 50_000
    assert len(unicodedata.normalize("NFC", event["cell_source"])) == 100_000
    detail["behavior_events"] = [event] * 200
    detail["code_snapshots"] = []
    writes = 0

    def unexpected_write(*_args, **_kwargs):
        nonlocal writes
        writes += 1
        raise AssertionError("NFC-expanded over-budget export must not write")

    monkeypatch.setattr(service, "get_detail", lambda _session_id: detail)
    monkeypatch.setattr(store, "write_training_record", unexpected_write)

    with pytest.raises(SessionLogIntegrityError) as captured:
        service.export_training_record(session_id)

    assert str(captured.value) == "Training record exceeds the approved export limit."
    assert writes == 0


def test_export_allows_small_decomposed_unicode_payload(tmp_path):
    store, session, job_store, _ = terminal_session_fixture(tmp_path)
    session_id = str(session["session_id"])
    service = SessionLogService(
        root=tmp_path,
        session_store=store,
        job_store=job_store,
        review_store=ReviewStore(tmp_path),
    )
    detail = service.get_detail(session_id)
    detail["behavior_events"][0]["cell_source"] = "label = 'e\u0301'\n"

    original_get_detail = service.get_detail

    def detail_with_decomposed_source(requested_session_id: str):
        if requested_session_id == session_id:
            return copy.deepcopy(detail)
        return original_get_detail(requested_session_id)

    service.get_detail = detail_with_decomposed_source  # type: ignore[method-assign]

    exported = service.export_training_record(session_id)

    assert exported["session_id"] == session_id
    stored = store.read_training_record(session_id)
    assert stored is not None
    assert stored["behavior_events"][0]["cell_source"] == "label = '\u00e9'\n"


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


def test_detail_omits_snapshots_for_events_without_source(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    payload = batch(session_id, sequence=1)
    segments = payload["segments"]
    assert isinstance(segments, list)
    row = event(session_id, 1)
    row.pop("cell_source")
    segments[0] = row
    payload["content_hash"] = sha256_json(
        {
            "first_sequence": 1,
            "last_sequence": 1,
            "segments": segments,
        }
    )
    store.append_batch(session_id, **payload)

    detail = service_for(store).get_detail(session_id)

    assert len(detail["behavior_events"]) == 1
    assert detail["code_snapshots"] == []
    assert str(tmp_path) not in json.dumps(detail, ensure_ascii=False)


def test_detail_scrubs_synthetic_posix_windows_and_unc_paths(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    append_synthetic_event(
        store,
        session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000021",
        error_message=(
            "File /Users/synthetic/private/posix.py, "
            "C:\\Users\\synthetic\\private\\windows.py, "
            "\\\\server\\share\\private\\unc.py"
        ),
        notebook_id="/Users/synthetic/private/lesson.ipynb",
    )
    append_synthetic_event(
        store,
        session_id,
        sequence=2,
        segment_id="20000000-0000-4000-8000-000000000022",
        notebook_id="opaque-notebook-42",
    )

    detail = service_for(store).get_detail(session_id)
    serialized = json.dumps(detail, ensure_ascii=False)

    assert "/Users/synthetic/private" not in serialized
    assert "C:\\Users\\synthetic\\private" not in serialized
    assert "\\\\server\\share\\private" not in serialized
    assert "posix.py" in serialized
    assert "windows.py" in serialized
    assert "unc.py" in serialized
    assert detail["behavior_events"][0]["notebook_id"] == "lesson.ipynb"
    assert detail["behavior_events"][1]["notebook_id"] == "opaque-notebook-42"


def test_summary_projects_validated_event_count(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    append_synthetic_event(
        store,
        session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000023",
    )

    summary = service_for(store)._summary(session_id)

    assert summary["event_count"] == 1


def test_summary_keeps_review_count_unknown_without_valid_terminal_result(
    tmp_path,
):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    finalized = store.finalize(session_id, last_sequence=0)
    jobs = AnalysisJobStore(tmp_path)
    job = jobs.create(session=finalized, input_snapshot_hash="a" * 64)
    attempt = jobs.begin_attempt(str(job["job_id"]))
    jobs.finish_attempt(
        str(job["job_id"]),
        str(attempt["attempt_id"]),
        status="ready",
        analysis_id="40000000-0000-4000-8000-000000000021",
        error_code=None,
        prompt_snapshot_hash="b" * 64,
        raw_response_snapshot_hash="c" * 64,
    )
    store.attach_job(session_id, str(job["job_id"]))

    summary = service_for(store)._summary(session_id)

    assert summary["analysis_status"] == "ready"
    assert summary["review_count"] is None


def test_session_not_found_error_propagates(tmp_path):
    service = service_for(SessionStore(tmp_path))

    with pytest.raises(SessionNotFoundError):
        service.get_detail("10000000-0000-4000-8000-000000000099")


def test_detail_preserves_source_and_deleted_content_byte_for_byte(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    source = (
        'print("/Users/synthetic/private/source.py")\n'
        r"print(r'C:\Users\synthetic\private\source.py')" + "\n"
        r"print(r'\\server\share\private\source.py')"
    )
    deleted = (
        "/Users/synthetic/private/deleted.py\n"
        r"C:\Users\synthetic\private\deleted.py"
    )
    append_synthetic_event(
        store,
        session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000031",
        cell_source=source,
        deleted_content=deleted,
    )

    detail = service_for(store).get_detail(session_id)

    assert detail["behavior_events"][0]["cell_source"] == source
    assert detail["behavior_events"][0]["deleted_content"] == deleted
    assert detail["code_snapshots"][0]["source"] == source


def test_detail_keeps_adjacent_sources_differing_only_by_path_separate(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    first_source = 'open("/Users/synthetic/one/config.py")'
    second_source = 'open("/Users/synthetic/two/config.py")'
    append_synthetic_event(
        store,
        session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000032",
        cell_source=first_source,
    )
    append_synthetic_event(
        store,
        session_id,
        sequence=2,
        segment_id="20000000-0000-4000-8000-000000000033",
        cell_source=second_source,
    )

    snapshots = service_for(store).get_detail(session_id)["code_snapshots"]

    assert [row["source"] for row in snapshots] == [
        first_source,
        second_source,
    ]
    assert snapshots[0]["snapshot_id"] != snapshots[1]["snapshot_id"]


def test_detail_scrubs_extensionless_diagnostic_paths_only(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    append_synthetic_event(
        store,
        session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000034",
        error_message=(
            "File /Users/synthetic/private/project, "
            "C:\\Users\\synthetic\\private\\workspace, "
            "\\\\server\\share\\private\\folder"
        ),
        execution_result="failed at '/private/synthetic/private/segment'",
    )

    event_row = service_for(store).get_detail(session_id)["behavior_events"][0]

    assert event_row["error_message"] == "File project, workspace, folder"
    assert event_row["execution_result"] == "failed at 'segment'"


def test_detail_does_not_over_redact_urls_routes_pointers_or_opaque_ids(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    diagnostic = (
        "https://example.test/api/v1/items?next=/api/v1/items "
        "route=/api/v1/items pointer=/items/0 id=opaque-notebook-42"
    )
    append_synthetic_event(
        store,
        session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000035",
        error_message=diagnostic,
        execution_result=diagnostic,
        notebook_id="opaque-notebook-42",
    )

    event_row = service_for(store).get_detail(session_id)["behavior_events"][0]

    assert event_row["error_message"] == diagnostic
    assert event_row["execution_result"] == diagnostic
    assert event_row["notebook_id"] == "opaque-notebook-42"


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        ("File /root/private/secret", "File secret"),
        (
            'path="/Library/Application Support/Example App/cache"',
            'path="cache"',
        ),
        (
            r'file_path="C:\Program Files\Example App\config"',
            r'file_path="config"',
        ),
        (
            r'unc="\\server\share\Example Folder\artifact"',
            r'unc="artifact"',
        ),
        (
            "path=/Library/Application Support/Example App/cache",
            "path=cache",
        ),
        (
            r"file_path=C:\Program Files\Example App\config",
            "file_path=config",
        ),
        (
            r"unc=\\server\share\Example Folder\artifact",
            "unc=artifact",
        ),
        (
            'route=/data/records pointer=/Users/0 '
            'route="/data/records" pointer="/Users/0" '
            'url=https://example.test/data/records '
            'uri="https://example.test/Users/0"',
            'route=/data/records pointer=/Users/0 '
            'route="/data/records" pointer="/Users/0" '
            'url=https://example.test/data/records '
            'uri="https://example.test/Users/0"',
        ),
    ],
)
def test_detail_scrubs_explicit_paths_without_changing_structured_contexts(
    tmp_path,
    diagnostic,
    expected,
):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    append_synthetic_event(
        store,
        session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000036",
        error_message=diagnostic,
    )

    event_row = service_for(store).get_detail(session_id)["behavior_events"][0]

    assert event_row["error_message"] == expected


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        (
            "literal \x000\x00 File /custom/private/log",
            "literal \x000\x00 File log",
        ),
        (
            "literal \x000\x00 route=/data/records File /custom/private/log",
            "literal \x000\x00 route=/data/records File log",
        ),
    ],
)
def test_detail_preserves_literal_nul_markers_while_scrubbing_diagnostics(
    tmp_path,
    diagnostic,
    expected,
):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    append_synthetic_event(
        store,
        session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000041",
        error_message=diagnostic,
    )

    event_row = service_for(store).get_detail(session_id)["behavior_events"][0]

    assert event_row["error_message"] == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/System/Volumes/private/secret", "secret"),
        ("/Applications/Example App/cache", "cache"),
        ("/custom/private/log", "log"),
    ],
)
def test_detail_scrubs_arbitrary_unlabelled_posix_diagnostic_paths(
    tmp_path,
    path,
    expected,
):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    append_synthetic_event(
        store,
        session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000042",
        error_message=f"failure at {path}",
    )

    event_row = service_for(store).get_detail(session_id)["behavior_events"][0]

    assert event_row["error_message"] == f"failure at {expected}"


def test_detail_normalizes_source_event_missing_snapshot_timestamp(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    store.append_batch(session_id, **batch(session_id, sequence=1))
    raw_path = tmp_path / "sessions" / session_id / "raw_events.jsonl"
    stored = [
        json.loads(line)
        for line in raw_path.read_text(encoding="utf-8").splitlines()
    ]
    stored[0].pop("started_at")
    raw_path.write_text(
        "\n".join(json.dumps(row) for row in stored) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SessionLogIntegrityError) as captured:
        service_for(store).get_detail(session_id)

    assert str(captured.value) == "Stored session log is incomplete or unsafe."


@pytest.mark.parametrize(
    "diagnostic",
    [
        "https://example.test/?next=route=/Users/0",
        "url=https://example.test/?next=route=/Users/0",
    ],
)
def test_detail_preserves_nested_raw_url_and_structured_contexts(tmp_path, diagnostic):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    append_synthetic_event(
        store,
        session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000051",
        error_message=diagnostic,
    )

    event_row = service_for(store).get_detail(session_id)["behavior_events"][0]

    assert event_row["error_message"] == diagnostic


def test_detail_preserves_repeated_overlapping_protected_contexts(tmp_path):
    store, session = started_session(tmp_path)
    session_id = str(session["session_id"])
    diagnostic = " ".join(
        "https://example.test/?next=route=/Users/0"
        for _ in range(300)
    )
    append_synthetic_event(
        store,
        session_id,
        sequence=1,
        segment_id="20000000-0000-4000-8000-000000000052",
        error_message=diagnostic,
    )

    event_row = service_for(store).get_detail(session_id)["behavior_events"][0]

    assert event_row["error_message"] == diagnostic
