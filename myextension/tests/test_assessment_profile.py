import copy

import pytest
from jsonschema import ValidationError

from myextension.canonical_json import sha256_json
from myextension.dimension_profile_store import (
    DimensionProfileStore,
    ProfileConfirmationError,
    ProfileConflictError,
)
from myextension.profile_validator import ProfileValidationError
from myextension.schema_registry import validate_schema
from myextension.tests.profile_v3_fixtures import profile_v3_draft


def _dimension():
    return {
        "knowledge_point_id": "KP_A1B2C3D4",
        "name": "知识点：循环边界",
        "question": "学生是否通过代码和验证正确处理循环边界？",
        "evidence_criteria": [
            {
                "id": "support-1",
                "direction": "support",
                "statement": "使用边界样例验证循环范围",
            },
            {
                "id": "exclude-1",
                "direction": "exclude",
                "statement": "只得到一次偶然正确输出不计入",
            },
        ],
        "levels": [
            {
                "code": "possible",
                "name": "可能出现",
                "definition": "客户端占位值",
            },
            {
                "code": "clear",
                "name": "明显出现",
                "definition": "客户端占位值",
            },
        ],
        "analysis_config": {
            "mode": "llm_evidence",
            "minimum_observation": {"edit_event_count": 999},
        },
    }


def make_assessment_profile(*, confirmed: bool = True):
    problem_context = {
        "statement": "编写 calculate_average(numbers)，返回数字列表的平均值。",
        "language": "python",
        "submission_contract": {
            "kind": "function",
            "entrypoint": "calculate_average",
        },
    }
    knowledge_points = [
        {
            "id": "KP_A1B2C3D4",
            "name": "循环边界",
            "description": "正确遍历全部输入元素并处理边界。",
            "source": "teacher",
            "order": 0,
        }
    ]
    assessment_tests = [
        {
            "id": "TEST_A1B2C3D4",
            "name": "普通整数列表",
            "knowledge_point_ids": ["KP_A1B2C3D4"],
            "kind": "function_call",
            "input": "[[78, 85, 92, 66, 88]]",
            "expected": "81.8",
            "enabled": True,
            "source": "teacher",
            "order": 0,
        }
    ]
    knowledge_hash = sha256_json(
        {
            "problem_context": problem_context,
            "knowledge_points": knowledge_points,
        }
    )
    tests_hash = sha256_json(
        {
            "problem_context": problem_context,
            "knowledge_points_hash": knowledge_hash,
            "assessment_tests": assessment_tests,
        }
    )
    return {
        "schema_version": 2,
        "problem_id": "average-debug",
        "title": "平均分知识点分析",
        "problem_context": problem_context,
        "knowledge_points": knowledge_points,
        "assessment_tests": assessment_tests,
        "confirmations": {
            "knowledge_points_hash": knowledge_hash if confirmed else None,
            "tests_hash": tests_hash if confirmed else None,
        },
        "dimensions": [_dimension()],
    }


def test_v2_draft_is_normalized_without_enabling_knowledge_inference(tmp_path):
    payload = make_assessment_profile()
    payload["title"] = "  平均分知识点分析  "
    payload["knowledge_points"][0]["name"] = "  循环边界  "

    created = DimensionProfileStore(tmp_path).create_draft(payload)

    assert created["schema_version"] == 2
    assert created["title"] == "平均分知识点分析"
    assert created["knowledge_points"][0]["name"] == "循环边界"
    assert created["dimensions"][0]["knowledge_point_id"] == "KP_A1B2C3D4"
    assert created["dimensions"][0]["code"].startswith("CUSTOM_")
    assert created["dimensions"][0]["analysis_config"] == {
        "mode": "llm_evidence",
        "minimum_observation": {
            "valid_observation_duration_ms": 30000,
            "edit_event_count": 1,
        },
    }


def test_v2_publish_requires_current_teacher_confirmations(tmp_path):
    store = DimensionProfileStore(tmp_path)
    created = store.create_draft(make_assessment_profile(confirmed=False))

    with pytest.raises(ProfileConflictError):
        store.publish(created["profile_id"])


