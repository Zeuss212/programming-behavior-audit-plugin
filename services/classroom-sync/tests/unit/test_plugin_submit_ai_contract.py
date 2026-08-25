from __future__ import annotations

from classroom_sync.routers.plugin import SubmitBriefRequest


def test_submit_request_accepts_explicit_ai_consent_and_private_input() -> None:
    """The current student client must not be rejected before a consented job can queue."""
    analysis_input = {
        "lesson": {"title": "字典读取"},
        "knowledge_points": [
            {
                "knowledge_point_id": "KP_DICT0001",
                "name": "字典读取",
                "description": "使用 get 读取数据。",
                "question": "是否处理缺失键？",
                "evidence_criteria": [],
            }
        ],
        "evidence_events": [
            {
                "event_id": "chunk-1#event-1",
                "sequence": 1,
                "kind": "edit",
                "description": "编辑了字典读取代码。",
            }
        ],
        "code_snapshots": [],
    }

    request = SubmitBriefRequest.model_validate(
        {
            "summary": "完成字典读取。",
            "knowledge_points": [],
            "process_overview": [],
            "issues": [],
            "reason": "student_manual",
            "request_ai_analysis": True,
            "analysis_input": analysis_input,
        }
    )

    assert request.request_ai_analysis is True
    assert request.analysis_input is not None
    assert request.analysis_input.model_dump(mode="json") == analysis_input
