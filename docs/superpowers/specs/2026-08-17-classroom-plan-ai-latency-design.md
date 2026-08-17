# 课堂方案 AI 建议延迟修复设计

**日期：** 2026-08-17  
**状态：** 已确认，待实现  
**范围：** 本地课堂演示；不合并 `main`、不推送、不部署。

## 目标

使教师在课堂方案页点击“AI 生成建议”时，复用项目内已验证的 GLM Coding Plan 请求方式，避免默认深度思考造成的长时间等待和浏览器中止请求。成功时仍只返回教师可编辑的方案草稿。

## 根因和依据

- 本地课堂环境当前配置为 `https://ark.cn-beijing.volces.com/api/plan/v3`，与 Jupyter 已验证的 `https://ark.cn-beijing.volces.com/api/coding/v3` 不一致。
- `OpenAiPlanSuggestionService` 只发送通用 Chat Completions 字段，未发送 Jupyter 已验证的 `thinking: {"type": "disabled"}` 与 `response_format: {"type": "json_object"}`。
- 项目已有真实合成对照表明：相同规模请求在关闭思考并要求 JSON 后，从约 29 秒的截断响应降为约 8 秒的正常响应。
- 前端先前的 20 秒请求上限导致 Nginx 记录 499；将其提高到 45 秒只用于避免页面过早中止，不替代服务端加速。

## 方案

仅对教师主动触发的“课堂方案建议”使用专用参数：

```json
{
  "max_tokens": 2048,
  "thinking": {"type": "disabled"},
  "response_format": {"type": "json_object"}
}
```

共享 `OpenAiCompletionClient` 增加两个显式可选参数，以便方案建议能携带上述字段，而异步学生简报分析继续保持原有请求体和预算。方案建议若 Provider 以 `finish_reason=length` 返回，只使用 4096 token 再请求一次；其他失败不重试、不展示原始 Provider 内容。

本地被忽略的 `deploy/classroom/local-demo/.env.ai` 只将非密钥 `CLASSROOM_AI_BASE_URL` 改为已验证的 `https://ark.cn-beijing.volces.com/api/coding/v3`，模型和 Key 保持不变。通过 Compose 重建 `sync-api`、`deadline-worker` 与代理加载新配置。

## 边界和回滚

- 不记录或显示 API Key、完整提示词、Provider 正文或真实课堂数据。
- 不修改学生提交路径、AI 简报分析、教师发布权限或数据库结构。
- 不自动发布课堂；AI 结果始终需要教师应用和保存。
- 代码回滚为本分支修复前提交；本地配置回滚只需把 `CLASSROOM_AI_BASE_URL` 改回此前值并重建容器。

## 验收

1. 单元测试证明课堂方案建议请求带 `thinking`、`response_format` 和 2048 预算；简报分析请求不带这些专用字段。
2. 单元测试证明仅 `finish_reason=length` 触发一次 4096 恢复请求。
3. 后端全量测试、Ruff、Mypy 通过；前端 API 测试、类型检查和构建通过。
4. 本地容器配置只确认变量存在，不显示密钥；教师使用合成方案输入完成一次真实建议生成。记录耗时和安全结果码，停止于一次请求。
