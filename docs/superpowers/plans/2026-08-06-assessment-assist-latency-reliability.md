# Assessment Assist Latency and Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `0.2.1` 的知识点和测试建议使用快速、闭合的 Ark JSON 请求，在失败时返回可操作的安全错误码，并重新生成可安装、可回滚的完整交付文件。

**Architecture:** 保留 `chat_json()` 面向完整会话分析的现有默认行为，只增加闭合的可选参数；`assessment_assistant` 是唯一启用 `2048 → 4096`、关闭思考和 JSON 模式的调用方。路由把 Provider 传输错误归一为稳定码，`GuidedProfileEditor` 根据稳定码和当前步骤显示提示并保留草稿；通过 TDD、全量回归、隔离 wheel、一次合成 Provider 请求和全新本地预览完成交付。

**Tech Stack:** Python 3.10+、`urllib.request`、Tornado/Jupyter Server 2、pytest/pytest-jupyter、TypeScript 5.5、Jest 29/jsdom、JupyterLab 4、Hatch/Jupyter Builder、uv、POSIX shell。

## Global Constraints

- 分支固定为 `fix/ui-hotfix-0.2.1`，工作目录固定为 `.worktrees/ui-hotfix-0.2.1`；开始每个任务前先确认 `git status --short`，不得覆盖不属于本轮的修改。
- 包版本、模型和非密钥运行值保持 `0.2.1`、`glm-5-2-260617`、`ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3`。
- 作者辅助固定使用 `token_budgets=(2048, 4096)`、`thinking={"type":"disabled"}`、`response_format={"type":"json_object"}`；完整会话分析默认仍为 `(8192, 16384)` 且不注入这两个字段。
- 不发送真实题目、真实知识点、学生数据、现有草稿、API Key 或 Provider 响应正文；真实网络验收只允许一次合成外部请求，第二次调用必须在本地阻断。
- Provider 失败不得生成本地模板冒充 AI 结果；必须保留教师草稿并允许手工继续。
- 不修改分析状态机、证据提取、120 秒整次分析预算、60 秒单次 Provider 超时、Dockerfile 或 BLUEDOT 启动逻辑。
- 不删除或覆盖旧 wheel/ZIP，不修改项目 `.venv` 或系统 Python；所有最终安装验证使用 `/private/tmp` 下的新隔离目录。
- 不构建或推送 Docker 镜像，不登录镜像仓库，不注册 BLUEDOT，不推送 Git；外部部署停留在管理员可复现步骤。
- 只有实际命令通过后才能在 README、MANIFEST 或验证报告中记录通过；失败项必须准确标记为未验证。

---

## File Structure

- `myextension/llm_transport.py`：保留共享 Provider 传输职责；新增闭合的 token、thinking 和 JSON 请求选项及参数校验，不写作者业务规则。
- `myextension/assessment_assistant.py`：定义作者辅助专用预算，并让知识点/测试两个入口统一启用快速结构化请求。
- `myextension/routes.py`：把 `LlmTransportError` 映射为安全 API code；不暴露上游正文、题目或密钥。
- `src/ui/guidedProfileEditor.ts`：按 API code 和当前作者步骤选择可操作中文提示；不解析 Provider 私有错误。
- `myextension/tests/test_dimension_analyzer.py`：保护共享传输默认值与新可选请求体，防止完整分析被意外降级。
- `myextension/tests/test_assessment_assistant.py`：保护作者辅助调用点、首预算和一次截断恢复。
- `myextension/tests/test_assessment_assist_api.py`：保护 HTTP 状态、稳定错误码、retryable 和隐私边界。
- `src/__tests__/assessmentPlanEditor.spec.ts`：保护错误提示、草稿保留和手工继续路径。
- `myextension/tests/test_labextension_artifact.py`：要求编译前端和交付 wheel 包含本轮新错误契约标记。
- `README.md`：面向开发者和本地用户说明建议请求与故障排查。
- `deploy/bluedot/release-0.2.1/{README.md,runtime.env.example,SHA256SUMS,artifacts/myextension-0.2.1-py3-none-any.whl}`：镜像管理员使用的最新安装文件和制品身份。
- `myextension-0.2.1-BLUEDOT-完整交付包/{00_从这里开始.md,README.md,runtime.env.example,MANIFEST.json,SHA256SUMS,artifacts/myextension-0.2.1-py3-none-any.whl}`：人和 Codex 可直接理解的完整交付目录。
- `docs/2026-08-06-assessment-assist-latency-reliability-verification.md`：记录真实 RED/GREEN、合成网络验收、隔离预览、未执行外部动作和回滚点。
- `myextension-0.2.1-BLUEDOT-完整交付包-20260806-assessment-assist-fix.zip` 与同名 `.sha256`：新建最终归档，不覆盖既有 UI-hotfix 归档。

---

### Task 1: Add closed authoring controls to the shared JSON transport

**Files:**
- Modify: `myextension/llm_transport.py`
- Test: `myextension/tests/test_dimension_analyzer.py`