def test_v2_profile_preserves_a_valid_automatic_evaluation_rule(tmp_path):
    payload = make_assessment_profile(confirmed=False)
    payload["knowledge_points"][0]["automatic_evaluation"] = {
        "mode": "all",
        "summary": "创建字典并使用带默认值的安全查询后成功运行。",
        "requirements": [
            {"kind": "successful_execution"},
            {"kind": "dict_literal_assignment"},
            {"kind": "dict_get_with_default"},
        ],
    }

    created = DimensionProfileStore(tmp_path).create_draft(payload)

    assert created["knowledge_points"][0]["automatic_evaluation"] == {
        "mode": "all",
        "summary": "创建字典并使用带默认值的安全查询后成功运行。",
        "requirements": [
            {"kind": "successful_execution"},
            {"kind": "dict_literal_assignment"},
            {"kind": "dict_get_with_default"},
        ],
    }


def test_v2_rejects_a_stale_knowledge_confirmation():
    payload = make_assessment_profile()
    payload["knowledge_points"][0]["name"] = "修改后的知识点"

    with pytest.raises(ProfileValidationError) as caught:
        DimensionProfileStore("/unused").create_draft(payload)

    assert caught.value.code == "stale_knowledge_confirmation"


def test_v2_rejects_tests_that_reference_unknown_knowledge_points():
    payload = make_assessment_profile(confirmed=False)
    payload["assessment_tests"][0]["knowledge_point_ids"] = ["KP_00000000"]

    with pytest.raises(ProfileValidationError) as caught:
        DimensionProfileStore("/unused").create_draft(payload)

    assert caught.value.code == "unknown_knowledge_point_reference"


def test_v2_publish_hash_covers_question_knowledge_tests_and_dimensions(tmp_path):
    store = DimensionProfileStore(tmp_path)
    created = store.create_draft(make_assessment_profile())
    published = store.publish(created["profile_id"])
    reloaded = store.get_version(created["profile_id"], published["version"])

    assert reloaded == published
    assert published["schema_version"] == 2
    assert published["content_hash"] == sha256_json(
        {
            key: value
            for key, value in published.items()
            if key
            not in {
                "content_hash",
                "deployment_status",
                "preview_status",
            }
        }
    )

    changed = copy.deepcopy(make_assessment_profile())
    changed["problem_context"]["statement"] += " 保留一位小数。"
    assert sha256_json(changed) != sha256_json(make_assessment_profile())


def test_v2_question_only_draft_is_saved_before_knowledge_points_exist(tmp_path):
    payload = make_assessment_profile(confirmed=False)
    payload["knowledge_points"] = []
    payload["assessment_tests"] = []
    payload["dimensions"] = []

    created = DimensionProfileStore(tmp_path).create_draft(payload)

    assert created["knowledge_points"] == []
    assert created["assessment_tests"] == []
    assert created["dimensions"] == []
    assert created["confirmations"] == {
        "knowledge_points_hash": None,
        "tests_hash": None,
    }


def test_v2_schema_allows_empty_draft_tests_but_rejects_empty_published_tests():
    draft = make_assessment_profile(confirmed=False)
    draft["assessment_tests"] = []
    validate_schema("profile-draft-v2", draft)
    published = {
        **draft,
        "profile_id": "123e4567-e89b-42d3-a456-426614174000",
        "version": 1,
        "content_hash": "a" * 64,
        "deployment_status": "pilot",
        "preview_status": "pending_real_samples",
    }

    with pytest.raises(ValidationError):
        validate_schema("profile-version-v2", published)


def test_v2_publish_rejects_empty_confirmed_profile(tmp_path):
    payload = make_assessment_profile(confirmed=False)
    payload["knowledge_points"] = []
    payload["assessment_tests"] = []
    payload["dimensions"] = []
    knowledge_hash = sha256_json(
        {
            "problem_context": payload["problem_context"],
            "knowledge_points": [],
        }
    )
    payload["confirmations"] = {
        "knowledge_points_hash": knowledge_hash,
        "tests_hash": sha256_json(
            {
                "problem_context": payload["problem_context"],
                "knowledge_points_hash": knowledge_hash,
                "assessment_tests": [],
            }
        ),
    }
    store = DimensionProfileStore(tmp_path)
    created = store.create_draft(payload)

    with pytest.raises(ProfileConfirmationError):
        store.publish(created["profile_id"])


