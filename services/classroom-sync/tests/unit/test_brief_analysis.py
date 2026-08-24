from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest

from classroom_sync.config import Settings
from classroom_sync.errors import UpstreamUnavailableError
from classroom_sync.services.brief_analysis import BriefAnalysisInput, OpenAiBriefAnalysisService
from classroom_sync.services.plan_suggestions import AiProviderSettings, OpenAiCompletionClient


def valid_source() -> BriefAnalysisInput:
    return BriefAnalysisInput.model_validate(
        {
            "lesson": {"title": "Python 字典课堂练习"},
            "knowledge_points": [
                {
                    "knowledge_point_id": "KP_DICT0001",
                    "name": "字典查询",
                    "description": "使用 get 处理键不存在。",
                    "question": "学生是否选择了恰当的查询方式？",
                    "evidence_criteria": [
                        {
                            "id": "uses-get",
                            "direction": "support",
                            "statement": "使用 get 并明确默认值。",
                        }
                    ],
                }
            ],
            "evidence_events": [
                {
                    "event_id": "chunk-1#event-1",
                    "sequence": 1,
                    "kind": "edit",
                    "description": "编辑了代码。",
                },
                {
                    "event_id": "chunk-1#event-2",
                    "sequence": 2,
                    "kind": "run_success",
                    "description": "完成一次无异常运行；这不代表答案一定正确。",
                },
            ],
            "code_snapshots": [
                {
                    "event_id": "chunk-1#event-1",
                    "source": 'records = {"A": 1}\nprint(records.get("B", 0))',
                }
            ],
        }
    )


def valid_result() -> dict[str, object]:
    return {
        "knowledge_point_analyses": [
            {
                "knowledge_point_id": "KP_DICT0001",
                "status": "observed",
                "evidence_event_ids": ["chunk-1#event-1", "chunk-1#event-2"],
                "teaching_suggestion": "请追问学生为什么要设置默认值。",
            }
        ],
        "teacher_note": "仅反映本次过程证据，仍需教师结合作品复核。",
    }


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("source", "open('/Users/student/.ssh/id_rsa').read()"),
        ("source", "token = 'ghp_abcdefghijklmnopqrstuvwxyz123456'"),
        ("source", "endpoint = 'https://storage.example/private'"),
        ("description", "原始输出包含学生的诊断信息。"),
    ],
)
def test_private_analysis_input_rejects_sensitive_client_payloads(
    field: str,
    unsafe_value: str,
) -> None:
    payload = valid_source().model_dump(mode="json")
    if field == "source":
        payload["code_snapshots"][0][field] = unsafe_value
    else:
        payload["evidence_events"][0][field] = unsafe_value

    with pytest.raises(ValueError, match="sensitive"):
        BriefAnalysisInput.model_validate(payload)


@pytest.mark.parametrize(
    "source",
    [
        'token = "sk-1234567890abcdef"',
        'secret = "aB3_fGh7JkLm9NpQr2StUv4WxYz6CdEf"',
        "# aB3_fGh7JkLm9NpQr2StUv4WxYz6CdEf",
        "%env PASSWORD=aB3_fGh7JkLm9NpQr2StUv4WxYz6CdEf",
        "%env PASSWORD=9f4e7c2a1d8b6e3f0a5c9d7b2e4f8a1c6d3b0e9f5a7c2d8e4b1f6a9c3d7e0b5f",
        "%env PASSWORD=ab12.cd34.ef56.gh78.ij90.kl12.mn34.op56",
        "%env PASSWORD=QWxhZGRpbjpvcGVuIHNlc2FtZV9TZWNyZXQxMjM0NTY3ODkwPQ==",
    ],
)
def test_private_analysis_input_rejects_unlabelled_opaque_secrets(source: str) -> None:
    payload = valid_source().model_dump(mode="json")
    payload["code_snapshots"][0]["source"] = source

    with pytest.raises(ValueError, match="sensitive"):
        BriefAnalysisInput.model_validate(payload)


def service_for_response(payload: object, recorded: list[httpx.Request] | None = None):
    provider = AiProviderSettings.from_settings(
        Settings(
            database_url="sqlite://",
            ai_base_url="https://ai.example/v1",
            ai_model="classroom-model",
            ai_api_key="server-only-secret",
            ai_timeout_seconds=30,
        )
    )
    assert provider is not None

    def responder(request: httpx.Request) -> httpx.Response:
        if recorded is not None:
            recorded.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
        )

    return OpenAiBriefAnalysisService(
        OpenAiCompletionClient(provider, httpx.Client(transport=httpx.MockTransport(responder)))
    )


