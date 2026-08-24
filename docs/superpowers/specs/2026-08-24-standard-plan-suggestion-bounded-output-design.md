# 标准端点课堂方案建议闭合输出设计

## 背景与现场证据

本地正式课堂服务已加载标准端点的 `1200` 输出预算和单次调用限制。真实合成验收中，Provider 在 `26.409` 秒后返回 `finish_reason=length`，响应正文未形成闭合 JSON，安全诊断在 `json.loads` 阶段得到 `JSONDecodeError`。同一现场的 DNS、TLS、配置注入和服务健康检查均通过。

当前统一提示要求模型除标题和知识点外，还可生成结构复杂的 `automatic_evaluation`。标准端点不启用 JSON 模式，且不能通过增加预算解决：此前等价的 `2048` 请求发生传输层失败。因此根因是标准端点承担了超过其稳定闭合能力的扩展输出契约，而不是网络、密钥、字段校验或服务健康问题。

## 目标

标准 Provider 端点应在一次请求、`1200` 输出预算内稳定生成可编辑的核心课堂方案 JSON。结果只要求：

- 非空 `title`；
- 1–10 个 `knowledge_points`；
- 每个知识点包含非空、受长度限制的 `name` 和 `description`。

Coding Plan 端点继续支持现有的 JSON 模式、关闭思考、可选 `automatic_evaluation` 和长度恢复能力。

## 非目标

- 不修改 Provider 密钥、Base URL 或模型名。
- 不重置课堂数据、PostgreSQL 或 MinIO 数据卷。
- 不放宽现有字段长度、敏感文本和绝对路径过滤。
- 不在标准端点增加自动重试、响应修复调用或第二次 Provider 请求。
- 不改变教师必须确认和编辑 AI 草稿后才能发布的产品边界。

## 方案选择

采用按端点拆分提示词的方案。

不采用所有端点统一精简，因为这会无证据地削弱 Coding Plan 的自动评估能力。不采用扩大标准端点预算或自动重试，因为现场已观察到 `2048` 传输失败，且重试会增加延迟和配额消耗。

## 组件与接口

`OpenAiPlanSuggestionService` 继续以 `_uses_coding_plan_profile` 作为唯一端点能力判定。消息构造边界改为显式接收是否包含自动评估说明：

- 标准端点调用核心提示，只描述 `title`、`knowledge_points[].name` 和 `knowledge_points[].description`；
- Coding Plan 端点调用扩展提示，在核心提示上增加 `automatic_evaluation` 的闭合枚举与约束。

Provider 响应仍由同一安全解析器处理。标准端点即使意外返回 `automatic_evaluation`，也只能在通过现有本地白名单校验后进入瞬时建议对象；提示词本身不再诱导生成该字段。

不新增配置项、数据库字段、API 路径或响应字段。

## 数据流

### 标准端点

1. 教师输入经现有 `PlanSuggestionInput` 长度校验。
2. 服务构造核心提示。
3. 单次 Provider 请求使用 `temperature=0.2`、`max_tokens=1200`，不发送 `thinking` 或 `response_format`。
4. 无论 `finish_reason` 为何，标准端点不发起第二次请求。
5. 本地解析并验证闭合 JSON；成功后返回可编辑草稿，失败后返回安全错误。

### Coding Plan 端点

1. 教师输入经相同校验。
2. 服务构造包含自动评估约束的扩展提示。
3. 首次请求使用现有 `2048`、关闭思考和 JSON 模式。
4. 仅 `finish_reason=length` 时保留现有一次 `4096` 长度恢复。
5. 继续使用相同的本地安全解析和过滤。

## 错误与安全

- 标准端点传输失败继续映射为 `ai_suggestion_upstream_unavailable`。
- 非闭合 JSON 或字段契约失败继续映射为不可重试的 `ai_suggestion_response_invalid`。
- 不记录 Provider 正文、Authorization、API Key、真实课程内容或学生数据。
- 真实验收只发送明确标记的合成课堂题目。
- 每个真实验收周期最多一条 Provider 请求；失败后先分析安全元数据，再决定下一轮代码变更。

## 测试策略

先按 TDD 新增失败测试：

1. 标准端点请求正文不包含 `automatic_evaluation` 及其枚举说明。
2. 标准端点仍使用 `1200`，且 `finish_reason=length` 时调用数保持 1。
3. Coding Plan 请求仍包含自动评估说明、JSON 模式和关闭思考参数。
4. Coding Plan 长度恢复测试仍为 `2048 → 4096` 两次调用。

实现后运行方案建议单元测试、方案建议路由集成测试、Ruff、mypy 和 `git diff --check`。

## 部署、验收与回滚

只重建并重新创建本地 `sync-api` 与 `deadline-worker`，使用既有私有 `.env.ai` 注入配置，不重置或删除数据卷。

真实合成验收必须同时满足：

- `sync-api` 为 `running healthy`；
- 旧兼容接口 `AiSuggestionSettings` 可用；
- 标准端点实际请求数为 1；
- 响应为闭合 JSON，并通过本地方案契约；
- 返回 1–10 个知识点；
- 不保存或显示 Provider 正文、密钥或真实课堂数据。

部署前记录当前镜像 ID 作为回滚点。若新镜像启动失败，使用该镜像重新创建两个服务；若 Provider 验收失败但服务健康，保留教师手动填写路径，停止连续请求并根据安全分类进入下一轮最小修复。

## 完成停止点

只有离线质量门禁和一次真实合成验收全部通过后，才能宣称本轮修复完成。不得自动提交、合并、推送或发布其余已有 worktree 修改。