def test_v2_preserves_exact_test_input_and_expected_whitespace(tmp_path):
    payload = make_assessment_profile(confirmed=False)
    payload["assessment_tests"][0]["input"] = "  [[78, 85, 92, 66, 88]]\n"
    payload["assessment_tests"][0]["expected"] = "\n81.8  "
    knowledge_hash = sha256_json(
        {
            "problem_context": payload["problem_context"],
            "knowledge_points": payload["knowledge_points"],
        }
    )
    payload["confirmations"] = {
        "knowledge_points_hash": knowledge_hash,
        "tests_hash": sha256_json(
            {
                "problem_context": payload["problem_context"],
                "knowledge_points_hash": knowledge_hash,
                "assessment_tests": payload["assessment_tests"],
            }
        ),
    }
    store = DimensionProfileStore(tmp_path)

    created = store.create_draft(payload)
    published = store.publish(created["profile_id"])

    assert created["assessment_tests"][0]["input"] == "  [[78, 85, 92, 66, 88]]\n"
    assert created["assessment_tests"][0]["expected"] == "\n81.8  "
    assert published["assessment_tests"] == created["assessment_tests"]


def _add_duplicate_material_requirement_mapping(payload):
    payload["knowledge_points"].append(
        {
            "id": "KP_ABCDEFGH",
            "material_requirement_id": "REQ_LINK_TAIL_INSERT",
            "name": "重复材料职责",
            "description": "两个知识点不能指向同一材料要求。",
            "source": "teacher",
            "order": 1,
        }
    )
    payload["dimensions"].append(
        {
            "knowledge_point_id": "KP_ABCDEFGH",
            "name": "重复材料职责",
            "question": "该维度使测试聚焦于材料要求唯一性。",
            "evidence_criteria": [
                {
                    "id": "CRIT_ABCDEFGH",
                    "material_requirement_id": "REQ_LINK_TAIL_INSERT",
                    "statement": "此条件仅用于验证重复职责拒绝。",
                    "required": False,
                }
            ],
            "verification_bindings": [
                {
                    "criterion_id": "CRIT_ABCDEFGH",
                    "kind": "detector_profile",
                    "detector_profile_id": "address_undefined_leak_v1",
                }
            ],
            "analysis_config": {"mode": "evidence_binding"},
        }
    )


def _set_test_field_and_rehash(payload, field, value):
    assessment_test = payload["assessment_tests"][0]
    assessment_test[field] = value
    assessment_test["content_hash"] = sha256_json(
        {
            key: item
            for key, item in assessment_test.items()
            if key != "content_hash"
        }
    )


