import json
import logging
import os
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from myextension.api_base import JsonAPIHandler
from myextension.analysis_job_store import AnalysisJobStore
from myextension.analysis_worker import AnalysisQueueFullError, AnalysisWorker
from myextension.canonical_json import canonical_json_bytes, sha256_json
from myextension.dimension_profile_store import DimensionProfileStore
from myextension.log_folder_opener import (
    LogFolderOpenError,
    LogFolderOpenUnsupportedError,
)
from myextension.review_store import ReviewStore
from myextension.routes import (
    DimensionTemplatesRouteHandler,
    PilotAPIHandler,
    _PROFILE_STORE_CACHE,
    setup_route_handlers,
)
from myextension.session_store import SegmentConflictError, SequenceGapError
from myextension.session_log_service import SessionLogService
from myextension.session_log_artifacts import MAX_INLINE_LOG_BYTES
from myextension.schema_registry import validate_schema
from myextension.tests.test_assessment_profile import make_assessment_profile
from myextension.training_record_automation import TrainingRecordRefresher

LOG_DIR_ENV_VAR = "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR"
OPENAPI_PATH = Path(__file__).parents[2] / "docs" / "openapi" / "myextension-v1.yaml"


def make_profile_payload():
    return {
        "schema_version": 1,
        "problem_id": "average-debug",
        "title": "平均分调试题",
        "dimensions": [
            {
                "code": "CUSTOM_A1B2C3D4",
                "name": "失败后是否继续验证",
                "question": "学生运行失败后，是否修改相关代码并再次运行？",
                "evidence_criteria": [
                    {
                        "id": "support-1",
                        "direction": "support",
                        "statement": "失败后修改相关代码并再次运行",
                    },
                    {
                        "id": "exclude-1",
                        "direction": "exclude",
                        "statement": "只修改注释不计入",
                    },
                ],
                "levels": [
                    {
                        "code": "possible",
                        "name": "可能出现",
                        "definition": "存在一次完整但范围有限的相关行为",
                    },
                    {
                        "code": "clear",
                        "name": "明显出现",
                        "definition": "在多个阶段持续出现相关行为",
                    },
                ],
                "teaching_actions": {
                    "possible": "结合证据询问学生的调试思路",
                    "clear": "安排一次修改后立即验证的短练习",
                },
                "analysis_config": {
                    "mode": "llm_evidence",
                    "minimum_observation": {
                        "valid_observation_duration_ms": 30000,
                        "edit_event_count": 1,
                    },
                },
            }
        ],
    }


def make_three_dimension_profile_payload():
    payload = make_profile_payload()
    seed = payload["dimensions"][0]
    dimensions = []
    for index, code in enumerate(
        (
            "CUSTOM_A1B2C3D4",
            "CUSTOM_B1C2D3E4",
            "CUSTOM_C1D2E3F4",
        ),
        start=1,
    ):
        dimension = json.loads(json.dumps(seed))
        dimension["code"] = code
        dimension["name"] = f"合成行为维度 {index}"
        dimension["question"] = f"是否出现合成行为链 {index}？"
        dimension["evidence_criteria"][0]["id"] = f"support-{index}"
        dimension["evidence_criteria"][1]["id"] = f"exclude-{index}"
        dimensions.append(dimension)
    payload["dimensions"] = dimensions
    return payload


def response_json(response):
    return json.loads(response.body)


def openapi_validator(schema_name):
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    resources = [
        (
            OPENAPI_PATH.as_uri(),
            Resource(contents=document, specification=DRAFT202012),
        )
    ]
    for schema_file in (
        OPENAPI_PATH.parents[2] / "myextension" / "api_schemas"
    ).glob("*.json"):
        resources.append(
            (
                schema_file.as_uri(),
                Resource.from_contents(
                    json.loads(schema_file.read_text(encoding="utf-8"))
                ),
            )
        )
    return Draft202012Validator(
        {
            "$ref": (
                f"{OPENAPI_PATH.as_uri()}"
                f"#/components/schemas/{schema_name}"
            )
        },
        registry=Registry().with_resources(resources),
    )


def assert_request_id(payload):
    parsed = UUID(payload["request_id"])
    assert str(parsed) == payload["request_id"]


def assert_error_response(response, status, code):
    assert response.code == status
    payload = response_json(response)
    validate_schema("error-v1", payload)
    assert payload["code"] == code
    assert_request_id(payload)
    return payload


def test_training_refresh_failure_keeps_logging_failure_isolated():
    private_marker = "/private/synthetic-refresh-lifecycle-secret-731"
    warning_calls = []

    class ExplodingLogger:
        def warning(self, *args):
            warning_calls.append(args)
            raise RuntimeError("synthetic logging failure")

    class ExplodingHandler:
        log = ExplodingLogger()

        def _session_log_service(self):
            raise RuntimeError(private_marker)

    assert PilotAPIHandler._refresh_training_record(
        ExplodingHandler(),
        "10000000-0000-4000-8000-000000000001",
    ) is False
    assert warning_calls == [("training_record_refresh_failed",)]
    assert private_marker not in str(warning_calls)


async def create_profile(jp_fetch, payload=None):
    return await jp_fetch(
        "myextension",
        "dimension-profiles",
        method="POST",
        body=json.dumps(payload or make_profile_payload()),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )


class SynchronousDeterministicWorker:
    def __init__(self, session_store, job_store):
        self.session_store = session_store
        self.job_store = job_store
        self.enqueued = []

    def enqueue(self, job_id):
        if job_id not in self.enqueued:
            self.enqueued.append(job_id)


class SegmentBoundaryStore:
    def __init__(self, root):
        self.root = Path(root)
        self.receipts = {}
        self.last_sequence = 0
        self.calls = []

    def append_batch(self, session_id, **batch):
        self.calls.append((session_id, batch))
        segment_id = batch["segment_id"]
        prior = self.receipts.get(segment_id)
        if prior is not None:
            if prior["content_hash"] != batch["content_hash"]:
                raise SegmentConflictError("synthetic conflict")
            return dict(prior["receipt"])
        if batch["first_sequence"] != self.last_sequence + 1:
            raise SequenceGapError(
                [(self.last_sequence + 1, batch["first_sequence"] - 1)]
            )
        receipt = {
            "session_id": session_id,
            "segment_id": segment_id,
            "accepted_count": len(batch["segments"]),
            "last_contiguous_sequence": batch["last_sequence"],
        }
        self.receipts[segment_id] = {
            "content_hash": batch["content_hash"],
            "receipt": receipt,
        }
        self.last_sequence = batch["last_sequence"]
        return dict(receipt)


class QueueFailsOnceWorker(SynchronousDeterministicWorker):
    def __init__(self, session_store, job_store):
        super().__init__(session_store, job_store)
        self.fail_next_enqueue = False
        self.enqueue_calls = []

    def enqueue(self, job_id):
        self.enqueue_calls.append(job_id)
        if self.fail_next_enqueue:
            self.fail_next_enqueue = False
            raise AnalysisQueueFullError("synthetic queue full")
        super().enqueue(job_id)


def install_synchronous_worker(jp_web_app, monkeypatch):
    live_worker = jp_web_app.settings["myextension_analysis_worker"]
    live_job_store = jp_web_app.settings["myextension_analysis_job_store"]
    worker = SynchronousDeterministicWorker(
        live_worker.session_store,
        live_job_store,
    )
    monkeypatch.setitem(
        jp_web_app.settings,
        "myextension_analysis_worker",
        worker,
    )
    monkeypatch.setitem(
        jp_web_app.settings,
        "myextension_analysis_job_store",
        live_job_store,
    )
    return worker


def install_segment_boundary(jp_web_app, monkeypatch):
    live_worker = jp_web_app.settings["myextension_analysis_worker"]
    job_store = jp_web_app.settings["myextension_analysis_job_store"]
    session_store = SegmentBoundaryStore(live_worker.session_store.root)
    worker = SynchronousDeterministicWorker(session_store, job_store)
    monkeypatch.setitem(
        jp_web_app.settings,
        "myextension_analysis_worker",
        worker,
    )
    return session_store


def frozen_segment_batch(
    *,
    segment_id="20000000-0000-4000-8000-000000000020",
    first_sequence=1,
    content_hash="a" * 64,
):
    return {
        "schema_version": 1,
        "segment_id": segment_id,
        "first_sequence": first_sequence,
        "last_sequence": first_sequence,
        "content_hash": content_hash,
        "segments": [
            {
                "session_seq": first_sequence,
                "event_id": f"synthetic-session:{first_sequence}",
                "segment_type": "code_writing",
                "started_at": "2026-07-28T10:00:00Z",
                "ended_at": "2026-07-28T10:00:01Z",
                "duration_ms": 1000,
            }
        ],
    }


async def create_published_profile(jp_fetch):
    created_response = await create_profile(jp_fetch)
    assert created_response.code == 201
    created = response_json(created_response)
    published_response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        created["profile_id"],
        "publish",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert published_response.code == 200
    return response_json(published_response)


async def start_pilot_session(jp_fetch, profile):
    response = await jp_fetch(
        "myextension",
        "sessions",
        "start",
        method="POST",
        body=json.dumps(
            {
                "schema_version": 1,
                "problem_id": profile["problem_id"],
                "profile_id": profile["profile_id"],
                "profile_version": profile["version"],
                "profile_content_hash": profile["content_hash"],
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert response.code == 201
    return response_json(response)


async def finalize_empty_session(jp_fetch, jp_web_app, monkeypatch):
    worker = install_synchronous_worker(jp_web_app, monkeypatch)
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)
    response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 0}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert response.code == 202
    return worker, started, response_json(response)


async def test_classroom_brief_route_returns_finalized_local_brief(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    _worker, started, _finalized = await finalize_empty_session(
        jp_fetch,
        jp_web_app,
        monkeypatch,
    )

    response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "brief",
        raise_error=False,
    )

    assert response.code == 200
    payload = response_json(response)
    validate_schema("classroom-brief-response-v1", payload)
    openapi_validator("ClassroomBriefResponse").validate(payload)
    assert payload["session_id"] == started["session_id"]
    assert payload["status"] == "complete"
    assert payload["data_completeness"] == "complete"
    assert response.headers["Cache-Control"] == "no-store"


async def test_classroom_brief_route_hides_collecting_session(
    jp_fetch,
):
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)

    response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "brief",
        raise_error=False,
    )

    assert_error_response(response, 409, "classroom_brief_not_ready")


