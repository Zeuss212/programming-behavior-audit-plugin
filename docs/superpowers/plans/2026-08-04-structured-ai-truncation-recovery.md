# Structured AI Truncation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Only use subagent-driven execution if the user explicitly requests it and each writer has an isolated worktree. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复结构化 AI 首次响应被 4096 token 截断的问题，并确保 AI 分析失败时不再展示“部分结果 / 查看 0 条证据”的误导性占位卡。

**Architecture:** 在共享 `chat_json()` 边界为所有结构化调用增加 8192 初始预算，并仅对 `finish_reason=length` 执行一次 16384 恢复请求；后台记录同一 attempt 内的两次真实响应。结果页独立识别“AI 失败且没有可用维度结论”，显示采集完成、分析未完成和服务端数据质量原因；ordinary partial 继续显示，但不生成空证据控件。

**Tech Stack:** Python 3.12、pytest、TypeScript 5.5、Jest/jsdom、JupyterLab 4、Yarn/JLPM、hatch/uv wheel 构建

## Global Constraints

- 结构化请求首次 `max_tokens=8192`；仅 `finish_reason=length` 时以 `max_tokens=16384` 重试一次，不存在第三次调用。
- 普通 malformed JSON、业务校验失败、认证失败和不可重试 HTTP 错误不得触发截断恢复。
- 不引入 provider 专用参数、新依赖、API Schema 变更、数据库迁移或 Profile 迁移。
- 不执行模型返回的测试代码；自动化验证只使用固定合成响应。
- 不读取、输出、修改或删除现有 API Key，不调用真实或付费 AI。
- 不修改采集、候选事件选择、覆盖门槛、教师复核契约或 ordinary partial 的合法结论。
- 不重启当前 `127.0.0.1:8899` 预览、不安装新 wheel、不推送或部署。
- 当前目录不是 Git 仓库；所有 commit 步骤以文件清单、哈希和验证记录替代。

---

## File Structure

- `myextension/llm_transport.py`：唯一的结构化输出预算、长度截断分类和一次恢复循环。
- `myextension/tests/test_dimension_analyzer.py`：共享传输的失败分类、调用预算和非截断不重试测试；提供合成截断响应夹具。
- `myextension/tests/test_assessment_assistant.py`：证明一次教师操作可以从首次截断自动恢复并生成测试建议。
- `myextension/tests/test_analysis_job_store.py`：证明后台分析在同一 attempt 内恢复、进入 ready、保留两条私有响应和证据声明。
- `src/ui/analysisResultView.ts`：AI 失败空状态和空证据控件抑制。
- `src/__tests__/analysisResultView.spec.ts`：失败空状态、ordinary partial、`not_observed` 兼容回归。
- `docs/2026-08-04-structured-ai-truncation-recovery-verification.md`：最终验证命令、结果、wheel 哈希和未执行项。
- `项目交接文档.md`：将两个 P0 与 wheel 状态更新为本轮真实结果。
- `docs/superpowers/specs/2026-08-04-structured-ai-truncation-recovery-design.md`：完成后更新状态。
- `dist/myextension-0.2.0-py3-none-any.whl`：重新构建的本地交付制品。

---

### Task 1: 共享传输截断恢复与后端纵向回归

**Files:**
- Modify: `myextension/llm_transport.py:20-275`
- Modify: `myextension/tests/test_dimension_analyzer.py:16-27,701-729`
- Modify: `myextension/tests/test_assessment_assistant.py:130-190`
- Modify: `myextension/tests/test_analysis_job_store.py:22-29,859-1003`

**Interfaces:**
- Consumes: `chat_json(system_prompt: str, user_payload: Mapping[str, object], client: JsonClient | None) -> LlmTransportResult`；现有 `_RecordingRetryingClient` 继续作为后台注入客户端。
- Produces: `STRUCTURED_OUTPUT_TOKEN_BUDGETS: tuple[int, int] = (8192, 16384)`；`_response_was_truncated(response: Mapping[str, object]) -> bool`；第二次长度截断抛出 `LlmTransportError("provider_response_truncated")`。
- Preserves: `LlmTransportResult` 字段、网络退避策略、业务维度修复次数、API 错误结构和 raw response 私有包装格式。

