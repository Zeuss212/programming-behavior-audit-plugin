import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from myextension.schema_registry import schema_path, validate_schema


def _draft_dimension(**overrides):
    dimension = {
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
    dimension.update(overrides)
    return dimension


def _profile_draft(**overrides):
    payload = {
        "schema_version": 1,
        "problem_id": "average-debug",
        "title": "平均分调试题",
        "dimensions": [_draft_dimension()],
    }
    payload.update(overrides)
    return payload


def _profile_version(**overrides):
    payload = {
        "schema_version": 1,
        "profile_id": "123e4567-e89b-12d3-a456-426614174000",
        "version": 1,
        "problem_id": "average-debug",
        "title": "平均分调试题",
        "dimensions": [_draft_dimension()],
        "content_hash": "a" * 64,
        "deployment_status": "pilot",
        "preview_status": "completed",
    }
    payload.update(overrides)
    return payload


def test_profile_draft_accepts_teacher_language_fields():
    validate_schema("profile-draft-v1", {
        "schema_version": 1,
        "problem_id": "average-debug",
        "title": "平均分调试题",
        "dimensions": [{
            "code": "CUSTOM_A1B2C3D4",
            "name": "失败后是否继续验证",
            "question": "学生运行失败后，是否修改相关代码并再次运行？",
            "evidence_criteria": [
                {
                    "id": "support-1",
                    "direction": "support",
                    "statement": "失败后修改相关代码并再次运行"
                },
                {
                    "id": "exclude-1",
                    "direction": "exclude",
                    "statement": "只修改注释不计入"
                }
            ],
            "levels": [
                {
                    "code": "possible",
                    "name": "可能出现",
                    "definition": "存在一次完整但范围有限的相关行为"
                },
                {
                    "code": "clear",
                    "name": "明显出现",
                    "definition": "在多个阶段持续出现相关行为"
                }
            ],
            "teaching_actions": {
                "possible": "结合证据询问学生的调试思路",
                "clear": "安排一次修改后立即验证的短练习"
            },
            "analysis_config": {
                "mode": "llm_evidence",
                "minimum_observation": {
                    "valid_observation_duration_ms": 30000,
                    "edit_event_count": 1
                }
            }
        }]
    })


def test_profile_draft_rejects_unknown_analysis_mode():
    with pytest.raises(ValidationError):
        validate_schema("profile-draft-v1", {
            "schema_version": 1,
            "problem_id": "average-debug",
            "title": "平均分调试题",
            "dimensions": [],
            "analysis_mode": "free_prompt"
        })


def test_dimension_result_requires_null_level_when_not_observed():
    with pytest.raises(ValidationError):
        validate_schema("dimension-result-v1", {
            "schema_version": 1,
            "dimension_code": "CUSTOM_A1B2C3D4",
            "decision": {
                "status": "resolved",
                "final_evidence_status": "not_observed",
                "final_level_code": "possible"
            }
        })


def test_profile_draft_allows_custom_dimension_without_server_code():
    dimension = _draft_dimension()
    del dimension["code"]
    validate_schema("profile-draft-v1", _profile_draft(dimensions=[dimension]))


def test_profile_draft_allows_explicit_no_known_exclusion():
    dimension = _draft_dimension()
    dimension["evidence_criteria"] = [_draft_dimension()["evidence_criteria"][0]]
    dimension["no_known_exclusion"] = True
    validate_schema("profile-draft-v1", _profile_draft(dimensions=[dimension]))


def test_profile_draft_accepts_builtin_code_and_run_count_observation():
    dimension = _draft_dimension(
        code="DEBUG_CHAIN",
        teaching_actions={
            "possible": "询问思路",
            "clear": "安排练习",
            "not_observed": "继续常规观察",
        },
        analysis_config={
            "mode": "llm_evidence",
            "minimum_observation": {"run_count": 0},
        },
    )
    validate_schema("profile-draft-v1", _profile_draft(dimensions=[dimension]))


def test_profile_version_accepts_builtin_code_single_run_count_and_not_observed_action():
    dimension = _draft_dimension(
        code="DEBUG_CHAIN",
        teaching_actions={"possible": "询问", "clear": "练习", "not_observed": "继续观察"},
        analysis_config={"mode": "llm_evidence", "minimum_observation": {"run_count": 1}},
    )
    validate_schema("profile-version-v1", _profile_version(dimensions=[dimension]))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda dimension: dimension.pop("code"),
        lambda dimension: dimension["analysis_config"].update({"minimum_observation": {}}),
        lambda dimension: dimension["analysis_config"].update({"minimum_observation": {"unknown": 1}}),
    ],
)
def test_profile_version_rejects_missing_or_invalid_dimension_contract(mutate):
    dimension = _draft_dimension()
    mutate(dimension)
    with pytest.raises(ValidationError):
        validate_schema("profile-version-v1", _profile_version(dimensions=[dimension]))


