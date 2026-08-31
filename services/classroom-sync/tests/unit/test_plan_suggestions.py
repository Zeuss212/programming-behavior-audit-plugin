"""Unit tests for the server-only AI classroom-plan suggestion adapter."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from classroom_sync.config import Settings
from classroom_sync.errors import AiSuggestionUnavailableError, UpstreamUnavailableError
from classroom_sync.services.plan_suggestions import (
    AiProviderSettings,
    AiSuggestionSettings,
    OpenAiCompletionClient,
    OpenAiPlanSuggestionService,
    PlanSuggestionInput,
)

MATERIAL_BUNDLE_HASH = "b" * 64


def cpp_input(*, requirement_count: int = 2) -> PlanSuggestionInput:
    return PlanSuggestionInput(
        profile_kind="cpp_v3",
        title="C++ 循环练习",
        statement="读入整数并计算累加值。",
        material_bundle_hash=MATERIAL_BUNDLE_HASH,
        material_requirements=tuple(
            {
                "id": f"requirement-{index}",
                "name": f"要求 {index}",
                "source_statement": f"学生完成第 {index} 项任务。",
            }
            for index in range(1, requirement_count + 1)
        ),
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


def response_with(content: str, *, finish_reason: str | None = None) -> httpx.Response:
    choice: dict[str, object] = {"message": {"content": content}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return httpx.Response(
        200,
        json={"choices": [choice]},
    )


def test_completion_client_uses_coding_plan_endpoint_without_serializing_key() -> None:
    recorded: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return response_with("课堂方案草稿")

    client = httpx.Client(transport=httpx.MockTransport(responder))
    settings = AiProviderSettings.from_settings(
        configured_settings(
            ai_base_url="https://open.bigmodel.cn/api/coding/paas/v4",
            ai_model="glm-5.2",
            ai_timeout_seconds=30,
        )
    )
    assert settings is not None

    result = OpenAiCompletionClient(settings, client).complete(
        [{"role": "user", "content": "生成一个课堂方案"}],
        temperature=0.2,
        max_tokens=1200,
    )

    assert result == "课堂方案草稿"
    assert recorded[0].url == "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
    assert recorded[0].headers["authorization"] == "Bearer server-only-secret"
    assert "server-only-secret" not in recorded[0].content.decode("utf-8")


def test_adapter_uses_the_fast_json_profile_for_bounded_teaching_text() -> None:
    recorded: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return response_with(
            json.dumps(
                {
                    "title": "字典课堂练习",
                    "knowledge_points": [
                        {
                            "name": "字典读取",
                            "description": "按键读取并验证结果。",
                            "automatic_evaluation": {
                                "mode": "all",
                                "summary": "创建字典后进行安全查询并成功运行。",
                                "requirements": [
                                    {"kind": "successful_execution"},
                                    {"kind": "dict_literal_assignment"},
                                    {"kind": "dict_get_with_default"},
                                ],
                            },
                        }
                    ],
                }
            )
        )

    client = httpx.Client(transport=httpx.MockTransport(responder))
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(
            configured_settings(ai_base_url="https://ark.example/api/coding/v3")
        ),
        client,
    )

    result = service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))

    assert result.title == "字典课堂练习"
    assert result.knowledge_points[0].name == "字典读取"
    assert result.knowledge_points[0].automatic_evaluation is not None
    assert [
        requirement.kind
        for requirement in result.knowledge_points[0].automatic_evaluation.requirements
    ] == ["successful_execution", "dict_literal_assignment", "dict_get_with_default"]
    assert recorded[0].url == "https://ark.example/api/coding/v3/chat/completions"
    assert recorded[0].headers["authorization"] == "Bearer server-only-secret"
    body = json.loads(recorded[0].content)
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 2048
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}
    system_content = body["messages"][0]["content"]
    assert "automatic_evaluation" in system_content
    assert "dict_get_with_default" in system_content
    assert "server-only-secret" not in recorded[0].content.decode("utf-8")
    assert "实现字典查询" in recorded[0].content.decode("utf-8")


def test_adapter_uses_standard_chat_completions_profile_for_plan_endpoint() -> None:
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

    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(
            configured_settings(ai_base_url="https://ark.example/api/plan/v3")
        ),
        httpx.Client(transport=httpx.MockTransport(responder)),
    )

    result = service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))

    assert result.title == "字典课堂练习"
    body = json.loads(recorded[0].content)
    assert body["max_tokens"] == 1200
    assert "thinking" not in body
    assert "response_format" not in body
    system_content = body["messages"][0]["content"]
    assert "automatic_evaluation" not in system_content
    assert "dict_get_with_default" not in system_content
    assert body["messages"][1]["content"] == json.dumps(
        {"title": "", "statement": "实现字典查询"}, ensure_ascii=False
    )


def test_cpp_adapter_returns_only_points_linked_to_submitted_material_requirements() -> None:
    recorded: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return response_with(
            json.dumps(
                {
                    "title": "C++ 循环练习",
                    "knowledge_points": [
                        {
                            "material_requirement_id": "requirement-1",
                            "name": "循环累加",
                            "description": "根据材料要求完成有界循环。",
                        },
                        {
                            "material_requirement_id": "requirement-2",
                            "name": "输入输出",
                            "description": "根据材料要求读取并输出结果。",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        )

    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(configured_settings()),
        httpx.Client(transport=httpx.MockTransport(responder)),
    )

    result = service.generate(cpp_input())

    assert [point.material_requirement_id for point in result.knowledge_points] == [
        "requirement-1",
        "requirement-2",
    ]
    body = json.loads(recorded[0].content)
    assert "requirement-1" in body["messages"][1]["content"]
    assert MATERIAL_BUNDLE_HASH in body["messages"][1]["content"]
    assert "assessment_tests" not in body["messages"][1]["content"]
    assert "starter_source" not in body["messages"][1]["content"]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "title": "C++ 练习",
            "knowledge_points": [
                {
                    "material_requirement_id": "unknown-requirement",
                    "name": "未知要求",
                    "description": "不允许引用未提交的材料要求。",
                }
            ],
        },
        {
            "title": "C++ 练习",
            "knowledge_points": [
                {
                    "material_requirement_id": "requirement-1",
                    "name": "要求一",
                    "description": "第一次引用。",
                },
                {
                    "material_requirement_id": "requirement-1",
                    "name": "要求一重复",
                    "description": "不允许重复引用。",
                },
            ],
        },
        {
            "title": "C++ 练习",
            "knowledge_points": [
                {
                    "material_requirement_id": "requirement-1",
                    "name": "要求一",
                    "description": "不允许模型生成测试。",
                    "tests": [{"input": "1", "expected_stdout": "1"}],
                }
            ],
        },
        {
            "title": "C++ 练习",
            "knowledge_points": [
                {
                    "material_requirement_id": "requirement-1",
                    "name": "要求一",
                    "description": "不允许额外字段。",
                    "provider_metadata": {"model": "unsafe"},
                }
            ],
        },
        {
            "title": "C++ 练习",
            "knowledge_points": [
                {
                    "material_requirement_id": "requirement-1",
                    "name": "要求一",
                    "description": "不允许额外顶层字段。",
                }
            ],
            "explanation": "非契约字段",
        },
    ],
    ids=["unknown-id", "duplicate-id", "generated-tests", "point-extra", "top-extra"],
)
def test_cpp_adapter_rejects_output_outside_the_material_requirement_contract(
    payload: dict[str, object],
) -> None:
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(configured_settings()),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: response_with(json.dumps(payload, ensure_ascii=False))
            )
        ),
    )

    with pytest.raises(UpstreamUnavailableError, match="ai_suggestion_response_invalid"):
        service.generate(cpp_input())


def test_cpp_adapter_rejects_more_than_ten_material_requirement_points() -> None:
    payload = {
        "title": "C++ 练习",
        "knowledge_points": [
            {
                "material_requirement_id": f"requirement-{index}",
                "name": f"要求 {index}",
                "description": f"对应材料要求 {index}。",
            }
            for index in range(1, 12)
        ],
    }
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(configured_settings()),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: response_with(json.dumps(payload, ensure_ascii=False))
            )
        ),
    )

    with pytest.raises(UpstreamUnavailableError, match="ai_suggestion_response_invalid"):
        service.generate(cpp_input(requirement_count=11))


def test_adapter_keeps_a_complete_standard_plan_response_without_a_second_provider_call() -> None:
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
            ),
            finish_reason="length",
        )

    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(
            configured_settings(ai_base_url="https://ark.example/api/plan/v3")
        ),
        httpx.Client(transport=httpx.MockTransport(responder)),
    )

    result = service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))

    assert result.title == "字典课堂练习"
    assert len(recorded) == 1


def test_adapter_retries_once_with_4096_tokens_after_a_length_response() -> None:
    recorded: list[httpx.Request] = []
    responses = iter(
        [
            response_with(
                json.dumps({"title": "未完成的课堂方案", "knowledge_points": []}),
                finish_reason="length",
            ),
            response_with(
                json.dumps(
                    {
                        "title": "字典课堂练习",
                        "knowledge_points": [
                            {"name": "字典读取", "description": "按键读取并验证结果。"}
                        ],
                    }
                )
            ),
        ]
    )

    def responder(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return next(responses)

    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(
            configured_settings(ai_base_url="https://ark.example/api/coding/v3")
        ),
        httpx.Client(transport=httpx.MockTransport(responder)),
    )

    result = service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))

    assert result.title == "字典课堂练习"
    assert [json.loads(request.content)["max_tokens"] for request in recorded] == [2048, 4096]
    assert all(
        json.loads(request.content)["thinking"] == {"type": "disabled"}
        and json.loads(request.content)["response_format"] == {"type": "json_object"}
        for request in recorded
    )


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

    with pytest.raises(UpstreamUnavailableError, match="ai_suggestion_response_invalid"):
        service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))


def test_adapter_rejects_provider_output_with_sensitive_transport_text() -> None:
    content = json.dumps(
        {
            "title": "字典课堂练习",
            "knowledge_points": [
                {
                    "name": "字典读取",
                    "description": "查看 https://provider.example/raw-output 后完成练习。",
                }
            ],
        }
    )
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(configured_settings()),
        httpx.Client(transport=httpx.MockTransport(lambda _request: response_with(content))),
    )

    with pytest.raises(UpstreamUnavailableError, match="ai_suggestion_response_invalid"):
        service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))


def test_adapter_discards_an_unsupported_optional_automatic_evaluation_rule() -> None:
    content = json.dumps(
        {
            "title": "字典课堂练习",
            "knowledge_points": [
                {
                    "name": "字典读取",
                    "description": "按键读取并验证结果。",
                    "automatic_evaluation": {
                        "mode": "all",
                        "summary": "这是不安全的规则。",
                        "requirements": [{"kind": "arbitrary_python"}],
                    },
                }
            ],
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: response_with(content)))
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(configured_settings()), client
    )

    result = service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))

    assert result.title == "字典课堂练习"
    assert result.knowledge_points[0].name == "字典读取"
    assert result.knowledge_points[0].automatic_evaluation is None


def test_adapter_ignores_noncontract_explanation_fields_from_the_provider() -> None:
    content = json.dumps(
        {
            "title": "字典课堂练习",
            "teaching_rationale": "该字段只供模型说明，不能进入课堂方案。",
            "knowledge_points": [
                {
                    "name": "字典读取",
                    "description": "按键读取并验证结果。",
                    "evidence_hint": "观察 get 调用。",
                }
            ],
        }
    )
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(configured_settings()),
        httpx.Client(transport=httpx.MockTransport(lambda _request: response_with(content))),
    )

    result = service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))

    assert result.title == "字典课堂练习"
    assert result.knowledge_points[0].model_dump() == {
        "name": "字典读取",
        "description": "按键读取并验证结果。",
        "automatic_evaluation": None,
    }


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


def test_adapter_logs_only_the_safe_provider_failure_code(caplog: pytest.LogCaptureFixture) -> None:
    target_logger = logging.getLogger("classroom_sync.services.plan_suggestions")
    was_disabled = target_logger.disabled
    previous_propagate = target_logger.propagate
    target_logger.disabled = False
    target_logger.propagate = True
    caplog.set_level("WARNING", logger=target_logger.name)
    try:
        service = OpenAiPlanSuggestionService(
            AiSuggestionSettings.from_settings(configured_settings()),
            httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(429))),
        )

        with pytest.raises(UpstreamUnavailableError, match="ai_suggestion_upstream_unavailable"):
            service.generate(PlanSuggestionInput(title="", statement="不应记录的教学目标"))

        assert "ai_provider_rate_limited" in caplog.text
        assert "不应记录的教学目标" not in caplog.text
        assert "server-only-secret" not in caplog.text
    finally:
        target_logger.disabled = was_disabled
        target_logger.propagate = previous_propagate


def test_adapter_maps_provider_timeouts_to_a_safe_retryable_error() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timeout")

    client = httpx.Client(transport=httpx.MockTransport(timeout))
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings.from_settings(configured_settings()), client
    )

    with pytest.raises(UpstreamUnavailableError, match="ai_suggestion_upstream_unavailable"):
        service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))
