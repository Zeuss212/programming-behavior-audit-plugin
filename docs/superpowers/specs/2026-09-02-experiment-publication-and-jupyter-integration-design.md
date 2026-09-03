# 实验发布、学情分析与 Jupyter 插件闭环设计

日期：2026-09-02  
目标后端分支：`codex/main-backend-integration-20260831`  
目标前端分支：`codex/student-experiment-ui-20260831`

## 1. 目标与现状

教师已经能够在实验管理中创建 BAMS 实验环境，并在考核评价页把行为监测范围和
评价维度保存到 `experiment_assessment_configs`。当前链路仍有三个相互关联的断点：

1. 保存评价配置不会发布课堂计划，学情分析只能通过
   `experiment_plan_bindings` 查找已发布版本，因此显示“尚未发布课堂方案”。
2. `experiment_plan_bindings` 目前由学生任务同步顺带创建。学生绑定解析失败时，
   即使 `plan_versions` 已经产生，实验仍无法查询到发布版本。
3. 学生找不到匹配的 `student_assignments` 时，前端会直接打开不带课堂 ticket 的
   Jupyter。插件因没有课堂注册和上下文，无法进入受限学生模式；上下文请求失败时，
   当前插件还会完全跳过侧栏初始化。

本设计把“保存评价方案”升级为“保存并发布课堂任务”，让一个用户动作完成评价、
计划、资源、学生任务和 Jupyter 启动上下文的闭环，同时保持 BAMS 不变。

## 2. 范围

### 2.1 本轮包含

- 新增实验级幂等发布命令和发布状态查询。
- 恢复并升级学生绑定元数据协议；持久化参与学生与环境映射。
- 在计划发布事务中建立实验到版本的绑定。
- 持久化并执行截止后查看、工作台和提交策略。
- 将实验资源冻结到不可变计划版本，并补齐学生端与插件端资源读取接口。
- 修复学情分析对“未配置、未发布、部分同步、已发布”的状态判断。
- 禁止课堂模式无 assignment 时绕过 ticket 打开 Jupyter。
- 合入并适配现有 Jupyter 学生模式 fail-closed、上下文校验和知识点展示成果。
- 补充前端、classroom-sync、Jupyter 扩展和迁移测试。

### 2.2 明确不包含

- 不修改 BAMS 源码、BAMS 数据库或 BAMS 现有 API 语义。
- 不把前端或 classroom-sync 直接连接到 BAMS 数据库。
- 不生成未经产品规则定义的自动数值评分、成绩、排名或惩罚结论。
- 不在本轮执行生产数据库迁移、镜像推送、工作台模板替换或线上部署。
- 不自动覆盖学生工作区已有文件；课堂资源只以不可变清单和显式下载方式交付。
- 不把 BAMS bearer token、课堂插件 token 或 AI 密钥写入浏览器持久存储、日志或数据库。

## 3. 方案比较

### 3.1 采用：classroom-sync 主导的幂等发布命令

前端提交完整发布意图，classroom-sync 负责校验、保存、发布、冻结和同步。跨 BAMS
读取与本地数据库事务无法组成分布式事务，因此命令记录每个可恢复阶段，并依赖
operation ID、自然键和版本唯一约束实现安全重试。

优点：状态权威、错误可定位、浏览器刷新后可恢复、不会把部分完成误报为成功。

### 3.2 不采用：前端继续串行编排多个现有接口

改动较小，但 draft ID、plan version ID 和同步进度只保存在页面状态中。浏览器刷新、
网络中断或最后一步失败都可能留下无法解释的半完成状态，正是当前问题的来源。

### 3.3 不采用：读取时根据评价配置合成“已发布”状态

这会让学情页面看起来可用，却没有不可变计划、学生任务和插件 ticket，属于伪造业务
状态，并会扩大授权绕过风险。

## 4. 领域状态与权威来源

一个实验由 `(space_id, parent_algorithm_id)` 唯一标识。`experiment_name` 只用于显示，
不能作为关联键。