**Interfaces:**
- Consumes: existing `chat_json(*, system_prompt: str, user_payload: Mapping[str, object], client: JsonClient | None = None) -> LlmTransportResult` and `STRUCTURED_OUTPUT_TOKEN_BUDGETS = (8192, 16384)`.
- Produces: `ThinkingMode = Literal["enabled", "disabled", "auto"]` and the extended keyword-only signature `chat_json(*, system_prompt: str, user_payload: Mapping[str, object], client: JsonClient | None = None, token_budgets: Sequence[int] = STRUCTURED_OUTPUT_TOKEN_BUDGETS, thinking_mode: ThinkingMode | None = None, json_mode: bool = False) -> LlmTransportResult`.
- Preserves: every existing caller sees the same budgets and request fields unless it explicitly passes the new options.

- [ ] **Step 1: Add failing tests for explicit authoring fields and default isolation**

Add these tests next to the existing `test_transport_retries_one_length_truncation_with_larger_budget` cases:

```python
def test_transport_supports_bounded_authoring_json_requests():
    requests: list[dict[str, object]] = []

    def client(request):
        requests.append(dict(request))
        return {"assessment_tests": []}

    response = chat_json(
        system_prompt="synthetic system",
        user_payload={"synthetic": True},
        client=client,
        token_budgets=(2048, 4096),
        thinking_mode="disabled",
        json_mode=True,
    )

    assert response.payload == {"assessment_tests": []}
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 2048
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert requests[0]["response_format"] == {"type": "json_object"}


def test_transport_defaults_do_not_enable_authoring_only_fields():
    requests: list[dict[str, object]] = []

    def client(request):
        requests.append(dict(request))
        return {"dimensions": []}

    chat_json(
        system_prompt="synthetic system",
        user_payload={"synthetic": True},
        client=client,
    )

    assert requests[0]["max_tokens"] == 8192
    assert "thinking" not in requests[0]
    assert "response_format" not in requests[0]
```

- [ ] **Step 2: Add failing parameter-validation tests**

Add the exact closed-value cases:

```python
@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"token_budgets": ()}, "token_budgets"),
        ({"token_budgets": (0,)}, "token_budgets"),
        ({"token_budgets": (True,)}, "token_budgets"),
        ({"thinking_mode": "unknown"}, "thinking_mode"),
        ({"json_mode": 1}, "json_mode"),
    ],
)
def test_transport_rejects_invalid_request_controls(options, message):
    with pytest.raises(ValueError, match=message):
        chat_json(
            system_prompt="synthetic system",
            user_payload={"synthetic": True},
            client=lambda _request: {"dimensions": []},
            **options,
        )
```

- [ ] **Step 3: Run the focused tests and capture the RED result**

Run:

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_dimension_analyzer.py::test_transport_supports_bounded_authoring_json_requests \
  myextension/tests/test_dimension_analyzer.py::test_transport_defaults_do_not_enable_authoring_only_fields \
  myextension/tests/test_dimension_analyzer.py::test_transport_rejects_invalid_request_controls
```

Expected: the explicit-controls and validation cases fail because `chat_json()` does not accept the new keywords; the default-isolation case already passes and acts as a non-regression sentinel.

- [ ] **Step 4: Implement the minimal closed request options**

Extend the imports and signature in `myextension/llm_transport.py`:

```python
from collections.abc import Callable, Mapping, Sequence
from typing import Literal

ThinkingMode = Literal["enabled", "disabled", "auto"]


def chat_json(
    *,
    system_prompt: str,
    user_payload: Mapping[str, object],
    client: JsonClient | None = None,
    token_budgets: Sequence[int] = STRUCTURED_OUTPUT_TOKEN_BUDGETS,
    thinking_mode: ThinkingMode | None = None,
    json_mode: bool = False,
) -> LlmTransportResult:
```

At the top of `chat_json()`, before configuration loading or any client call, normalize and validate the options:

```python
    budgets = tuple(token_budgets)
    if not budgets or any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        for value in budgets
    ):
        raise ValueError("token_budgets must contain positive integers")
    if thinking_mode not in {None, "enabled", "disabled", "auto"}:
        raise ValueError("thinking_mode is invalid")
    if not isinstance(json_mode, bool):
        raise ValueError("json_mode must be boolean")