def test_brief_analysis_sends_only_private_bounded_evidence_and_validates_result() -> None:
    recorded: list[httpx.Request] = []

    result = service_for_response(valid_result(), recorded).generate(valid_source())

    assert result.knowledge_point_analyses[0].status == "observed"
    body = json.loads(recorded[0].content.decode("utf-8"))
    encoded = json.dumps(body, ensure_ascii=False)
    assert body["temperature"] == 0
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}
    assert "chunk-1#event-1" in encoded
    assert "records.get" in encoded
    assert "server-only-secret" not in encoded
    assert "/Users/" not in encoded
    assert "不得评分" in body["messages"][0]["content"]
    assert "不得判定答案正确" in body["messages"][0]["content"]
    assert "不得出现 score、grade、points、correct、incorrect" in body["messages"][0]["content"]
    assert "teaching_suggestion 字段" in body["messages"][0]["content"]


def test_brief_analysis_normalizes_the_strict_observation_alias_to_teaching_suggestion() -> None:
    payload = valid_result()
    row = payload["knowledge_point_analyses"][0]
    row["observation"] = row.pop("teaching_suggestion")

    result = service_for_response(payload).generate(valid_source())

    assert result.knowledge_point_analyses[0].teaching_suggestion == "请追问学生为什么要设置默认值。"


def test_brief_analysis_normalizes_safe_common_provider_aliases() -> None:
    payload = {
        "analysis": {
            "knowledge_points": [
                {
                    "knowledge_point_id": "KP_DICT0001",
                    "status": "已掌握",
                    "evidence_ids": ["chunk-1#event-1"],
                    "suggestion": "请追问学生如何处理不存在的键。",
                }
            ],
            "summary": "仅依据本次过程证据安排后续追问。",
        }
    }

    result = service_for_response(payload).generate(valid_source())

    row = result.knowledge_point_analyses[0]
    assert row.status == "observed"
    assert row.evidence_event_ids == ["chunk-1#event-1"]
    assert row.teaching_suggestion == "请追问学生如何处理不存在的键。"
    assert result.teacher_note == "仅依据本次过程证据安排后续追问。"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["knowledge_point_analyses"][0]["evidence_event_ids"].append(
            "chunk-9#event-9"
        ),
        lambda value: value["knowledge_point_analyses"][0].update(
            {"evidence_event_ids": []}
        ),
        lambda value: value["knowledge_point_analyses"][0].update(
            {"status": "not_observed"}
        ),
        lambda value: value["knowledge_point_analyses"][0].update(
            {"knowledge_point_id": "KP_UNKNOWN"}
        ),
        lambda value: value["knowledge_point_analyses"][0].update(
            {"teaching_suggestion": "查看 /Users/student/private.py"}
        ),
        lambda value: value["knowledge_point_analyses"][0].update(
            {"teaching_suggestion": "查看 https://storage.example/private"}
        ),
    ],
)
def test_brief_analysis_rejects_unbound_or_unsafe_provider_results(mutate) -> None:
    payload = deepcopy(valid_result())
    mutate(payload)

    with pytest.raises(UpstreamUnavailableError) as error:
        service_for_response(payload).generate(valid_source())

    assert error.value.code == "ai_brief_analysis_response_invalid"
    assert error.value.retryable is True


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "本次评分为 90 分。",
        "The score is 90 points.",
        "答案正确，可以提交。",
        "The answer is correct.",
        "请查看源码中的实现。",
        "Review the source code before class.",
        "这是模型的原始输出。",
        "Here is the raw model output.",
    ],
)
@pytest.mark.parametrize("field", ["teacher_note", "teaching_suggestion"])
def test_brief_analysis_rejects_grading_correctness_source_and_raw_output(
    unsafe_text: str,
    field: str,
) -> None:
    payload = deepcopy(valid_result())
    if field == "teacher_note":
        payload["teacher_note"] = unsafe_text
    else:
        payload["knowledge_point_analyses"][0]["teaching_suggestion"] = unsafe_text

    with pytest.raises(UpstreamUnavailableError) as error:
        service_for_response(payload).generate(valid_source())

    assert error.value.code == "ai_brief_analysis_response_invalid"
    assert error.value.retryable is True


def test_brief_analysis_maps_malformed_json_to_safe_terminal_error() -> None:
    provider = AiProviderSettings.from_settings(
        Settings(
            database_url="sqlite://",
            ai_base_url="https://ai.example/v1",
            ai_model="classroom-model",
            ai_api_key="server-only-secret",
        )
    )
    assert provider is not None
    service = OpenAiBriefAnalysisService(
        OpenAiCompletionClient(
            provider,
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200, json={"choices": [{"message": {"content": "not-json"}}]}
                    )
                )
            ),
        )
    )

    with pytest.raises(UpstreamUnavailableError) as error:
        service.generate(valid_source())

    assert error.value.code == "ai_brief_analysis_response_invalid"
    assert error.value.retryable is True
