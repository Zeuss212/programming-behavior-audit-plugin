from __future__ import annotations

import json

import httpx
import pytest

from classroom_sync.config import Settings
from classroom_sync.errors import UpstreamUnavailableError
from classroom_sync.services.brief_analysis import BriefAnalysisInput, OpenAiBriefAnalysisService
from classroom_sync.services.plan_suggestions import AiProviderSettings, OpenAiCompletionClient


def test_brief_analysis_messages_omit_evidence_addresses_and_credentials() -> None:
    source = BriefAnalysisInput.from_brief_payload(
        {
            "summary": "完成一次字典读取。",
            "knowledge_points": [
                {
                    "name": "字典读取",
                    "status": "partial",
                    "demonstrated": "完成读取",
                    "gap": "未测空键",
                    "teacher_suggestion": "补充测试",
                    "evidence_refs": ["chunk-1#event-1"],
                    "object_key": "private/evidence.jsonl",
                }
            ],
            "process_overview": ["运行两次"],
            "issues": ["缺少空键测试"],
            "access_token": "must-not-leave-server",
        }
    )

    messages = OpenAiBriefAnalysisService.messages_for(source)
    encoded = json.dumps(messages, ensure_ascii=False)

    assert "完成一次字典读取" in encoded
    assert "chunk-1#event-1" not in encoded
    assert "object_key" not in encoded
    assert "access_token" not in encoded
    assert "must-not-leave-server" not in encoded


def test_brief_analysis_adapter_sends_only_allowlisted_text_and_validates_glm_json() -> None:
    """Removing input filtering or result bounds would expose this provider boundary."""
    recorded: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "learning_overview": "已完成字典读取并进行了运行验证。",
                                    "evidence_based_observations": ["简报显示完成两次运行。"],
                                    "teaching_suggestions": ["追问缺失键的处理方式。"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    provider = AiProviderSettings.from_settings(
        Settings(
            database_url="sqlite://",
            ai_base_url="https://open.bigmodel.cn/api/coding/paas/v4",
            ai_model="glm-5.2",
            ai_api_key="server-only-secret",
            ai_timeout_seconds=30,
        )
    )
    assert provider is not None
    service = OpenAiBriefAnalysisService(
        OpenAiCompletionClient(provider, httpx.Client(transport=httpx.MockTransport(responder)))
    )
    source = BriefAnalysisInput.from_brief_payload(
        {
            "summary": "完成一次字典读取。",
            "knowledge_points": [{"name": "字典读取", "evidence_refs": ["chunk-1#event-1"]}],
            "process_overview": ["运行两次"],
            "issues": ["缺少空键测试"],
            "access_token": "must-not-leave-server",
        }
    )

    result = service.generate(source)

    assert result.learning_overview == "已完成字典读取并进行了运行验证。"
    assert recorded[0].url == "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
    assert recorded[0].headers["authorization"] == "Bearer server-only-secret"
    encoded = recorded[0].content.decode("utf-8")
    assert "chunk-1#event-1" not in encoded
    assert "must-not-leave-server" not in encoded
    assert "server-only-secret" not in encoded


def test_brief_analysis_adapter_maps_malformed_glm_json_to_safe_retryable_error() -> None:
    """Malformed provider output must never be saved as a student brief revision."""
    provider = AiProviderSettings.from_settings(
        Settings(
            database_url="sqlite://",
            ai_base_url="https://open.bigmodel.cn/api/coding/paas/v4",
            ai_model="glm-5.2",
            ai_api_key="server-only-secret",
            ai_timeout_seconds=30,
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
        service.generate(BriefAnalysisInput("摘要", (), (), ()))

    assert error.value.code == "ai_brief_analysis_response_invalid"
    assert error.value.retryable is False


def test_brief_analysis_adapter_maps_coding_plan_authorization_to_safe_terminal_error() -> None:
    """A provider denial must be diagnosable without retaining its response body."""
    provider = AiProviderSettings.from_settings(
        Settings(
            database_url="sqlite://",
            ai_base_url="https://open.bigmodel.cn/api/coding/paas/v4",
            ai_model="glm-5.2",
            ai_api_key="server-only-secret",
            ai_timeout_seconds=30,
        )
    )
    assert provider is not None
    service = OpenAiBriefAnalysisService(
        OpenAiCompletionClient(
            provider,
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(403, text="provider-private-detail")
                )
            ),
        )
    )

    with pytest.raises(UpstreamUnavailableError) as error:
        service.generate(BriefAnalysisInput("摘要", (), (), ()))

    assert error.value.code == "ai_provider_authorization_or_policy_rejected"
    assert error.value.retryable is False
    assert "provider-private-detail" not in str(error.value)