```

Iterate over `budgets` instead of the global constant, then add only explicitly selected fields:

```python
    for max_tokens in budgets:
        request_body: dict[str, object] = {
            "model": model_name,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if thinking_mode is not None:
            request_body["thinking"] = {"type": thinking_mode}
        if json_mode:
            request_body["response_format"] = {"type": "json_object"}
```

- [ ] **Step 5: Run focused GREEN and existing truncation regression**

Run:

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_dimension_analyzer.py::test_transport_supports_bounded_authoring_json_requests \
  myextension/tests/test_dimension_analyzer.py::test_transport_defaults_do_not_enable_authoring_only_fields \
  myextension/tests/test_dimension_analyzer.py::test_transport_rejects_invalid_request_controls \
  myextension/tests/test_dimension_analyzer.py::test_transport_retries_one_length_truncation_with_larger_budget \
  myextension/tests/test_dimension_analyzer.py::test_transport_stops_after_second_length_truncation \
  myextension/tests/test_dimension_analyzer.py::test_transport_does_not_retry_malformed_nontruncated_json
```

Expected: all cases pass; existing recovery still requests `[8192, 16384]` and malformed non-truncated JSON still makes one request.

- [ ] **Step 6: Review the transport diff and commit**

Run:

```bash
git diff --check
git diff -- myextension/llm_transport.py myextension/tests/test_dimension_analyzer.py
git add myextension/llm_transport.py myextension/tests/test_dimension_analyzer.py
git commit -m "fix: add bounded structured authoring transport"
```

Expected: the commit contains only the shared transport options and their tests; no assessment call site has changed yet.

---

### Task 2: Apply fast structured requests only to assessment authoring

**Files:**
- Modify: `myextension/assessment_assistant.py`
- Test: `myextension/tests/test_assessment_assistant.py`

**Interfaces:**
- Consumes: Task 1 `chat_json(*, system_prompt: str, user_payload: Mapping[str, object], client: JsonClient | None, token_budgets: Sequence[int], thinking_mode: ThinkingMode | None, json_mode: bool) -> LlmTransportResult`.
- Produces: `ASSESSMENT_ASSIST_TOKEN_BUDGETS: tuple[int, int] = (2048, 4096)` and both `recommend_knowledge_points()` / `generate_assessment_tests()` calls containing the authoring-only fields.
- Preserves: existing closed-field, Chinese-text, knowledge-point reference, submission-kind, coverage and JSON Schema validation.

- [ ] **Step 1: Strengthen the knowledge recommendation request-body test**

In `test_recommendation_treats_prompt_injection_as_question_data`, append:

```python
    assert seen["max_tokens"] == 2048
    assert seen["thinking"] == {"type": "disabled"}
    assert seen["response_format"] == {"type": "json_object"}
```

- [ ] **Step 2: Strengthen the test-generation request and recovery tests**

In `test_generated_tests_are_closed_and_reference_current_points_only`, append the same three assertions against `seen`. In `test_generated_tests_recovers_one_length_truncation_without_second_user_action`, change only the expected budgets:

```python
    assert [request["max_tokens"] for request in requests] == [
        2048,
        4096,
    ]
    assert all(
        request["thinking"] == {"type": "disabled"}
        and request["response_format"] == {"type": "json_object"}
        for request in requests
    )
```

- [ ] **Step 3: Run focused tests and capture the RED result**

Run:

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_assessment_assistant.py::test_recommendation_treats_prompt_injection_as_question_data \
  myextension/tests/test_assessment_assistant.py::test_generated_tests_are_closed_and_reference_current_points_only \
  myextension/tests/test_assessment_assistant.py::test_generated_tests_recovers_one_length_truncation_without_second_user_action
```

Expected: failures show missing `thinking` / `response_format` and old `[8192, 16384]` budgets.

- [ ] **Step 4: Implement the authoring-only call-site options**

Add the constant below the system prompts:

```python
ASSESSMENT_ASSIST_TOKEN_BUDGETS: tuple[int, int] = (2048, 4096)
```

Change `_payload_from_chat()` so its only `chat_json()` call is:

```python
        return chat_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
            client=client,
            token_budgets=ASSESSMENT_ASSIST_TOKEN_BUDGETS,
            thinking_mode="disabled",
            json_mode=True,
        ).payload
```

Do not pass these options from `dimension_analyzer`, `llm_labeler`, `analysis_job_store` or any complete-session call path.

- [ ] **Step 5: Run authoring GREEN and shared-default non-regression**

Run:

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_assessment_assistant.py \
  myextension/tests/test_dimension_analyzer.py -k "assessment or transport"
```

Expected: all selected cases pass; the authoring tests see `[2048, 4096]`, while default transport tests still see `[8192, 16384]` without authoring fields.

- [ ] **Step 6: Review the authoring diff and commit**

Run:

```bash
git diff --check
git diff -- myextension/assessment_assistant.py myextension/tests/test_assessment_assistant.py
git add myextension/assessment_assistant.py myextension/tests/test_assessment_assistant.py
git commit -m "fix: bound assessment suggestion generation"
```

Expected: one vertical change proving both knowledge and test suggestions use the same fast structured settings.

---

### Task 3: Return stable privacy-safe Provider failure codes

**Files:**
- Modify: `myextension/routes.py`
- Test: `myextension/tests/test_assessment_assist_api.py`

**Interfaces:**
- Consumes: `LlmTransportError.error_code: str`, `LlmTransportError.http_status: int | None`, the route-specific `failure_code`, and existing `finish_error(status, code, message, retryable=True)`.
- Produces: `_assessment_assist_transport_code(error: LlmTransportError, fallback: str) -> str` with the exact mapping approved in the design.
- Preserves: `ai_not_configured` remains HTTP 409; request/output validation contracts remain unchanged; all mapped Provider failures remain HTTP 502 and `retryable: true`.

- [ ] **Step 1: Add the parameterized route-contract test**

Add imports and this test to `test_assessment_assist_api.py`:

```python
import pytest


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
```

Place `from myextension.llm_transport import LlmTransportError` at module scope. The marker is synthetic and must not appear in the response.

- [ ] **Step 2: Run the API test and capture the RED result**

Run:

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_assessment_assist_api.py::test_test_assist_maps_provider_failures_without_private_echo
```

Expected: mapped cases fail because every transport error currently returns `test_generation_failed`; the unknown fallback case passes.

- [ ] **Step 3: Implement the closed mapping helper**

Add this module-level helper above `AssessmentAssistRouteHandler`:

```python
def _assessment_assist_transport_code(
    error: LlmTransportError,
    fallback: str,
) -> str:
    if error.error_code == "provider_timeout":
        return "ai_provider_timeout"
    if error.error_code == "provider_network_error":
        return "ai_provider_network_error"
    if error.error_code == "provider_response_truncated":
        return "ai_response_truncated"
    if error.error_code == "provider_response_invalid":
        return "ai_response_invalid"
    if error.error_code != "provider_http_error":
        return fallback
    if error.http_status in {401, 403}:
        return "ai_provider_auth_failed"
    if error.http_status == 429:
        return "ai_provider_rate_limited"
    if error.http_status is not None and 400 <= error.http_status < 500:
        return "ai_provider_request_rejected"
    if error.http_status is not None and 500 <= error.http_status < 600:
        return "ai_provider_unavailable"
    return fallback
