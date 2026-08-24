from __future__ import annotations

import json

import pytest

from myextension.classroom_ai_analysis_input import build_analysis_input


def _profile() -> dict[str, object]:
    return {
        "title": "Python 字典课堂练习",
        "knowledge_points": [
            {
                "id": "KP_DICT0001",
                "name": "字典查询",
                "description": "使用键查询数据并处理键不存在的情况。",
            }
        ],
        "dimensions": [
            {
                "knowledge_point_id": "KP_DICT0001",
                "code": "DICT_LOOKUP",
                "name": "字典查询",
                "question": "学生是否能选择恰当的字典查询方式？",
                "evidence_criteria": [
                    {
                        "id": "uses-get",
                        "direction": "support",
                        "statement": "使用 get 并明确默认值。",
                    }
                ],
            }
        ],
    }


def _detail(source: str = 'records = {"A": 1}\nprint(records.get("B", 0))') -> dict[str, object]:
    return {
        "behavior_events": [
            {
                "event_id": "local-1",
                "session_seq": 1,
                "segment_type": "code_writing",
                "cell_source": source,
                "file_path": "/Users/student/private/lesson.ipynb",
            },
            {
                "event_id": "local-2",
                "session_seq": 2,
                "segment_type": "code_execution",
                "execution_result": "failure",
                "error_message": "File /Users/student/private/lesson.py, line 1",
            },
            {
                "event_id": "local-3",
                "session_seq": 3,
                "segment_type": "code_writing",
                "cell_source": source + "\n# fixed",
                "file_path": "/Users/student/private/lesson.ipynb",
            },
            {
                "event_id": "local-4",
                "session_seq": 4,
                "segment_type": "code_execution",
                "execution_result": "success",
            },
        ]
    }


def _ranges() -> list[dict[str, object]]:
    return [
        {
            "sequence": 1,
            "first_event_sequence": 1,
            "last_event_sequence": 4,
        }
    ]


def test_builder_reuses_safe_candidate_selection_and_emits_only_bounded_fields() -> None:
    payload = build_analysis_input(_profile(), _detail(), _ranges())

    assert set(payload) == {
        "lesson",
        "knowledge_points",
        "evidence_events",
        "code_snapshots",
    }
    assert [row["event_id"] for row in payload["evidence_events"]] == [
        "chunk-1#event-1",
        "chunk-1#event-2",
        "chunk-1#event-3",
        "chunk-1#event-4",
    ]
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "error_message" not in encoded
    assert "/Users/" not in encoded
    assert "file_path" not in encoded
    assert "local-" not in encoded
    assert payload["knowledge_points"][0]["question"].startswith("学生是否")
    assert sum(len(row["source"]) for row in payload["code_snapshots"]) <= 12_000


def test_builder_applies_one_deterministic_snapshot_budget() -> None:
    payload = build_analysis_input(_profile(), _detail("x" * 13_000), _ranges())

    snapshots = payload["code_snapshots"]
    assert sum(len(row["source"]) for row in snapshots) == 12_000
    assert snapshots[0]["source"] == "x" * 12_000


def test_builder_does_not_invent_event_ids_when_no_uploaded_range_matches() -> None:
    payload = build_analysis_input(_profile(), _detail(), [])

    assert payload["evidence_events"] == []
    assert payload["code_snapshots"] == []


@pytest.mark.parametrize(
    "secret",
    [
        "student@example.edu",
        "https://storage.example.edu/private/lesson.ipynb",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzdHVkZW50MDAxIn0.signaturevalue",
        "ghp_1234567890abcdefghijklmnopqrstuvwxyz",
        "AKIAIOSFODNN7EXAMPLE",
        "Q7mZ4vP2xR8nL5sT1wY6cD9hK3bF0jN7qA4eU",
    ],
)
def test_builder_redacts_identity_urls_and_secret_like_source_literals(secret: str) -> None:
    source = f'credential = "{secret}"\nprint("exercise")'

    payload = build_analysis_input(_profile(), _detail(source), _ranges())

    encoded = json.dumps(payload, ensure_ascii=False)
    assert secret not in encoded
    assert "[redacted]" in encoded


def test_builder_redacts_unquoted_opaque_environment_value() -> None:
    secret = "aB3_fGh7JkLm9NpQr2StUv4WxYz6CdEf"
    payload = build_analysis_input(
        _profile(),
        _detail(f"%env PASSWORD={secret}\nprint('exercise')"),
        _ranges(),
    )

    encoded = json.dumps(payload, ensure_ascii=False)
    assert secret not in encoded
    assert "[redacted]" in encoded
