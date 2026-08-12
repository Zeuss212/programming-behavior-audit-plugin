# 真实课堂监控平台实施总计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将教师发布方案、学生接受并监控、误关恢复、手动或自动提交、教师查看单份简报与后端证据连成可生产验证的课堂闭环。

**Architecture:** 新增独立课堂监控同步服务作为可信中枢；Jupyter 插件保持本地优先采集并向同步服务增量上传；FinColab 前端只通过共享 API 管理方案、任务、巡视和简报。所有新入口受功能开关控制，当前演示页面在联调门槛前保持不变。

**Tech Stack:** Python 3.10+、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL、S3/MinIO、Jupyter Server 2、JupyterLab 4、TypeScript 5、Vue 3、Vite、Vitest/Jest/Pytest、Docker/nerdctl、Nginx。

## Global Constraints

- 设计来源：`docs/superpowers/specs/2026-08-12-teacher-student-classroom-platform-design.md`。
- 工作台只能通过 `https://14.103.139.131:40037` 进入；`40002` 不作为学生入口。
- 学生不能创建、修改或发布方案，也不能配置教师 AI 密钥；必须由服务端能力校验，不只隐藏按钮。
- 方案绑定教师实验并自动同步到全部学生子工作台；已开始会话固定方案版本。
- 页面关闭不等于提交；证据截止前重新进入必须恢复同一会话。
- 实际下课时间取计划下课与教师提前下课的较早值；其后 15 分钟自动收口。
- 每名学生对教师只交付一份结构化简报；日志正文保存到私有对象存储，数据库只保存索引和教学结论。
- AI 失败不得阻塞基础简报、证据保存或提交终态。
- 当前已有工作区改动属于用户；实施必须在隔离 worktree/分支中进行，不得清理或覆盖。
- 前端、插件、同步服务分开提交、分别验证；部署、推送和真实数据迁移必须经过新的发布门槛。
- 前端满足 WCAG 2.1 AA：键盘可达、状态不只依靠颜色、完整加载/错误/空状态，并验证 320/768/1024/1440 px。

---

## 1. 子计划与执行顺序

| 顺序 | 子计划 | 独立交付物 | 进入下一阶段的门槛 |
| --- | --- | --- | --- |
| 1 | [同步服务与共享契约](2026-08-12-classroom-sync-service.md) | 可本地运行的 API、迁移、PostgreSQL/MinIO 集成测试 | 契约、权限、调度、幂等测试全绿 |
| 2 | [Jupyter 插件接入](2026-08-12-classroom-jupyter-plugin.md) | 学生模式、票据注册、恢复、证据发件箱、提交协调 | 插件前后端测试、构建、故障恢复全绿 |
| 3 | [FinColab 教师与学生端](2026-08-12-classroom-fincolab-frontend.md) | 方案向导、课堂巡视、学生接受与提交状态、简报页 | Vitest、类型检查、构建、可访问性验证全绿 |
| 4 | [联调、盲审与部署](2026-08-12-classroom-integration-deployment.md) | 可回滚镜像、测试课程灰度、课堂盲审证据 | 45 分钟课堂、15 分钟收口、权限和回滚验证通过 |

不得跳过顺序并先改线上前端。子计划 1–3 可以开发在不同仓库/分支，但跨端契约冻结后才能并行；同一个 worktree 同一时间只允许一个实施负责人。

## 2. 仓库与分支边界

### 主仓库

路径：`/Users/sxh/编程行为监控分析插件_交付版_20260727`

包含：

- `services/classroom-sync/`：新增共享服务；
- `myextension/`、`src/`：Jupyter 插件；
- `deploy/bluedot/`：插件课堂镜像与验证脚本；
- `docs/superpowers/`：契约和交付文档。

执行前从包含设计提交 `74a57b7` 的基线创建隔离 worktree，建议分支：

```bash
git worktree add .worktrees/classroom-platform -b codex/classroom-platform main
```

同步服务和插件采用独立提交序列；若后续需要拆 PR，在验证通过后从相应提交点再拆分分支，不在脏主目录搬运修改。

### FinColab 前端仓库/worktree

路径：`/Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/lab-platform-frontend`