```

Change only the `LlmTransportError` branch in `_finish_assist_error()`:

```python
        if isinstance(error, LlmTransportError):
            self.finish_error(
                502,
                _assessment_assist_transport_code(
                    error,
                    self.failure_code,
                ),
                self.failure_message,
                retryable=True,
            )
            return
```

- [ ] **Step 4: Run route GREEN and adjacent API regressions**

Run:

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_assessment_assist_api.py \
  myextension/tests/test_routes.py -k "ai_config or assessment"
```

Expected: all selected cases pass; missing AI remains 409 and no synthetic marker is echoed.

- [ ] **Step 5: Review the API contract diff and commit**

Run:

```bash
git diff --check
git diff -- myextension/routes.py myextension/tests/test_assessment_assist_api.py
git add myextension/routes.py myextension/tests/test_assessment_assist_api.py
git commit -m "fix: expose safe assessment provider errors"
```

Expected: the commit changes only error categorization, not response bodies with Provider details and not request validation.

---

### Task 4: Show actionable step-specific errors and preserve manual work

**Files:**
- Modify: `src/ui/guidedProfileEditor.ts`
- Test: `src/__tests__/assessmentPlanEditor.spec.ts`

**Interfaces:**
- Consumes: `ApiError.status`, `ApiError.code`, and `kind: "knowledge" | "tests"`.
- Produces: `assistFailureMessage(error, kind)` mappings for `ai_provider_timeout`, `ai_provider_network_error`, `ai_provider_auth_failed`, `ai_provider_rate_limited`, `ai_provider_request_rejected`, `ai_provider_unavailable`, `ai_response_truncated`, and `ai_response_invalid`.
- Preserves: `ai_not_configured` wording, unknown-error fallback, existing request-generation guards, autosave, merge behavior and all teacher-authored state.

- [ ] **Step 1: Import `ApiError` and add parameterized knowledge-step message tests**

Add:

```typescript
import { ApiError } from '../models/apiError';
```

Then add this table inside the existing `describe` block:

```typescript
it.each([
  ['ai_provider_timeout', '生成超时，当前草稿已保留'],
  ['ai_provider_network_error', '检查网络、DNS、TLS 或代理'],
  ['ai_provider_auth_failed', '检查 API Key 和模型权限'],
  ['ai_provider_rate_limited', '稍后重试，并检查额度或并发限制'],
  ['ai_provider_request_rejected', '检查 Base URL、模型和参数兼容性'],
  ['ai_provider_unavailable', 'AI 服务暂时不可用，请稍后重试'],
  ['ai_response_truncated', '减少知识点数量或描述长度后重试'],
  ['ai_response_invalid', '检查模型是否支持结构化 JSON 输出']
])(
  'shows actionable knowledge guidance for %s',
  async (code, expectedMessage) => {
    request.mockRejectedValueOnce(new ApiError(502, code, 'safe', true));
    const editor = createEditor();
    completeQuestion(editor);

    clickButton(editor, '下一步：确认知识点');
    await flushPromises();

    expect(editor.node.textContent).toContain(expectedMessage);
    expect(editor.node.textContent).toContain('添加自定义知识点');
    editor.dispose();
  }
);
```

- [ ] **Step 2: Add a test-step timeout case that proves teacher edits survive**

Add:

```typescript
it('keeps manual tests when AI test regeneration times out', async () => {
  request.mockRejectedValue(
    new ApiError(502, 'ai_provider_timeout', 'safe', true)
  );
  const editor = createEditor();
  completeQuestion(editor, { teacherFocus: '循环边界' });
  clickButton(editor, '下一步：确认知识点');
  clickButton(editor, '我已确认以上知识点');
  await flushPromises();

  clickButton(editor, '添加手工测试');
  setField(fieldByLabel(editor.node, '测试 1 名称'), '教师保留的边界测试');
  clickButton(editor, '重新生成测试建议');
  await flushPromises();

  expect(editor.node.textContent).toContain('测试建议生成超时，当前草稿已保留');
  expect(fieldByLabel(editor.node, '测试 1 名称').value).toBe(
    '教师保留的边界测试'
  );
  expect(editor.node.textContent).toContain('添加手工测试');
  editor.dispose();
});
```

- [ ] **Step 3: Run the focused Jest cases and capture the RED result**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/assessmentPlanEditor.spec.ts \
  --runInBand --coverage=false \
  -t "actionable knowledge guidance|keeps manual tests"