def _add_test_knowledge_mismatch(payload):
    payload["knowledge_points"].append(
        {
            "id": "KP_ABCDEFGH",
            "material_requirement_id": "REQ_SECOND",
            "name": "第二知识点",
            "description": "需要独立的测评维度。",
            "source": "teacher",
            "order": 1,
        }
    )
    _set_test_field_and_rehash(payload, "knowledge_point_ids", ["KP_ABCDEFGH"])
    payload["dimensions"].append(
        {
            "knowledge_point_id": "KP_ABCDEFGH",
            "name": "第二知识点",
            "question": "此维度让测试聚焦于知识点绑定。",
            "evidence_criteria": [
                {
                    "id": "CRIT_ABCDEFGH",
                    "material_requirement_id": "REQ_SECOND",
                    "statement": "第二知识点只由检测器证明。",
                    "required": False,
                }
            ],
            "verification_bindings": [
                {
                    "criterion_id": "CRIT_ABCDEFGH",
                    "kind": "detector_profile",
                    "detector_profile_id": "address_undefined_leak_v1",
                }
            ],
            "analysis_config": {"mode": "evidence_binding"},
        }
    )


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda payload: payload["knowledge_points"].append(
                {**copy.deepcopy(payload["knowledge_points"][0]), "order": 1}
            ),
            "duplicate_knowledge_point_id",
        ),
        (
            lambda payload: payload["assessment_tests"][1].update(
                {"id": payload["assessment_tests"][0]["id"]}
            ),
            "duplicate_assessment_test_id",
        ),
        (
            lambda payload: payload["assessment_tests"][1].update({"order": 2}),
            "invalid_assessment_test_order",
        ),
        (
            lambda payload: _add_test_knowledge_mismatch(payload),
            "assessment_test_knowledge_point_mismatch",
        ),
        (
            lambda payload: _set_test_field_and_rehash(
                payload, "criterion_ids", ["CRIT_ABCDEFGH"]
            ),
            "assessment_test_criterion_mismatch",
        ),
        (
            lambda payload: payload["knowledge_points"].append(
                {
                    "id": "KP_ABCDEFGH",
                    "material_requirement_id": "REQ_SECOND",
                    "name": "第二知识点",
                    "description": "需要独立的测评维度。",
                    "source": "teacher",
                    "order": 1,
                }
            ),
            "missing_knowledge_point_dimension",
        ),
        (
            lambda payload: _add_duplicate_material_requirement_mapping(payload),
            "duplicate_material_requirement_mapping",
        ),
        (
            lambda payload: payload["dimensions"][0]["evidence_criteria"].append(
                {
                    "id": "CRIT_ABCDEFGH",
                    "material_requirement_id": "REQ_LINK_TAIL_INSERT",
                    "statement": "逆置后输出顺序正确。",
                    "required": True,
                }
            ),
            "missing_verification_binding",
        ),
        (
            lambda payload: payload["confirmations"].update({"tests_hash": "0" * 64}),
            "stale_tests_confirmation",
        ),
        (
            lambda payload: payload["assessment_tests"][0].update(
                {"content_hash": "0" * 64}
            ),
            "stale_assessment_test_content_hash",
        ),
    ],
    ids=[
        "duplicate-knowledge-point-id",
        "duplicate-assessment-test-id",
        "non-continuous-test-order",
        "test-knowledge-mismatch",
        "test-criterion-mismatch",
        "missing-dimension",
        "duplicate-material-requirement",
        "missing-required-criterion-binding",
        "stale-tests-confirmation",
        "stale-test-content-hash",
    ],
)
def test_v3_rejects_semantic_draft_contract_violations(mutate, code):
    """Each case catches a broken v3 relationship schema alone cannot express."""
    payload = profile_v3_draft()
    mutate(payload)

    with pytest.raises(ProfileValidationError) as caught:
        DimensionProfileStore("/unused").create_draft(payload)

    assert caught.value.code == code


def test_v3_normalizes_teacher_prose_but_preserves_exact_test_io_bytes(tmp_path):
    """Whitespace in executable input/output is content, unlike teacher prose."""
    payload = profile_v3_draft()
    payload["title"] = "  链表尾插与逆置  "
    payload["assessment_tests"][0]["input"] = "  6\n1 2 3 4 5 6\n"
    payload["assessment_tests"][0]["expected_stdout"] = "\n倒置前为：1 2 3 4 5 6 \n"
    test_without_hash = {
        key: value
        for key, value in payload["assessment_tests"][0].items()
        if key != "content_hash"
    }
    payload["assessment_tests"][0]["content_hash"] = sha256_json(test_without_hash)
    payload["confirmations"] = {
        key: None for key in payload["confirmations"]
    }

    created = DimensionProfileStore(tmp_path).create_draft(payload)

    assert created["title"] == "链表尾插与逆置"
    assert created["assessment_tests"][0]["input"] == "  6\n1 2 3 4 5 6\n"
    assert created["assessment_tests"][0]["expected_stdout"] == "\n倒置前为：1 2 3 4 5 6 \n"


def test_v3_publish_and_reload_preserves_the_complete_immutable_contract(tmp_path):
    """A confirmed, linked C++ profile can become an immutable version."""
    store = DimensionProfileStore(tmp_path)

    created = store.create_draft(profile_v3_draft())
    published = store.publish(created["profile_id"])

    assert published["schema_version"] == 3
    assert published["starter_source"] == created["starter_source"]
    assert store.get_version(created["profile_id"], published["version"]) == published


def test_v3_publish_requires_all_confirmation_hashes(tmp_path):
    """Publishing cannot turn a partially confirmed C++ draft immutable."""
    payload = profile_v3_draft()
    payload["confirmations"]["material_bundle_hash"] = None
    store = DimensionProfileStore(tmp_path)
    created = store.create_draft(payload)

    with pytest.raises(ProfileConfirmationError):
        store.publish(created["profile_id"])