async def test_classroom_brief_route_returns_partial_after_explicit_abandon(
    jp_fetch,
):
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)
    abandoned = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "abandon",
        method="POST",
        body=json.dumps({"reason": "synthetic_explicit_abandon"}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert abandoned.code == 200

    response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "brief",
        raise_error=False,
    )

    assert response.code == 200
    payload = response_json(response)
    validate_schema("classroom-brief-response-v1", payload)
    assert payload["status"] == "partial"
    assert payload["data_completeness"] == "partial"


async def test_session_logs_list_is_fixed_order_and_local_logs_are_immediate(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    """Catches filesystem-order lists and waiting for AI before local logs."""

    _worker, started, _finalized = await finalize_empty_session(
        jp_fetch,
        jp_web_app,
        monkeypatch,
    )

    response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "logs",
        raise_error=False,
    )

    assert response.code == 200
    payload = response_json(response)
    validate_schema("session-log-list-response-v1", payload)
    openapi_validator("SessionLogListResponse").validate(payload)
    assert payload["session_id"] == started["session_id"]
    assert [row["kind"] for row in payload["logs"]] == [
        "operation",
        "process",
        "analysis",
    ]
    assert [row["status"] for row in payload["logs"]] == [
        "ready",
        "ready",
        "generating",
    ]
    assert payload["logs"][0]["filename"] == "operation_log.json"
    assert payload["logs"][1]["filename"] == "process_log.md"
    assert payload["logs"][2]["size_bytes"] is None
    assert payload["logs"][2]["generated_at"] is None


async def test_session_log_view_and_download_return_only_allowlisted_artifact(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    """Catches redirecting to a server path or exposing arbitrary files."""

    _worker, started, _finalized = await finalize_empty_session(
        jp_fetch,
        jp_web_app,
        monkeypatch,
    )
    parts = (
        "myextension",
        "sessions",
        started["session_id"],
        "logs",
        "operation",
    )

    view = await jp_fetch(*parts, raise_error=False)
    download = await jp_fetch(*parts, "download", raise_error=False)

    assert view.code == 200
    assert view.headers["Content-Type"].startswith("application/json")
    assert view.headers["Cache-Control"] == "no-store"
    assert json.loads(view.body)["session"]["session_id"] == started["session_id"]
    assert download.code == 200
    assert download.body == view.body
    assert download.headers["Content-Disposition"] == (
        'attachment; filename="operation_log.json"'
    )
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert download.headers["Cache-Control"] == "no-store"

    not_ready = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "logs",
        "analysis",
        raise_error=False,
    )
    assert_error_response(not_ready, 409, "session_log_not_ready")

    unknown = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "logs",
        "..%2Fsession.json",
        raise_error=False,
    )
    assert unknown.code in {400, 404}
    assert str(Path.home()) not in unknown.body.decode("utf-8", errors="ignore")