```

Expected: all new expectations fail against the current generic “AI 暂时不可用” wording; the manual field itself remains present, proving state preservation already works.

- [ ] **Step 4: Implement the minimal safe-code message table**

Keep the existing `ai_not_configured` branch, then add this table and fallback inside `assistFailureMessage()`:

```typescript
    if (error instanceof ApiError) {
      const subject = kind === 'knowledge' ? '知识点建议' : '测试建议';
      const messages: Readonly<Record<string, string>> = {
        ai_provider_timeout: `${subject}生成超时，当前草稿已保留，可重试或手工继续。`,
        ai_provider_network_error:
          '无法连接 AI 服务，请检查网络、DNS、TLS 或代理；当前草稿已保留。',
        ai_provider_auth_failed:
          'AI 鉴权失败，请检查 API Key 和模型权限；也可先手工继续。',
        ai_provider_rate_limited:
          'AI 请求受限，请稍后重试，并检查额度或并发限制。',
        ai_provider_request_rejected:
          'AI 拒绝了请求，请检查 Base URL、模型和参数兼容性。',
        ai_provider_unavailable:
          'AI 服务暂时不可用，请稍后重试；当前草稿已保留。',
        ai_response_truncated:
          'AI 输出被截断，请减少知识点数量或描述长度后重试。',
        ai_response_invalid:
          'AI 返回格式无效，请检查模型是否支持结构化 JSON 输出。'
      };
      const mapped = messages[error.code];
      if (mapped) {
        return mapped;
      }
    }
```

Retain the current `knowledge` / `tests` generic return as the final fallback. Do not render `error.message`, `error.details` or arbitrary server text.

- [ ] **Step 5: Run frontend GREEN, formatting and type gates**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/assessmentPlanEditor.spec.ts --runInBand --coverage=false
.venv/bin/jlpm prettier:base --check
.venv/bin/jlpm eslint:check
.venv/bin/jlpm build:lib:prod
```

Expected: the entire editor suite passes; Prettier, ESLint and TypeScript exit `0`. If Prettier reports only the modified test or source, run `.venv/bin/jlpm prettier:base --write src/ui/guidedProfileEditor.ts src/__tests__/assessmentPlanEditor.spec.ts`, review the diff, and repeat all four commands.

- [ ] **Step 6: Review the frontend diff and commit**

Run:

```bash
git diff --check
git diff -- src/ui/guidedProfileEditor.ts src/__tests__/assessmentPlanEditor.spec.ts
git add src/ui/guidedProfileEditor.ts src/__tests__/assessmentPlanEditor.spec.ts
git commit -m "fix: explain assessment suggestion failures"
```

Expected: no state mutation or Provider detail rendering is added; only stable-code presentation and tests change.

---

### Task 5: Run source regressions and synchronize the `0.2.1` wheel delivery

**Files:**
- Modify: `myextension/tests/test_labextension_artifact.py`
- Modify: `README.md`
- Modify: `deploy/bluedot/release-0.2.1/README.md`
- Modify: `deploy/bluedot/release-0.2.1/runtime.env.example`
- Modify: `deploy/bluedot/release-0.2.1/SHA256SUMS`
- Replace: `deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl`
- Modify: `myextension-0.2.1-BLUEDOT-完整交付包/00_从这里开始.md`
- Modify: `myextension-0.2.1-BLUEDOT-完整交付包/README.md`
- Modify: `myextension-0.2.1-BLUEDOT-完整交付包/runtime.env.example`
- Modify: `myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json`
- Modify: `myextension-0.2.1-BLUEDOT-完整交付包/SHA256SUMS`
- Replace: `myextension-0.2.1-BLUEDOT-完整交付包/artifacts/myextension-0.2.1-py3-none-any.whl`
- Create: `docs/2026-08-06-assessment-assist-latency-reliability-verification.md`

**Interfaces:**
- Consumes: Tasks 1–4 source commits and unchanged package version `0.2.1`.
- Produces: one freshly built wheel copied byte-for-byte to both delivery roots, matching SHA/README/MANIFEST metadata and a verification record containing only executed evidence.
- Preserves: both Docker scripts and Dockerfile behavior, old delivery archives, saved user data, current provider secret and current running preview until Task 6 has a replacement.

- [ ] **Step 1: Add a failing compiled-artifact marker gate**

Append stable frontend error-code markers to `REQUIRED_TASK_12_MARKERS`:

```python
REQUIRED_TASK_12_MARKERS = (
    "编程行为分析",
    "本次会话结果",
    "分析详情",
    "教师复核",
    "jp-BehaviorAudit-sidebarTab",
    "ai_provider_timeout",
    "ai_response_invalid",
)
```

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_labextension_artifact.py
```

Expected: RED because the current compiled repository labextension and existing delivery wheel predate the new frontend error table.

- [ ] **Step 2: Run all source-level quality gates before packaging**

Run in this order and record exact counts in the verification document only after each exits `0`:

```bash
.venv/bin/python -m pytest -q myextension/tests --ignore=myextension/tests/test_labextension_artifact.py
.venv/bin/jlpm test --runInBand
.venv/bin/jlpm stylelint:check
.venv/bin/jlpm prettier:base --check
.venv/bin/jlpm eslint:check
.venv/bin/jlpm build:lib:prod
.venv/bin/jupyter-builder build .
```

Expected: backend and frontend full suites pass; three static gates pass; TypeScript and JupyterLab prebuilt builds finish successfully. If pytest-jupyter is blocked from binding a loopback test port, rerun the same pytest command with permission limited to local loopback binding and record the constraint.

- [ ] **Step 3: Make the compiled-artifact marker green before wheel build**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_labextension_artifact.py
```

Expected: the repository half now contains both new markers, while the delivery-wheel half still fails byte identity against the old wheel. This is the expected packaging RED; do not weaken the assertion.

