# API_CONTRACTS · 课堂同步服务契约

## 网关与鉴权

- 对外路径统一为 `/classroom-api/v1/classroom/*`；服务内部路由为 `/v1/classroom/*`。
- 课堂 API 使用 Bearer Token；代理必须透传 Authorization，日志不得记录 token、ticket、请求体或学生源码。
- `GET /health/ready` 仅用于服务就绪检查，不替代业务鉴权与权限校验。

## 现有关键资源

- 课堂草稿：`/v1/classroom/plans/drafts`。
- 已发布实验方案查询：`/v1/classroom/plans/experiments/{spaceId}/{parentAlgorithmId}`。
- 发布与任务同步：发布草稿后生成版本，再同步学生任务。
- 旧知识点评分策略：`/v1/classroom/plans/drafts/{draftId}/evaluation-policy`；其 items 使用 `knowledge_point_id` 与 `weight_bps`，不得改变既有语义。

## 独立评价配置待对接契约

- 新页面需要独立于知识点的评价维度，以及五项行为监测范围（含 `paste_behavior`）。
- 推荐新增 `assessment-config` 资源，不在旧 `evaluation-policy` 中塞入维度名称、说明或学生可见性。
- 保存配置必须使用草稿 revision / config revision 乐观锁；总权重使用 `weight_bps`，总和严格为 `10000`。
- 变更接口必须同步更新 OpenAPI/schema、前端领域映射、契约测试和本文件。