@pytest.mark.parametrize(
    "minimum_observation",
    [{}, {"unknown_count": 1}],
)
def test_profile_draft_rejects_empty_or_unknown_minimum_observation(minimum_observation):
    dimension = _draft_dimension(
        analysis_config={
            "mode": "llm_evidence",
            "minimum_observation": minimum_observation,
        }
    )
    with pytest.raises(ValidationError):
        validate_schema("profile-draft-v1", _profile_draft(dimensions=[dimension]))


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda payload: payload["dimensions"][0].update({"analysis_config": {"mode": "rule"}}), "unknown mode"),
        (lambda payload: payload["dimensions"][0].update({"evidence_criteria": [_draft_dimension()["evidence_criteria"][0]]}), "no exclusion declaration"),
        (lambda payload: payload["dimensions"][0].update({"levels": [_draft_dimension()["levels"][0], {"code": "partial", "name": "部分", "definition": "错误等级"}]}), "guided levels"),
        (lambda payload: payload["dimensions"][0].update({"extra": True}), "closed nested object"),
    ],
)
def test_profile_draft_rejects_invalid_dimension_contract(mutate, reason):
    payload = _profile_draft()
    mutate(payload)
    with pytest.raises(ValidationError):
        validate_schema("profile-draft-v1", payload)


@pytest.mark.parametrize(
    ("schema_name", "payload"),
    [
        ("error-v1", {
            "schema_version": 1,
            "request_id": "request-123",
            "code": "invalid_payload",
            "message": "Payload is invalid.",
            "retryable": False,
            "details": {"field": "title", "reason": "required"},
        }),
        ("profile-version-v1", {
            "schema_version": 1,
            "profile_id": "123e4567-e89b-12d3-a456-426614174000",
            "version": 1,
            "problem_id": "average-debug",
            "title": "平均分调试题",
            "dimensions": [_draft_dimension()],
            "content_hash": "a" * 64,
            "deployment_status": "pilot",
            "preview_status": "pending_real_samples",
        }),
        ("session-start-v1", {
            "schema_version": 1,
            "problem_id": "average-debug",
            "profile_id": "123e4567-e89b-12d3-a456-426614174000",
            "profile_version": 1,
            "profile_content_hash": "b" * 64,
        }),
        ("segment-batch-v1", {
            "schema_version": 1,
            "segment_id": "123e4567-e89b-12d3-a456-426614174000",
            "first_sequence": 7,
            "last_sequence": 8,
            "content_hash": "c" * 64,
            "segments": [
                {
                    "session_seq": 7,
                    "event_id": "event-7",
                    "segment_type": "code_writing",
                    "started_at": "2026-07-28T10:00:00Z",
                    "ended_at": "2026-07-28T10:00:01Z",
                    "duration_ms": 1000,
                },
                {
                    "session_seq": 8,
                    "event_id": "event-8",
                    "segment_type": "code_execution",
                    "started_at": "2026-07-28T10:00:02Z",
                    "ended_at": "2026-07-28T10:00:03Z",
                    "duration_ms": 1000,
                },
            ],
        }),
        ("dimension-result-v1", {
            "schema_version": 1,
            "dimension_code": "CUSTOM_A1B2C3D4",
            "decision": {
                "status": "resolved",
                "final_evidence_status": "observed",
                "final_level_code": "clear",
                "display_label": "明显出现",
                "source": "llm_evidence",
            },
            "ai_result": {
                "confidence": 0.8,
                "explanation": "说明",
                "evidence_claims": [{
                    "event_id": "event-7",
                    "criterion_id": "support-1",
                    "direction": "support",
                    "claim": "修改后再次运行",
                }],
            },
        }),
    ],
)
def test_closed_object_schemas_accept_core_valid_payloads(schema_name, payload):
    validate_schema(schema_name, payload)


@pytest.mark.parametrize(
    ("schema_name", "payload"),
    [
        ("error-v1", {"schema_version": 1, "request_id": "request-1", "code": "bad", "message": "Bad.", "retryable": False, "details": {"field": "x", "unknown": True}}),
        ("profile-version-v1", {**_profile_draft(), "profile_id": "not-a-uuid", "version": 1, "content_hash": "a" * 64, "deployment_status": "pilot", "preview_status": "completed"}),
        ("session-start-v1", {"schema_version": 1, "problem_id": "average-debug", "profile_id": "123e4567-e89b-12d3-a456-426614174000", "profile_version": 0, "profile_content_hash": "b" * 64}),
        ("segment-batch-v1", {"schema_version": 1, "segment_id": "123e4567-e89b-12d3-a456-426614174000", "first_sequence": 7, "last_sequence": 8, "content_hash": "c" * 64, "segments": [{"session_seq": 7, "event_id": "event-7", "segment_type": "code_writing", "started_at": "2026-07-28T10:00:00Z", "ended_at": "2026-07-28T10:00:01Z", "duration_ms": 1000}]}),
        ("dimension-result-v1", {"schema_version": 1, "dimension_code": "CUSTOM_A1B2C3D4", "decision": {"status": "needs_review", "final_evidence_status": "observed", "final_level_code": "unknown"}}),
    ],
)
def test_closed_object_schemas_reject_core_invalid_payloads(schema_name, payload):
    with pytest.raises(ValidationError):
        validate_schema(schema_name, payload)


