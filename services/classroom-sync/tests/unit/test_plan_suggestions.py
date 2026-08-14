"""Unit tests for the server-only AI classroom-plan suggestion adapter."""

from __future__ import annotations

import json

import httpx
import pytest

from classroom_sync.config import Settings
from classroom_sync.errors import AiSuggestionUnavailableError, UpstreamUnavailableError
from classroom_sync.services.plan_suggestions import (
    AiSuggestionSettings,
    OpenAiPlanSuggestionService,
    PlanSuggestionInput,
)


def configured_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "sqlite://",
        "ai_base_url": "https://ai.example/v1",
        "ai_model": "classroom-model",
        "ai_api_key": "server-only-secret",
        "ai_timeout_seconds": 15,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def response_with(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
    )


def test_adapter_posts_only_bounded_teaching_text_and_validates_output() -> None:
    recorded: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return response_with(
            json.dumps(
                {
                    "title": "字典课堂练习",
                    "knowledge_points": [
                        {"name": "字典读取", "description": "按键读取并验证结果。"}
                    ],
                }
            )
        )

    client = httpx.Client(transport=httpx.MockTransport(responder))
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(configured_settings()), client
    )

    result = service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))

    assert result.title == "字典课堂练习"
    assert result.knowledge_points[0].name == "字典读取"
    assert recorded[0].url == "https://ai.example/v1/chat/completions"
    assert recorded[0].headers["authorization"] == "Bearer server-only-secret"
    body = json.loads(recorded[0].content)
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 1200
    assert "server-only-secret" not in recorded[0].content.decode("utf-8")
    assert "实现字典查询" in recorded[0].content.decode("utf-8")


@pytest.mark.parametrize(
    "base_url",
    [
        "http://ai.example/v1",
        "https://user@ai.example/v1",
        "https://ai.example/v1?unsafe=true",
        "https://ai.example/v1#fragment",
    ],
)
def test_ai_settings_reject_insecure_or_ambiguous_provider_urls(base_url: str) -> None:
    with pytest.raises(AiSuggestionUnavailableError, match="ai_suggestion_not_configured"):
        AiSuggestionSettings.from_settings(configured_settings(ai_base_url=base_url))


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        json.dumps({"title": "x", "knowledge_points": []}),
        json.dumps(
            {
                "title": "x",
                "knowledge_points": [
                    {"name": f"知识点{i}", "description": "说明"} for i in range(11)
                ],
            }
        ),
        json.dumps(
            {
                "title": "x",
                "knowledge_points": [{"name": "名" * 51, "description": "说明"}],
            }
        ),
        json.dumps(
            {
                "title": "x",
                "knowledge_points": [{"name": "知识点", "description": "说" * 501}],
            }
        ),
    ],
)
def test_adapter_rejects_malformed_or_out_of_bound_provider_output(content: str) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: response_with(content)))
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(configured_settings()), client
    )

    with pytest.raises(UpstreamUnavailableError, match="ai_suggestion_upstream_unavailable"):
        service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))


@pytest.mark.parametrize("status_code", [429, 500])
def test_adapter_maps_provider_unavailability_to_a_safe_retryable_error(status_code: int) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(status_code))
    )
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(configured_settings()), client
    )

    with pytest.raises(UpstreamUnavailableError, match="ai_suggestion_upstream_unavailable"):
        service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))


def test_adapter_maps_provider_timeouts_to_a_safe_retryable_error() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timeout")

    client = httpx.Client(transport=httpx.MockTransport(timeout))
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(configured_settings()), client
    )

    with pytest.raises(UpstreamUnavailableError, match="ai_suggestion_upstream_unavailable"):
        service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))