| 信息 | 权威来源 |
| --- | --- |
| 课程、成员、父/子算法项目、工作台、算力、数据集 | BAMS HTTP API |
| 评价配置草稿 | `experiment_assessment_configs` |
| 参与学生及其子项目/工作台 | 新增 `experiment_student_bindings` |
| 计划草稿 | `plan_drafts` |
| 已发布不可变计划 | `plan_versions` |
| 当前实验发布版本 | `experiment_plan_bindings` |
| 学生任务 | `student_assignments` |
| 教师上传资源元数据 | `experiment_resources` |
| 某计划版本冻结的资源 | 新增 `plan_version_resources` |
| 资源与行为证据二进制 | S3/MinIO；数据库仅保存 `object_key` |
| 插件会话、证据和简报 | `monitor_sessions`、`evidence_chunks`、`student_briefs` |

## 5. 数据库设计

迁移文件放在 `services/classroom-sync/migrations/versions/`。当前 head 为 `0012`；实施时
先读取实际 Alembic head，再使用下一个连续 revision，不能与并行迁移复用编号。

### 5.1 `experiment_student_bindings`

字段：

| 字段 | 类型与约束 | 说明 |
| --- | --- | --- |
| `id` | `String(64)` PK | UUID |
| `space_id` | `String(128)` 非空 | BAMS 课程/空间 ID |
| `parent_algorithm_id` | `String(128)` 非空 | 教师父实验 ID |
| `student_id` | `String(128)` 非空 | BAMS roster 用户 ID |
| `student_username` | `String(200)` 非空 | 发布时用户名快照 |
| `child_algorithm_id` | `String(128)` 可空 | BAMS 学生子项目 |
| `workbench_id` | `String(128)` 可空 | BAMS 工作台 |
| `provisioning_state` | `String(32)` 非空 | `pending/provisioning/ready/failed` |
| `environment_blueprint` | JSON 非空 | 框架、模板、数据集、项目类型和资源规格 |
| `failure_code` | `String(128)` 可空 | 稳定错误码，不保存上游响应正文 |
| `created_at` | 带时区时间 | 创建时间 |
| `updated_at` | 带时区时间 | 最近变更时间 |

唯一约束：

```text
(space_id, parent_algorithm_id, student_id)
```

同一学生只能有一个当前绑定。子项目或工作台改变时更新该行；已发布任务继续引用原
assignment 中的不可变 ID，不回写历史版本。

### 5.2 运行策略快照

给 `plan_drafts` 和 `plan_versions` 增加非空 JSON 字段 `runtime_policy`：

```json
{
  "allow_view_after_end": true,
  "allow_workbench_after_end": false,
  "allow_submit_after_end": false
}
```

旧记录迁移使用当前产品默认值。发布时把 draft 策略复制到 plan version；后续编辑不
改变已接受任务所绑定版本。

### 5.3 `plan_version_resources`

字段：

| 字段 | 类型与约束 | 说明 |
| --- | --- | --- |
| `plan_version_id` | FK 非空 | `plan_versions.id` |
| `resource_id` | FK 非空 | `experiment_resources.id` |
| `role` | `String(32)` 非空 | `assignment_material/code_framework` |
| `ordinal` | Integer 非空 | 稳定展示顺序 |
| `created_at` | 带时区时间 | 冻结时间 |

主键或唯一约束为 `(plan_version_id, resource_id)`。资源一旦被发布版本引用，就不能
物理删除；教师删除只影响后续版本，历史下载仍指向原对象。

### 5.4 发布操作状态

优先使用已有 draft revision、plan version source revision、experiment binding 和
assignment 自然键推导进度，不新增通用工作流表。若实现调查证明单一 operation ID
无法跨请求恢复，才增加范围明确的 `experiment_publication_operations`；不得为了方便
先引入通用任务引擎。

## 6. 学生绑定协议

恢复前端 `student-binding/codec.ts` 与后端 `auth/student_binding.py` 的规范 V1 编码。
新建学生子项目的 description 第一行同时包含父实验标记和 canonical Base64URL 绑定：

```text
[FINCOLAB_PARENT_PROJECT_ID:<parent>][FINCOLAB_STUDENT_BINDING_V1:<payload>]
```

payload 精确包含：

