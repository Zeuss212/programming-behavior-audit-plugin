# 课堂方案版本链修复设计

## 背景与问题

可信课堂四步向导把“新建课堂版本”建模为新的 `PlanDraft`。现有发布逻辑同时把
`draft.id` 当作长期 `plan_id`，因此每个新草稿都会创建一个新的方案并从版本 1
开始。`source_draft_revision` 也缺少草稿身份，两个 revision 都为 0 的草稿无法形成
可靠的发布幂等键。

本修复只改变可信课堂方案的持久化版本语义，不改变实验管理页面、四步向导接口、
已发布版本内容、内容哈希或已开始学生任务。

## 目标与非目标

目标：

- 同一已绑定课堂方案的新编写会话继承稳定 `plan_id` 和 `profile_id`。
- 每次成功发布得到严格递增的版本号。
- 同一草稿 revision 的重试返回同一 `PlanVersion`。
- 未接受任务可随同步移动到新版；已接受、进行中或已完成任务保留旧版。
- 迁移保留历史 `PlanVersion` 内容与哈希。

非目标：

- 不合并历史上已经错误产生的两个不同 `plan_id`。
- 不把传统“新建方案”强制改成“新建版本”。没有 authoring session 的新草稿仍创建
  新的版本链并从 v1 开始。
- 不修改前后端 HTTP 契约，也不部署或清理现有交付产物。

## 数据模型

新增 `PlanSeries`，一行代表一条真正的方案版本链：

- `id`：稳定 `plan_id`，主键。
- `profile_id`：稳定测评 profile 身份，唯一。
- `space_id`、`parent_algorithm_id`：所属实验范围。
- `latest_version`：已提交的最大版本号，初始为 0。

`PlanDraft` 新增非空 `plan_id` 并引用 `PlanSeries.id`。同一版本链的多个草稿共享
`profile_id`，因此移除草稿表对 `profile_id` 的单列唯一约束。

`PlanVersion` 新增非空 `source_draft_id`，并增加唯一约束
`(source_draft_id, source_draft_revision)`。`plan_id` 继续与 `version` 组成唯一键。

`ExperimentPlanBinding` 仍只表示该实验当前对学生可见的已同步版本，不承担版本计数；
`StudentAssignment` 的自然键和既有状态迁移规则保持不变。

## 数据流与事务

创建草稿时：

1. 无 authoring session 的传统草稿创建新的 `PlanSeries`，`series.id == draft.id`。
2. 可信课堂草稿锁定当前实验 binding；存在 binding 时复用其 `plan_id` 对应的
   `PlanSeries`，不存在时创建新链。
3. 草稿保存稳定的 `plan_id/profile_id` 快照。

发布时：

1. 锁定草稿、authoring session 和对应 `PlanSeries`。
2. 先按 `(draft.id, draft.revision)` 查找已发布版本；存在则原样返回。
3. 检查 authoring session 仍可发布，并执行既有材料发布门禁。
4. 使用 `series.latest_version + 1` 构造内容和哈希，插入 `PlanVersion` 后同步更新
   `latest_version`。
5. 在同一事务内关闭 authoring session，并把 `published_plan_id` 写为稳定
   `draft.plan_id`。

同一版本链的并发发布会争用 `PlanSeries` 行锁并依次获得 N、N+1。插入、内容校验
或审计失败会回滚版本计数、版本行和 authoring session 状态。

## 迁移与回滚

新增 Alembic `0009`：

- 为每个历史 `plan_id` 创建一行 `PlanSeries`，`latest_version` 取历史最大版本。
- 为尚未发布的历史草稿创建 `latest_version=0` 的版本链。
- 历史 `PlanDraft.plan_id` 回填为 `draft.id`。
- 历史 `PlanVersion.source_draft_id` 回填为 `plan_id`；旧实现中两者语义相同。
- 不更新任何历史 `PlanVersion` 的 profile、时间、哈希或教师身份。

降级只有在每个 `profile_id` 仍只对应一个草稿时安全；若新功能已产生同一链的多个
草稿，降级必须明确拒绝并要求从迁移前备份恢复，避免删除历史草稿或版本。

## 错误与兼容性

- binding 引用不存在的版本链时返回明确冲突，不静默创建或猜测身份。
- authoring session 已发布时，仅精确的同草稿 revision 重试可成功。
- 旧的 schema v2 同草稿再次发布仍递增版本；新建 schema v2 草稿仍是新方案 v1。
- API 响应字段不变，前端只会看到正确递增的 `version`。

## 验收

- 迁移从 `0008` 正确回填旧数据，空库可往返迁移。
- 两个连续可信 authoring session 发布同一 `plan_id/profile_id` 的 v1、v2。
- 同一草稿 revision 重试返回相同版本 ID，失败事务不消耗版本号。
- v1 已接受任务不变，v1 未接受任务同步后移动到 v2。
- 后端 pytest、Ruff、mypy 和 `git diff --check` 全部通过。

