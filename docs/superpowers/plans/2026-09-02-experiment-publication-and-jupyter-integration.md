# 实验发布、学情分析与 Jupyter 插件闭环：实施计划

> **For Codex:** 按 TDD 逐项执行。每项必须先写一个能暴露目标缺陷的测试，确认该测试在旧代码上失败，再写最小实现并重跑相关测试。

**目标：** 把教师保存的实验评价配置可靠地发布为课堂任务；让学情分析按发布状态正确展示；保证课堂学生只能带 ticket 进入 Jupyter，且插件在学生上下文暂不可用时仍能以受限模式启动。

**范围：** 两个隔离 worktree：`classroom-publication-repair-20260902`（classroom-sync 与 Jupyter 扩展）和 `lab-platform-frontend-student-experiment-ui-20260831`（平台前端）。不修改 BAMS，不推送、不部署、不执行生产迁移。

## 1. 建立回归基线和契约测试

**文件：**
- 新增/调整：`services/classroom-sync/tests/**`
- 新增/调整：`lab-platform-frontend/src/**/*.test.ts`
- 新增/调整：`src/**/*.test.ts`（Jupyter 扩展）

**步骤：**
1. 分别记录后端、前端和扩展现有的定向测试命令与结果。
2. 每个缺陷先以真实可观察边界写一个回归测试：发布后查询、学生无 assignment 的入口、含 ticket 的插件启动。
3. 对每项测试明确其能防住的代码变异，避免只断言 mock 调用。

## 2. 恢复学生绑定协议并使 BAMS 查询兼容

**文件：**
- 新增：`lab-platform-frontend/src/modules/student-binding/codec.ts`
- 新增：`lab-platform-frontend/src/modules/student-binding/codec.test.ts`
- 修改：`lab-platform-frontend/src/views/admin/AdminProjectsView.vue`
- 新增：`services/classroom-sync/src/classroom_sync/auth/student_binding.py`
- 修改：`services/classroom-sync/src/classroom_sync/auth/fincolab.py`
- 新增/修改：`services/classroom-sync/tests/**student_binding*`

**步骤：**
1. 先用固定 V1 marker 样例测试前端编码与后端解析；旧代码应因缺少协议而失败。
2. 实现规范化 V1 marker（父项目、空间、学生 ID、用户名）并在教师创建学生项目时写入描述。
3. 后端严格解析 marker，兼容旧命名规则；只接受名单中的学生，允许子项目由教师创建或学生拥有。
4. 对缺少 workbench ID 的 BAMS 项目补充详情读取，禁止让缺失数据静默变成可进入状态。

## 3. 让发布原子建立实验—计划绑定和课堂状态

**文件：**
- 修改：`services/classroom-sync/src/classroom_sync/services/plans.py`
- 修改：`services/classroom-sync/src/classroom_sync/services/read_models.py`
- 修改：`services/classroom-sync/src/classroom_sync/services/assignments.py`
- 修改：`services/classroom-sync/src/classroom_sync/routers/plans.py`
- 新增/修改：`services/classroom-sync/tests/**plan*`, `tests/**read_model*`

**步骤：**
1. 先测试发布草稿后可通过实验范围查询到该版本，即使学生任务同步尚未完成；旧实现应返回 `experiment_plan_binding_not_found`。
2. 在同一数据库事务内创建/更新 `ExperimentPlanBinding` 并返回版本、配置与发布状态。
3. 保持手动同步接口的向后兼容性，但不再把它作为绑定存在的前置条件。
4. 增加状态端点，区分 `not_configured`、`configured`、`unpublished`、`partial`、`published`，供前端显示精确原因。

## 4. 新增实验级发布、参与者和运行策略持久化

**文件：**
- 新增迁移：`services/classroom-sync/alembic/versions/*`
- 修改：`services/classroom-sync/src/classroom_sync/models.py`
- 修改：`services/classroom-sync/src/classroom_sync/contracts/classroom/v1/**`
- 修改：`services/classroom-sync/src/classroom_sync/services/plans.py`, `services/assignments.py`
- 修改：`services/classroom-sync/src/classroom_sync/routers/**`, `main.py`
- 新增/修改：`services/classroom-sync/tests/**`

**步骤：**
1. 先测试发布命令幂等保存名单、运行策略、版本资源快照和学生绑定，而不触碰 BAMS 写接口。
2. 添加 `experiment_student_bindings`、`runtime_policy`、版本资源快照及唯一约束；迁移可升级且有降级路径。
3. 以事务发布计划、冻结资源、创建待分配任务；BAMS 读取在事务前验证，失败只产生明确 `partial` 状态，不伪造成功。
4. 暴露参与者、发布、状态、学生资源及插件资源的有权限接口；补齐错误码和 OpenAPI 契约。

## 5. 前端改为“保存并发布”，修复学情分析和学生入口

**文件：**
- 修改：`lab-platform-frontend/src/modules/classroom-monitoring/api.ts`, `types.ts`
- 修改：`lab-platform-frontend/src/modules/classroom-monitoring/components/LegacyPlanWizard.vue`
- 修改：`lab-platform-frontend/src/views/admin/AdminClassroomPlanView.vue`
- 修改：`lab-platform-frontend/src/views/admin/AdminLearningAnalyticsView.vue`
- 修改：`lab-platform-frontend/src/views/student/StudentExperimentDetailView.vue`
- 修改：`lab-platform-frontend/nginx.conf`
- 新增/修改：对应 `*.test.ts`

**步骤：**
1. 先测试保存后调用发布并以发布状态为准刷新学情分析；旧代码只保存配置。
2. 配置页采用“保存并发布课堂任务”，明确显示发布/部分同步失败及可重试状态。
3. 学情分析读取课堂状态而非只把绑定缺失翻译为“未配置”。
4. 课堂模式且无 assignment 时阻断进入，不得退回裸 Jupyter URL；持 ticket URL 才允许打开。
5. 将 class-sync 反代上传限制与后端 10 MiB 资源限制统一。

## 6. 将 Jupyter 学生模式从“无上下文即无插件”改为受限降级

**文件：**
- 修改/新增：`src/index.ts`, `src/utils/classroomUiBootstrap.ts`, `src/utils/platformContext.ts`
- 修改/新增：`src/components/**`, `src/services/**`, `src/**/*.test.ts`

**步骤：**
1. 先测试 URL 含课堂 ticket 而平台上下文请求暂不可用时，仍挂载受限学生插件壳；旧代码只记录错误并退出。
2. 实现 ticket 检测、受限学生上下文、上下文刷新和失败提示；无 ticket 仍不进入课堂模式。
3. 接入知识点与资源只读接口，禁止学生端在降级状态获得教师权限或绕过截止策略。
4. 仅提交扩展源码、测试和构建配置；不提交 `.whl`、压缩包或镜像二进制物。

## 7. 验证、文档与交接

**文件：**
- 修改：相关 README/环境变量示例（仅当实现新增变量或启动方式时）

**步骤：**
1. 运行后端迁移检查、定向及相关全量测试；运行前端 Vitest/类型检查/构建；运行扩展单测和构建。
2. 使用本地 class-sync 与前端做冒烟：保存并发布 → 查询学情 → 学生 ticket 入口 → 插件受限壳。
3. `git diff --check`、检查未跟踪二进制和全部变更范围。
4. 分仓库提交，报告命令、结果、迁移文件、环境变量与“需重新构建/部署 Jupyter 镜像才能影响线上环境”的限制。

