# 课堂评价配置持久化与开发环境对齐设计

设计日期：2026-09-02

## 1. 目标与已确认故障

本轮恢复教师端独立评价维度页面，并让评价配置成为课堂同步服务中的持久化资源。与此同时，修复 5174 开发模式把远程 FinColab 与本地 demo 课堂服务混用造成的两类错误：

- 学生首页通过远程 `/v1/organizations/spaces` 读取项目，但当前账号或强制 space 不可访问时，首页普通数据整体失败并显示“无权访问平台”。
- 教师评价页把远程 FinColab bearer token 发给 `127.0.0.1:18080`；该容器却通过本地 demo FinColab 校验身份，因此课堂方案读取必然失败。

`.env.local-demo` 已从 Git 跟踪中移除，并不是当前 5174 进程的配置来源。当前问题来自 `.env.development` 本身组合了两个不一致的身份域。

## 2. 本轮范围、非目标与停止点

### 范围

- 后端新增草稿级 `assessment-config` 资源、数据库迁移、权限校验、乐观锁、稳定错误码和发布快照。
- 新发布的课堂版本把评价配置纳入不可变内容和内容哈希；历史版本继续按旧 schema 读取。
- 前端恢复五项行为监测、独立 AI 评价维度、增删改、学生可见、万分比权重、服务端保存快照和本地恢复。
- 现有课堂草稿、发布、任务同步和工作台流程继续复用。
- 学生首页把普通平台数据和可选课堂扩展分开处理；课堂服务失败不清空普通实验数据。
- 明确 development 与 local-demo 两种完整拓扑，禁止在一个 Vite 模式中混合远程和本地身份域。

### 非目标

- 不把知识点评分 `evaluation-policy` 改造成新资源；它只保留兼容用途。
- 不在本轮调用付费 AI。基础“AI 默认设置”只应用固定候选维度，不自动保存。
- 不伪造历史实验缺失的开放时间、参与范围或实验材料。
- 不修改真实数据库、部署线上服务、推送分支或清理其他 worktree。

### 停止点

代码、迁移、契约和测试在隔离分支完成；本地一致环境的 API/页面冒烟通过后停止，不推送、不部署。

## 3. 方案比较与选择

### 方案 A：独立评价配置表 + 发布快照（采用）

草稿评价配置存入 `assessment_configs`，以 `draft_id` 一对一关联课堂草稿。PUT 同时校验课堂草稿修订号和配置修订号。发布时在同一数据库事务中读取并锁定配置，将规范化配置写入 `PlanVersion.assessment_config`，并把它包含在 schema v2 的内容哈希中。

该方案保持知识点分析模型和评分维度解耦，也保证刷新可恢复、多窗口不会静默覆盖、学生任务引用的版本内容不可变。

### 方案 B：继续把权重写入 `evaluation-policy`（拒绝）

现有资源只表达知识点权重，强行扩展会把评分维度重新绑定知识点，并破坏旧客户端兼容语义。

### 方案 C：评价配置只保存在前端或塞入 profile（拒绝）

前端存储不能跨刷新和跨终端恢复；profile v2/v3 的 `dimensions` 是知识点证据分析结构，不是本页的独立评分维度。混用会污染插件分析契约。

## 4. 数据模型

### 4.1 草稿配置

新增 `assessment_configs`：

- `draft_id`：主键、外键指向 `plan_drafts.id`，删除受限。
- `schema_version`：当前固定为 1。
- `config_revision`：从 0 开始，每次成功 PUT 增加 1。
- `monitoring_scopes`：JSON，只允许五个固定布尔字段。
- `evaluation_dimensions`：JSON，保存规范化维度数组。
- `created_at`、`updated_at`。

配置的权限和生命周期从所属 `PlanDraft` 派生，不重复保存 teacher、space 或 experiment 标识。

### 4.2 评价维度

每个维度包含：

