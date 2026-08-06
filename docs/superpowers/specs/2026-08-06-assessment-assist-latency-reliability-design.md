# 0.2.1 测试建议生成延迟与可靠性修复设计

日期：2026-08-06  
适用分支：`fix/ui-hotfix-0.2.1`

## 1. 目标与验收边界

本轮修复教师创建方案时“生成测试建议”连续等待约 60 秒后失败的问题，并让失败原因可操作、可区分。版本仍保持 `0.2.1`，通过 Git 提交、wheel SHA-256 和新的交付 ZIP 区分。

验收标准：

1. 知识点和测试建议请求明确关闭深度思考、要求 JSON 对象，并使用专用的 `2048 → 4096` 输出预算；
2. 五个知识点的等规模合成测试建议在一次 Provider 请求内返回有效闭合结构；
3. 会话结束后的完整行为分析继续使用现有分析预算和重试逻辑，不被本修复降级；
4. 超时、网络、鉴权、限额、请求拒绝、服务不可用、截断和无效 JSON 在后端使用稳定安全码，在前端显示对应处理建议；
5. Provider 失败时保留教师草稿并允许手工继续，不伪造或冒充 AI 结果；
6. 前后端全量测试、静态检查、生产构建、wheel 结构、隔离安装和真实合成冒烟验证全部通过后才重建交付 ZIP。

## 2. 非目标与禁止操作

- 不更换当前 `glm-5-2-260617` 模型；真实最小请求已证明该模型、Key 和接口可用；
- 不修改真实题目、知识点、学生数据或现有草稿；
- 不生成未标注的本地模板来冒充 AI 建议；
- 不改变会话分析的证据提取、分析深度、任务状态机或 120 秒总预算；
- 不推送 Git、构建或推送 Docker 镜像、注册 BLUEDOT 工作台；
- 不在日志、测试输出、文档或提交中记录 API Key、Provider 响应正文或真实草稿内容。

## 3. 已验证根因

### 3.1 现场证据

- `POST /myextension/assessment-assist/tests` 连续三次在约 `60.05–60.62` 秒后返回 `502`；
- 不带 Key 的同一 Ark 路由 HEAD 检查在 `0.062` 秒返回 `401`，说明 DNS、TLS、代理路径和服务路由可达；
- 当前模型的 64-token 合成 JSON 请求在 `1.567` 秒内成功，排除 Key、额度、模型名和基础鉴权故障；
- 五个知识点的等规模合成测试请求即使将可见输出上限降为 `2048`，仍在约 `28.984` 秒以长度截断结束；
- 对完全相同的合成输入增加 `thinking: {"type": "disabled"}` 和 `response_format: {"type": "json_object"}` 后，在 `7.894` 秒内一次成功并返回 5 条有效测试。

以上真实诊断只使用合成题目和合成知识点，没有发送真实草稿或学生数据。

### 3.2 代码原因

`assessment_assistant._payload_from_chat()` 直接复用 `llm_transport.chat_json()` 的通用默认值。该默认值为完整分析准备，输出预算为 `8192 → 16384`，请求没有关闭 Ark 默认深度思考，也没有声明 JSON 响应模式。

测试建议比最小问答需要更多结构字段；深度思考 token 与可见内容共享生成预算。非流式调用会等待整次生成完成，因此首个 `8192` 请求触发固定 60 秒 Provider 超时。路由随后把所有 `LlmTransportError` 压缩为 `test_generation_failed`，前端又把除“未配置”以外的错误统一显示为“AI 暂时不可用”，使配置人员无法区分原因。

## 4. 方案比较与选择

### 方案 A：更换模型

不能解释当前模型的 1.567 秒成功请求，也不能修复通用预算和错误透明度问题。模型切换还会改变输出质量和成本，不采用。

### 方案 B：Provider 失败后自动填充本地模板

可让演示继续，但容易把本地规则结果误认为 AI 建议，并掩盖真实配置问题，不采用。

### 方案 C：建议链路专用参数与安全错误码

只调整作者辅助请求，关闭不必要的深度思考、声明 JSON 模式、缩小输出预算，并保留一次截断恢复；同时把 Provider 错误安全映射到可操作提示。真实合成对照已证明该方向把同规模请求从失败降到 7.894 秒成功，因此采用。

## 5. 技术设计

### 5.1 传输层接口

扩展 `chat_json()` 的内部关键字参数，使调用方可以显式选择：

- `token_budgets`：默认仍为 `(8192, 16384)`；
- `thinking_mode`：默认 `None`，保持现有请求不变；作者辅助传入 `"disabled"`；
- `json_mode`：默认 `False`；作者辅助传入 `True`。

参数只接受闭合类型和值。`thinking_mode` 仅允许 `enabled`、`disabled`、`auto` 或 `None`；token 预算必须是非空正整数序列。开启时生成：

```json
{
  "thinking": {"type": "disabled"},
  "response_format": {"type": "json_object"}
}
```

默认值不改变 `dimension_analyzer`、`llm_labeler` 和 `AnalysisWorker` 的请求体。自定义测试 client 仍接收最终闭合请求体，便于直接断言参数。