- [ ] **Step 4: Build a clean wheel and copy identical bytes to both delivery roots**

Run:

```bash
uv build --wheel --offline
cp -p dist/myextension-0.2.1-py3-none-any.whl \
  deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
cp -p dist/myextension-0.2.1-py3-none-any.whl \
  myextension-0.2.1-BLUEDOT-完整交付包/artifacts/myextension-0.2.1-py3-none-any.whl
shasum -a 256 dist/myextension-0.2.1-py3-none-any.whl
```

Expected: the build succeeds; record the printed 64-character digest as `NEW_WHEEL_SHA`. It must differ from the prior UI-hotfix digest `c7bffe0ad1528715b9bdd371965d0bc52d762429c31e2f3664d3136a60547386`.

- [ ] **Step 5: Update operator documentation with exact behavior and actual identities**

Use `apply_patch` for text and JSON files. Apply these exact content rules:

1. In root `README.md`, add a “建议生成性能与故障排查” subsection stating: authoring suggestions internally use 2048/4096, thinking disabled and JSON object mode; full-session analysis remains 8192/16384; list all eight stable codes and the same safe actions as Task 4.
2. In both delivery READMEs, change the opening description to include the 2026-08-06 assessment-assist reliability fix; add a subsection stating no new environment variable is required and that model switching is not part of the fix.
3. In both `runtime.env.example` files, keep all four values unchanged and insert these non-secret comments above `ARK_BASE_URL`:

```text
# Authoring suggestions use internal 2048/4096 JSON requests with thinking
# disabled. Whole-session analysis keeps its separate 120-second budget.
```

4. Update both `SHA256SUMS` files and both delivery README digest blocks to `NEW_WHEEL_SHA`.
5. Update `00_从这里开始.md` to call this the latest assessment-assist-fix `0.2.1` folder and point readers to the troubleshooting section.
6. Update `MANIFEST.json` with `NEW_WHEEL_SHA`, the Task 4 commit from `git rev-parse HEAD`, exact test/build results from Steps 2–3, and `"Real or paid AI provider"` still under `not_validated` until Task 6 succeeds.
7. Create `docs/2026-08-06-assessment-assist-latency-reliability-verification.md` recording the synthetic-only diagnosis, Tasks 1–4 RED/GREEN commands, exact Step 2 counts, current wheel digest, privacy boundary, old digest, rollback commit `05628ef`, and explicit non-execution of Docker/push/BLUEDOT.

Do not record the API Key, tokenized preview URL, Provider response content, actual draft path or actual draft metadata.

- [ ] **Step 6: Run artifact, checksum, JSON, script and byte-identity gates**

Run:

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_labextension_artifact.py \
  myextension/tests/test_bluedot_release.py
.venv/bin/check-wheel-contents \
  deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
.venv/bin/python -m zipfile -t \
  deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
.venv/bin/python -m json.tool \
  myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json
shasum -a 256 -c deploy/bluedot/release-0.2.1/SHA256SUMS
shasum -a 256 -c myextension-0.2.1-BLUEDOT-完整交付包/SHA256SUMS
cmp \
  deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl \
  myextension-0.2.1-BLUEDOT-完整交付包/artifacts/myextension-0.2.1-py3-none-any.whl
cmp \
  deploy/bluedot/release-0.2.1/README.md \
  myextension-0.2.1-BLUEDOT-完整交付包/README.md
cmp \
  deploy/bluedot/release-0.2.1/runtime.env.example \
  myextension-0.2.1-BLUEDOT-完整交付包/runtime.env.example
sh -n deploy/bluedot/release-0.2.1/build_image.sh
sh -n deploy/bluedot/release-0.2.1/verify_image.sh
```

Expected: pytest passes; wheel content and ZIP checks pass; JSON parses; both checksum checks print `OK`; all three `cmp` and both syntax checks exit `0`.

- [ ] **Step 7: Install only into a new isolated target**

Run:

```bash
ACCEPTANCE_ROOT="$(mktemp -d /private/tmp/myextension-assist-fix.XXXXXX)"
.venv/bin/python -m pip install --no-deps --no-cache-dir \
  --target "$ACCEPTANCE_ROOT/site" \
  deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
PYTHONPATH="$ACCEPTANCE_ROOT/site" .venv/bin/python -P -c \
  "import myextension; assert myextension.__version__ == '0.2.1'; print(myextension.__version__)"
JUPYTER_PATH="$ACCEPTANCE_ROOT/site/share/jupyter" \
  PYTHONPATH="$ACCEPTANCE_ROOT/site" \
  .venv/bin/python -P -m jupyter labextension list
```

Expected: import prints `0.2.1`; labextension list reports enabled `myextension v0.2.1`. Keep the exact `ACCEPTANCE_ROOT` in the execution transcript for Task 6, but do not commit the temporary path.

- [ ] **Step 8: Commit the synchronized wheel delivery**

Run:

```bash
git diff --check
git add \
  README.md \
  myextension/tests/test_labextension_artifact.py \
  deploy/bluedot/release-0.2.1 \
  myextension-0.2.1-BLUEDOT-完整交付包 \
  docs/2026-08-06-assessment-assist-latency-reliability-verification.md