- `id`：1–64 字符，只允许稳定的字母、数字、下划线和连字符；同一配置内唯一。
- `name`：去除首尾空白后 1–50 字符。
- `description`：去除首尾空白后最多 500 字符。
- `weight_bps`：1–10000 的整数；合计必须恰好 10000。
- `student_visible`：布尔值。
- `order`：从 1 开始的唯一正整数；响应按其升序规范化。

维度数量为 1–10。维度不得出现 `knowledge_point_id` 或未声明字段。

### 4.3 行为监测范围

固定五项均必须显式存在：

- `coding_process`
- `revision_process`
- `run_and_debug`
- `thinking_and_pause`
- `paste_behavior`

值必须为布尔值，不接受额外字段。

## 5. API 契约

保留：

```text
GET/PUT /v1/classroom/plans/drafts/{draftId}/evaluation-policy
```

新增：

```text
GET /v1/classroom/plans/drafts/{draftId}/assessment-config
PUT /v1/classroom/plans/drafts/{draftId}/assessment-config
```

本轮不新增付费或异步建议接口；前端固定默认候选不需要服务端调用。

### GET

GET 对合法、可编辑且尚未配置的草稿返回服务端默认配置并在事务中持久化，避免“读到一个默认值但刷新后来源不确定”。响应包括：

- `draft_id`
- `draft_revision`
- `config_revision`
- `schema_version`
- `monitoring_scopes`
- `evaluation_dimensions`
- `total_bps`

### PUT

请求必须包含 `expected_draft_revision`、`expected_config_revision`、`monitoring_scopes` 和 `evaluation_dimensions`。只使用这两个显式预期版本做并发控制；不接受可混淆的可选修订字段。

成功后返回完整最新资源。冲突返回 409 和 `assessment_config_stale`，details 可包含当前修订号，但不得包含敏感数据。

稳定错误码：

- `assessment_config_invalid`
- `assessment_config_stale`
- `assessment_dimension_duplicate`
- `assessment_weight_total_invalid`
- `assessment_draft_not_editable`
- `plan_draft_not_found`

教师身份先由 bearer token 解析，再检查草稿 owner，最后用 FinColab 重新验证所属实验 owner。所有响应继续使用现有统一错误 envelope。

## 6. 发布一致性与版本兼容

### 6.1 发布事务

`PlanService.publish_draft` 在锁定 `PlanDraft` 后读取并锁定对应 `AssessmentConfig`。若新评价页面创建的草稿缺少配置，发布返回 `assessment_config_missing`；旧调用方创建且没有配置的草稿仍按 schema v1 发布，保持兼容。

前端新流程在发布前 PUT 配置，因此新版本产生 schema v2 内容：

```json
{
  "schema_version": 2,
  "plan_id": "...",
  "version": 2,
  "profile": {},
  "assessment_config": {},
  "scheduled_start_at": "...",
  "scheduled_end_at": "...",
  "ai_policy": "prohibited",
  "published_at": "...",
  "content_hash": "..."
}
```

`assessment_config` 在计算 `content_hash` 前进入规范化内容。`PlanVersion` 新增可空 JSON 列和 `content_schema_version`：迁移把历史行设为 1；新评价版本设为 2。历史哈希不重算。

### 6.2 幂等性

现有 `(source_draft_id, source_draft_revision)` 发布幂等键继续生效。PUT 配置会同步增加 draft revision，因此已保存的新配置对应一个新的可发布源修订。重复发布同一修订返回同一 plan version。

## 7. 页面和状态流

进入“考核评价管理”时：

1. 读取当前已发布课堂版本。
2. 基于当前版本恢复一个可编辑新草稿；不得让前端重填或猜测时间、范围和材料。
3. GET `assessment-config`，以响应初始化表单和“上次保存快照”。
4. 页面展示一个完整评价配置卡：教学目标与知识点、五项行为监测、独立评价维度、底部操作。
5. “AI 默认设置”只替换当前未保存表单；教师仍需点击保存。
6. “恢复上次配置”只恢复最近一次 GET/PUT 成功后的服务端快照。
7. “保存评价方案”先 PUT 配置，再发布新课堂版本，最后同步学生任务。