```json
{
  "parent_algorithm_id": "...",
  "space_id": "...",
  "student_id": "...",
  "student_username": "..."
}
```

后端校验：

1. 教师是父实验 owner 且属于该课程。
2. payload 使用 canonical JSON、URL-safe 无 padding 编码，无重复字段或未知版本。
3. `student_id` 与 `student_username` 精确匹配当前课程 student roster。
4. 子项目 owner 只能是当前教师或目标学生。
5. 子项目列表缺失 owner/workbench 时，通过详情接口补全。
6. 同一学生、子项目、工作台出现重复或冲突时 fail closed。

旧项目只有父标记时，继续使用严格的 legacy 项目名规则匹配；新项目不得再写 legacy
格式。绑定元数据是教师授权下的课程映射，不改变 BAMS 对项目访问本身的权限。

## 7. API 设计

### 7.1 保存参与学生

```http
PUT /v1/classroom/experiments/{space_id}/{parent_algorithm_id}/participants
GET /v1/classroom/experiments/{space_id}/{parent_algorithm_id}/participants
```

PUT 使用 replace 语义，并携带 `expected_revision`。后端先验证全部 roster 和已存在的
子项目/工作台，再在单个数据库事务中 upsert。校验失败不写入部分列表；环境创建
失败的学生可以显式保存为 `failed`，但必须有稳定 `failure_code`。

### 7.2 保存并发布课堂任务

```http
POST /v1/classroom/experiments/{space_id}/{parent_algorithm_id}/publish
```

请求包含：

- `operation_id`
- `expected_assessment_config_revision`
- 评价配置完整内容
- 实验标题与题目说明
- 明确输入的开始、结束时间
- `runtime_policy`
- 参与学生 revision

若当前 scope 已有可复用 draft，命令更新该 draft；若没有，则使用请求中的完整计划
字段创建 draft。不得在服务端编造时间或知识点。缺少发布必需字段返回 422 和稳定
错误码，由前端保留表单并定位字段。

成功响应：

```json
{
  "status": "published",
  "plan_version_id": "...",
  "plan_id": "...",
  "version": 1,
  "assessment_config_revision": 2,
  "participant_count": 20,
  "assignment_count": 20,
  "resource_count": 3,
  "issues": []
}
```

如果计划已经发布但部分环境或 assignment 无法同步，响应使用 `partial`，返回已发布
版本和逐学生稳定问题列表。前端不得显示“配置完成”，重试相同 operation 只继续未完成
阶段，不重复创建版本。

### 7.3 发布状态

```http
GET /v1/classroom/experiments/{space_id}/{parent_algorithm_id}/status
```

返回：

- `assessment_configured`
- `assessment_config_revision`
- `plan_published`
- `plan_version_id`
- `participant_count`
- `ready_participant_count`
- `assignment_count`
- `resource_count`
- `publication_state`: `not_configured/configured/unpublished/partial/published`
- `issues`: 仅稳定错误码和受限实体 ID

### 7.4 学生资源

学生 BAMS bearer 授权：

```http
GET /v1/classroom/student/assignments/{assignment_id}/resources
GET /v1/classroom/student/assignments/{assignment_id}/resources/{resource_id}/download
```

插件 JWT 授权：

```http
GET /v1/classroom/plugin/sessions/{session_id}/resources
GET /v1/classroom/plugin/sessions/{session_id}/resources/{resource_id}/download
```

两组接口只能读取 assignment 所绑定 plan version 的冻结资源。assignment owner、课程
成员、plugin token session scope 任一不匹配都返回 403；不能返回 S3 object key、存储
凭证或其他学生的元数据。

### 7.5 实验归档

```http
POST /v1/classroom/experiments/{space_id}/{parent_algorithm_id}/archive
```

该接口先停止新 ticket 和后续发布，保留计划版本、任务、证据、简报和审计记录。前端
只有归档成功后才调用现有 BAMS 删除接口。归档不物理删除历史数据或对象存储内容。

## 8. 发布事务与恢复

发布步骤：