def test_dimension_result_requires_evidence_claim_for_observed_result():
    with pytest.raises(ValidationError):
        validate_schema("dimension-result-v1", {
            "schema_version": 1,
            "dimension_code": "CUSTOM_A1B2C3D4",
            "decision": {
                "status": "resolved",
                "final_evidence_status": "observed",
                "final_level_code": "possible",
                "display_label": "可能出现",
                "source": "llm_evidence",
            },
            "ai_result": {"confidence": 0.8, "explanation": "说明", "evidence_claims": []},
        })


@pytest.mark.parametrize("missing_field", ["event_id", "criterion_id"])
def test_dimension_result_observed_claim_requires_event_and_criterion_references(missing_field):
    claim = {
        "event_id": "event-7",
        "criterion_id": "support-1",
        "direction": "support",
        "claim": "修改后再次运行",
    }
    del claim[missing_field]
    with pytest.raises(ValidationError):
        validate_schema("dimension-result-v1", {
            "schema_version": 1,
            "dimension_code": "DEBUG_CHAIN",
            "decision": {
                "status": "resolved",
                "final_evidence_status": "observed",
                "final_level_code": "possible",
                "display_label": "可能出现",
                "source": "llm_evidence",
            },
            "ai_result": {"confidence": 0.8, "explanation": "说明", "evidence_claims": [claim]},
        })


@pytest.mark.parametrize("status", ["needs_review", "partial", "failed"])
@pytest.mark.parametrize(
    "decision",
    [
        {"final_evidence_status": "observed", "final_level_code": None},
        {"final_evidence_status": None, "final_level_code": "possible"},
    ],
)
def test_dimension_result_non_resolved_status_requires_both_final_fields_null(status, decision):
    with pytest.raises(ValidationError):
        validate_schema("dimension-result-v1", {
            "schema_version": 1,
            "dimension_code": "DEBUG_CHAIN",
            "decision": {"status": status, "display_label": "待复核", "source": "coverage", **decision},
        })


def test_dimension_result_resolved_observed_requires_non_null_level():
    with pytest.raises(ValidationError):
        validate_schema("dimension-result-v1", {
            "schema_version": 1,
            "dimension_code": "DEBUG_CHAIN",
            "decision": {
                "status": "resolved",
                "final_evidence_status": "observed",
                "final_level_code": None,
                "display_label": "明显出现",
                "source": "llm_evidence",
            },
            "ai_result": {"confidence": 0.8, "explanation": "说明", "evidence_claims": [{
                "event_id": "event-7",
                "criterion_id": "support-1",
                "direction": "support",
                "claim": "修改后再次运行",
            }]},
        })


def test_dimension_result_accepts_closed_optional_analysis_metadata():
    validate_schema("dimension-result-v1", {
        "schema_version": 1,
        "dimension_code": "DEBUG_CHAIN",
        "decision": {
            "status": "resolved",
            "final_evidence_status": "observed",
            "final_level_code": "clear",
            "display_label": "明显出现",
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
            "evidence_claims": [{
                "event_id": "event-7",
                "criterion_id": "support-1",
                "direction": "support",
                "claim": "模型证据",
            }],
            "explanation": "模型证据说明",
        },
        "review": {"revision": 0, "status": "unreviewed"},
    })