- [x] **Step 1: 在共享传输测试中加入真实失败形状和三条行为测试**

在 `myextension/tests/test_dimension_analyzer.py` 的 `llm_transport` import 中加入 `LlmTransportError`，并在 transport 测试附近加入：

```python
def truncated_provider_response() -> dict[str, object]:
    return {
        "model": "synthetic-model",
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "synthetic hidden reasoning",
                },
            }
        ],
    }


def test_transport_retries_one_length_truncation_with_larger_budget():
    requests: list[dict[str, object]] = []

    def client(request):
        requests.append(dict(request))
        if len(requests) == 1:
            return truncated_provider_response()
        return {
            "model": "synthetic-model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": '{"dimensions":[]}',
                    },
                }
            ],
        }

    response = chat_json(
        system_prompt="synthetic system",
        user_payload={"synthetic": True},
        client=client,
    )

    assert response.payload == {"dimensions": []}
    assert [request["max_tokens"] for request in requests] == [8192, 16384]


def test_transport_stops_after_second_length_truncation():
    requests: list[dict[str, object]] = []

    def client(request):
        requests.append(dict(request))
        return truncated_provider_response()

    with pytest.raises(
        LlmTransportError,
        match="provider_response_truncated",
    ):
        chat_json(
            system_prompt="synthetic system",
            user_payload={"synthetic": True},
            client=client,
        )

    assert [request["max_tokens"] for request in requests] == [8192, 16384]


def test_transport_does_not_retry_malformed_nontruncated_json():
    requests: list[dict[str, object]] = []

    def client(request):
        requests.append(dict(request))
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "not-json"},
                }
            ]
        }

    with pytest.raises(LlmTransportError, match="provider_response_invalid"):
        chat_json(
            system_prompt="synthetic system",
            user_payload={"synthetic": True},
            client=client,
        )

    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 8192
```

- [x] **Step 2: 增加测试建议的一次用户操作恢复测试**

在 `myextension/tests/test_assessment_assistant.py` 加入：

```python
def test_generated_tests_recovers_one_length_truncation_without_second_user_action():
    from myextension.assessment_assistant import generate_assessment_tests

    requests: list[dict[str, object]] = []

    def client(request):
        requests.append(dict(request))
        if len(requests) == 1:
            return {
                "model": "synthetic-model",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "synthetic hidden reasoning",
                        },
                    }
                ],
            }
        return {
            "assessment_tests": [
                {
                    "name": "普通整数列表",
                    "knowledge_point_ids": ["KP_A1B2C3D4"],
                    "kind": "function_call",
                    "input": "[[78, 85, 92]]",
                    "expected": "85.0",
                }
            ]
        }

    result = generate_assessment_tests(
        "编写 calculate_average(numbers)。",
        submission_contract=FUNCTION_CONTRACT,
        knowledge_points=KNOWLEDGE_POINTS,
        client=client,
    )

    assert result["assessment_tests"][0]["name"] == "普通整数列表"
    assert [request["max_tokens"] for request in requests] == [8192, 16384]
```

- [x] **Step 3: 增加后台同一 attempt 恢复与审计测试**

在 `myextension/tests/test_analysis_job_store.py` 从 `test_dimension_analyzer` import `truncated_provider_response`，并加入：

