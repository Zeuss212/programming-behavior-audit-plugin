import importlib.util
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import ValidationError

from classroom_sync.domain.schemas import ClassroomSchemaRegistry


@pytest.fixture
def schema_registry() -> ClassroomSchemaRegistry:
    repository_root = Path(__file__).resolve().parents[4]
    return ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")


def valid_student_brief() -> dict[str, object]:
    return {
        "schema_version": 1,
        "brief_id": "a3eaa710-9d0c-4fef-99ac-c5b7a042cc51",
        "session_id": "23d7d803-524a-4d9f-b8bd-152a540dba12",
        "assignment_id": "d7647a1a-89c3-4c6d-9b5f-7e803918aa9d",
        "plan_id": "2b16b5c0-4e58-48f9-9448-9067de005e4a",
        "plan_version": 1,
        "revision": 1,
        "status": "completed",
        "data_completeness": "complete",
        "submission_reason": "student_manual",
        "submitted_at": "2026-08-12T08:40:00Z",
        "evidence_cutoff_at": "2026-08-12T08:45:00Z",
        "active_duration_ms": 1920000,
        "summary": "完成了主要功能，建议复核边界输入。",
        "knowledge_points": [
            {
                "knowledge_point_id": "kp-dict",
                "name": "字典数据结构",
                "status": "partial",
                "evidence_refs": ["chunk-3#event-18"],
                "demonstrated": "能够创建和读取字典。",
                "gap": "未证明空键处理。",
                "teacher_suggestion": "查看失败测试并追问边界输入。",
            }
        ],
        "process_overview": ["完成两次运行并根据一次错误修改代码。"],
        "issues": ["缺少空键输入的验证。"],
        "ai_analysis_status": "not_requested",
        "generated_at": "2026-08-12T08:40:05Z",
    }


def valid_contract_payloads() -> dict[str, dict[str, object]]:
    profile_draft = {
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
                    {
                        "code": "possible",
                        "name": "可能掌握",
                        "definition": "有一次正确读取。",
                    },
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
                "analysis_config": {
                    "mode": "llm_evidence",
                    "minimum_observation": {"run_count": 1},
                },
            }
        ],
    }
    profile_version = {
        **profile_draft,
        "profile_id": "2b16b5c0-4e58-48f9-9448-9067de005e4a",
        "version": 1,
        "content_hash": "a" * 64,
        "deployment_status": "pilot",
        "preview_status": "pending_real_samples",
    }
    return {
        "plan-draft": {
            "schema_version": 1,
            "draft_id": "09e4e1cc-9155-42dd-a951-632148040bd8",
            "space_id": "classroom-space",
            "parent_algorithm_id": "parent-algorithm",
            "title": "字典课堂练习",
            "profile": profile_draft,
            "scheduled_start_at": "2026-08-12T08:00:00Z",
            "scheduled_end_at": "2026-08-12T08:30:00Z",
            "ai_policy": "prohibited",
            "revision": 0,
            "updated_at": "2026-08-12T07:50:00Z",
        },
        "plan-version": {
            "schema_version": 1,
            "plan_id": "2b16b5c0-4e58-48f9-9448-9067de005e4a",
            "version": 1,
            "space_id": "classroom-space",
            "parent_algorithm_id": "parent-algorithm",
            "profile": profile_version,
            "content_hash": "b" * 64,
            "scheduled_start_at": "2026-08-12T08:00:00Z",
            "scheduled_end_at": "2026-08-12T08:30:00Z",
            "ai_policy": "prohibited",
            "published_at": "2026-08-12T07:55:00Z",
        },
        "student-assignment": {
            "schema_version": 1,
            "assignment_id": "d7647a1a-89c3-4c6d-9b5f-7e803918aa9d",
            "space_id": "classroom-space",
            "parent_algorithm_id": "parent-algorithm",
            "child_algorithm_id": "child-algorithm",
            "workbench_id": "student-workbench",
            "student_id": "student-001",
            "plan_id": "2b16b5c0-4e58-48f9-9448-9067de005e4a",
            "plan_version": 1,
            "status": "pending_acceptance",
            "scheduled_start_at": "2026-08-12T08:00:00Z",
            "scheduled_end_at": "2026-08-12T08:30:00Z",
        },
        "monitor-session": {
            "schema_version": 1,
            "session_id": "23d7d803-524a-4d9f-b8bd-152a540dba12",
            "assignment_id": "d7647a1a-89c3-4c6d-9b5f-7e803918aa9d",
            "plan_id": "2b16b5c0-4e58-48f9-9448-9067de005e4a",
            "plan_version": 1,
            "status": "collecting",
            "scheduled_end_at": "2026-08-12T08:30:00Z",
            "actual_end_at": "2026-08-12T08:30:00Z",
            "evidence_cutoff_at": "2026-08-12T08:45:00Z",
            "last_activity_at": "2026-08-12T08:10:00Z",
            "last_heartbeat_at": "2026-08-12T08:10:00Z",
            "last_contiguous_sequence": 18,
        },
        "evidence-chunk-manifest": {
            "schema_version": 1,
            "session_id": "23d7d803-524a-4d9f-b8bd-152a540dba12",
            "sequence": 3,
            "content_sha256": "c" * 64,
            "content_encoding": "gzip",
            "media_type": "application/json",
            "compressed_bytes": 512,
            "uncompressed_bytes": 2048,
            "first_event_sequence": 17,
            "last_event_sequence": 18,
            "object_key": f"classrooms/c1/sessions/s1/chunks/00000003-{'c' * 64}.json.gz",
            "created_at": "2026-08-12T08:10:00Z",
        },
        "student-brief": valid_student_brief(),
        "teacher-review": {
            "schema_version": 1,
            "review_id": "9d385ffa-cdfe-4fbf-9b6c-6eeabf19c911",
            "session_id": "23d7d803-524a-4d9f-b8bd-152a540dba12",
            "teacher_id": "teacher-001",
            "knowledge_point_reviews": [
                {
                    "knowledge_point_id": "kp-dict",
                    "status": "mastered",
                    "reason": "课堂口头追问通过。",
                }
            ],
            "comment": "可进入下一节练习。",
            "created_at": "2026-08-12T08:50:00Z",
        },
        "error": {
            "schema_version": 1,
            "error": {
                "code": "assignment_not_found",
                "message": "未找到课堂任务。",
                "retryable": False,
                "request_id": "req-001",
            },
        },
    }