当前基线分支：`version-2026-08-04`。实施前先记录 `git status` 和 HEAD，再从已确认基线创建新的隔离 worktree，建议分支：`codex/classroom-ui`。不得把前端提交混入主仓库提交。

## 3. 跨计划冻结契约

子计划 1 首先生成并发布以下 JSON Schema，其他计划只消费生成的 TypeScript/Python 类型，不复制手写枚举：

```text
contracts/classroom/v1/plan-draft.schema.json
contracts/classroom/v1/plan-version.schema.json
contracts/classroom/v1/student-assignment.schema.json
contracts/classroom/v1/monitor-session.schema.json
contracts/classroom/v1/evidence-chunk-manifest.schema.json
contracts/classroom/v1/student-brief.schema.json
contracts/classroom/v1/teacher-review.schema.json
contracts/classroom/v1/error.schema.json
```

跨端固定枚举：

```text
assignment_status = pending_acceptance | ready | active | submitted
session_status = collecting | temporarily_offline | submitting | pending_upload | completed | partial
submission_reason = student_manual | teacher_ended | system_deadline
mastery_status = mastered | partial | not_demonstrated | review_required
```

任何跨端字段变更必须在同一变更中更新 Schema、服务端验证、插件类型、前端类型和契约测试；不能只改一端。

## 4. 统一质量门禁

### 同步服务

```bash
cd services/classroom-sync
python -m ruff check .
python -m mypy src
python -m pytest -q
python -m alembic upgrade head
python -m alembic downgrade -1
python -m alembic upgrade head
```

### Jupyter 插件

```bash
python -m pytest myextension/tests -q
jlpm lint:check
jlpm test --runInBand
jlpm build:prod
```

### FinColab 前端

现有 `npm run lint` 会写入文件，验证阶段使用只读命令：

```bash
npx --no-install oxlint .
npx --no-install eslint .
npm run type-check
npm test -- --run
npm run build
```

### 跨系统

```bash
docker compose -f deploy/classroom/docker-compose.test.yml up -d --wait
python scripts/classroom_contract_smoke.py
python scripts/classroom_fault_smoke.py
docker compose -f deploy/classroom/docker-compose.test.yml down
```

若任何基线命令在未修改代码时已失败，先保存失败日志并隔离基线问题；未建立对照前不得把失败称为历史问题。

## 5. 阶段检查点

### 检查点 A：契约冻结

- OpenAPI 与八个 JSON Schema 可生成且无循环/未解析引用；
- 现有 Profile v2 能无损嵌入 `PlanVersion.profile`；
- 插件和前端生成类型编译通过；
- 外部身份与成员接口已通过真实只读样本验证。

### 检查点 B：本地纵向闭环

- 教师 API 发布方案并同步任务；
- 学生 API 接受任务并签发一次性票据；
- 插件交换票据、创建会话、上传证据和提交简报；
- 教师 API 能读取该简报并按权限获取证据目录；
- 全流程不依赖 AI。

### 检查点 C：前端集成

- 功能开关关闭时现有演示页面 DOM、路由和工作台打开流程不回归；
- 开关开启时教师和学生新流程可完成；
- 学生调用教师写接口得到 403；
- 页面误关后恢复同一会话，URL 中不残留票据。

### 检查点 D：部署授权

进入真实服务器前必须重新确认：目标主机与端口、候选镜像 digest、数据库备份、对象存储桶、Secret 注入、旧镜像 digest、回滚命令和停止条件。没有新的明确授权时只构建本地产物，不上传、不推送、不替换容器。

## 6. 最终完成定义

只有四份子计划全部通过且联调证据归档后，才能宣称平台闭环完成。完成证据至少包含：

- 各仓库 commit SHA 和工作区状态；
- OpenAPI/Schema 版本与数据库迁移版本；
- 所有质量命令的完整结果；
- 45 分钟采集、误关恢复、断网补传、服务重启后调度恢复；
- 实际下课后 15 分钟内全部会话进入终态；
- 学生越权、教师跨课程访问和票据重放均被拒绝；
- AI 故障下基础简报正常；
- 容器释放后教师仍能查看简报和已上传证据；
- 候选部署与旧版本回滚各执行一次且结果可复现。
