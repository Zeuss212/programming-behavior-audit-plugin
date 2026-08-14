# VSCode 学生端一键结束、AI 分析与导出设计

日期：2026-08-14
范围：`vscode-extension` 的学生端本地会话结束流程

## 1. 本轮目标与边界

学生在周末演示中可执行一次明确的主操作：结束当前行为监控、生成本地课堂简报、按默认选项生成 AI 建议，并选择目录导出完整会话包。

本轮只修改 VSCode 插件。不会修改课堂平台前端、BAMS/Jupyter、行为采集字段、已有会话数据或远端服务；不会上传、自动发送或删除任何会话资料。

## 2. 当前事实与问题

- `behaviorAudit.finishCapture` 只会结束会话并生成本地课堂简报。
- `behaviorAudit.analyzeSession` 与 `behaviorAudit.exportSession` 是两个独立命令。
- 导出器仅在已存在 `ai_analysis` artifact 时才额外导出 `ai_analysis.json`。

因此当前功能虽能分别完成简报、AI 分析和导出，但不能可靠演示为一次完整流程，且导出结果不能保证带有 AI 状态说明。

## 3. 方案选择

采用“一键结束并导出”方案：新增学生端主入口，默认勾选“同时生成 AI 建议”，保留现有三个独立命令作为故障恢复入口。

不采用保留三步操作的方案，因为课堂演示中容易遗漏 AI 分析；不采用后端生成，因为当前没有相应契约，且超出周末演示范围。

## 4. 用户流程

1. 学生开始本地行为监控。
2. 学生点击“结束监控并导出”。
3. 插件要求确认结束；确认后将会话转换为 `completed`，再生成 `operation_log.json`、`process_log.md` 与 `classroom_brief.json`。
4. 默认启用 AI：插件基于本地课堂简报执行 AI 分析，并把结果或安全的跳过/失败说明写为同一份分析 artifact。
5. 插件请求选择导出目录；取消选择不删除已结束会话或已生成 artifact，可继续使用“导出上次会话”。
6. 插件导出完整目录，并显示“已导出（AI 已完成 / 已跳过 / AI 失败但本地简报已导出）”的明确结果。

中断会话继续使用“继续中断会话”，不会被该流程自动结束或自动放弃。工作台停止、释放和 Jupyter 服务不在本轮流程中。

## 5. 导出契约

导出目录名继续使用会话 ID，包含：

- `operation_log.json`
- `process_log.md`
- `classroom_brief.json`
- `analysis_log.json`
- `plan_snapshot.json`
- `manifest.json`

`analysis_log.json` 始终存在，并只会处于以下一种安全状态：

- `completed`：包含 AI 结论、建议与其允许导出的依据；
- `skipped`：未配置 AI 或学生取消 AI；
- `failed`：AI 调用失败或超时，但只记录经过脱敏的原因和重试提示。

`manifest.json` 必须包含 `analysis_log.json` 的字节数与 SHA-256。旧的 `ai_analysis` 本地 artifact 保持兼容读取，但导出文件名统一为 `analysis_log.json`。

## 6. 模块与数据流

- 命令/侧边栏层：新增一键命令与默认勾选的 AI 选项；展示分阶段结果，不直接拼接导出内容。
- 工作流层：顺序协调结束会话、本地简报、可选 AI、选择目录与导出，返回结构化结果，避免让 UI 层处理业务异常。
- AI 适配层：复用 `CompatibleAiClient.analyzeSession`；将未配置与异常归一成无敏感信息的 `skipped` 或 `failed` artifact。
- 简报与导出层：保留现有 `FileReportService` 和 `FileSessionExporter`；导出器固定写出并在 manifest 校验 `analysis_log.json`。
- 存储层：复用 `ai_analysis` artifact kind，不迁移历史文件。

## 7. 错误处理与安全

- 结束会话或生成本地简报失败：停止流程，不显示导出成功。
- AI 未配置、网络异常、超时或模型错误：写入安全状态后继续导出。
- 用户取消目录选择：返回“已生成，尚未导出”，保留本地数据。
- 同名且非空的导出目录：拒绝覆盖并提示改选目录。
- UI、artifact 与日志不得写入 API Key、Authorization 请求头、完整模型响应或绝对工作区路径。

## 8. 验收与测试

1. 有活动会话时，一次主操作使会话完成、生成简报并导出。
2. AI 成功、未配置和失败三种情况下，都导出 `analysis_log.json`，且 manifest 哈希一致。
3. AI 失败不会阻断本地简报和导出。
4. 取消目录选择后可用既有导出命令再次导出。
5. 原有结束、分析和导出命令持续可用。
6. 单元测试覆盖工作流结果、导出命名、AI 失败脱敏与目录取消；再运行插件单测、lint、类型检查、构建和 VSCode 集成冒烟测试。

## 9. 回滚

不迁移数据。回滚只需隐藏一键入口并恢复原导出文件名映射；已有 `ai_analysis` artifact 和原始独立命令保持可用。