1. 使用教师 bearer 从 BAMS 验证 principal、课程角色和父实验 owner。
2. 读取并验证参与学生绑定；需要的 BAMS 网络读取在数据库写事务前完成。
3. 以 scope lock 串行化同一实验的发布。
4. 用 optimistic revision 保存评价配置和 plan draft。
5. 产生或复用相同 source draft revision 的 `plan_versions`。
6. 在同一事务内 upsert `experiment_plan_bindings`。
7. 冻结 `plan_version_resources`。
8. 为 `ready` 参与者幂等 upsert `student_assignments`。
9. 写入不含凭证和上游正文的 audit event。

计划版本和实验 binding 必须一起提交。学生任务同步的个别失败不能撤销已经发布的
计划版本，但结果必须为 `partial`，并允许安全重试。已接受或已开始的旧 assignment
继续绑定旧版本；只更新 `pending_acceptance` assignment。

## 9. 学情分析行为

学情分析先读取实验状态，再决定是否读取 plan monitoring：

| 状态 | 页面行为 |
| --- | --- |
| `not_configured` | 显示“尚未保存评价配置” |
| `configured` / `unpublished` | 显示“评价已保存，课堂任务尚未发布”及发布入口 |
| `partial` | 展示已发布计划和已同步学生，同时列出未下发人数与重试入口 |
| `published` 且无学生 | 显示“课堂任务已发布，当前无参与学生” |
| `published` | 加载 monitoring 和学生列表 |

404 `experiment_plan_binding_not_found` 不再被解释为所有配置都缺失。页面刷新和课程/
实验切换继续使用 generation guard，避免旧请求覆盖新选择。

## 10. 学生进入与 Jupyter 插件

### 10.1 前端入口

课堂功能开启时：

- 课堂 assignment 状态读取失败：禁止进入。
- 找到当前 child/workbench 但没有 assignment：禁止进入并显示“课堂任务尚未下发”。
- assignment 已提交、未开始、已截止且策略不允许：禁止进入。
- assignment 合法：必要时 accept，签发一次性 launch ticket，再打开 ticketed URL。

只有明确关闭课堂功能的兼容模式才能打开普通无 ticket 工作台。

### 10.2 Jupyter 扩展初始化

把 `codex/jupyter-knowledge-points-20260902` 中的源码改动移植到目标后端分支，不直接
合并其二进制制品提交。关键行为：

1. 清理 URL fragment 前记录 `behavior_ticket` 是否出现。
2. ticket 注册成功后读取平台 context。
3. ticket 注册或 context 读取失败时，若观察到 classroom ticket，初始化受限学生
   context，而不是完全不挂载侧栏或回退本地教师模式。
4. 对学生 context、capabilities、session 和 profile 做运行时校验。
5. 刷新失败时保留最后可信课堂快照，并给出可重试状态。
6. 学生侧栏展示发布版本知识点、评价中 `student_visible=true` 的维度、资源清单、
   监控状态和提交入口。
7. monitoring scopes 随发布版本进入 plugin session credentials，控制分析输入选择；
   客观采集仍保留最小审计事件，不能因 UI 开关破坏会话连续性。

Jupyter 浏览器不保存 BAMS bearer。Jupyter Server 保存短期 plugin session context，
通过 plugin JWT 调用 classroom-sync；ticket 一次使用并立即从地址栏移除。

## 11. 运行策略执行

服务端为权威校验点：

- `allow_view_after_end`：控制学生任务详情和冻结资源读取。
- `allow_workbench_after_end`：控制 launch ticket 签发和会话恢复。
- `allow_submit_after_end`：控制学生提交；自动截止任务仍使用既有证据 cutoff 规则。

前端按钮状态只用于反馈，不作为授权。旧计划使用迁移后的默认策略以保持当前行为。

## 12. 错误处理

新增稳定错误码至少包括：

- `experiment_publication_input_incomplete`
- `experiment_publication_revision_stale`
- `experiment_participants_revision_stale`
- `experiment_participant_not_in_roster`
- `experiment_participant_environment_missing`
- `student_binding_marker_malformed`
- `student_binding_marker_unknown_version`
- `student_binding_username_mismatch`
- `child_owner_contract_conflict`
- `child_workbench_unverified`
- `assignment_not_published`
- `assignment_workbench_closed`
- `assignment_submission_closed`
- `plan_resource_not_available`
- `experiment_archived`