git commit -m "build: deliver assessment assist reliability fix"
git status --short
```

Expected: commit succeeds; no tracked source or delivery change remains unstaged, and every prior ZIP remains present and unchanged.

---

### Task 6: Perform one synthetic Provider acceptance, start the latest preview, and archive

**Files:**
- Modify: `docs/2026-08-06-assessment-assist-latency-reliability-verification.md`
- Modify: `myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json`
- Create: `myextension-0.2.1-BLUEDOT-完整交付包-20260806-assessment-assist-fix.zip`
- Create: `myextension-0.2.1-BLUEDOT-完整交付包-20260806-assessment-assist-fix.zip.sha256`
- Runtime only: `$ACCEPTANCE_ROOT/{data,workspace}` and a loopback-only JupyterLab process.

**Interfaces:**
- Consumes: Task 5 isolated wheel site, current saved AI config at `/private/tmp/myextension-ui-hotfix-final.hPnQD3/data/.ark_ai_config.json`, and the user's explicit authorization for synthetic Provider calls.
- Produces: at most one external synthetic Provider request, a clean-data wheel-based preview, a non-secret verification record, the new ZIP/digest, and local rollback tag `assessment-assist-delivery-0.2.1`.
- Preserves: the saved config file, its permissions and contents; the old preview data/draft; all previous archives; no actual draft or student data is read or sent.

- [ ] **Step 1: Confirm the saved config exists without displaying it**

Run:

```bash
test -f /private/tmp/myextension-ui-hotfix-final.hPnQD3/data/.ark_ai_config.json
stat -f '%Sp' /private/tmp/myextension-ui-hotfix-final.hPnQD3/data/.ark_ai_config.json
```

Expected: the file exists and permissions are private. Do not run `cat`, `jq`, `sed`, checksum or any command that outputs its contents or permits correlating the secret.

- [ ] **Step 2: Run exactly one synthetic external acceptance request**

Run the following from the worktree. The wrapper blocks any second external call before network I/O and prints only count, test count and elapsed seconds:

```bash
PYTHONPATH="$ACCEPTANCE_ROOT/site" \
JUPYTERLAB_BEHAVIOR_AUDIT_AI_CONFIG_PATH=/private/tmp/myextension-ui-hotfix-final.hPnQD3/data/.ark_ai_config.json \
.venv/bin/python -P - <<'PY'
import time

from myextension import llm_transport
from myextension.assessment_assistant import generate_assessment_tests

external_calls = 0
original_client = llm_transport.provider_json_client


def one_call_only(request_body, *, timeout_sec):
    global external_calls
    if external_calls >= 1:
        raise llm_transport.LlmTransportError(
            "diagnostic_second_call_blocked"
        )
    external_calls += 1
    return original_client(request_body, timeout_sec=timeout_sec)


llm_transport.provider_json_client = one_call_only
statement = (
    "编写函数 analyze_scores(values)，接收整数列表并返回去除空列表影响后的统计结果。"
    "实现时需要处理空列表、单元素、负数、重复值和普通列表；返回值必须稳定，"
    "不得依赖全局状态。请依据函数调用输入和期望输出，为五个可观察知识点生成"
    "结构化测试建议。" + "请使用可观察输入输出验证边界条件。" * 20
)[:195]
assert len(statement) == 195
knowledge_points = [
    {
        "id": f"KP_{index:08d}",
        "name": name,
        "description": description,
    }
    for index, (name, description) in enumerate(
        [
            ("空列表边界", "明确判断空列表并返回约定结果。"),
            ("单元素处理", "单元素输入不发生错误且结果正确。"),
            ("负数计算", "负数参与计算时保持数值规则一致。"),
            ("重复值处理", "重复元素均按输入次数参与计算。"),
            ("普通列表统计", "普通整数列表得到稳定的预期结果。"),
        ],
        start=1,
    )
]
started = time.monotonic()
result = generate_assessment_tests(
    statement,
    submission_contract={"kind": "function", "entrypoint": "analyze_scores"},
    knowledge_points=knowledge_points,
)
elapsed = time.monotonic() - started
tests = result["assessment_tests"]
assert external_calls == 1
assert len(tests) == 5
assert {point["id"] for point in knowledge_points} == {
    point_id
    for test in tests
    for point_id in test["knowledge_point_ids"]
}
print(
    f"synthetic_provider_ok calls={external_calls} "
    f"tests={len(tests)} elapsed_sec={elapsed:.3f}"
)
PY
```

Expected: one line beginning `synthetic_provider_ok calls=1 tests=5`; elapsed time is below 60 seconds. If it fails, record only the exception's safe code and elapsed time, keep `Real or paid AI provider` under `not_validated`, do not retry, and continue with offline wheel/preview verification without claiming real Provider success.

- [ ] **Step 3: Prepare a clean preview workspace and identify the new frontend**

Run:

```bash
mkdir -p "$ACCEPTANCE_ROOT/data" "$ACCEPTANCE_ROOT/workspace"
cp -p demo/macos_real_ai/demo_notebook.ipynb \
  "$ACCEPTANCE_ROOT/workspace/demo_notebook.ipynb"
unzip -Z1 \
  deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl \
  | rg 'share/jupyter/labextensions/myextension/static/remoteEntry\.[0-9a-f]+\.js$'