def test_dimension_result_rejects_duplicate_top_level_evidence_claims():
    payload = {
        "schema_version": 1,
        "dimension_code": "DEBUG_CHAIN",
        "decision": {"status": "resolved", "final_evidence_status": "observed", "final_level_code": "clear", "display_label": "明显", "source": "llm_evidence"},
        "ai_result": {"confidence": 0.8, "evidence_claims": [{"event_id": "event-7", "criterion_id": "support-1", "direction": "support", "claim": "证据"}], "explanation": "说明"},
        "evidence_claims": [{"event_id": "event-7", "criterion_id": "support-1", "direction": "support", "claim": "重复证据"}],
    }
    with pytest.raises(ValidationError):
        validate_schema("dimension-result-v1", payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["decision"].pop("display_label"),
        lambda payload: payload["data_quality"].pop("reason"),
        lambda payload: payload["data_quality"].update({"unknown": True}),
        lambda payload: payload["ai_result"].pop("explanation"),
        lambda payload: payload["ai_result"].update({"confidence": 1.1}),
        lambda payload: payload["ai_result"].update({"explanation": "x" * 501}),
        lambda payload: payload["review"].pop("status"),
        lambda payload: payload["review"].update({"revision": -1}),
    ],
)
def test_dimension_result_rejects_invalid_required_metadata(mutate):
    payload = {
        "schema_version": 1,
        "dimension_code": "DEBUG_CHAIN",
        "decision": {"status": "resolved", "final_evidence_status": "observed", "final_level_code": "clear", "display_label": "明显", "source": "llm_evidence"},
        "data_quality": {"missing_required_signals": [], "observation_opportunities": 1, "reason_code": None, "reason": None},
        "ai_result": {"confidence": 0.8, "evidence_claims": [{"event_id": "event-7", "criterion_id": "support-1", "direction": "support", "claim": "证据"}], "explanation": "说明"},
        "review": {"revision": 0, "status": "reviewed"},
    }
    mutate(payload)
    with pytest.raises(ValidationError):
        validate_schema("dimension-result-v1", payload)


@pytest.mark.parametrize("evidence_status", ["not_observed", "insufficient_evidence", "not_computable"])
def test_dimension_result_requires_null_level_for_non_level_evidence_status(evidence_status):
    with pytest.raises(ValidationError):
        validate_schema("dimension-result-v1", {
            "schema_version": 1,
            "dimension_code": "CUSTOM_A1B2C3D4",
            "decision": {
                "status": "resolved",
                "final_evidence_status": evidence_status,
                "final_level_code": "possible",
                "display_label": "未观察",
                "source": "coverage",
            },
        })


def test_dimension_result_allows_null_final_fields_for_non_resolved_decision():
    validate_schema("dimension-result-v1", {
        "schema_version": 1,
        "dimension_code": "CUSTOM_A1B2C3D4",
        "decision": {
            "status": "needs_review",
            "final_evidence_status": None,
            "final_level_code": None,
            "display_label": "待复核",
            "source": "coverage",
        },
    })


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": 1,
            "segment_id": "123e4567-e89b-12d3-a456-426614174000",
            "first_sequence": 8,
            "last_sequence": 7,
            "content_hash": "c" * 64,
            "segments": [{"session_seq": 8, "event_id": "event-8", "segment_type": "code_writing", "started_at": "2026-07-28T10:00:00Z", "ended_at": "2026-07-28T10:00:01Z", "duration_ms": 1000}],
        },
        {
            "schema_version": 1,
            "segment_id": "123e4567-e89b-12d3-a456-426614174000",
            "first_sequence": 7,
            "last_sequence": 8,
            "content_hash": "c" * 64,
            "segments": [{"session_seq": 7, "event_id": "event-7", "segment_type": "code_writing", "started_at": "2026-07-28T10:00:00Z", "ended_at": "2026-07-28T10:00:01Z", "duration_ms": 1000}],
        },
    ],
)
def test_segment_batch_rejects_invalid_sequence_range_semantics(payload):
    with pytest.raises(ValidationError):
        validate_schema("segment-batch-v1", payload)


def test_segment_batch_accepts_full_real_segment_and_rejects_legacy_or_private_fields():
    payload = {
        "schema_version": 1,
        "segment_id": "123e4567-e89b-12d3-a456-426614174000",
        "first_sequence": 1,
        "last_sequence": 1,
        "content_hash": "c" * 64,
        "segments": [{
            "session_seq": 1,
            "event_id": "123e4567-e89b-42d3-a456-426614174000:1",
            "segment_type": "code_writing",
            "started_at": "2026-07-28T10:00:00Z",
            "ended_at": "2026-07-28T10:00:01Z",
            "duration_ms": 1000,
            "inserted_char_count": 4,
            "deleted_char_count": 0,
            "paste_char_count": 0,
            "cell_source": "x = 1",
            "execution_result": "success",
            "error_type": "SyntheticError",
            "error_message": "fixed synthetic error",
            "deleted_content": "x",
            "thinking_of": "synthetic concept",
            "previous_cell_index": 0,
            "next_cell_index": 1,
            "previous_notebook_path": "before.ipynb",
            "next_notebook_path": "after.ipynb",
            "kernel_status": "idle",
            "deleted_is_full_line": False,
            "had_paste": False,
            "document_type": "notebook_cell",
            "file_path": "synthetic.py",
            "file_name": "synthetic.py",
            "notebook_path": "synthetic.ipynb",
            "notebook_id": "notebook-synthetic",
            "cell_id": "cell-synthetic",
            "cell_index": 0,
            "cell_type": "code",
        }],
    }
    validate_schema("segment-batch-v1", payload)

    for forbidden in ("sequence", "private_student_id"):
        invalid = json.loads(json.dumps(payload))
        invalid["segments"][0][forbidden] = 1
        with pytest.raises(ValidationError):
            validate_schema("segment-batch-v1", invalid)