```python
def test_worker_recovers_truncated_response_in_same_attempt(tmp_path):
    session_store, job_store, job = create_worker_job(tmp_path)
    requests: list[dict[str, object]] = []
    waits: list[float] = []

    def provider(request, *, timeout_sec):
        requests.append(dict(request))
        assert timeout_sec == 90
        if len(requests) == 1:
            return truncated_provider_response()
        return provider_response(str(job["session_id"]))

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        wait=waits.append,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))

    updated = job_store.get(str(job["job_id"]))
    assert updated["status"] == "ready"
    assert len(updated["attempt_ids"]) == 1
    assert waits == []
    assert [request["max_tokens"] for request in requests] == [8192, 16384]

    attempt_id = str(updated["active_attempt_id"])
    raw_path = (
        tmp_path
        / "jobs"
        / str(job["job_id"])
        / "attempts"
        / f"{attempt_id}.raw_response.json"
    )
    assert len(json.loads(raw_path.read_text())["responses"]) == 2

    result_path = (
        tmp_path / "analyses" / str(updated["analysis_id"]) / "result.json"
    )
    analysis = json.loads(result_path.read_text())
    assert analysis["dimension_results"][0]["ai_result"]["evidence_claims"]
    worker.shutdown()
```

- [x] **Step 4: 运行 RED 测试并确认失败原因**

Run:

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_dimension_analyzer.py::test_transport_retries_one_length_truncation_with_larger_budget \
  myextension/tests/test_dimension_analyzer.py::test_transport_stops_after_second_length_truncation \
  myextension/tests/test_dimension_analyzer.py::test_transport_does_not_retry_malformed_nontruncated_json \
  myextension/tests/test_assessment_assistant.py::test_generated_tests_recovers_one_length_truncation_without_second_user_action \
  myextension/tests/test_analysis_job_store.py::test_worker_recovers_truncated_response_in_same_attempt
```

Expected: 新的截断恢复测试失败，因为首次长度截断立即变成 `provider_response_invalid`，请求体没有 `max_tokens`，且不会发起第二次调用；malformed JSON 测试保持一次调用并失败关闭。

- [x] **Step 5: 在共享传输层实施最小恢复循环**

在 `myextension/llm_transport.py` 常量区加入：

```python
STRUCTURED_OUTPUT_TOKEN_BUDGETS = (8192, 16384)
```

在 `_provider_payload()` 前加入：

```python
def _response_was_truncated(response: Mapping[str, object]) -> bool:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    first = choices[0]
    return (
        isinstance(first, Mapping)
        and first.get("finish_reason") == "length"
    )