async def test_session_log_inline_limit_does_not_truncate_full_download(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    """Catches silently truncating a large log and presenting it as complete."""

    worker, started, _finalized = await finalize_empty_session(
        jp_fetch,
        jp_web_app,
        monkeypatch,
    )
    oversized = b"x" * (MAX_INLINE_LOG_BYTES + 1)
    worker.session_store.write_log_artifact(
        started["session_id"],
        "operation_log.json",
        oversized,
    )
    parts = (
        "myextension",
        "sessions",
        started["session_id"],
        "logs",
        "operation",
    )

    view = await jp_fetch(*parts, raise_error=False)
    download = await jp_fetch(*parts, "download", raise_error=False)

    assert_error_response(view, 413, "session_log_too_large")
    assert download.code == 200
    assert download.body == oversized


@pytest.mark.parametrize(
    ("session_id", "kind", "status", "code"),
    [
        ("not-a-uuid", None, 400, "invalid_session_id"),
        ("{session_id}", "unknown", 400, "invalid_session_log_kind"),
    ],
)
async def test_session_log_api_rejects_invalid_identifiers(
    jp_fetch,
    jp_web_app,
    monkeypatch,
    session_id,
    kind,
    status,
    code,
):
    """Catches allowing untrusted identifiers to become filesystem paths."""

    _worker, started, _finalized = await finalize_empty_session(
        jp_fetch,
        jp_web_app,
        monkeypatch,
    )
    actual_session_id = (
        started["session_id"] if session_id == "{session_id}" else session_id
    )
    parts = ["myextension", "sessions", actual_session_id, "logs"]
    if kind is not None:
        parts.append(kind)

    response = await jp_fetch(*parts, raise_error=False)

    assert_error_response(response, status, code)


def test_obsolete_session_log_routes_are_not_registered():
    class WebApp:
        settings = {"base_url": "/"}

        def add_handlers(self, host_pattern, handlers):
            self.host_pattern = host_pattern
            self.handlers = handlers

    web_app = WebApp()
    setup_route_handlers(web_app)
    patterns = {pattern for pattern, _handler in web_app.handlers}

    assert "/myextension/log-folder/open" in patterns
    assert {
        "/myextension/session-" "logs",
        r"/myextension/sessions/([^/]+)/log-" r"detail",
        r"/myextension/sessions/([^/]+)/training-record",
    }.isdisjoint(patterns)


async def test_log_folder_open_returns_closed_private_response(
    jp_fetch,
    monkeypatch,
):
    monkeypatch.setattr(
        "myextension.routes.LogFolderOpener.open_sessions_folder",
        lambda self: "macos",
    )
    response = await jp_fetch(
        "myextension",
        "log-folder",
        "open",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert response.code == 200
    payload = response_json(response)
    assert payload["opened"] is True
    assert payload["platform"] == "macos"
    assert set(payload) == {"schema_version", "request_id", "opened", "platform"}
    validate_schema("log-folder-open-response-v1", payload)
    assert str(Path.home()) not in response.body.decode("utf-8")


async def test_log_folder_open_rejects_nonempty_request_body(jp_fetch):
    response = await jp_fetch(
        "myextension",
        "log-folder",
        "open",
        method="POST",
        body='{"unexpected": true}',
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert_error_response(
        response,
        422,
        "log_folder_open_validation_failed",
    )


async def test_log_folder_open_normalizes_invalid_json(jp_fetch):
    response = await jp_fetch(
        "myextension",
        "log-folder",
        "open",
        method="POST",
        body='{"path": "/private/invalid-json"',
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert_error_response(response, 422, "log_folder_open_validation_failed")
    assert "/private/invalid-json" not in response.body.decode("utf-8")


async def test_log_folder_open_normalizes_nonobject_json(jp_fetch):
    response = await jp_fetch(
        "myextension",
        "log-folder",
        "open",
        method="POST",
        body='["/private/nonobject-json"]',
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert_error_response(response, 422, "log_folder_open_validation_failed")
    assert "/private/nonobject-json" not in response.body.decode("utf-8")


async def test_log_folder_open_normalizes_unsupported_errors(
    jp_fetch,
    monkeypatch,
):
    def raise_unsupported(self):
        raise LogFolderOpenUnsupportedError("unsupported /private/path")

    monkeypatch.setattr(
        "myextension.routes.LogFolderOpener.open_sessions_folder",
        raise_unsupported,
    )
    response = await jp_fetch(
        "myextension",
        "log-folder",
        "open",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    payload = assert_error_response(response, 409, "log_folder_open_unsupported")
    assert "/private/path" not in response.body.decode("utf-8")
    assert "unsupported" not in payload["message"]


@pytest.mark.parametrize("error", [LogFolderOpenError, TimeoutError])
async def test_log_folder_open_normalizes_failures_and_timeouts(
    jp_fetch,
    monkeypatch,
    error,
):
    def raise_open_error(self):
        raise error("failed /private/path")

    monkeypatch.setattr(
        "myextension.routes.LogFolderOpener.open_sessions_folder",
        raise_open_error,
    )
    response = await jp_fetch(
        "myextension",
        "log-folder",
        "open",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    payload = assert_error_response(response, 500, "log_folder_open_failed")
    assert "/private/path" not in response.body.decode("utf-8")
    assert "failed" not in payload["message"]


async def test_log_folder_open_rejects_cookie_auth_without_xsrf(
    http_server_client,
    jp_base_url,
    jp_serverapp,
    monkeypatch,
):
    token = jp_serverapp.identity_provider.token
    login_response = await http_server_client.fetch(
        f"{jp_base_url}login?token={token}",
        follow_redirects=False,
        raise_error=False,
    )
    assert login_response.code == 302
    cookie_header = "; ".join(
        value.split(";", 1)[0]
        for value in login_response.headers.get_list("Set-Cookie")
    )
    authenticated_get = await http_server_client.fetch(
        f"{jp_base_url}myextension/dimension-templates",
        headers={"Cookie": cookie_header},
        follow_redirects=False,
        raise_error=False,
    )
    assert authenticated_get.code == 200

    open_calls: list[str] = []
    monkeypatch.setattr(
        "myextension.routes.LogFolderOpener.open_sessions_folder",
        lambda self: open_calls.append("opened") or "macos",
    )
    response = await http_server_client.fetch(
        f"{jp_base_url}myextension/log-folder/open",
        method="POST",
        body="{}",
        headers={
            "Content-Type": "application/json",
            "Cookie": cookie_header,
        },
        follow_redirects=False,
        raise_error=False,
    )

    assert response.code == 403
    assert open_calls == []


def public_result(*, session, job, attempt_id):
    analysis_id = str(
        uuid5(
            NAMESPACE_URL,
            f"{job['job_id']}:{attempt_id}:{session['session_id']}",
        )
    )
    return {
        "schema_version": 1,
        "analysis_id": analysis_id,
        "job_id": job["job_id"],
        "attempt_id": attempt_id,
        "session_id": session["session_id"],
        "profile_id": session["profile_id"],
        "profile_version": session["profile_version"],
        "profile_content_hash": session["profile_content_hash"],
        "status": "ready",
        "dimension_results": [
            {
                "schema_version": 1,
                "dimension_code": "CUSTOM_A1B2C3D4",
                "decision": {
                    "status": "resolved",
                    "final_evidence_status": "not_observed",
                    "final_level_code": None,
                    "display_label": "未发现明显证据",
                    "source": "llm_evidence",
                },
                "data_quality": {
                    "missing_required_signals": [],
                    "observation_opportunities": 1,
                    "reason_code": None,
                    "reason": None,
                },
                "ai_result": {
                    "confidence": 0.8,
                    "evidence_claims": [],
                    "explanation": "固定合成分析结果",
                },
            }
        ],
        "provenance": {
            "analysis_pipeline_version": "pilot-v1",
            "feature_extractor_version": "pilot-v1",
            "signal_dictionary_version": "pilot-v1",
            "signal_dictionary_hash": session["signal_dictionary_hash"],
            "model_name": "deterministic-test-double",
            "model_version": "1",
            "model_parameters": {"temperature": 0},
            "prompt_version": "pilot-v1",
            "prompt_content_hash": "a" * 64,
            "provider_request_id": None,
            "raw_response_hash": "b" * 64,
            "input_snapshot_hash": job["input_snapshot_hash"],
        },
    }


def publish_public_result(root, result):
    result_path = root / "analyses" / result["analysis_id"] / "result.json"
    result_path.parent.mkdir(mode=0o700, parents=True)
    result_path.write_bytes(canonical_json_bytes(result))
    os.chmod(result_path, 0o600)
    return result_path


async def prepare_reviewable_analysis(jp_fetch, worker):
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)
    event_id = f"{started['session_id']}:1"
    events = [{"session_seq": 1, "event_id": event_id}]
    worker.session_store.append_batch(
        started["session_id"],
        segment_id="20000000-0000-4000-8000-000000000030",
        first_sequence=1,
        last_sequence=1,
        content_hash=sha256_json(
            {
                "first_sequence": 1,
                "last_sequence": 1,
                "segments": events,
            }
        ),
        segments=events,
    )
    finalized = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 1}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    job_id = response_json(finalized)["analysis_job_id"]
    attempt = worker.job_store.begin_attempt(job_id)
    session = worker.session_store.read(started["session_id"])
    job = worker.job_store.get(job_id)
    result = public_result(
        session=session,
        job=job,
        attempt_id=attempt["attempt_id"],
    )
    row = result["dimension_results"][0]
    row["decision"].update(
        {
            "final_evidence_status": "observed",
            "final_level_code": "possible",
            "display_label": "可能出现",
        }
    )
    row["ai_result"]["evidence_claims"] = [
        {
            "event_id": event_id,
            "criterion_id": "support-1",
            "direction": "support",
            "claim": "固定合成证据",
        }
    ]
    publish_public_result(Path(worker.session_store.root), result)
    worker.job_store.finish_attempt(
        job_id,
        attempt["attempt_id"],
        status="ready",
        analysis_id=result["analysis_id"],
        error_code=None,
    )
    return started, event_id


async def test_create_publish_and_read_profile(jp_fetch, monkeypatch, tmp_path):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    created = await create_profile(jp_fetch)

    assert created.code == 201
    draft = response_json(created)
    openapi_validator("ProfileDraftResponse").validate(draft)
    assert_request_id(draft)

    published = await jp_fetch(
        "myextension",
        "dimension-profiles",
        draft["profile_id"],
        "publish",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert published.code == 200
    version = response_json(published)
    openapi_validator("ProfileVersionResponse").validate(version)
    assert version["deployment_status"] == "pilot"
    assert version["preview_status"] == "pending_real_samples"

    fetched = await jp_fetch(
        "myextension",
        "dimension-profiles",
        draft["profile_id"],
        "versions",
        "1",
        raise_error=False,
    )
    assert fetched.code == 200
    fetched_version = response_json(fetched)
    openapi_validator("ProfileVersionResponse").validate(fetched_version)
    assert fetched_version["content_hash"] == version["content_hash"]


async def test_templates_match_closed_openapi_response(jp_fetch):
    response = await jp_fetch(
        "myextension",
        "dimension-templates",
        raise_error=False,
    )

    assert response.code == 200
    payload = response_json(response)
    openapi_validator("TemplateListResponse").validate(payload)
    assert [item["template_id"] for item in payload["templates"]] == [
        "repeated-editing",
        "debug-chain",
        "repeated-run-failures",
        "pause-without-validation",
    ]
    assert all("schema_version" not in item for item in payload["templates"])


async def test_update_list_and_stale_revision_conflict(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    created_response = await create_profile(jp_fetch)
    assert created_response.code == 201
    created = response_json(created_response)
    changed = make_profile_payload()
    changed["dimensions"][0]["question"] = "学生修改后是否立即重新运行验证？"

    updated_response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        created["profile_id"],
        "draft",
        method="PUT",
        body=json.dumps({"revision": 1, "draft": changed}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert updated_response.code == 200
    updated = response_json(updated_response)
    openapi_validator("ProfileDraftResponse").validate(updated)
    assert updated["revision"] == 2
    assert (
        updated["dimensions"][0]["question"]
        == "学生修改后是否立即重新运行验证？"
    )

    stale_response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        created["profile_id"],
        "draft",
        method="PUT",
        body=json.dumps({"revision": 1, "draft": make_profile_payload()}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert_error_response(
        stale_response,
        409,
        "draft_revision_conflict",
    )

    published_response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        created["profile_id"],
        "publish",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert published_response.code == 200

    matching = await jp_fetch(
        "myextension",
        "dimension-profiles",
        params={"problem_id": "  average-debug  "},
        raise_error=False,
    )
    matching_payload = response_json(matching)
    openapi_validator("ProfileListResponse").validate(matching_payload)
    assert [profile["profile_id"] for profile in matching_payload["profiles"]] == [
        created["profile_id"]
    ]

    nonmatching = await jp_fetch(
        "myextension",
        "dimension-profiles",
        params={"problem_id": "different-problem"},
        raise_error=False,
    )
    nonmatching_payload = response_json(nonmatching)
    openapi_validator("ProfileListResponse").validate(nonmatching_payload)
    assert nonmatching_payload["profiles"] == []


@pytest.mark.parametrize("problem_id", ["", "   ", "x" * 201])
async def test_profile_list_rejects_invalid_problem_id_query(
    jp_fetch, monkeypatch, tmp_path, problem_id
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        params={"problem_id": problem_id},
        raise_error=False,
    )

    assert_error_response(response, 400, "invalid_problem_id")


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        ('{"schema_version":', "invalid_json"),
        ("[]", "invalid_json_object"),
    ],
)
async def test_profile_create_rejects_malformed_or_nonobject_json(
    jp_fetch, monkeypatch, tmp_path, body, expected_code
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        method="POST",
        body=body,
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert_error_response(response, 400, expected_code)


async def test_malformed_json_never_logs_request_body_secret(
    jp_fetch, monkeypatch, tmp_path, caplog
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    private_marker = "UNIQUE_MALFORMED_STUDENT_SECRET_7391"
    caplog.set_level("DEBUG")

    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        method="POST",
        body=f'{{"student_text":"{private_marker}"',
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert_error_response(response, 400, "invalid_json")
    assert all(private_marker not in record.getMessage() for record in caplog.records)


async def test_json_reader_does_not_call_body_logging_framework_helper(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    def forbidden_helper(_handler):
        raise AssertionError("get_json_body must not be used")

    monkeypatch.setattr(JsonAPIHandler, "get_json_body", forbidden_helper)

    response = await create_profile(jp_fetch)

    assert response.code == 201


async def test_invalid_utf8_is_a_safe_invalid_json_error(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        method="POST",
        body=b'{"student_text":"\xff"}',
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert_error_response(response, 400, "invalid_json")


async def test_unexpected_json_decoder_error_remains_safe_500(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    private_marker = "UNIQUE_JSON_DECODER_SECRET_4826"
    request_body = json.dumps(make_profile_payload())
    real_loads = json.loads

    def explode(_value):
        raise RuntimeError(private_marker)

    monkeypatch.setattr(json, "loads", explode)
    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        method="POST",
        body=request_body,
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    monkeypatch.setattr(json, "loads", real_loads)

    assert_error_response(response, 500, "internal_error")
    assert private_marker not in response.body.decode("utf-8")


async def test_request_size_is_checked_before_json_parsing(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    oversized_malformed = b'{"student_text":"' + (b"x" * 1_048_576)

    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        method="POST",
        body=oversized_malformed,
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert_error_response(response, 413, "request_too_large")


async def test_draft_request_size_precedes_profile_id_validation(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    oversized_malformed = b'{"student_text":"' + (b"x" * 1_048_576)

    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        "NOT-A-UUID",
        "draft",
        method="PUT",
        body=oversized_malformed,
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert_error_response(response, 413, "request_too_large")


async def test_publish_rejects_malformed_json_object(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    created_response = await create_profile(jp_fetch)
    created = response_json(created_response)

    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        created["profile_id"],
        "publish",
        method="POST",
        body='{"malformed":',
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert_error_response(response, 400, "invalid_json")


async def test_publish_accepts_omitted_body_from_frozen_openapi(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    created_response = await create_profile(jp_fetch)
    created = response_json(created_response)

    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        created["profile_id"],
        "publish",
        method="POST",
        body="",
        raise_error=False,
    )

    assert response.code == 200
    openapi_validator("ProfileVersionResponse").validate(response_json(response))


def test_openapi_rejects_published_v2_without_assessment_tests():
    payload = make_assessment_profile(confirmed=False)
    payload.update(
        {
            "profile_id": "123e4567-e89b-42d3-a456-426614174000",
            "version": 1,
            "content_hash": "a" * 64,
            "deployment_status": "pilot",
            "preview_status": "pending_real_samples",
            "request_id": "223e4567-e89b-42d3-a456-426614174000",
        }
    )
    payload["assessment_tests"] = []

    with pytest.raises(ValidationError):
        openapi_validator("ProfileVersionResponse").validate(payload)


async def test_publish_rejects_nonempty_json_object(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    created_response = await create_profile(jp_fetch)
    created = response_json(created_response)

    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        created["profile_id"],
        "publish",
        method="POST",
        body=json.dumps({"ignored": True}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    error = assert_error_response(
        response,
        422,
        "profile_validation_failed",
    )
    assert error["details"] == {"field": "$", "reason": "unknown_field"}


@pytest.mark.parametrize("semantic", [False, True])
async def test_profile_validation_errors_are_safe_closed_422_responses(
    jp_fetch, monkeypatch, tmp_path, semantic
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    payload = make_profile_payload()
    private_text = "学生隐私-private-payload"
    if semantic:
        payload["dimensions"][0]["dimension_type"] = "knowledge_inference"
        payload["dimensions"][0]["question"] = private_text
    else:
        payload["dimensions"] = []
        payload["title"] = private_text

    response = await create_profile(jp_fetch, payload)

    error = assert_error_response(
        response,
        422,
        "profile_validation_failed",
    )
    assert set(error["details"]) == {"field", "reason"}
    assert error["details"]["field"]
    assert error["details"]["reason"]
    assert private_text not in response.body.decode("utf-8")


async def test_v2_semantic_validation_reports_the_exact_confirmation_field(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    payload = make_assessment_profile()
    payload["knowledge_points"][0]["name"] = "修改后的知识点"

    response = await create_profile(jp_fetch, payload)

    error = assert_error_response(
        response,
        422,
        "profile_validation_failed",
    )
    assert error["details"] == {
        "field": "confirmations.knowledge_points_hash",
        "reason": "stale_knowledge_confirmation",
    }


async def test_draft_validation_details_prefix_nested_draft_path(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    created_response = await create_profile(jp_fetch)
    created = response_json(created_response)
    invalid_draft = make_profile_payload()
    invalid_draft["dimensions"][0]["question"] = ""

    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        created["profile_id"],
        "draft",
        method="PUT",
        body=json.dumps({"revision": 1, "draft": invalid_draft}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    error = assert_error_response(
        response,
        422,
        "profile_validation_failed",
    )
    assert error["details"] == {
        "field": "draft.dimensions[0].question",
        "reason": "too_short",
    }


async def test_missing_profile_and_version_return_safe_404(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    missing_id = "00000000-0000-0000-0000-000000000000"

    publish = await jp_fetch(
        "myextension",
        "dimension-profiles",
        missing_id,
        "publish",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert_error_response(publish, 404, "profile_not_found")

    version = await jp_fetch(
        "myextension",
        "dimension-profiles",
        missing_id,
        "versions",
        "1",
        raise_error=False,
    )
    assert_error_response(version, 404, "profile_version_not_found")


@pytest.mark.parametrize(
    "parts",
    [
        ("dimension-profiles", "NOT-A-UUID", "publish"),
        (
            "dimension-profiles",
            "123E4567-E89B-12D3-A456-426614174000",
            "publish",
        ),
        (
            "dimension-profiles",
            "123e4567-e89b-12d3-a456-426614174000",
            "versions",
            "0",
        ),
    ],
)
async def test_noncanonical_profile_or_version_segments_return_400(
    jp_fetch, monkeypatch, tmp_path, parts
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    is_publish = parts[-1] == "publish"

    response = await jp_fetch(
        "myextension",
        *parts,
        method="POST" if is_publish else "GET",
        body="{}" if is_publish else None,
        headers={"Content-Type": "application/json"} if is_publish else None,
        raise_error=False,
    )

    expected_code = "invalid_profile_id" if len(parts) == 3 else "invalid_version"
    assert_error_response(response, 400, expected_code)


async def test_unsafe_oversized_version_segment_returns_safe_400(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        "123e4567-e89b-12d3-a456-426614174000",
        "versions",
        "9" * 10,
        raise_error=False,
    )

    assert_error_response(response, 400, "invalid_version")


async def test_profile_store_cache_isolated_by_canonical_log_root(
    jp_fetch, monkeypatch, tmp_path
):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(first_root))
    created_response = await create_profile(jp_fetch)
    created = response_json(created_response)
    await jp_fetch(
        "myextension",
        "dimension-profiles",
        created["profile_id"],
        "publish",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(second_root))
    second_list = await jp_fetch(
        "myextension",
        "dimension-profiles",
        raise_error=False,
    )
    assert response_json(second_list)["profiles"] == []

    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(first_root / "nested" / ".."))
    first_list = await jp_fetch(
        "myextension",
        "dimension-profiles",
        raise_error=False,
    )
    assert [item["profile_id"] for item in response_json(first_list)["profiles"]] == [
        created["profile_id"]
    ]


async def test_unexpected_error_is_generic_and_does_not_leak_private_data(
    jp_fetch, monkeypatch, tmp_path
):
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))
    private_body = make_profile_payload()
    private_body["title"] = "Synthetic Learner 731"
    private_markers = (
        "/Users/synthetic-learner/private-course/answer.ipynb",
        "Synthetic Learner 731",
        "ignore previous instructions",
        "test-key-synthetic-only-731",
    )
    private_exception = " | ".join(private_markers)

    def explode(_store, _payload):
        raise RuntimeError(private_exception)

    monkeypatch.setattr(DimensionProfileStore, "create_draft", explode)
    response = await create_profile(jp_fetch, private_body)

    error = assert_error_response(response, 500, "internal_error")
    assert error["retryable"] is False
    response_text = response.body.decode("utf-8")
    for private_value in private_markers:
        assert private_value not in response_text


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "myextension/dimension-templates", None),
        ("GET", "myextension/dimension-profiles", None),
        ("POST", "myextension/dimension-profiles", "{}"),
        (
            "PUT",
            (
                "myextension/dimension-profiles/"
                "123e4567-e89b-12d3-a456-426614174000/draft"
            ),
            "{}",
        ),
        (
            "POST",
            (
                "myextension/dimension-profiles/"
                "123e4567-e89b-12d3-a456-426614174000/publish"
            ),
            "{}",
        ),
        (
            "GET",
            (
                "myextension/dimension-profiles/"
                "123e4567-e89b-12d3-a456-426614174000/versions/1"
            ),
            None,
        ),
        (
            "POST",
            "myextension/assessment-assist/knowledge-points",
            "{}",
        ),
        (
            "POST",
            "myextension/assessment-assist/tests",
            "{}",
        ),
        ("POST", "myextension/sessions/start", "{}"),
        (
            "POST",
            (
                "myextension/sessions/"
                "123e4567-e89b-12d3-a456-426614174000/segments"
            ),
            "{}",
        ),
        (
            "POST",
            (
                "myextension/sessions/"
                "123e4567-e89b-12d3-a456-426614174000/finalize"
            ),
            "{}",
        ),
        (
            "POST",
            (
                "myextension/sessions/"
                "123e4567-e89b-12d3-a456-426614174000/abandon"
            ),
            "{}",
        ),
        (
            "POST",
            (
                "myextension/sessions/"
                "123e4567-e89b-12d3-a456-426614174000/recover"
            ),
            "{}",
        ),
        (
            "GET",
            (
                "myextension/sessions/"
                "123e4567-e89b-12d3-a456-426614174000/brief"
            ),
            None,
        ),
        (
            "GET",
            "myextension/sessions/123e4567-e89b-12d3-a456-426614174000",
            None,
        ),
        (
            "DELETE",
            "myextension/sessions/123e4567-e89b-12d3-a456-426614174000",
            "{}",
        ),
        (
            "GET",
            (
                "myextension/analysis-jobs/"
                "123e4567-e89b-12d3-a456-426614174000"
            ),
            None,
        ),
        (
            "POST",
            (
                "myextension/analysis-jobs/"
                "123e4567-e89b-12d3-a456-426614174000/retry"
            ),
            "{}",
        ),
        (
            "GET",
            (
                "myextension/sessions/"
                "123e4567-e89b-12d3-a456-426614174000/analysis"
            ),
            None,
        ),
        (
            "PATCH",
            (
                "myextension/sessions/"
                "123e4567-e89b-12d3-a456-426614174000/"
                "analysis/CUSTOM_A1B2C3D4/review"
            ),
            "{}",
        ),
        ("POST", "myextension/log-folder/open", "{}"),
        ("POST", "myextension/platform/register", "{}"),
        ("GET", "myextension/platform/context", None),
        ("POST", "myextension/platform/context", ""),
    ],
)
async def test_every_new_api_verb_rejects_unauthenticated_requests(
    http_server_client, jp_base_url, method, path, body
):
    response = await http_server_client.fetch(
        f"{jp_base_url}{path}",
        method=method,
        body=body,
        headers={"Content-Type": "application/json"} if body is not None else None,
        allow_nonstandard_methods=method == "DELETE",
        follow_redirects=False,
        raise_error=False,
    )

    error = assert_error_response(response, 403, "forbidden")
    assert error["retryable"] is False


async def test_framework_unhandled_error_uses_closed_private_envelope(
    jp_fetch, monkeypatch
):
    private_marker = "UNIQUE_FRAMEWORK_EXCEPTION_SECRET_5830"

    def explode(_handler):
        raise RuntimeError(private_marker)

    monkeypatch.setattr(DimensionTemplatesRouteHandler, "get", explode)

    response = await jp_fetch(
        "myextension",
        "dimension-templates",
        raise_error=False,
    )

    error = assert_error_response(response, 500, "internal_error")
    assert set(error) == {
        "schema_version",
        "request_id",
        "code",
        "message",
        "retryable",
    }
    assert private_marker not in response.body.decode("utf-8")


@pytest.mark.parametrize(
    ("parts", "method", "body", "expected_code"),
    [
        (("hello",), "GET", None, 200),
        (("ai-config",), "GET", None, 200),
        (("behavior-events",), "POST", "{}", 400),
        (("run-python-file",), "POST", "{}", 400),
        (("latest-analysis",), "GET", None, 200),
    ],
)
async def test_legacy_routes_remain_registered(
    jp_fetch, parts, method, body, expected_code
):
    response = await jp_fetch(
        "myextension",
        *parts,
        method=method,
        body=body,
        headers={"Content-Type": "application/json"} if body is not None else None,
        raise_error=False,
    )

    assert response.code == expected_code
    assert response_json(response)


async def test_legacy_behavior_endpoint_never_schedules_ai_and_is_deprecated(
    jp_fetch,
    monkeypatch,
    tmp_path,
):
    import myextension.routes as routes_module

    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(tmp_path))

    def forbidden_scheduler(*_args, **_kwargs):
        raise AssertionError("legacy endpoint must not schedule AI")

    monkeypatch.setattr(
        routes_module,
        "schedule_label_segments",
        forbidden_scheduler,
        raising=False,
    )
    response = await jp_fetch(
        "myextension",
        "behavior-events",
        method="POST",
        body=json.dumps(
            {
                "session_id": "0d5f9d13-0000-4000-8000-000000000001",
                "segments": [
                    {
                        "segment_type": "code_writing",
                        "started_at": "2026-07-28T09:00:00+08:00",
                        "ended_at": "2026-07-28T09:00:01+08:00",
                        "duration_ms": 1000,
                    }
                ],
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert response.code == 200
    payload = response_json(response)
    assert payload["llm_labeling"] == "disabled"
    assert (
        payload["deprecation"]
        == "Use /sessions/start, /segments and /finalize."
    )


async def test_segment_api_success_and_exact_replay(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    store = install_segment_boundary(jp_web_app, monkeypatch)
    session_id = "10000000-0000-4000-8000-000000000020"
    body = frozen_segment_batch()

    first = await jp_fetch(
        "myextension",
        "sessions",
        session_id,
        "segments",
        method="POST",
        body=json.dumps(body),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    replay = await jp_fetch(
        "myextension",
        "sessions",
        session_id,
        "segments",
        method="POST",
        body=json.dumps(body),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert first.code == replay.code == 202
    first_payload = response_json(first)
    replay_payload = response_json(replay)
    openapi_validator("SegmentReceiptResponse").validate(first_payload)
    assert {
        key: replay_payload[key]
        for key in (
            "session_id",
            "segment_id",
            "accepted_count",
            "last_contiguous_sequence",
        )
    } == {
        key: first_payload[key]
        for key in (
            "session_id",
            "segment_id",
            "accepted_count",
            "last_contiguous_sequence",
        )
    }
    assert store.calls[0][1] == {
        key: value for key, value in body.items() if key != "schema_version"
    }


async def test_segment_api_maps_content_conflict(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    install_segment_boundary(jp_web_app, monkeypatch)
    session_id = "10000000-0000-4000-8000-000000000021"
    first = frozen_segment_batch()
    await jp_fetch(
        "myextension",
        "sessions",
        session_id,
        "segments",
        method="POST",
        body=json.dumps(first),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    changed = frozen_segment_batch(content_hash="b" * 64)

    response = await jp_fetch(
        "myextension",
        "sessions",
        session_id,
        "segments",
        method="POST",
        body=json.dumps(changed),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert_error_response(response, 409, "segment_conflict")


async def test_segment_api_maps_normalized_sequence_gap(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    install_segment_boundary(jp_web_app, monkeypatch)
    session_id = "10000000-0000-4000-8000-000000000022"

    response = await jp_fetch(
        "myextension",
        "sessions",
        session_id,
        "segments",
        method="POST",
        body=json.dumps(
            frozen_segment_batch(
                segment_id="20000000-0000-4000-8000-000000000022",
                first_sequence=3,
            )
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    error = assert_error_response(response, 409, "sequence_gap")
    assert error["details"] == {
        "field": "first_sequence",
        "reason": "missing_ranges:1-2",
    }


async def test_session_start_finalize_replay_and_public_job_use_live_services(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    worker = install_synchronous_worker(jp_web_app, monkeypatch)
    profile = await create_published_profile(jp_fetch)

    started = await start_pilot_session(jp_fetch, profile)

    openapi_validator("SessionStartResponse").validate(started)
    assert set(started) == {
        "schema_version",
        "request_id",
        "session_id",
        "problem_id",
        "profile_id",
        "profile_version",
        "profile_content_hash",
        "signal_dictionary_version",
        "signal_dictionary_hash",
        "status",
        "last_contiguous_sequence",
    }
    assert worker.session_store.read(started["session_id"])["profile_id"] == profile[
        "profile_id"
    ]
    original_enqueue = worker.enqueue

    def assert_refresh_precedes_enqueue(job_id):
        job = worker.job_store.get(job_id)
        if job["session_id"] == started["session_id"]:
            assert worker.session_store.read_training_record(
                started["session_id"]
            ) is not None
        original_enqueue(job_id)

    worker.enqueue = assert_refresh_precedes_enqueue

    first_finalize = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 0}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    first_record_path = (
        Path(worker.session_store.root)
        / "sessions"
        / started["session_id"]
        / "training_record.json"
    )
    first_record = worker.session_store.read_training_record(
        started["session_id"]
    )
    replay_finalize = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 0}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert first_finalize.code == 202
    assert replay_finalize.code == 202
    finalized = response_json(first_finalize)
    replayed = response_json(replay_finalize)
    record = worker.session_store.read_training_record(started["session_id"])
    validate_schema("training-record-v1", record)
    assert record["ai_analysis"] is None
    assert record["teacher_reviews"] == []
    assert record["session"]["analysis_job_id"] == finalized["analysis_job_id"]
    assert first_record_path.is_file()
    assert first_record["export"]["content_hash"] == record["export"][
        "content_hash"
    ]
    assert str(worker.session_store.root) not in first_finalize.body.decode("utf-8")
    openapi_validator("SessionFinalizeResponse").validate(finalized)
    assert replayed["analysis_job_id"] == finalized["analysis_job_id"]
    assert worker.enqueued == [finalized["analysis_job_id"]]

    job_response = await jp_fetch(
        "myextension",
        "analysis-jobs",
        finalized["analysis_job_id"],
        raise_error=False,
    )
    assert job_response.code == 200
    job_payload = response_json(job_response)
    openapi_validator("AnalysisJobResponse").validate(job_payload)
    assert set(job_payload) == {
        "schema_version",
        "request_id",
        "job_id",
        "session_id",
        "status",
        "active_attempt_id",
        "attempt_ids",
        "analysis_id",
        "error_code",
    }

    analysis_response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "analysis",
        raise_error=False,
    )
    assert analysis_response.code == 202
    openapi_validator("AnalysisJobResponse").validate(
        response_json(analysis_response)
    )

    refresh_failure_session = await start_pilot_session(jp_fetch, profile)
    refresh_failure_event_id = f"{refresh_failure_session['session_id']}:1"
    refresh_failure_events = [
        {"session_seq": 1, "event_id": refresh_failure_event_id}
    ]
    worker.session_store.append_batch(
        refresh_failure_session["session_id"],
        segment_id="20000000-0000-4000-8000-000000000023",
        first_sequence=1,
        last_sequence=1,
        content_hash=sha256_json(
            {
                "first_sequence": 1,
                "last_sequence": 1,
                "segments": refresh_failure_events,
            }
        ),
        segments=refresh_failure_events,
    )
    monkeypatch.setattr(
        TrainingRecordRefresher,
        "refresh",
        lambda self, session_id: False,
    )

    refresh_failure_response = await jp_fetch(
        "myextension",
        "sessions",
        refresh_failure_session["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 1}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert refresh_failure_response.code == 202
    refresh_failure_payload = response_json(refresh_failure_response)
    persisted_session = worker.session_store.read(
        refresh_failure_session["session_id"]
    )
    assert persisted_session["status"] == "finalized"
    assert persisted_session["analysis_job_id"] == refresh_failure_payload[
        "analysis_job_id"
    ]
    assert worker.job_store.get(
        refresh_failure_payload["analysis_job_id"]
    )["session_id"] == refresh_failure_session["session_id"]
    assert worker.enqueued[-1] == refresh_failure_payload["analysis_job_id"]
    assert worker.session_store.read_events(
        refresh_failure_session["session_id"]
    ) == refresh_failure_events

    lifecycle_failure_session = await start_pilot_session(jp_fetch, profile)
    lifecycle_failure_event_id = f"{lifecycle_failure_session['session_id']}:1"
    lifecycle_failure_events = [
        {"session_seq": 1, "event_id": lifecycle_failure_event_id}
    ]
    worker.session_store.append_batch(
        lifecycle_failure_session["session_id"],
        segment_id="20000000-0000-4000-8000-000000000024",
        first_sequence=1,
        last_sequence=1,
        content_hash=sha256_json(
            {
                "first_sequence": 1,
                "last_sequence": 1,
                "segments": lifecycle_failure_events,
            }
        ),
        segments=lifecycle_failure_events,
    )

    def raise_service_constructor(_handler):
        raise RuntimeError("/private/synthetic-service-constructor-secret-731")

    monkeypatch.setattr(
        PilotAPIHandler,
        "_session_log_service",
        raise_service_constructor,
    )
    lifecycle_failure_response = await jp_fetch(
        "myextension",
        "sessions",
        lifecycle_failure_session["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 1}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert lifecycle_failure_response.code == 202
    lifecycle_failure_payload = response_json(lifecycle_failure_response)
    lifecycle_persisted_session = worker.session_store.read(
        lifecycle_failure_session["session_id"]
    )
    assert lifecycle_persisted_session["status"] == "finalized"
    assert lifecycle_persisted_session["analysis_job_id"] == (
        lifecycle_failure_payload["analysis_job_id"]
    )
    assert worker.job_store.get(
        lifecycle_failure_payload["analysis_job_id"]
    )["session_id"] == lifecycle_failure_session["session_id"]
    assert worker.enqueued[-1] == lifecycle_failure_payload["analysis_job_id"]
    assert worker.session_store.read_events(
        lifecycle_failure_session["session_id"]
    ) == lifecycle_failure_events
    assert str(worker.session_store.root) not in (
        lifecycle_failure_response.body.decode("utf-8")
    )


async def test_complete_three_dimension_pilot_flow_is_exact_and_review_is_additive(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    created_response = await create_profile(
        jp_fetch,
        make_three_dimension_profile_payload(),
    )
    assert created_response.code == 201
    created = response_json(created_response)
    published_response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        created["profile_id"],
        "publish",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert published_response.code == 200
    published = response_json(published_response)
    assert len(published["dimensions"]) == 3
    started = await start_pilot_session(jp_fetch, published)
    session_id = started["session_id"]

    def event(
        sequence,
        segment_type,
        started_at,
        ended_at,
        duration_ms,
        **extra,
    ):
        return {
            "session_seq": sequence,
            "event_id": f"{session_id}:{sequence}",
            "segment_type": segment_type,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "notebook_id": "synthetic-notebook",
            "notebook_path": "synthetic.ipynb",
            "cell_id": "synthetic-cell",
            "cell_index": 0,
            **extra,
        }

    events = [
        event(
            1,
            "code_writing",
            "2026-07-29T01:00:00Z",
            "2026-07-29T01:00:31Z",
            31_000,
            inserted_char_count=12,
            cell_source="answer = missing",
        ),
        event(
            2,
            "code_execution",
            "2026-07-29T01:00:31Z",
            "2026-07-29T01:00:32Z",
            1_000,
            execution_result="failure",
            error_type="NameError",
            error_message="synthetic missing value",
            cell_source="answer = missing",
        ),
        event(
            3,
            "code_writing",
            "2026-07-29T01:00:32Z",
            "2026-07-29T01:00:33Z",
            1_000,
            inserted_char_count=8,
            cell_source="answer = 1",
        ),
        event(
            4,
            "code_execution",
            "2026-07-29T01:00:33Z",
            "2026-07-29T01:00:34Z",
            1_000,
            execution_result="success",
            cell_source="answer = 1",
        ),
    ]

    def batch(segment_id, first_sequence, batch_events):
        last_sequence = first_sequence + len(batch_events) - 1
        content = {
            "first_sequence": first_sequence,
            "last_sequence": last_sequence,
            "segments": batch_events,
        }
        return {
            "schema_version": 1,
            "segment_id": segment_id,
            **content,
            "content_hash": sha256_json(content),
        }

    first_batch = batch(
        "20000000-0000-4000-8000-000000000041",
        1,
        events[:2],
    )
    second_batch = batch(
        "20000000-0000-4000-8000-000000000042",
        3,
        events[2:],
    )
    first_receipt = await jp_fetch(
        "myextension",
        "sessions",
        session_id,
        "segments",
        method="POST",
        body=json.dumps(first_batch),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    replay_receipt = await jp_fetch(
        "myextension",
        "sessions",
        session_id,
        "segments",
        method="POST",
        body=json.dumps(first_batch),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    second_receipt = await jp_fetch(
        "myextension",
        "sessions",
        session_id,
        "segments",
        method="POST",
        body=json.dumps(second_batch),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert first_receipt.code == replay_receipt.code == second_receipt.code == 202
    assert {
        key: response_json(first_receipt)[key]
        for key in (
            "session_id",
            "segment_id",
            "accepted_count",
            "last_contiguous_sequence",
        )
    } == {
        key: response_json(replay_receipt)[key]
        for key in (
            "session_id",
            "segment_id",
            "accepted_count",
            "last_contiguous_sequence",
        )
    }
    assert response_json(second_receipt)["last_contiguous_sequence"] == 4

    live_worker = jp_web_app.settings["myextension_analysis_worker"]
    job_store = jp_web_app.settings["myextension_analysis_job_store"]
    session_store = live_worker.session_store
    support_ids = {
        dimension["code"]: next(
            criterion["id"]
            for criterion in dimension["evidence_criteria"]
            if criterion["direction"] == "support"
        )
        for dimension in published["dimensions"]
    }

    def provider(_request, *, timeout_sec):
        assert timeout_sec == 60
        return {
            "model": "synthetic-three-dimension-model",
            "id": "synthetic-provider-request",
            "dimensions": [
                {
                    "dimension_code": code,
                    "evidence_status": "observed",
                    "level_code": "possible",
                    "confidence": 0.8,
                    "evidence_claims": [
                        {
                            "event_id": f"{session_id}:2",
                            "criterion_id": criterion_id,
                            "direction": "support",
                            "claim": "合成失败后出现修改和成功验证。",
                        }
                    ],
                    "explanation": "只使用固定合成事件。",
                }
                for code, criterion_id in support_ids.items()
            ],
        }

    worker = AnalysisWorker(
        Path(session_store.root),
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        synchronous=True,
    )
    monkeypatch.setitem(
        jp_web_app.settings,
        "myextension_analysis_worker",
        worker,
    )
    try:
        finalized_response = await jp_fetch(
            "myextension",
            "sessions",
            session_id,
            "finalize",
            method="POST",
            body=json.dumps({"schema_version": 1, "last_sequence": 4}),
            headers={"Content-Type": "application/json"},
            raise_error=False,
        )
        assert finalized_response.code == 202
        finalized = response_json(finalized_response)
        job_id = finalized["analysis_job_id"]
        job = job_store.get(job_id)
        assert job["status"] == "ready"
        assert len(job["attempt_ids"]) == 1
        assert [
            path.name
            for path in (Path(session_store.root) / "jobs").iterdir()
            if path.is_dir()
        ] == [job_id]

        stored_events = session_store.read_events(session_id)
        real_event_ids = {row["event_id"] for row in stored_events}
        assert [row["session_seq"] for row in stored_events] == [1, 2, 3, 4]
        assert len(real_event_ids) == 4

        analysis_response = await jp_fetch(
            "myextension",
            "sessions",
            session_id,
            "analysis",
            raise_error=False,
        )
        assert analysis_response.code == 200
        result = response_json(analysis_response)
        openapi_validator("SessionAnalysisResponse").validate(result)
        assert {
            row["dimension_code"] for row in result["dimension_results"]
        } == set(support_ids)
        for row in result["dimension_results"]:
            assert row["decision"]["status"] == "resolved"
            assert row["decision"]["final_evidence_status"] == "observed"
            claims = row["ai_result"]["evidence_claims"]
            assert claims
            assert all(claim["event_id"] in real_event_ids for claim in claims)
            assert all(
                claim["criterion_id"] == support_ids[row["dimension_code"]]
                for claim in claims
            )

        result_path = (
            Path(session_store.root)
            / "analyses"
            / result["analysis_id"]
            / "result.json"
        )
        original_result_bytes = result_path.read_bytes()
        target = result["dimension_results"][0]
        review_response = await jp_fetch(
            "myextension",
            "sessions",
            session_id,
            "analysis",
            target["dimension_code"],
            "review",
            method="PATCH",
            body=json.dumps(
                {
                    "revision": target["review"]["revision"],
                    "decision_status": "resolved",
                    "evidence_status": "observed",
                    "level_code": "clear",
                    "evidence_event_ids": [f"{session_id}:2"],
                    "reason_code": "teacher_correction",
                    "comment": "固定合成教师修正",
                }
            ),
            headers={"Content-Type": "application/json"},
            raise_error=False,
        )
        assert review_response.code == 200
        reviewed = response_json(review_response)
        assert reviewed["decision"]["final_level_code"] == "clear"
        assert reviewed["review"] == {"revision": 1, "status": "reviewed"}
        assert result_path.read_bytes() == original_result_bytes
    finally:
        worker.shutdown()


async def test_session_start_rejects_unpublished_and_mismatched_profile(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    install_synchronous_worker(jp_web_app, monkeypatch)
    created_response = await create_profile(jp_fetch)
    created = response_json(created_response)
    unpublished = {
        "schema_version": 1,
        "problem_id": created["problem_id"],
        "profile_id": created["profile_id"],
        "profile_version": 1,
        "profile_content_hash": "a" * 64,
    }

    missing = await jp_fetch(
        "myextension",
        "sessions",
        "start",
        method="POST",
        body=json.dumps(unpublished),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert_error_response(missing, 404, "profile_version_not_found")

    published_response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        created["profile_id"],
        "publish",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    published = response_json(published_response)
    mismatched = dict(unpublished)
    mismatched["profile_content_hash"] = "f" * 64
    mismatched["problem_id"] = published["problem_id"]

    conflict = await jp_fetch(
        "myextension",
        "sessions",
        "start",
        method="POST",
        body=json.dumps(mismatched),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert_error_response(conflict, 409, "profile_mismatch")


@pytest.mark.parametrize(
    ("failure_point", "first_status", "first_code"),
    [
        ("create", 500, "internal_error"),
        ("attach", 500, "internal_error"),
        ("enqueue", 429, "analysis_queue_full"),
    ],
)
async def test_finalize_replays_each_post_finalize_failure_window(
    jp_fetch,
    jp_web_app,
    monkeypatch,
    failure_point,
    first_status,
    first_code,
):
    worker = install_synchronous_worker(jp_web_app, monkeypatch)
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)

    if failure_point == "create":
        real_create = worker.job_store.create
        calls = 0

        def flaky_create(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("synthetic create failure")
            return real_create(**kwargs)

        monkeypatch.setattr(worker.job_store, "create", flaky_create)
    elif failure_point == "attach":
        real_attach = worker.session_store.attach_job
        calls = 0

        def flaky_attach(session_id, job_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("synthetic attach failure")
            return real_attach(session_id, job_id)

        monkeypatch.setattr(worker.session_store, "attach_job", flaky_attach)
    else:
        real_enqueue = worker.enqueue
        calls = 0

        def flaky_enqueue(job_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise AnalysisQueueFullError("synthetic queue full")
            return real_enqueue(job_id)

        monkeypatch.setattr(worker, "enqueue", flaky_enqueue)

    request = {
        "schema_version": 1,
        "last_sequence": 0,
    }
    first = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps(request),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert_error_response(first, first_status, first_code)

    replay = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps(request),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert replay.code == 202
    finalized = response_json(replay)
    openapi_validator("SessionFinalizeResponse").validate(finalized)
    job_dirs = list((Path(worker.session_store.root) / "jobs").iterdir())
    assert len(job_dirs) == 1
    assert finalized["analysis_job_id"] == job_dirs[0].name
    assert worker.enqueued == [finalized["analysis_job_id"]]


async def test_session_lifecycle_projection_and_closed_validation(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    install_synchronous_worker(jp_web_app, monkeypatch)
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)

    abandoned_response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "abandon",
        method="POST",
        body=json.dumps({"reason": "browser_closed"}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert abandoned_response.code == 200
    abandoned = response_json(abandoned_response)
    openapi_validator("SessionStateResponse").validate(abandoned)
    assert abandoned["status"] == "abandoned"
    assert "ended_at" not in abandoned
    assert "abandonment_reason" not in abandoned

    recovered_response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "recover",
        method="POST",
        body=json.dumps(
            {
                "actor": "local-teacher",
                "reason": "继续补传未完成记录",
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert recovered_response.code == 200
    recovered = response_json(recovered_response)
    openapi_validator("SessionStateResponse").validate(recovered)
    assert recovered["status"] == "collecting"

    unknown_field = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "abandon",
        method="POST",
        body=json.dumps({"reason": "browser_closed", "private": "student-data"}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    error = assert_error_response(
        unknown_field,
        422,
        "session_validation_failed",
    )
    assert error["details"] == {
        "field": "$",
        "reason": "unknown_or_missing_field",
    }
    assert "student-data" not in unknown_field.body.decode("utf-8")

    invalid_path = await jp_fetch(
        "myextension",
        "sessions",
        "NOT-A-UUID",
        raise_error=False,
    )
    assert_error_response(invalid_path, 400, "invalid_session_id")


async def test_retry_and_active_job_delete_boundary(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    worker = install_synchronous_worker(jp_web_app, monkeypatch)
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)
    finalized_response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 0}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    job_id = response_json(finalized_response)["analysis_job_id"]

    blocked = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        method="DELETE",
        body=json.dumps(
            {
                "actor": "local-teacher",
                "reason": "试点数据删除",
                "confirm_session_id": started["session_id"],
            }
        ),
        headers={"Content-Type": "application/json"},
        allow_nonstandard_methods=True,
        raise_error=False,
    )
    assert_error_response(blocked, 409, "active_analysis_job")
    assert worker.session_store.read(started["session_id"])

    attempt = worker.job_store.begin_attempt(job_id)
    worker.job_store.finish_attempt(
        job_id,
        attempt["attempt_id"],
        status="error",
        analysis_id=None,
        error_code="analysis_worker_failed",
    )
    retried_response = await jp_fetch(
        "myextension",
        "analysis-jobs",
        job_id,
        "retry",
        method="POST",
        body=json.dumps({"reason": "teacher_requested"}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert retried_response.code == 200
    retried = response_json(retried_response)
    openapi_validator("AnalysisJobResponse").validate(retried)
    assert retried["status"] == "queued"
    assert worker.enqueued == [job_id]


async def test_retry_queue_full_is_resumable_only_for_the_exact_reason(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    live_worker = jp_web_app.settings["myextension_analysis_worker"]
    job_store = jp_web_app.settings["myextension_analysis_job_store"]
    worker = QueueFailsOnceWorker(live_worker.session_store, job_store)
    monkeypatch.setitem(
        jp_web_app.settings,
        "myextension_analysis_worker",
        worker,
    )
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)
    finalized = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 0}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    job_id = response_json(finalized)["analysis_job_id"]
    attempt = job_store.begin_attempt(job_id)
    job_store.finish_attempt(
        job_id,
        attempt["attempt_id"],
        status="error",
        analysis_id=None,
        error_code="analysis_worker_failed",
    )
    worker.enqueued.clear()
    worker.fail_next_enqueue = True
    request = {"reason": "teacher_requested"}

    first = await jp_fetch(
        "myextension",
        "analysis-jobs",
        job_id,
        "retry",
        method="POST",
        body=json.dumps(request),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert_error_response(first, 429, "analysis_queue_full")

    mismatch = await jp_fetch(
        "myextension",
        "analysis-jobs",
        job_id,
        "retry",
        method="POST",
        body=json.dumps({"reason": "different_reason"}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert_error_response(mismatch, 409, "analysis_retry_conflict")

    replay = await jp_fetch(
        "myextension",
        "analysis-jobs",
        job_id,
        "retry",
        method="POST",
        body=json.dumps(request),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert replay.code == 200
    payload = response_json(replay)
    openapi_validator("AnalysisJobResponse").validate(payload)
    assert payload["status"] == "queued"
    assert worker.enqueued == [job_id]
    retry_audit = (
        Path(worker.session_store.root)
        / "jobs"
        / job_id
        / "retry_history.jsonl"
    )
    assert len(retry_audit.read_text(encoding="utf-8").splitlines()) == 1


async def test_terminal_session_delete_returns_closed_projection_and_safe_audit(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    worker = install_synchronous_worker(jp_web_app, monkeypatch)
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)
    finalized = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 0}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    job_id = response_json(finalized)["analysis_job_id"]
    attempt = worker.job_store.begin_attempt(job_id)
    worker.job_store.finish_attempt(
        job_id,
        attempt["attempt_id"],
        status="error",
        analysis_id=None,
        error_code="analysis_worker_failed",
    )
    request = {
        "actor": "local-teacher",
        "reason": "试点数据删除",
        "confirm_session_id": started["session_id"],
    }

    response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        method="DELETE",
        body=json.dumps(request),
        headers={"Content-Type": "application/json"},
        allow_nonstandard_methods=True,
        raise_error=False,
    )

    assert response.code == 200
    payload = response_json(response)
    openapi_validator("DeletedSessionResponse").validate(payload)
    assert set(payload) == {
        "schema_version",
        "request_id",
        "deleted_session_id",
    }
    assert not (
        Path(worker.session_store.root)
        / "sessions"
        / started["session_id"]
    ).exists()
    audit_text = (
        Path(worker.session_store.root)
        / "audit"
        / "session_deletions.jsonl"
    ).read_text(encoding="utf-8")
    assert request["reason"] not in audit_text


async def test_review_overlays_latest_append_without_mutating_result(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    worker = install_synchronous_worker(jp_web_app, monkeypatch)
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)
    event_id = f"{started['session_id']}:1"
    events = [
        {
            "session_seq": 1,
            "event_id": event_id,
            "segment_type": "code_writing",
            "started_at": "2026-07-28T10:00:00Z",
            "ended_at": "2026-07-28T10:00:01Z",
            "duration_ms": 1000,
        }
    ]
    worker.session_store.append_batch(
        started["session_id"],
        segment_id="20000000-0000-4000-8000-000000000010",
        first_sequence=1,
        last_sequence=1,
        content_hash=sha256_json(
            {
                "first_sequence": 1,
                "last_sequence": 1,
                "segments": events,
            }
        ),
        segments=events,
    )
    finalized_response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 1}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    job_id = response_json(finalized_response)["analysis_job_id"]
    attempt = worker.job_store.begin_attempt(job_id)
    session = worker.session_store.read(started["session_id"])
    job = worker.job_store.get(job_id)
    result = public_result(
        session=session,
        job=job,
        attempt_id=attempt["attempt_id"],
    )
    row = result["dimension_results"][0]
    row["decision"].update(
        {
            "final_evidence_status": "observed",
            "final_level_code": "possible",
            "display_label": "可能出现",
        }
    )
    row["ai_result"]["evidence_claims"] = [
        {
            "event_id": event_id,
            "criterion_id": "support-1",
            "direction": "support",
            "claim": "固定合成证据",
        }
    ]
    result_path = publish_public_result(
        Path(worker.session_store.root),
        result,
    )
    original_result_bytes = result_path.read_bytes()
    worker.job_store.finish_attempt(
        job_id,
        attempt["attempt_id"],
        status="ready",
        analysis_id=result["analysis_id"],
        error_code=None,
    )
    root = Path(worker.session_store.root)
    service = SessionLogService(
        root=root,
        session_store=worker.session_store,
        job_store=worker.job_store,
        review_store=ReviewStore(root),
    )
    assert TrainingRecordRefresher(
        service,
        logger=logging.getLogger(__name__),
    ).refresh(started["session_id"])
    training_record_path = (
        root / "sessions" / started["session_id"] / "training_record.json"
    )
    before_record_bytes = training_record_path.read_bytes()
    before_source_state_hash = worker.session_store.read_training_record(
        started["session_id"]
    )["export"]["source_state_hash"]

    review_response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "analysis",
        "CUSTOM_A1B2C3D4",
        "review",
        method="PATCH",
        body=json.dumps(
            {
                "revision": 0,
                "decision_status": "resolved",
                "evidence_status": "observed",
                "level_code": "clear",
                "evidence_event_ids": [event_id],
                "reason_code": "teacher_correction",
                "comment": "根据运行后的修改行为进行修正",
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert review_response.code == 200
    effective = response_json(review_response)
    openapi_validator("DimensionResultResponse").validate(effective)
    assert effective["decision"] == {
        "status": "resolved",
        "final_evidence_status": "observed",
        "final_level_code": "clear",
        "display_label": "明显出现",
        "source": "llm_evidence",
    }
    assert effective["review"] == {"revision": 1, "status": "reviewed"}
    assert result_path.read_bytes() == original_result_bytes
    refreshed_record = worker.session_store.read_training_record(
        started["session_id"]
    )
    assert refreshed_record["teacher_reviews"][-1]["dimension_code"] == (
        "CUSTOM_A1B2C3D4"
    )
    assert refreshed_record["teacher_reviews"][-1]["revision"] == 1
    assert refreshed_record["export"]["source_state_hash"] != (
        before_source_state_hash
    )
    assert training_record_path.read_bytes() != before_record_bytes

    reloaded_response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "analysis",
        raise_error=False,
    )
    assert reloaded_response.code == 200
    reloaded = response_json(reloaded_response)
    openapi_validator("SessionAnalysisResponse").validate(reloaded)
    assert reloaded["dimension_results"][0] == {
        key: value
        for key, value in effective.items()
        if key != "request_id"
    }
    assert result_path.read_bytes() == original_result_bytes

    training_record_bytes_before_failure = training_record_path.read_bytes()
    monkeypatch.setattr(
        TrainingRecordRefresher,
        "refresh",
        lambda self, session_id: False,
    )
    refresh_failure_response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "analysis",
        "CUSTOM_A1B2C3D4",
        "review",
        method="PATCH",
        body=json.dumps(
            {
                "revision": 1,
                "decision_status": "needs_review",
                "evidence_status": None,
                "level_code": None,
                "evidence_event_ids": [],
                "reason_code": "uncertain",
                "comment": "需要再次确认",
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert refresh_failure_response.code == 200
    assert response_json(refresh_failure_response)["review"] == {
        "revision": 2,
        "status": "reviewed",
    }
    assert ReviewStore(root).list(
        result["analysis_id"],
        "CUSTOM_A1B2C3D4",
    )[-1]["revision"] == 2
    assert training_record_path.read_bytes() == training_record_bytes_before_failure

    def raise_unexpected_refresh(_refresher, _session_id):
        raise RuntimeError("/private/synthetic-refresh-call-secret-731")

    monkeypatch.setattr(
        TrainingRecordRefresher,
        "refresh",
        raise_unexpected_refresh,
    )
    lifecycle_failure_response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "analysis",
        "CUSTOM_A1B2C3D4",
        "review",
        method="PATCH",
        body=json.dumps(
            {
                "revision": 2,
                "decision_status": "resolved",
                "evidence_status": "observed",
                "level_code": "possible",
                "evidence_event_ids": [event_id],
                "reason_code": "teacher_correction",
                "comment": "异常隔离后仍保留复核",
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert lifecycle_failure_response.code == 200
    assert response_json(lifecycle_failure_response)["review"] == {
        "revision": 3,
        "status": "reviewed",
    }
    assert ReviewStore(root).list(
        result["analysis_id"],
        "CUSTOM_A1B2C3D4",
    )[-1]["revision"] == 3
    assert training_record_path.read_bytes() == training_record_bytes_before_failure
    assert str(root) not in lifecycle_failure_response.body.decode("utf-8")

    stale_response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "analysis",
        "CUSTOM_A1B2C3D4",
        "review",
        method="PATCH",
        body=json.dumps(
            {
                "revision": 0,
                "decision_status": "needs_review",
                "evidence_status": None,
                "level_code": None,
                "evidence_event_ids": [],
                "reason_code": "uncertain",
                "comment": "需要再次确认",
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    assert_error_response(stale_response, 409, "review_revision_conflict")


@pytest.mark.parametrize(
    ("dimension_code", "body_change", "status", "code"),
    [
        (
            "UNKNOWN_DIMENSION",
            {},
            404,
            "analysis_dimension_not_found",
        ),
        (
            "CUSTOM_A1B2C3D4",
            {"level_code": "unknown"},
            422,
            "review_validation_failed",
        ),
        (
            "CUSTOM_A1B2C3D4",
            {"evidence_event_ids": ["unknown-event"]},
            422,
            "review_validation_failed",
        ),
    ],
)
async def test_review_rejects_invalid_dimension_level_or_event(
    jp_fetch,
    jp_web_app,
    monkeypatch,
    dimension_code,
    body_change,
    status,
    code,
):
    worker = install_synchronous_worker(jp_web_app, monkeypatch)
    started, event_id = await prepare_reviewable_analysis(jp_fetch, worker)
    body = {
        "revision": 0,
        "decision_status": "resolved",
        "evidence_status": "observed",
        "level_code": "possible",
        "evidence_event_ids": [event_id],
        "reason_code": "teacher_correction",
        "comment": "固定合成复核",
    }
    body.update(body_change)

    response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "analysis",
        dimension_code,
        "review",
        method="PATCH",
        body=json.dumps(body),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert_error_response(response, status, code)


async def test_session_analysis_maps_partial_and_error_states(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    worker = install_synchronous_worker(jp_web_app, monkeypatch)
    profile = await create_published_profile(jp_fetch)

    partial_session = await start_pilot_session(jp_fetch, profile)
    partial_finalize = await jp_fetch(
        "myextension",
        "sessions",
        partial_session["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 0}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    partial_job_id = response_json(partial_finalize)["analysis_job_id"]
    partial_attempt = worker.job_store.begin_attempt(partial_job_id)
    partial_job = worker.job_store.get(partial_job_id)
    stored_session = worker.session_store.read(partial_session["session_id"])
    result = public_result(
        session=stored_session,
        job=partial_job,
        attempt_id=partial_attempt["attempt_id"],
    )
    result["status"] = "partial"
    publish_public_result(Path(worker.session_store.root), result)
    worker.job_store.finish_attempt(
        partial_job_id,
        partial_attempt["attempt_id"],
        status="partial",
        analysis_id=result["analysis_id"],
        error_code="ai_not_configured",
    )

    partial_response = await jp_fetch(
        "myextension",
        "sessions",
        partial_session["session_id"],
        "analysis",
        raise_error=False,
    )
    assert partial_response.code == 200
    partial_payload = response_json(partial_response)
    openapi_validator("SessionAnalysisResponse").validate(partial_payload)
    assert partial_payload["status"] == "partial"
    assert partial_payload["error_code"] == "ai_not_configured"

    error_session = await start_pilot_session(jp_fetch, profile)
    error_finalize = await jp_fetch(
        "myextension",
        "sessions",
        error_session["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 0}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    error_job_id = response_json(error_finalize)["analysis_job_id"]
    error_attempt = worker.job_store.begin_attempt(error_job_id)
    worker.job_store.finish_attempt(
        error_job_id,
        error_attempt["attempt_id"],
        status="error",
        analysis_id=None,
        error_code="analysis_worker_failed",
    )

    error_response = await jp_fetch(
        "myextension",
        "sessions",
        error_session["session_id"],
        "analysis",
        raise_error=False,
    )
    error = assert_error_response(
        error_response,
        409,
        "analysis_worker_failed",
    )
    assert error["retryable"] is True


async def test_session_analysis_rejects_cross_session_result_identity(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    worker = install_synchronous_worker(jp_web_app, monkeypatch)
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)
    finalized = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 0}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    job_id = response_json(finalized)["analysis_job_id"]
    attempt = worker.job_store.begin_attempt(job_id)
    session = worker.session_store.read(started["session_id"])
    job = worker.job_store.get(job_id)
    result = public_result(
        session=session,
        job=job,
        attempt_id=attempt["attempt_id"],
    )
    result["session_id"] = "00000000-0000-0000-0000-000000000099"
    publish_public_result(Path(worker.session_store.root), result)
    worker.job_store.finish_attempt(
        job_id,
        attempt["attempt_id"],
        status="ready",
        analysis_id=result["analysis_id"],
        error_code=None,
    )

    response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "analysis",
        raise_error=False,
    )

    assert_error_response(response, 500, "internal_error")
    assert "00000000-0000-0000-0000-000000000099" not in response.body.decode(
        "utf-8"
    )


async def test_session_routes_fail_closed_when_live_services_are_missing(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    monkeypatch.delitem(
        jp_web_app.settings,
        "myextension_analysis_worker",
        raising=False,
    )

    response = await jp_fetch(
        "myextension",
        "sessions",
        "00000000-0000-0000-0000-000000000000",
        raise_error=False,
    )

    error = assert_error_response(response, 503, "service_unavailable")
    assert error["retryable"] is True


async def test_absent_session_and_job_are_404(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    install_synchronous_worker(jp_web_app, monkeypatch)
    missing = "00000000-0000-0000-0000-000000000000"

    session_response = await jp_fetch(
        "myextension",
        "sessions",
        missing,
        raise_error=False,
    )
    job_response = await jp_fetch(
        "myextension",
        "analysis-jobs",
        missing,
        raise_error=False,
    )

    assert_error_response(session_response, 404, "session_not_found")
    assert_error_response(job_response, 404, "analysis_job_not_found")


async def test_present_session_or_job_with_missing_dependency_is_safe_500(
    jp_fetch,
    jp_web_app,
    monkeypatch,
    tmp_path,
):
    worker = install_synchronous_worker(jp_web_app, monkeypatch)
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)
    root = Path(worker.session_store.root)
    profile_path = (
        root
        / "sessions"
        / started["session_id"]
        / "profile.json"
    )
    profile_path.unlink()

    corrupt_session = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        raise_error=False,
    )
    assert_error_response(corrupt_session, 500, "internal_error")

    second = await start_pilot_session(jp_fetch, profile)
    finalized = await jp_fetch(
        "myextension",
        "sessions",
        second["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 0}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    job_id = response_json(finalized)["analysis_job_id"]
    attempt = worker.job_store.begin_attempt(job_id)
    attempt_path = (
        root
        / "jobs"
        / job_id
        / "attempts"
        / f"{attempt['attempt_id']}.json"
    )
    attempt_path.unlink()

    corrupt_job = await jp_fetch(
        "myextension",
        "analysis-jobs",
        job_id,
        raise_error=False,
    )
    error = assert_error_response(corrupt_job, 500, "internal_error")
    response_text = corrupt_job.body.decode("utf-8")
    assert set(error) == {
        "schema_version",
        "request_id",
        "code",
        "message",
        "retryable",
    }
    assert attempt_path.name not in response_text
    assert str(tmp_path) not in response_text


async def test_terminal_job_with_missing_result_dependency_is_safe_500(
    jp_fetch,
    jp_web_app,
    monkeypatch,
):
    worker = install_synchronous_worker(jp_web_app, monkeypatch)
    profile = await create_published_profile(jp_fetch)
    started = await start_pilot_session(jp_fetch, profile)
    finalized = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "finalize",
        method="POST",
        body=json.dumps({"schema_version": 1, "last_sequence": 0}),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )
    job_id = response_json(finalized)["analysis_job_id"]
    attempt = worker.job_store.begin_attempt(job_id)
    analysis_id = str(
        uuid5(
            NAMESPACE_URL,
            f"{job_id}:{attempt['attempt_id']}:{started['session_id']}",
        )
    )
    worker.job_store.finish_attempt(
        job_id,
        attempt["attempt_id"],
        status="ready",
        analysis_id=analysis_id,
        error_code=None,
    )

    response = await jp_fetch(
        "myextension",
        "sessions",
        started["session_id"],
        "analysis",
        raise_error=False,
    )

    assert_error_response(response, 500, "internal_error")
    assert "result.json" not in response.body.decode("utf-8")