def test_schema_lookup_rejects_unsafe_and_unknown_names():
    with pytest.raises(ValueError, match="Invalid schema name"):
        schema_path("../profile-draft-v1")
    with pytest.raises(KeyError):
        schema_path("not-a-schema-v1")


OPENAPI_PATH = Path(__file__).parents[2] / "docs" / "openapi" / "myextension-v1.yaml"


def _resolve_ref(document, reference, base_path):
    if reference.startswith("#/"):
        target = document
        for part in reference[2:].split("/"):
            target = target[part]
        return target

    path_part, _, fragment = reference.partition("#")
    target_path = (base_path.parent / path_part).resolve()
    target = json.loads(target_path.read_text(encoding="utf-8"))
    if fragment:
        for part in fragment.removeprefix("/").split("/"):
            target = target[part]
    return target


def _resolve_openapi_node(document, node, base_path):
    while "$ref" in node:
        node = _resolve_ref(document, node["$ref"], base_path)
    return node


def _openapi_validator(document, schema_name):
    resources = [(OPENAPI_PATH.as_uri(), Resource(contents=document, specification=DRAFT202012))]
    for schema_file in (OPENAPI_PATH.parents[2] / "myextension" / "api_schemas").glob("*.json"):
        resources.append((schema_file.as_uri(), Resource.from_contents(json.loads(schema_file.read_text(encoding="utf-8")))))
    return Draft202012Validator(
        {"$ref": f"{OPENAPI_PATH.as_uri()}#/components/schemas/{schema_name}"},
        registry=Registry().with_resources(resources),
    )


def test_openapi_response_schemas_reject_missing_published_code_and_observed_evidence():
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    published_dimension = _draft_dimension()
    del published_dimension["code"]
    with pytest.raises(ValidationError):
        _openapi_validator(document, "ProfileVersionResponse").validate({
            "schema_version": 1,
            "request_id": "request-1",
            "profile_id": "123e4567-e89b-12d3-a456-426614174000",
            "problem_id": "average-debug",
            "title": "平均分调试题",
            "version": 1,
            "dimensions": [published_dimension],
            "content_hash": "a" * 64,
            "deployment_status": "pilot",
            "preview_status": "completed",
        })
    with pytest.raises(ValidationError):
        _openapi_validator(document, "DimensionResultResponse").validate({
            "schema_version": 1,
            "request_id": "request-1",
            "dimension_code": "DEBUG_CHAIN",
            "decision": {
                "status": "resolved",
                "final_evidence_status": "observed",
                "final_level_code": "clear",
                "display_label": "明显出现",
                "source": "llm_evidence",
            },
            "ai_result": {"confidence": 0.8, "evidence_claims": [], "explanation": "说明"},
        })


def test_openapi_template_and_review_payload_contracts_are_validated():
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    dimension = _draft_dimension()
    _openapi_validator(document, "TemplateListResponse").validate({
        "schema_version": 1,
        "request_id": "request-1",
        "templates": [{
            "template_id": "debug-chain",
            "version": 1,
            "deployment_status": "pilot",
            **dimension,
            "examples": [
                {"kind": "positive", "summary": "包含调试链"},
                {"kind": "negative", "summary": "缺少调试链"},
            ],
        }],
    })
    review_validator = _openapi_validator(document, "DimensionReview")
    review_validator.validate({
        "revision": 0,
        "decision_status": "resolved",
        "evidence_status": "observed",
        "level_code": "possible",
        "evidence_event_ids": ["event-7"],
        "reason_code": "teacher_confirmed",
        "comment": "教师确认",
    })
    with pytest.raises(ValidationError):
        review_validator.validate({
            "revision": -1,
            "decision_status": "resolved",
            "evidence_status": "observed",
            "level_code": "possible",
            "evidence_event_ids": ["event-7"],
            "reason_code": "teacher_confirmed",
            "comment": "教师确认",
        })