接口不得把 BAMS 原始响应、数据库连接串、对象 key、token、密码或堆栈传给浏览器。
服务端日志记录 request ID、阶段和稳定错误码，便于区分发布、同步与 Jupyter 注册故障。

## 13. 兼容和回滚

- 保留现有 draft、publish、sync、assessment 和 teacher resource 路由，避免破坏旧页面。
- 新发布页面改用组合命令；旧路由内部复用相同领域服务。
- 旧计划没有 `runtime_policy` 时按产品默认值读取。
- 旧学生项目没有 V1 标记时使用严格 legacy fallback；新建项目必须使用 V1。
- 已发布资源不物理删除。
- 数据库迁移提供 downgrade，但生产回滚前必须确认没有新版本引用新增字段或表。
- 插件源码提交与发布制品分离；未获部署授权前不替换真实镜像或模板。

## 14. 测试策略

### 14.1 前端

- V1 binding 编码与 Unicode golden vectors。
- 创建实验将精确 student ID/username 写入绑定元数据。
- 保存并发布按钮的 pending、partial、published、stale 和 retry 状态。
- 学情分析五种发布状态。
- 课堂开启且无 assignment 时不打开普通 Jupyter。
- assignment 正常时 accept → ticket → ticketed URL 顺序。
- 截止策略按钮与服务端拒绝反馈。
- 学生资源列表与下载授权错误反馈。

### 14.2 classroom-sync

- Alembic 全量升级、逐级降级和 SQLite/PostgreSQL 约束。
- 发布计划与 experiment binding 原子提交。
- 相同 operation/source revision 不重复产生版本。
- 评价、运行策略和资源进入不可变 plan version 快照。
- 参与学生 replace 与 optimistic revision。
- 规范绑定、legacy fallback、owner 兼容、详情补全和所有冲突拒绝路径。
- partial assignment sync 的状态和重试。
- 学生与 plugin 资源跨 assignment 访问拒绝。
- 三个截止策略在读取、ticket、恢复和提交端点强制执行。
- 归档后拒绝新 ticket/发布但保留历史读取。

### 14.3 Jupyter 扩展

- ticket 被移除前记录 classroom intent。
- ticket 注册/context 失败仍初始化受限学生 UI。
- 畸形 context 不能获得教师能力。
- 刷新失败保留可信快照。
- 显示知识点、学生可见评价维度和冻结资源。
- plugin session credentials 带 assessment/runtime/resource scope。
- Jest、TypeScript 构建、Python 测试、JupyterLab production build 和 wheel 内容检查。

### 14.4 关键端到端验收

1. 教师创建实验、选择学生、配置文件、时间、行为范围和评价维度。
2. 点击“保存并发布课堂任务”，返回 `published`；刷新页面状态仍一致。
3. 学情分析不再显示“尚未配置”，并显示真实参与人数。
4. 学生只看到自己的 assignment；点击进入后 URL 带一次性 fragment ticket。
5. Jupyter 加载后 ticket 从地址栏消失，左侧出现受限学生插件及本实验上下文。
6. 学生采集、提交后，教师学情页面出现真实会话和简报状态。
7. 任一阶段失败时页面显示准确阶段，可重试且不重复创建版本。

## 15. 实施顺序与停止点

实施拆成保持可构建的小闭环：

1. 学生绑定协议与持久化。
2. 发布事务中建立 experiment binding，新增状态接口。
3. 组合发布命令和前端“保存并发布”。
4. 学情分析状态修复。
5. 学生入口强制 assignment/ticket。
6. Jupyter fail-closed 和课堂 context 移植。
7. 运行策略持久化与授权。
8. 资源版本冻结及学生/plugin 读取接口。
9. 归档、回归、文档与交接。

本轮实现完成后的停止点是两个目标分支上的本地可审查提交及新鲜验证记录。推送、
生产迁移、镜像构建/推送、BAMS 模板替换和线上验收属于新的阶段门槛。