```

将 `chat_json()` 中单次 request/call 部分替换为下面的有界循环；现有 JSON 解析、元数据和 `LlmTransportResult` 返回代码保持在循环之后：

```python
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": json.dumps(
                user_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ]
    raw: Mapping[str, object] | None = None
    for max_tokens in STRUCTURED_OUTPUT_TOKEN_BUDGETS:
        request_body: dict[str, object] = {
            "model": model_name,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if client is not None:
            candidate = client(request_body)
            if not isinstance(candidate, Mapping):
                raise ValueError("LLM client response must be an object.")
            raw = candidate
        else:
            raw = provider_json_client(
                request_body,
                timeout_sec=REQUEST_TIMEOUT_SEC,
            )
        if not _response_was_truncated(raw):
            break
    else:
        raise LlmTransportError("provider_response_truncated")

    if raw is None:
        raise AssertionError("structured provider call produced no response")
```

- [x] **Step 6: 运行 GREEN 定向测试和后端关联回归**

Run:

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_dimension_analyzer.py \
  myextension/tests/test_assessment_assistant.py \
  myextension/tests/test_analysis_job_store.py
```

Expected: 三个文件全部通过；后台 raw wrapper 包含首次截断和恢复成功两条响应；已有网络重试和业务 repair 测试无变化。

- [x] **Step 7: 记录无 Git 检查点**

Run:

```bash
shasum -a 256 \
  myextension/llm_transport.py \
  myextension/tests/test_dimension_analyzer.py \
  myextension/tests/test_assessment_assistant.py \
  myextension/tests/test_analysis_job_store.py
```

Expected: 命令成功输出四个文件的 SHA-256；将输出记录到最终验证文档。当前目录无 Git，不执行 commit。

---

### Task 2: AI 分析失败空状态与零证据控件保护

**Files:**
- Modify: `src/ui/analysisResultView.ts:295-433`
- Modify: `src/__tests__/analysisResultView.spec.ts:345-380`

**Interfaces:**
- Consumes: 现有 `IAnalysisResult.error_code`、`IDimensionResult.decision.status`、`IDimensionResult.ai_result` 和 `data_quality.reason`。
- Produces: 当 `error_code === "ai_analysis_failed"` 且没有 `resolved` 维度和非空 `ai_result` 时的全局空状态；`dimensionCard()` 只在 `claims.length > 0` 时追加 evidence details。
- Preserves: `ai_not_configured` 空状态、ordinary partial 卡片与复核、有效 `not_observed`、`insufficient_evidence`、`not_computable` 和含有效部分结论的 partial。

- [x] **Step 1: 写入 AI 失败无可用结论的 RED 测试**

在 `src/__tests__/analysisResultView.spec.ts` 加入：

```typescript
it('separates completed collection from AI failure without zero-evidence cards', () => {
  const value = result({
    status: 'partial',
    error_code: 'ai_analysis_failed'
  });
  value.dimension_results[0].decision = {
    status: 'partial',
    final_evidence_status: null,
    final_level_code: null,
    display_label: 'synthetic unresolved',
    source: 'llm_evidence'
  };
  value.dimension_results[0].ai_result = null;
  value.dimension_results[0].data_quality = {
    missing_required_signals: [],
    observation_opportunities: 0,
    reason_code: 'minimum_observation_met',
    reason: '已达到最低观察要求'
  };

  const rendered = renderAnalysisResult(value, profile, () => undefined);

  expect(rendered.textContent).toContain('行为采集已完成');
  expect(rendered.textContent).toContain('AI 分析未完成，可重试分析');
  expect(rendered.textContent).toContain('已达到最低观察要求');
  expect(rendered.textContent).not.toContain('部分结果');
  expect(rendered.textContent).not.toContain('查看 0 条证据');
  expect(rendered.querySelector('.jp-BehaviorAudit-resultCard')).toBeNull();
  expect(rendered.querySelector('form')).toBeNull();
});
```

- [x] **Step 2: 加固 ordinary partial 和有效 not-observed 兼容测试**

在现有 `keeps ordinary partial analysis available for teacher review` 测试末尾加入：

```typescript
expect(rendered.textContent).not.toContain('查看 0 条证据');
expect(rendered.querySelector('.jp-BehaviorAudit-evidenceDetails')).toBeNull();
```

再加入：

```typescript
it('keeps a valid not-observed conclusion when another analysis error is present', () => {
  const value = result({
    status: 'partial',
    error_code: 'ai_analysis_failed'
  });
  value.dimension_results[0].decision = {
    status: 'resolved',
    final_evidence_status: 'not_observed',
    final_level_code: null,
    display_label: 'synthetic not observed',
    source: 'llm_evidence'
  };
  value.dimension_results[0].ai_result = {
    confidence: 0.7,
    evidence_claims: [],
    explanation: '达到观察要求但未发现相应行为。'
  };

  const rendered = renderAnalysisResult(value, profile, () => undefined);

  expect(rendered.textContent).toContain('未发现明显证据');
  expect(rendered.querySelector('.jp-BehaviorAudit-resultCard')).not.toBeNull();
  expect(rendered.textContent).not.toContain('AI 分析未完成，可重试分析');
  expect(rendered.textContent).not.toContain('查看 0 条证据');
});
```

- [x] **Step 3: 运行前端 RED 测试并确认失败原因**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm jest \
  src/__tests__/analysisResultView.spec.ts --runInBand --coverage=false
```

Expected: AI 失败测试仍得到“部分结果”和结果卡；ordinary partial 仍包含“查看 0 条证据”。有效 not-observed 兼容测试可以通过。

- [x] **Step 4: 实施失败空状态和非空证据条件渲染**

在 `dimensionCard()` 中将无条件追加替换为：

```typescript
const claims = result.ai_result?.evidence_claims ?? [];
if (claims.length > 0) {
  card.append(evidenceDetails(result.dimension_code, claims));
}
```

在 `renderAnalysisResult()` 设置 `heading` 后、汇总计数前先取得维度并加入：

```typescript
const dimensions = result.dimension_results ?? [];
const hasUsableDimensionResult = dimensions.some(
  value =>
    value.decision.status === 'resolved' ||
    (value.ai_result !== null && value.ai_result !== undefined)
);
if (result.error_code === 'ai_analysis_failed' && !hasUsableDimensionResult) {
  heading.textContent = '本次会话数据';
  const state = element('div', 'jp-BehaviorAudit-resultEmpty');
  state.setAttribute('role', 'status');
  const qualityReasons = [
    ...new Set(
      dimensions
        .map(value => value.data_quality?.reason?.trim())
        .filter((value): value is string => Boolean(value))
    )
  ];
  state.textContent = `行为采集已完成，AI 分析未完成，可重试分析。${
    qualityReasons.length > 0
      ? ` 数据质量：${qualityReasons.join('；')}。`
      : ''
  }`;
  root.append(heading, state);
  return root;
}
```

保留 `ai_not_configured` 分支，并删除其后重复的 `const dimensions` 声明。

- [x] **Step 5: 运行 GREEN 前端定向测试**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm jest \
  src/__tests__/analysisResultView.spec.ts --runInBand --coverage=false
```

Expected: 文件内全部测试通过；失败空状态无卡片/表单/0 证据；ordinary partial 和 not-observed 保持可见。

- [x] **Step 6: 运行前端关联回归与静态检查**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm jest \
  src/__tests__/analysisResultView.spec.ts \
  src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand --coverage=false
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
```

Expected: 两个关联测试文件通过；stylelint、Prettier 和 ESLint 均退出 0。

- [x] **Step 7: 记录无 Git 检查点**

Run:

```bash
shasum -a 256 \
  src/ui/analysisResultView.ts \
  src/__tests__/analysisResultView.spec.ts
```

Expected: 命令成功输出两个文件的 SHA-256；将输出记录到最终验证文档。当前目录无 Git，不执行 commit。

---

### Task 3: 全量验证、wheel 与项目交接更新

**Files:**
- Modify: `docs/superpowers/specs/2026-08-04-structured-ai-truncation-recovery-design.md`
- Create: `docs/2026-08-04-structured-ai-truncation-recovery-verification.md`
- Modify: `项目交接文档.md`
- Regenerate: `lib/**`
- Regenerate: `myextension/labextension/**`
- Regenerate: `dist/myextension-0.2.0-py3-none-any.whl`

**Interfaces:**
- Consumes: Task 1 的后端实现与测试、Task 2 的前端实现与测试、现有构建工具链。
- Produces: 可安装的 0.2.0 wheel、新 SHA-256、完整验证记录和准确的 P0 交接状态。
- Preserves: `dist/myextension-0.1.0-py3-none-any.whl` 回退制品，不安装或删除任何 wheel。

- [x] **Step 1: 运行后端全量回归**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests
```

Expected: 全部通过，0 failed；记录准确通过数和耗时。若沙箱端口权限导致 pytest-jupyter setup error，按 Codex 权限流程只为同一 pytest 命令请求本机端口权限后重跑，不改变测试内容。

- [x] **Step 2: 运行前端全量质量门禁**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:prod
```

Expected: lint 退出 0；Jest 0 failed；生产前端构建退出 0，并刷新 prebuilt labextension。

- [x] **Step 3: 离线重建并校验 wheel**

Run:

```bash
uv build --wheel --offline
.venv/bin/check-wheel-contents dist/myextension-0.2.0-py3-none-any.whl
.venv/bin/python -m zipfile -t dist/myextension-0.2.0-py3-none-any.whl
```

Expected: wheel 构建成功；内容检查输出 `OK`；zip 完整性输出 `Done testing`。

- [x] **Step 4: 校验 wheel 包含本轮后端和前端产物**

Run:

```bash
.venv/bin/python -c 'import zipfile; from pathlib import Path; wheel=Path("dist/myextension-0.2.0-py3-none-any.whl"); z=zipfile.ZipFile(wheel); source=Path("myextension/llm_transport.py").read_bytes(); assert z.read("myextension/llm_transport.py") == source; local_root=Path("myextension/labextension"); prefix="myextension-0.2.0.data/data/share/jupyter/labextensions/myextension/"; local_files=[path for path in local_root.rglob("*") if path.is_file()]; assert local_files; assert all(z.read(prefix + path.relative_to(local_root).as_posix()) == path.read_bytes() for path in local_files); print("WHEEL_BACKEND_AND_FRONTEND_MATCH")'
shasum -a 256 dist/myextension-0.2.0-py3-none-any.whl
```

Expected: 输出 `WHEEL_BACKEND_AND_FRONTEND_MATCH` 和新的 64 位 wheel SHA-256；新哈希不得仍是修复前的 `01e754cad2eeeb30f60acc29c7300571b8cdf68c85598c14305a8e2afa64c085`。

- [x] **Step 5: 写入验证记录和更新设计状态**

创建 `docs/2026-08-04-structured-ai-truncation-recovery-verification.md`，不先写模板值，只在对应命令实际执行后写入。文档必须包含以下已定义的标题和内容：

- 标题为“结构化 AI 截断恢复与失败态验证记录”，日期为 `2026-08-04`。
- “实现结果”记录 8192/16384 有界恢复、一次教师操作恢复、同一 attempt 的两条私有响应，以及 AI 失败空状态。
- “TDD 证据”以表格记录后端 RED、后端 GREEN、前端 RED、前端 GREEN；每行复制本计划实际执行的完整命令和观察到的精确通过或失败摘要。
- “全量验证”以表格记录后端 pytest、前端 lint、前端 Jest、生产构建、wheel 内容、wheel 完整性、wheel 源码/前端存在性；每行写完整命令、退出状态、通过数和耗时（命令有输出时）。
- “交付产物”写入 wheel 的绝对路径、Step 4 输出的完整 64 位 SHA-256，并记录 `dist/myextension-0.1.0-py3-none-any.whl` 仍保留。
- “未执行项与停止点”明确记录：未调用真实或付费 AI；未重启当前 JupyterLab 预览；未安装 wheel；未执行真实浏览器端到端 AI 流程、Windows 真机验收、推送或部署。
- 任何失败或未验证项保留原样并使用准确状态，不写推测值，不留空表格单元格或占位符。

将设计文件状态更新为“实现完成，验证结果见验证记录”。

- [x] **Step 6: 更新项目交接文档的真实交付状态**

在 `项目交接文档.md` 中同步：

- 顶部交付状态：两项 P0 已在源码和新 0.2.0 wheel 中修复，但真实付费 AI、本地预览安装回归和 Windows 真机仍待验收。
- 第 2 节当前 wheel SHA-256：替换为 Step 4 的实际新哈希。
- 第 9 节验证表：加入本轮实际后端、前端、构建和 wheel 结果。
- P0-1 状态：实现完成，列出 8192/16384 一次恢复和合成测试证据。
- P0-2 状态：实现完成，列出失败空状态和无 0 证据控件测试证据。
- 保留 Windows 真机、生产部署、真实数据与安全边界说明。

- [x] **Step 7: 最终新鲜验证与制品哈希复核**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand
.venv/bin/check-wheel-contents dist/myextension-0.2.0-py3-none-any.whl
.venv/bin/python -m zipfile -t dist/myextension-0.2.0-py3-none-any.whl
shasum -a 256 dist/myextension-0.2.0-py3-none-any.whl
```

Expected: 所有命令基于最终文件重新执行并通过；最终哈希与验证记录、交接文档完全一致。

- [x] **Step 8: 停止并交接**

不要重启 8899 预览、安装 wheel、调用真实 AI、运行 Windows 真机、推送或部署。报告修改文件、RED/GREEN 证据、全量命令、wheel 路径与哈希、未覆盖项，并等待用户授权下一阶段。
