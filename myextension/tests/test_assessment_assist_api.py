import json

import pytest

from myextension.llm_transport import LlmTransportError
from myextension.schema_registry import validate_schema
from myextension.tests.test_assessment_profile import make_assessment_profile


FUNCTION_CONTEXT = {
    "statement": "编写 calculate_average(numbers)，返回列表平均值。",
    "language": "python",
    "submission_contract": {
        "kind": "function",
        "entrypoint": "calculate_average",
    },
}


async def test_knowledge_assist_endpoint_returns_closed_candidates(
    jp_fetch,
    monkeypatch,
):
    import myextension.routes as routes

    def recommend(statement, *, submission_contract, teacher_focus):
        assert statement == FUNCTION_CONTEXT["statement"]
        assert submission_contract == FUNCTION_CONTEXT["submission_contract"]
        assert teacher_focus == ["循环"]
        rows = [
            {
                "id": "KP_A1B2C3D4",
                "name": "循环边界",
                "description": "正确遍历列表。",
                "evidence_question": "是否正确处理循环边界？",
                "support_statement": "使用边界样例进行验证。",
                "exclusion_statement": "硬编码单个结果不计入。",
                "source": "ai_suggestion",
                "order": 0,
            },
            {
                "id": "KP_B1C2D3E4",
                "name": "累加器",
                "description": "正确累计列表元素。",
                "evidence_question": "是否正确维护累计值？",
                "support_statement": "多组输入得到正确总和。",
                "exclusion_statement": "固定输出不计入。",
                "source": "ai_suggestion",
                "order": 1,
            },
            {
                "id": "KP_C1D2E3F4",
                "name": "平均值计算",
                "description": "使用总和除以元素数量。",
                "evidence_question": "是否正确完成除法？",
                "support_statement": "不同长度输入均得到正确结果。",
                "exclusion_statement": "只验证单个样例不计入。",
                "source": "ai_suggestion",
                "order": 2,
            },
        ]
        return {
            "knowledge_points": rows
        }

    monkeypatch.setattr(routes, "recommend_knowledge_points", recommend)
    response = await jp_fetch(
        "myextension",
        "assessment-assist",
        "knowledge-points",
        method="POST",
        body=json.dumps(
            {
                "schema_version": 1,
                "problem_context": FUNCTION_CONTEXT,
                "teacher_focus": ["循环"],
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert response.code == 200
    payload = json.loads(response.body)
    validate_schema("assessment-knowledge-response-v1", payload)
    assert payload["knowledge_points"][0]["name"] == "循环边界"


async def test_test_assist_endpoint_never_accepts_student_events(
    jp_fetch,
):
    response = await jp_fetch(
        "myextension",
        "assessment-assist",
        "tests",
        method="POST",
        body=json.dumps(
            {
                "schema_version": 1,
                "problem_context": FUNCTION_CONTEXT,
                "knowledge_points": [
                    {
                        "id": "KP_A1B2C3D4",
                        "name": "循环边界",
                        "description": "正确遍历列表。",
                    }
                ],
                "events": [{"student": "private"}],
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert response.code == 422
    payload = json.loads(response.body)
    assert payload["code"] == "assessment_assist_validation_failed"
    assert "private" not in response.body.decode("utf-8")


async def test_assist_endpoint_maps_missing_ai_without_echoing_question(
    jp_fetch,
    monkeypatch,
):
    import myextension.routes as routes
    from myextension.llm_transport import AiNotConfiguredError

    private_question = "SYNTHETIC_PRIVATE_QUESTION"

    def not_configured(*_args, **_kwargs):
        raise AiNotConfiguredError("synthetic")

    monkeypatch.setattr(routes, "recommend_knowledge_points", not_configured)
    response = await jp_fetch(
        "myextension",
        "assessment-assist",
        "knowledge-points",
        method="POST",
        body=json.dumps(
            {
                "schema_version": 1,
                "problem_context": {
                    **FUNCTION_CONTEXT,
                    "statement": private_question,
                },
                "teacher_focus": [],
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert response.code == 409
    payload = json.loads(response.body)
    assert payload["code"] == "ai_not_configured"
    assert private_question not in response.body.decode("utf-8")


@pytest.mark.parametrize(
    ("provider_error", "expected_code"),
    [
        (LlmTransportError("provider_timeout"), "ai_provider_timeout"),
        (
            LlmTransportError("provider_network_error"),
            "ai_provider_network_error",
        ),
        (
            LlmTransportError("provider_http_error", http_status=401),
            "ai_provider_auth_failed",
        ),
        (
            LlmTransportError("provider_http_error", http_status=403),
            "ai_provider_auth_failed",
        ),
        (
            LlmTransportError("provider_http_error", http_status=429),
            "ai_provider_rate_limited",
        ),
        (
            LlmTransportError("provider_http_error", http_status=400),
            "ai_provider_request_rejected",
        ),
        (
            LlmTransportError("provider_http_error", http_status=503),
            "ai_provider_unavailable",
        ),
        (
            LlmTransportError("provider_response_truncated"),
            "ai_response_truncated",
        ),
        (
            LlmTransportError("provider_response_invalid"),
            "ai_response_invalid",
        ),
        (
            LlmTransportError("synthetic_unknown_failure"),
            "test_generation_failed",
        ),
    ],
)
async def test_test_assist_maps_provider_failures_without_private_echo(
    jp_fetch,
    monkeypatch,
    provider_error,
    expected_code,
):
    import myextension.routes as routes

    private_marker = "SYNTHETIC_PRIVATE_ASSESSMENT_MARKER"

    def fail(*_args, **_kwargs):
        raise provider_error

    monkeypatch.setattr(routes, "generate_assessment_tests", fail)
    response = await jp_fetch(
        "myextension",
        "assessment-assist",
        "tests",
        method="POST",
        body=json.dumps(
            {
                "schema_version": 1,
                "problem_context": {
                    **FUNCTION_CONTEXT,
                    "statement": private_marker,
                },
                "knowledge_points": [
                    {
                        "id": "KP_A1B2C3D4",
                        "name": "循环边界",
                        "description": "正确遍历列表。",
                    }
                ],
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    payload = json.loads(response.body)
    assert response.code == 502
    assert payload["code"] == expected_code
    assert payload["retryable"] is True
    assert private_marker not in response.body.decode("utf-8")
    assert "provider_http_error" not in response.body.decode("utf-8")


async def test_profile_api_preserves_v2_schema_and_publishes_exact_snapshot(
    jp_fetch,
):
    create_response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        method="POST",
        body=json.dumps(make_assessment_profile()),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert create_response.code == 201
    created = json.loads(create_response.body)
    assert created["schema_version"] == 2
    profile_id = created["profile_id"]

    publish_response = await jp_fetch(
        "myextension",
        "dimension-profiles",
        profile_id,
        "publish",
        method="POST",
        body="{}",
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert publish_response.code == 200
    published = json.loads(publish_response.body)
    assert published["schema_version"] == 2
    assert published["problem_context"] == created["problem_context"]
    assert published["knowledge_points"] == created["knowledge_points"]
    assert published["assessment_tests"] == created["assessment_tests"]

    start_response = await jp_fetch(
        "myextension",
        "sessions",
        "start",
        method="POST",
        body=json.dumps(
            {
                "schema_version": 1,
                "problem_id": published["problem_id"],
                "profile_id": published["profile_id"],
                "profile_version": published["version"],
                "profile_content_hash": published["content_hash"],
            }
        ),
        headers={"Content-Type": "application/json"},
        raise_error=False,
    )

    assert start_response.code == 201
    started = json.loads(start_response.body)
    assert started["profile_id"] == published["profile_id"]
    assert started["profile_version"] == published["version"]
    assert started["profile_content_hash"] == published["content_hash"]