def test_openapi_session_analysis_rejects_duplicate_top_level_evidence_claims():
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    result = {
        "schema_version": 1,
        "dimension_code": "DEBUG_CHAIN",
        "decision": {"status": "resolved", "final_evidence_status": "observed", "final_level_code": "clear", "display_label": "明显", "source": "llm_evidence"},
        "ai_result": {"confidence": 0.8, "evidence_claims": [{"event_id": "event-7", "criterion_id": "support-1", "direction": "support", "claim": "证据"}], "explanation": "说明"},
    }
    response = {
        "schema_version": 1,
        "request_id": "request-1",
        "analysis_id": "123e4567-e89b-12d3-a456-426614174000",
        "job_id": "123e4567-e89b-12d3-a456-426614174000",
        "attempt_id": "123e4567-e89b-12d3-a456-426614174000",
        "session_id": "123e4567-e89b-12d3-a456-426614174000",
        "profile_id": "123e4567-e89b-12d3-a456-426614174000",
        "profile_version": 1,
        "profile_content_hash": "a" * 64,
        "status": "ready",
        "error_code": None,
        "dimension_results": [result],
        "provenance": {
            "analysis_pipeline_version": "1",
            "feature_extractor_version": "1",
            "signal_dictionary_version": "pilot-v1",
            "signal_dictionary_hash": "a" * 64,
            "model_name": "model",
            "model_version": None,
            "model_parameters": {"temperature": 0},
            "prompt_version": "1",
            "prompt_content_hash": "a" * 64,
            "provider_request_id": None,
            "raw_response_hash": "a" * 64,
            "input_snapshot_hash": "a" * 64,
        },
    }
    validator = _openapi_validator(document, "SessionAnalysisResponse")
    validator.validate(response)
    response["dimension_results"][0]["evidence_claims"] = [{"event_id": "event-7", "criterion_id": "support-1", "direction": "support", "claim": "重复证据"}]
    with pytest.raises(ValidationError):
        validator.validate(response)