### 5.2 作者辅助层

新增常量：

```text
ASSESSMENT_ASSIST_TOKEN_BUDGETS = (2048, 4096)
```

知识点建议和测试建议共同通过 `_payload_from_chat()` 使用：

```text
token_budgets=(2048, 4096)
thinking_mode=disabled
json_mode=true
```

第一次没有发生长度截断时只调用一次；只有 `finish_reason=length` 才使用 4096 进行一次恢复。输出仍经过现有闭合字段、中文、知识点引用、测试类型、覆盖范围和 JSON Schema 校验。

### 5.3 后端错误契约

`AssessmentAssistRouteHandler` 将 `LlmTransportError` 映射为以下安全码，响应不包含 Provider 正文、Key、题目或路径：

| Provider 状态 | API code | HTTP |
| --- | --- | --- |
| `provider_timeout` | `ai_provider_timeout` | 502 |
| `provider_network_error` | `ai_provider_network_error` | 502 |
| HTTP 401/403 | `ai_provider_auth_failed` | 502 |
| HTTP 429 | `ai_provider_rate_limited` | 502 |
| 其他 HTTP 4xx | `ai_provider_request_rejected` | 502 |
| HTTP 5xx | `ai_provider_unavailable` | 502 |
| `provider_response_truncated` | `ai_response_truncated` | 502 |
| `provider_response_invalid` | `ai_response_invalid` | 502 |
| 其他安全传输错误 | 原知识点/测试失败码 | 502 |

所有外部 Provider 错误保持 `retryable: true`；输入校验和 AI 未配置仍维持原状态码与契约。

### 5.4 前端错误提示

`GuidedProfileEditor.assistFailureMessage()` 按安全码和当前步骤给出短提示：

- 超时：说明建议生成超时，草稿已保留，可重试或手工继续；
- 网络：检查网络、DNS、TLS 或代理；
- 鉴权：检查 API Key 和模型权限；
- 限额：稍后重试并检查额度/并发；
- 请求拒绝：检查 Base URL、模型与 Provider 参数兼容性；
- 服务不可用：稍后重试；
- 截断：减少知识点数量或描述长度后重试；
- JSON 无效：检查模型的结构化 JSON 能力；
- 未知错误：保留现有手工继续提示。

界面不显示 Provider 原始响应或内部异常文本。

## 6. 数据流与状态

```text
教师点击生成建议
  → 前端发送题目上下文和已确认知识点
  → 后端 Schema 校验
  → 作者辅助层构造 2048-token、关闭思考、JSON 模式请求
  → Provider
      ├─ 正常结束：闭合输出校验 → 返回候选 → 合并且保留教师编辑
      ├─ 长度截断：只再请求一次 4096-token → 校验
      └─ 安全错误：稳定错误码 → 对应前端提示 → 草稿不变、允许手工继续
```

请求仍是无状态作者辅助，不保存 Provider 原始响应。草稿持久化继续由现有 autosave 负责。

## 7. 测试策略

严格按 RED → GREEN：

1. `test_assessment_assistant.py`
   - 先断言建议请求应包含关闭思考、JSON 模式和 2048 首预算；旧代码应失败；
   - 断言长度截断只恢复一次且第二预算为 4096；
   - 断言普通会话 `chat_json()` 默认仍使用 8192，不携带作者辅助参数。
2. `test_assessment_assist_api.py`
   - 参数化模拟 timeout、network、401、429、400、503、truncated、invalid；
   - 断言 HTTP、稳定错误码、`retryable`，以及响应不泄露合成私密标记。
3. `assessmentPlanEditor.spec.ts`
   - 参数化断言测试建议与知识点建议的可操作错误文案；
   - 断言失败后教师已编辑内容仍在、手工操作仍可用。
4. 全量验证
   - 前端 Jest；
   - 后端 pytest；
   - stylelint、Prettier、ESLint；
   - TypeScript 与 JupyterLab prebuilt 生产构建；
   - wheel 制品、发布脚本、ZIP 与 SHA-256。

## 8. 真实合成验收

使用用户已授权的当前 Provider 配置，只发送不含真实题目和学生数据的合成内容：195 字符合成题目、5 个合成知识点，输出预算 2048，关闭深度思考并启用 JSON 模式。

停止条件：最多一次正常请求；若失败只记录安全码，不发送 Provider 正文，不连续重试。验收结果应为一次请求成功、5 条闭合测试、耗时明显低于 60 秒。

## 9. 交付、兼容与回滚

- 版本号保持 `0.2.1`，重建两份逐字节一致的 wheel；
- 更新 `runtime.env.example`、README、MANIFEST、校验和与验证报告；
- 从新 wheel 进行隔离安装，在干净数据目录启动新预览并核对实际 `remoteEntry`；
- 新建带日期和修复标识的 ZIP，不覆盖旧交付文件；
- 提交保持纵向小闭环，创建本地交付标签；不推送、不部署；
- 回滚通过切回本轮前 Git 提交或重新安装旧 SHA-256 wheel 完成，数据目录不删除。