def test_student_brief_rejects_unknown_mastery_status(
    schema_registry: ClassroomSchemaRegistry,
):
    """A misspelled mastery state must not reach teacher-facing reports."""
    payload = valid_student_brief()
    knowledge_points = payload["knowledge_points"]
    assert isinstance(knowledge_points, list)
    first_point = knowledge_points[0]
    assert isinstance(first_point, dict)
    first_point["status"] = "failed"

    with pytest.raises(ValidationError):
        schema_registry.validate("student-brief", payload)


def test_student_brief_can_explain_missing_evidence_after_the_hard_deadline(
    schema_registry: ClassroomSchemaRegistry,
):
    """An auto-closed partial brief needs a truthful non-chunk evidence reference."""
    payload = valid_student_brief()
    knowledge_points = payload["knowledge_points"]
    assert isinstance(knowledge_points, list)
    first_point = knowledge_points[0]
    assert isinstance(first_point, dict)
    first_point["status"] = "not_demonstrated"
    first_point["evidence_refs"] = ["session#missing-evidence"]

    schema_registry.validate("student-brief", payload)


def test_student_brief_limits_evidence_references_per_knowledge_point(
    schema_registry: ClassroomSchemaRegistry,
):
    payload = valid_student_brief()
    knowledge_points = payload["knowledge_points"]
    assert isinstance(knowledge_points, list)
    first_point = knowledge_points[0]
    assert isinstance(first_point, dict)
    first_point["evidence_refs"] = [
        f"chunk-1#event-{sequence}" for sequence in range(1, 12)
    ]

    with pytest.raises(ValidationError):
        schema_registry.validate("student-brief", payload)


def test_student_brief_accepts_only_bounded_auxiliary_ai_analysis(
    schema_registry: ClassroomSchemaRegistry,
):
    payload = valid_student_brief()
    payload["ai_analysis"] = {
        "knowledge_point_analyses": [
            {
                "knowledge_point_id": "KP_DICT0001",
                "status": "observed",
                "evidence_event_ids": ["chunk-1#event-1"],
                "teaching_suggestion": "追问缺失键与默认值的处理方式。",
            }
        ],
        "teacher_note": "仅反映本次过程证据，仍需教师复核。",
    }

    schema_registry.validate("student-brief", payload)

    analysis = payload["ai_analysis"]
    assert isinstance(analysis, dict)
    analysis["teacher_note"] = "长" * 501

    with pytest.raises(ValidationError):
        schema_registry.validate("student-brief", payload)


def test_plan_version_requires_a_complete_profile_v2(
    schema_registry: ClassroomSchemaRegistry,
):
    """A published plan cannot discard Profile v2 execution requirements."""
    payload = deepcopy(valid_contract_payloads()["plan-version"])
    profile = payload["profile"]
    assert isinstance(profile, dict)
    del profile["dimensions"]

    with pytest.raises(ValidationError):
        schema_registry.validate("plan-version", payload)


@pytest.mark.parametrize("schema_name", sorted(valid_contract_payloads()))
def test_each_contract_rejects_unknown_top_level_fields(
    schema_registry: ClassroomSchemaRegistry,
    schema_name: str,
):
    """Unexpected request fields must not silently change the shared API contract."""
    payload = deepcopy(valid_contract_payloads()[schema_name])
    schema_registry.validate(schema_name, payload)

    payload["unrecognized_field"] = True

    with pytest.raises(ValidationError):
        schema_registry.validate(schema_name, payload)


def test_type_generator_emits_schema_owned_session_statuses(tmp_path: Path):
    """A new schema status must reach service code without a copied enum list."""
    repository_root = Path(__file__).resolve().parents[4]
    output = tmp_path / "generated_contracts.py"

    result = subprocess.run(
        [
            sys.executable,
            str(repository_root / "scripts" / "generate_classroom_types.py"),
            "--common-schema",
            str(repository_root / "contracts" / "classroom" / "v1" / "common.schema.json"),
            "--python-output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    module_spec = importlib.util.spec_from_file_location("generated_contracts", output)
    assert module_spec is not None
    assert module_spec.loader is not None
    generated_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(generated_module)
    session_status = generated_module.SessionStatus
    assert [member.value for member in session_status] == [
        "collecting",
        "temporarily_offline",
        "submitting",
        "pending_upload",
        "completed",
        "partial",
    ]


def test_type_generator_check_rejects_a_stale_generated_file(tmp_path: Path):
    """CI must catch a schema change that was not regenerated into service types."""
    repository_root = Path(__file__).resolve().parents[4]
    output = tmp_path / "generated_contracts.py"
    output.write_text("stale\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(repository_root / "scripts" / "generate_classroom_types.py"),
            "--common-schema",
            str(repository_root / "contracts" / "classroom" / "v1" / "common.schema.json"),
            "--python-output",
            str(output),
            "--check",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stderr == "Generated classroom types are stale.\n"
    assert output.read_text(encoding="utf-8") == "stale\n"