权重不是 100%、名称为空、重复 id/order 或维度超限时，按钮禁用并显示可读错误。409 提示“配置已在其他窗口修改，请刷新后重试”，不自动重放写请求。

## 8. 历史实验与初始化边界

评价页只能从已有课堂版本恢复开放时间、profile 和参与范围。对于早期因课堂服务身份错配而从未成功发布课堂版本的远程实验，服务端没有这些数据；页面必须显示“该实验尚无可恢复的课堂版本，请返回实验管理重新发布课堂配置”，而不是使用当前时间或默认 30 天。

创建实验流程继续在实验创建成功后立即发布初始课堂版本。修复后的环境必须保证该调用和当前 FinColab 属于同一身份域。这样后续评价页总能从不可变版本恢复草稿。

## 9. 环境拓扑

### 9.1 `local-demo`

完整本地身份域：

```text
Vue local-demo -> demo-fincolab:18082
Vue /classroom-api -> local demo nginx/sync
sync identity -> demo-fincolab
workbench -> 127.0.0.1:8888
```

只由显式 local-demo 启动脚本加载 `.env.local-demo`。

### 9.2 `development`

完整远程开发身份域：

```text
Vue development -> remote FinColab 40002
Vue /classroom-api -> 与远程 FinColab 配套的课堂同步服务
sync identity -> 同一 remote FinColab 和 organization
workbench -> BAMS HTTPS 40037
```

`VITE_CLASSROOM_PROXY_TARGET` 必须指向上述配套服务。若该服务未提供，课堂功能应关闭或显示独立不可用提示，普通平台流程仍可使用；禁止退回 local-demo 18080。

仓库只提交非敏感 example 和启动校验，不提交 bearer token、密钥或个人 `.env.*.local`。

## 10. 学生首页故障隔离

普通数据 `getStudentProjects`/实验记录是首页主内容；课堂任务是可选增强。两者分别加载和分别显示错误。课堂服务 401/403/503 不得把课程、实验和工作台统计清零。

如果远程 FinColab 本身返回“无权访问平台”，页面应保留精确可操作提示；代码不得把强制默认 space 当成账号已获授权。`resolveStudentSpaceContext` 只在 API 返回的可访问 space 中选择默认值。

## 11. 测试和验收

后端：

- API 集成测试覆盖默认创建、刷新持久化、权限、字段校验、10000 BPS、重复维度和双修订 409。
- 发布测试证明评价配置进入 schema v2、内容哈希和不可变版本；历史无配置草稿仍按 v1 兼容。
- 迁移测试从 0009 升级并验证历史行 schema 版本为 1。
- `ruff`、`mypy` 和课堂同步测试通过。

前端：

- 纯函数测试覆盖 BPS、固定 scope、默认维度、wire 映射和 409 错误。
- 组件测试覆盖五项范围、增删改、学生可见、恢复服务端快照、无效总分阻止保存。
- 发布测试证明顺序为恢复/创建草稿 -> GET/PUT config -> publish -> sync。
- 学生首页测试证明课堂服务失败不阻断普通数据。
- 类型检查、构建和相关 Vitest 通过。

运行验收：

- 5174 不读取 `.env.local-demo`，不进入 8888。
- 同一远程身份域下教师可打开评价页、保存、刷新读取并再次恢复。
- 学生首页普通数据在课堂服务不可用时仍显示。
- 不进行线上部署或真实数据迁移。

## 12. 回滚

前端提交可独立回退，旧 `evaluation-policy` 仍可工作。后端回滚先停止写入 schema v2，再回退应用；迁移 downgrade 仅删除新增配置与新版本快照列，执行前必须备份。历史 plan version 和原有 hash 不被改写。
