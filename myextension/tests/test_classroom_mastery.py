from __future__ import annotations

from myextension.classroom_mastery import evaluate_knowledge_points


def profile_with_requirements(*requirements: str) -> dict[str, object]:
    return {
        "knowledge_points": [
            {
                "id": "KP_DICT0001",
                "name": "字典安全查询",
                "automatic_evaluation": {
                    "mode": "all",
                    "summary": "创建字典后进行安全查询并成功运行。",
                    "requirements": [{"kind": requirement} for requirement in requirements],
                },
            }
        ]
    }


def detail_with_code(source: str, *, execution_result: str = "success") -> dict[str, object]:
    return {
        "behavior_events": [
            {"segment_type": "code_writing", "cell_source": source},
            {"segment_type": "code_execution", "execution_result": execution_result},
        ]
    }


def test_equivalent_dictionary_code_with_different_names_is_mastered() -> None:
    rows = evaluate_knowledge_points(
        profile_with_requirements(
            "successful_execution",
            "dict_literal_assignment",
            "dict_subscript_access",
            "dict_get_with_default",
        ),
        detail_with_code(
            'records = {"甲": 91, "乙": 88}\nprint(records["甲"])\nrecords.get("丙", "缺失")'
        ),
        ["chunk-1#event-1"],
    )

    assert rows[0]["status"] == "mastered"
    assert rows[0]["evidence_refs"] == ["chunk-1#event-1"]
    assert "records" not in str(rows[0])


def test_equivalent_dictionary_initialization_forms_are_mastered() -> None:
    for source in (
        'grades = dict([("甲", 91), ("乙", 88)])\nprint(grades.get("丙", "缺失"))',
        'grades = {}\ngrades["甲"] = 91\ngrades["乙"] = 88\nprint(grades.get("丙", "缺失"))',
    ):
        rows = evaluate_knowledge_points(
            profile_with_requirements(
                "successful_execution",
                "dict_literal_assignment",
                "dict_key_value_pairs",
                "dict_get_with_default",
                "print_call",
            ),
            detail_with_code(source),
            ["chunk-1#event-1"],
        )

        assert rows[0]["status"] == "mastered"


def test_missing_get_default_is_partial_after_successful_execution() -> None:
    rows = evaluate_knowledge_points(
        profile_with_requirements(
            "successful_execution",
            "dict_literal_assignment",
            "dict_get_with_default",
        ),
        detail_with_code('grades = {"一": 90, "二": 80}\ngrades.get("三")'),
        ["chunk-1#event-1"],
    )

    assert rows[0]["status"] == "partial"
    assert "默认值" in str(rows[0]["gap"])


def test_no_successful_execution_is_not_demonstrated() -> None:
    rows = evaluate_knowledge_points(
        profile_with_requirements("successful_execution", "dict_literal_assignment"),
        detail_with_code('records = {"甲": 91}', execution_result="failure"),
        ["chunk-1#event-1"],
    )

    assert rows[0]["status"] == "not_demonstrated"


def test_missing_automatic_rule_requires_teacher_review() -> None:
    rows = evaluate_knowledge_points(
        {"knowledge_points": [{"id": "KP_DICT0001", "name": "开放式表达"}]},
        detail_with_code('records = {"甲": 91}'),
        ["chunk-1#event-1"],
    )

    assert rows[0]["status"] == "review_required"