lsof -nP -iTCP:18997 -sTCP:LISTEN
```

Expected: exactly one `remoteEntry` filename is printed and port `18997` has no listener. If the port is occupied, inspect its owner and stop rather than killing it; select the first free loopback port from 18998 upward and record the chosen port.

- [ ] **Step 4: Start JupyterLab entirely from the isolated wheel**

Start one managed foreground process with:

```bash
PYTHONPATH="$ACCEPTANCE_ROOT/site" \
JUPYTER_PATH="$ACCEPTANCE_ROOT/site/share/jupyter" \
JUPYTER_CONFIG_PATH="$ACCEPTANCE_ROOT/site/etc/jupyter" \
JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR="$ACCEPTANCE_ROOT/data" \
.venv/bin/python -P -m jupyter lab \
  --no-browser \
  --ServerApp.ip=127.0.0.1 \
  --ServerApp.port=18997 \
  --ServerApp.port_retries=0 \
  --ServerApp.root_dir="$ACCEPTANCE_ROOT/workspace" \
  --ServerApp.password=''
```

Expected: `myextension` server extension loads, the terminal prints a generated loopback `/lab` URL with a runtime `token` query parameter, and the old `/private/tmp/myextension-ui-hotfix-final.hPnQD3/data` directory is not used as log/data storage.

- [ ] **Step 5: Perform HTTP and real-browser smoke acceptance**

Use the generated token only in the runtime request and browser address bar. Require:

1. `/lab` returns HTTP 200;
2. the loaded resource uses the exact Task 6 Step 3 `remoteEntry` filename;
3. the left label “行为分析” is upright;
4. “创建题目考核方案” opens with no pre-existing profiles in the clean data directory;
5. the UI contains the new timeout guidance after a locally mocked or unit-tested failure path; do not trigger a second real Provider call from the browser;
6. no source file from the worktree is loaded through `PYTHONPATH` or `JUPYTER_PATH`.

Use the project's browser automation skill for the visible checks. Save a local screenshot without a token in its filename. If browser automation is unavailable, mark visual acceptance pending and give the user the latest URL; do not infer visual success from HTTP 200.

- [ ] **Step 6: Record only non-secret acceptance evidence**

Use `apply_patch` to update the verification report and `MANIFEST.json` with:

- the synthetic request result as `calls=1`, returned test count and elapsed time, or its safe failure code;
- the non-token base URL `http://127.0.0.1:18997/lab`;
- exact new `remoteEntry` filename, HTTP result and visual result;
- clean data directory status stated generically, without the old draft metadata;
- explicit non-execution of Docker build/run, registry push, BLUEDOT registration and actual-draft transmission;
- rollback commit `05628ef`, prior wheel SHA `c7bffe0ad1528715b9bdd371965d0bc52d762429c31e2f3664d3136a60547386`, and new wheel SHA.

If the synthetic request passed, move `Real or paid AI provider` from `not_validated` to a precisely worded `synthetic_provider_acceptance` entry; never claim real course-data acceptance.

- [ ] **Step 7: Create the new archive without replacing older archives**

Run:

```bash
test ! -e myextension-0.2.1-BLUEDOT-完整交付包-20260806-assessment-assist-fix.zip
/usr/bin/zip -qr \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-assessment-assist-fix.zip \
  myextension-0.2.1-BLUEDOT-完整交付包 \
  -x '*/.DS_Store'
.venv/bin/python -m zipfile -t \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-assessment-assist-fix.zip
shasum -a 256 \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-assessment-assist-fix.zip
```

Use `apply_patch` to create the `.zip.sha256` file with the printed digest and exact filename, then run:

```bash
shasum -a 256 -c \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-assessment-assist-fix.zip.sha256
```

Expected: wheel archive and outer ZIP both pass integrity; all older archive filenames and hashes remain unchanged.

- [ ] **Step 8: Run the final full verification gate**

Run:

```bash
git diff --check
.venv/bin/python -m pytest -q myextension/tests
.venv/bin/jlpm test --runInBand
.venv/bin/jlpm stylelint:check
.venv/bin/jlpm prettier:base --check
.venv/bin/jlpm eslint:check
.venv/bin/jlpm build:lib:prod
.venv/bin/jupyter-builder build .
shasum -a 256 -c deploy/bluedot/release-0.2.1/SHA256SUMS
shasum -a 256 -c myextension-0.2.1-BLUEDOT-完整交付包/SHA256SUMS
shasum -a 256 -c \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-assessment-assist-fix.zip.sha256
git status --short
```

Expected: every command exits `0`; status contains only the two evidence files and new archive/checksum intended for the final commit. If rebuilding changes `myextension/labextension` bytes after the wheel was made, stop and return to Task 5 Step 4 so source, wheel and archive are regenerated from identical bytes.

- [ ] **Step 9: Commit evidence, create the local rollback tag, and stop**

Run:

```bash
git add \
  docs/2026-08-06-assessment-assist-latency-reliability-verification.md \
  myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-assessment-assist-fix.zip \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-assessment-assist-fix.zip.sha256
git commit -m "docs: record assessment assist fix verification"
test -z "$(git tag --list assessment-assist-delivery-0.2.1)"
git tag assessment-assist-delivery-0.2.1
git status --short
```

Expected: working tree is clean, the tag points to the evidence commit, and the new wheel/ZIP/digests are ready for user handoff. Stop here: provide the latest tokenized loopback preview URL, absolute folder/wheel/ZIP paths, both SHA-256 values, actual test counts, install/restart steps and rollback tag; do not push, deploy or stop/delete the preserved old data directory.