def test_openapi_contract_has_authenticated_typed_operations_and_resolvable_references():
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    expected_success_responses = {
        ("/myextension/platform/context", "get"): "PlatformContextResponse",
        ("/myextension/platform/context", "post"): "PlatformContextResponse",
        ("/myextension/platform/capture/bootstrap", "post"): "PlatformCaptureBootstrapResponse",
        ("/myextension/dimension-templates", "get"): "TemplateListResponse",
        ("/myextension/dimension-profiles", "get"): "ProfileListResponse",
        ("/myextension/dimension-profiles", "post"): "ProfileDraftResponse",
        ("/myextension/dimension-profiles/{profile_id}/draft", "put"): "ProfileDraftResponse",
        ("/myextension/dimension-profiles/{profile_id}/publish", "post"): "ProfileVersionResponse",
        ("/myextension/dimension-profiles/{profile_id}/versions/{version}", "get"): "ProfileVersionResponse",
        ("/myextension/assessment-assist/knowledge-points", "post"): "AssessmentKnowledgeResponse",
        ("/myextension/assessment-assist/tests", "post"): "AssessmentTestsResponse",
        ("/myextension/log-folder/open", "post"): "LogFolderOpenResponse",
        ("/myextension/sessions/start", "post"): "SessionStartResponse",
        ("/myextension/sessions/{session_id}/segments", "post"): "SegmentReceiptResponse",
        ("/myextension/sessions/{session_id}/finalize", "post"): "SessionFinalizeResponse",
        ("/myextension/sessions/{session_id}/logs", "get"): "SessionLogListResponse",
        ("/myextension/sessions/{session_id}/brief", "get"): "ClassroomBriefResponse",
        ("/myextension/sessions/{session_id}/abandon", "post"): "SessionStateResponse",
        ("/myextension/sessions/{session_id}/recover", "post"): "SessionStateResponse",
        ("/myextension/sessions/{session_id}", "get"): "SessionStateResponse",
        ("/myextension/sessions/{session_id}", "delete"): "DeletedSessionResponse",
        ("/myextension/analysis-jobs/{job_id}", "get"): "AnalysisJobResponse",
        ("/myextension/analysis-jobs/{job_id}/retry", "post"): "AnalysisJobResponse",
        ("/myextension/sessions/{session_id}/analysis", "get"): "SessionAnalysisResponse",
        ("/myextension/sessions/{session_id}/analysis/{dimension_code}/review", "patch"): "DimensionResultResponse",
    }
    expected_request_bodies = {
        ("/myextension/dimension-profiles", "post"): "ProfileDraft",
        ("/myextension/dimension-profiles/{profile_id}/draft", "put"): "ProfileDraftUpdate",
        ("/myextension/assessment-assist/knowledge-points", "post"): "AssessmentKnowledgeRequest",
        ("/myextension/assessment-assist/tests", "post"): "AssessmentTestsRequest",
        ("/myextension/log-folder/open", "post"): "LogFolderOpen",
        ("/myextension/sessions/start", "post"): "SessionStart",
        ("/myextension/sessions/{session_id}/segments", "post"): "SegmentBatch",
        ("/myextension/sessions/{session_id}/finalize", "post"): "SessionFinalize",
        ("/myextension/sessions/{session_id}/abandon", "post"): "SessionAbandon",
        ("/myextension/sessions/{session_id}/recover", "post"): "SessionRecover",
        ("/myextension/sessions/{session_id}", "delete"): "SessionDelete",
        ("/myextension/analysis-jobs/{job_id}/retry", "post"): "AnalysisRetry",
        ("/myextension/sessions/{session_id}/analysis/{dimension_code}/review", "patch"): "DimensionReview",
    }
    expected_parameters = {
        ("/myextension/platform/context", "get"): [],
        ("/myextension/platform/context", "post"): [],
        ("/myextension/platform/capture/bootstrap", "post"): [],
        ("/myextension/dimension-templates", "get"): [],
        ("/myextension/dimension-profiles", "get"): ["ProblemId"],
        ("/myextension/dimension-profiles", "post"): [],
        ("/myextension/dimension-profiles/{profile_id}/draft", "put"): ["ProfileId"],
        ("/myextension/dimension-profiles/{profile_id}/publish", "post"): ["ProfileId"],
        ("/myextension/dimension-profiles/{profile_id}/versions/{version}", "get"): ["ProfileId", "Version"],
        ("/myextension/assessment-assist/knowledge-points", "post"): [],
        ("/myextension/assessment-assist/tests", "post"): [],
        ("/myextension/log-folder/open", "post"): [],
        ("/myextension/sessions/start", "post"): [],
        ("/myextension/sessions/{session_id}/segments", "post"): ["SessionId"],
        ("/myextension/sessions/{session_id}/finalize", "post"): ["SessionId"],
        ("/myextension/sessions/{session_id}/logs", "get"): ["SessionId"],
        ("/myextension/sessions/{session_id}/brief", "get"): ["SessionId"],
        ("/myextension/sessions/{session_id}/abandon", "post"): ["SessionId"],
        ("/myextension/sessions/{session_id}/recover", "post"): ["SessionId"],
        ("/myextension/sessions/{session_id}", "get"): ["SessionId"],
        ("/myextension/sessions/{session_id}", "delete"): ["SessionId"],
        ("/myextension/analysis-jobs/{job_id}", "get"): ["JobId"],
        ("/myextension/analysis-jobs/{job_id}/retry", "post"): ["JobId"],
        ("/myextension/sessions/{session_id}/analysis", "get"): ["SessionId"],
        ("/myextension/sessions/{session_id}/analysis/{dimension_code}/review", "patch"): ["SessionId", "DimensionCode"],
    }

    operations = {
        (path, method): operation
        for path, item in document["paths"].items()
        for method, operation in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }
    raw_operations = {
        ("/myextension/sessions/{session_id}/logs/{kind}", "get"),
        ("/myextension/sessions/{session_id}/logs/{kind}/download", "get"),
    }
    assert set(operations) == set(expected_success_responses) | raw_operations
    assert document["components"]["securitySchemes"] == {
        "JupyterServerAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Use Jupyter token <value> in Authorization; same-origin login cookies are server-managed.",
        }
    }
    assert document["components"]["parameters"]["ProblemId"] == {
        "name": "problem_id",
        "in": "query",
        "required": False,
        "schema": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
        },
    }

    custom_statuses = {
        ("/myextension/platform/context", "get"): {
            "200", "401", "403", "409", "500", "503"
        },
        ("/myextension/platform/context", "post"): {
            "200", "401", "403", "404", "409", "422", "500", "502", "503"
        },
        ("/myextension/platform/capture/bootstrap", "post"): {
            "200", "401", "403", "404", "409", "500", "503"
        },
        ("/myextension/log-folder/open", "post"): {
            "200", "401", "403", "409", "413", "422", "500"
        },
        ("/myextension/sessions/{session_id}/logs", "get"): {
            "200", "400", "401", "403", "404", "500"
        },
        ("/myextension/sessions/{session_id}/brief", "get"): {
            "200", "400", "401", "403", "404", "409", "500"
        },
    }

    for operation_key, expected_response in expected_success_responses.items():
        operation = operations[operation_key]
        assert operation["security"] == [{"JupyterServerAuth": []}]
        assert operation.get("parameters", []) == [
            {"$ref": f"#/components/parameters/{parameter}"}
            for parameter in expected_parameters[operation_key]
        ]
        expected_statuses = set(
            custom_statuses.get(
                operation_key,
                {"400", "401", "403", "404", "409", "413", "422", "429", "500"},
            )
        )
        if operation_key in {
            ("/myextension/assessment-assist/knowledge-points", "post"),
            ("/myextension/assessment-assist/tests", "post"),
        }:
            expected_statuses.remove("404")
            expected_statuses.add("502")
        expected_statuses.add("201" if operation_key == ("/myextension/dimension-profiles", "post") or operation_key == ("/myextension/sessions/start", "post") else "202" if operation_key in {("/myextension/sessions/{session_id}/segments", "post"), ("/myextension/sessions/{session_id}/finalize", "post")} else "200")
        if operation_key == ("/myextension/sessions/{session_id}/analysis", "get"):
            expected_statuses.add("202")
        assert set(operation["responses"]) == expected_statuses
        primary_status = (
            "201" if operation_key in {
                ("/myextension/dimension-profiles", "post"),
                ("/myextension/sessions/start", "post"),
            }
            else "202" if operation_key in {
                ("/myextension/sessions/{session_id}/segments", "post"),
                ("/myextension/sessions/{session_id}/finalize", "post"),
            }
            else "200"
        )
        assert operation["responses"][primary_status] == {
            "$ref": f"#/components/responses/{expected_response}"
        }
        if operation_key in expected_request_bodies:
            assert operation["requestBody"] == {
                "$ref": f"#/components/requestBodies/{expected_request_bodies[operation_key]}"
            }
        for response in operation["responses"].values():
            resolved_response = _resolve_openapi_node(document, response, OPENAPI_PATH)
            response_schema = _resolve_openapi_node(
                document,
                resolved_response["content"]["application/json"]["schema"],
                OPENAPI_PATH,
            )
            variants = response_schema.get("oneOf", [response_schema])
            for variant in variants:
                resolved_variant = _resolve_openapi_node(
                    document,
                    variant,
                    OPENAPI_PATH,
                )
                assert resolved_variant.get("type") == "object", (
                    operation_key,
                    response,
                    resolved_variant,
                )
                assert resolved_variant["additionalProperties"] is False
                assert "request_id" in resolved_variant["required"]

    for operation_key in raw_operations:
        operation = operations[operation_key]
        assert operation["security"] == [{"JupyterServerAuth": []}]
        assert operation["parameters"] == [
            {"$ref": "#/components/parameters/SessionId"},
            {"$ref": "#/components/parameters/SessionLogKind"},
        ]
        expected_statuses = {"200", "400", "401", "403", "404", "409", "500"}
        if operation_key == (
            "/myextension/sessions/{session_id}/logs/{kind}",
            "get",
        ):
            expected_statuses.add("413")
        assert set(operation["responses"]) == expected_statuses
        success_content = operation["responses"]["200"]["content"]
        assert success_content
        for status, response in operation["responses"].items():
            if status == "200":
                continue
            assert response["$ref"].startswith("#/components/responses/")

    inline_log_content = operations[
        ("/myextension/sessions/{session_id}/logs/{kind}", "get")
    ]["responses"]["200"]["content"]
    assert inline_log_content == {
        "application/json": {"schema": {"type": "object"}},
        "text/markdown": {"schema": {"type": "string"}},
    }
    download_log_content = operations[
        ("/myextension/sessions/{session_id}/logs/{kind}/download", "get")
    ]["responses"]["200"]["content"]
    assert download_log_content == {
        "application/json": {
            "schema": {"type": "string", "format": "binary"}
        },
        "text/markdown": {
            "schema": {"type": "string", "format": "binary"}
        },
    }

    assert operations[("/myextension/sessions/{session_id}/analysis", "get")]["responses"]["202"] == {
        "$ref": "#/components/responses/AnalysisJobResponse"
    }

    log_folder_open = operations[("/myextension/log-folder/open", "post")]
    assert log_folder_open["operationId"] == "openLogFolder"
    assert log_folder_open["requestBody"] == {
        "$ref": "#/components/requestBodies/LogFolderOpen"
    }
    assert document["components"]["requestBodies"]["LogFolderOpen"] == {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "maxProperties": 0,
                }
            }
        },
    }
    assert document["components"]["schemas"]["LogFolderOpenResponse"] == {
        "$ref": "../../myextension/api_schemas/log-folder-open-response-v1.json"
    }
    _openapi_validator(document, "LogFolderOpenResponse").validate({
        "schema_version": 1,
        "request_id": "123e4567-e89b-12d3-a456-426614174000",
        "opened": True,
        "platform": "windows",
    })

    def visit(node):
        if isinstance(node, dict):
            if "$ref" in node:
                _resolve_ref(document, node["$ref"], OPENAPI_PATH)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(document)


def test_obsolete_public_log_schemas_are_removed():
    schema_root = Path(__file__).parents[1] / "api_schemas"

    assert not {
        "session-log-" "list-v1.json",
        "session-log-" "detail-v1.json",
        "training-record-" "response-v1.json",
    } & {path.name for path in schema_root.glob("*.json")}
    assert (schema_root / "training-record-v1.json").is_file()


def test_openapi_session_id_is_lowercase_canonical_uuid():
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert document["components"]["parameters"]["SessionId"]["schema"] == {
        "$ref": "#/components/schemas/CanonicalUuid"
    }
    validator = _openapi_validator(document, "CanonicalUuid")
    validator.validate("123e4567-e89b-12d3-a456-426614174000")
    with pytest.raises(ValidationError):
        validator.validate("123E4567-E89B-12D3-A456-426614174000")
