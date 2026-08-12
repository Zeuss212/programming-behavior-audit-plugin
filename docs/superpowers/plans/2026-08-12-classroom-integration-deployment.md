# 课堂平台联调、盲审与部署实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在隔离测试课程中验证同步服务、Jupyter 插件和 FinColab 前端的真实课堂闭环，并以候选端口、灰度开关和保留旧 digest 的方式安全部署。

**Architecture:** 本地先用 Docker Compose 启动 PostgreSQL、MinIO、同步 API、deadline worker、模拟 FinColab 身份服务和 Jupyter；契约与故障测试通过后才构建 Linux AMD64 候选镜像。线上先部署数据层和关闭功能的同步服务，再部署插件和前端候选，最后只对测试课程开启；任何不明确状态立即停止并回滚入口，不删除数据。

**Tech Stack:** Docker Compose、PostgreSQL、MinIO、FastAPI、JupyterLab 4、Vue/Nginx、Playwright/浏览器自动化、curl、nerdctl、SSH/SCP、SHA-256。

## Global Constraints

- 本计划包含部署动作，但设计确认不等于部署授权；执行真实 SSH/SCP、数据库迁移、容器替换和镜像推送前必须再次获得明确授权。
- 已知前端目标为 `root@14.103.163.121`、容器 `lab-platform-frontend`、公开端口 `5179:80`；执行前仍需只读确认实际状态和旧 digest。
- BAMS 工作台入口固定 `https://14.103.139.131:40037`；不将 40002 暴露为学生入口。
- 新 PostgreSQL、对象存储和同步服务的主机、端口、域名、证书和备份位置当前尚未由平台确认；未确认时只能执行本地联调。
- 数据库迁移前做备份；回滚不执行破坏性 downgrade，不删除已发布方案、简报或证据。
- 前端和插件功能开关默认关闭；候选验证失败时先关闭开关，再恢复旧镜像。
- 所有 Secret 通过运行时 Secret/环境注入，不进入 tar、镜像历史、命令输出或 Git。
- 远程命令只操作已解析的具体容器、镜像和文件；不得杀死未知进程或清理宽泛目录。

---

## File Map

```text
deploy/classroom/docker-compose.test.yml    # 本地全栈
deploy/classroom/mock-fincolab/             # 身份/成员只读模拟
deploy/classroom/nginx/classroom.conf        # /classroom-api 与 SSE 代理
scripts/classroom_contract_smoke.py          # 纵向 API 契约
scripts/classroom_fault_smoke.py             # 断网/重启/重复/截止
scripts/classroom_blind_audit.md              # 无开发上下文的测试脚本
scripts/classroom_release_manifest.py         # digest、schema、migration 清单
docs/runbooks/classroom-deployment.md         # 部署/回滚 runbook
docs/runbooks/classroom-operations.md         # 告警、备份、证据保留
releases/classroom-platform-<date>/           # 本地候选制品与 SHA256SUMS
```

### Task 1: 本地全栈 Compose 和契约冒烟

**Files:**
- Create: `deploy/classroom/docker-compose.test.yml`
- Create: `deploy/classroom/mock-fincolab/app.py`
- Create: `scripts/classroom_contract_smoke.py`
- Create: `scripts/__tests__/test_classroom_contract_smoke.py`

**Interfaces:**
- Services: `postgres`、`minio`、`minio-init`、`sync-api`、`deadline-worker`、`mock-fincolab`、`jupyter-student`、`frontend`。
- Seed identities: `teacher001` owns one course/parent experiment；`student001` has one child workbench；`student002` is a negative cross-course principal。

- [ ] **Step 1: 写冒烟失败测试**

脚本必须执行发布 -> 同步 -> 接受 -> ticket -> register -> evidence -> manual submit -> teacher brief，并断言每个 ID 的关联关系和最终单份简报。

- [ ] **Step 2: 运行并确认 Compose 缺失**

Run: `python -m pytest scripts/__tests__/test_classroom_contract_smoke.py -q`

Expected: FAIL，因为 compose 和 smoke client 尚不存在。

- [ ] **Step 3: 实现可重复 seed 和全栈网络**

所有容器使用内部 DNS 名，不写固定公网 IP；mock token 是测试专用不可用于生产。MinIO bucket 由一次性 init 服务创建并保持私有。

- [ ] **Step 4: 运行纵向闭环两次验证幂等**

```bash
docker compose -f deploy/classroom/docker-compose.test.yml up -d --wait
python scripts/classroom_contract_smoke.py
python scripts/classroom_contract_smoke.py --repeat-existing
docker compose -f deploy/classroom/docker-compose.test.yml down
```

Expected: 第一次创建全链路，第二次不重复任务、session、证据或逻辑简报。

- [ ] **Step 5: 提交**

```bash
git add deploy/classroom scripts/classroom_contract_smoke.py scripts/__tests__
git commit -m "test: add classroom integration environment"
```

### Task 2: 故障注入和 15 分钟自动收口

**Files:**
- Create: `scripts/classroom_fault_smoke.py`
- Create: `scripts/__tests__/test_classroom_fault_smoke.py`

**Interfaces:**
- Test clock: 同步服务和插件测试配置使用可注入 UTC clock；生产路径禁止客户端任意设置时间。
- Faults: browser reload、network partition、sync-api restart、worker restart、MinIO 503、duplicate submit、ticket replay、container stop。

- [ ] **Step 1: 写故障矩阵测试**

每个场景记录前后 session ID、last sequence、missing ranges、submission reason、brief revision 和对象数量。

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest scripts/__tests__/test_classroom_fault_smoke.py -q`

Expected: FAIL，因为 fault runner 不存在。

- [ ] **Step 3: 实现确定性故障步骤**

不要用不可控长时间 sleep。通过 test clock 推进到 `actual_end_at + 14:59` 断言仍活动，再推进 1 秒触发 worker；重启 worker 后重新 claim 数据库任务。

- [ ] **Step 4: 执行故障冒烟**

Run: `python scripts/classroom_fault_smoke.py --all`

Expected: 误关恢复同一 session；断网补传无重复；服务重启不漏截止任务；MinIO 故障进入 pending_upload；截止后 completed 或 partial；ticket 重放 409/401。

- [ ] **Step 5: 提交**

```bash
git add scripts/classroom_fault_smoke.py scripts/__tests__
git commit -m "test: verify classroom fault recovery"
```

### Task 3: Nginx、SSE 和 40037 边界验证

**Files:**
- Create: `deploy/classroom/nginx/classroom.conf`
- Create: `scripts/__tests__/test_classroom_nginx_config.py`
- Modify: `deploy/classroom/docker-compose.test.yml`

**Interfaces:**
- `/classroom-api/` -> sync API，保留 Authorization，设置请求体限制和 timeout。
- `/classroom-api/v1/classrooms/*/events` 禁用 proxy buffering，保留 `text/event-stream`。
- 工作台 URL 不由该 Nginx 代理，仍直接走 BAMS HTTPS `40037`。

- [ ] **Step 1: 写配置静态测试**

断言 SSE location 含 `proxy_buffering off`，API 不允许公开 MinIO 管理端口，上传大小与服务 2 MiB 压缩限制一致。

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest scripts/__tests__/test_classroom_nginx_config.py -q`

Expected: FAIL，因为 Nginx 配置不存在。

- [ ] **Step 3: 实现代理并加入本地 Compose**

启用标准安全头；不记录 Authorization 或 query；SSE 使用单独 location。前端环境 `VITE_CLASSROOM_SYNC_PREFIX=/classroom-api`。

- [ ] **Step 4: 验证 SSE 和 URL 边界**

Run: `nginx -t -c "$PWD/deploy/classroom/nginx/classroom.conf"`（容器内）并用 curl 观察两个连续 SSE 事件；运行前端 `workbench-ticket` 测试断言没有 40002。

Expected: 配置合法、事件不缓冲、学生 URL 只包含 40037。

- [ ] **Step 5: 提交**

```bash
git add deploy/classroom/nginx scripts/__tests__/test_classroom_nginx_config.py
git commit -m "ops: proxy classroom api and events"
```

### Task 4: 真实 45 分钟课堂 soak 与盲审脚本

**Files:**
- Create: `scripts/classroom_blind_audit.md`
- Create: `scripts/classroom_soak.py`
- Create: `docs/verification/classroom-acceptance-template.md`

**Interfaces:**
- Soak produces JSON report: heartbeat latency p50/p95、evidence chunks、duplicates、missing ranges、outbox peak、final status、brief revision。
- Blind audit roles: 一名教师、两名学生、一个观察员；测试者只按页面文字操作，不读取源代码或开发说明。

- [ ] **Step 1: 写 soak 自检**

脚本拒绝少于 45 分钟的 production acceptance 模式；`--accelerated` 仅用于开发，报告显式标记不能替代真实 45 分钟验收。

- [ ] **Step 2: 执行加速预检**

Run: `python scripts/classroom_soak.py --accelerated --students 30`

Expected: 30 个学生并发心跳/上传/提交无重复；报告标记 `acceptance_valid=false`。

- [ ] **Step 3: 执行真实 45 分钟测试**

Run: `python scripts/classroom_soak.py --duration-minutes 45 --students <真实班级上限>`

Expected: `acceptance_valid=true`，无数据丢失，p95 心跳延迟和提交完成时间记录到验收模板；具体性能阈值在平台容量确认后由验收人签字，不在代码中编造。

- [ ] **Step 4: 执行盲审流程**

教师发布并提前下课一次；学生 A 误关后恢复并手动提交；学生 B 关闭页面且不手动提交，验证 +15 分钟自动收口；教师查看一份简报并下钻一条证据；观察员记录所有阻塞和误解。

- [ ] **Step 5: 提交测试文档和脚本**

```bash
git add scripts/classroom_blind_audit.md scripts/classroom_soak.py docs/verification/classroom-acceptance-template.md
git commit -m "test: define classroom blind acceptance"
```

### Task 5: 发布清单、备份和回滚 runbook

**Files:**
- Create: `scripts/classroom_release_manifest.py`
- Create: `docs/runbooks/classroom-deployment.md`
- Create: `docs/runbooks/classroom-operations.md`
- Create: `releases/classroom-platform-<date>/SHA256SUMS`

**Interfaces:**
- Manifest records: sync image digest、worker digest、plugin image digest、frontend image digest、Git SHA、OpenAPI SHA、Schema SHA、Alembic revision、build platform。
- Rollback order: disable feature -> stop ticket issuance -> restore frontend/plugin old digest -> keep sync/database read-only -> verify old paths。

- [ ] **Step 1: 写清单完整性测试**

脚本遇到 floating tag、缺 digest、dirty worktree、未提交 Schema 或 migration mismatch 必须退出非零。

- [ ] **Step 2: 运行候选清单预检**

Run: `python scripts/classroom_release_manifest.py --check-only`

Expected: 在缺少任一真实 digest/迁移信息时明确失败，不生成虚假的完整清单。

- [ ] **Step 3: 编写逐条部署和回滚 runbook**

包含：PostgreSQL 备份和恢复验证、MinIO bucket/versioning、Secret 创建、迁移 dry-run、功能开关、候选端口、SSE、40037、5179、健康检查、监控和停止条件。命令中的未知主机/端口用必须人工填写的表格字段，而不是可直接误执行的占位 shell 变量。

- [ ] **Step 4: 由第二人只按 runbook 做本地演练**

Expected: 不阅读开发上下文也能完成部署、验证和回滚；所有不明确步骤在真实部署前修正文档。

- [ ] **Step 5: 提交**

```bash
git add scripts/classroom_release_manifest.py docs/runbooks releases/classroom-platform-*/SHA256SUMS
git commit -m "docs: add classroom deployment runbook"
```

### Task 6: 真实环境只读预检与部署授权门槛

**Files:**
- Modify: `docs/verification/classroom-acceptance-template.md`（记录预检，不写 Secret）

**Interfaces:**
- Known frontend read-only target: `root@14.103.163.121`、port `5179`。
- Unknown and required: sync host/domain、PostgreSQL endpoint、MinIO endpoint/bucket、TLS certificate、BAMS template identifier、old plugin digest。

- [ ] **Step 1: 只读检查所有目标**

检查 DNS/TLS、端口占用、当前前端容器/镜像 digest、BAMS 当前模板 digest、数据库版本/容量、对象桶策略和备份空间。不得执行 stop/remove/load/migrate。

- [ ] **Step 2: 填写影响与回滚表**

明确每个动作的目标、预计中断、数据影响、旧 digest、回滚命令、验证 URL 和停止条件。

- [ ] **Step 3: 请求新的部署授权**

向用户展示候选 SHA/digest、目标、备份、回滚和预计影响。没有明确授权时，本计划在此停止。

- [ ] **Step 4: 授权后先部署数据层和关闭功能的同步服务**

顺序：数据库备份 -> migration -> MinIO 私有桶 -> sync API/worker -> readiness -> 权限负测。功能开关保持关闭，现有用户路径不改变。

- [ ] **Step 5: 记录证据**

Expected: migration revision、backup ID、sync/worker digest、健康结果和权限测试写入验收模板；任何失败立即停止，不继续插件/前端。

### Task 7: 候选插件和前端灰度部署

**Files:**
- Modify: `docs/verification/classroom-acceptance-template.md`

**Interfaces:**
- Plugin: 使用新不可变 digest 创建测试工作台，不就地修改正在演示的容器。
- Frontend: 先使用未占用候选端口运行新镜像；验证后才替换 `lab-platform-frontend` 的 `5179:80`。
- Feature rollout: 仅测试课程 allowlist 开启。

- [ ] **Step 1: 部署插件候选到测试模板**

验证持久卷、平台模式、sync 出网、40037 URL、fragment 清理、学生写保护和旧普通 Notebook 模式。

- [ ] **Step 2: 在候选端口运行前端镜像**

只读确认候选端口未被占用；运行候选后验证教师/学生登录、旧实验路径、新功能关闭和打开两种状态。候选失败时删除的只能是确切候选容器，旧 5179 不动。

- [ ] **Step 3: 替换 5179 并保留旧 digest**

停止并移除确切的旧前端容器，使用候选 digest 重新创建 `5179:80`。立即验证本机和外部 `/admin/projects`、`/student/projects`、classroom routes 和 API 代理。

- [ ] **Step 4: 只对测试课程开启并执行盲审**

完成 Task 4 的教师、学生 A、学生 B 场景。任何权限、提交、恢复或旧页面回归失败，立即关闭 allowlist 并执行回滚。

- [ ] **Step 5: 记录或回滚**

成功时记录最终 digest、时间、验收人和所有结果；失败时恢复旧前端和插件 digest，保持同步服务数据只读以供诊断，不删除候选镜像和证据。

### Task 8: 生产观察期与最终完成门槛

**Files:**
- Modify: `docs/runbooks/classroom-operations.md`
- Modify: `docs/verification/classroom-acceptance-template.md`

**Interfaces:**
- Metrics: active sessions、heartbeat lag、outbox depth、evidence failures、deadline lag、completed/partial ratio、SSE fallback、object storage errors。
- Alerts: deadline lag > 60 seconds、outbox oldest > 5 minutes、evidence 5xx 持续、跨课程 403 异常峰值、存储余量不足。

- [ ] **Step 1: 观察至少一个完整测试课程周期**

记录上课前、课堂中、实际下课、+15 分钟和容器释放后的指标与页面状态。

- [ ] **Step 2: 验证数据保留和容器释放**

释放学生测试容器后，教师仍能查看简报和已经上传的证据；BAMS 持久卷和对象存储中的数据关系可追溯。

- [ ] **Step 3: 执行一次真实回滚演练**

关闭功能并恢复旧前端/插件 digest，验证普通 Notebook 和原课程实验；随后只有在再次确认后重新启用候选。

- [ ] **Step 4: 汇总未覆盖范围**

明确未验证的最大班级规模、学校数据保留政策、外部 AI 限制和任何 BAMS 运维依赖，不把它们隐藏在“已完成”中。

- [ ] **Step 5: 完成判定**

仅当总计划“最终完成定义”的全部证据存在且无未解决 P0/P1 问题时，标记真实课堂平台完成；否则准确标记为“实现完成、生产验收未完成”或具体阶段状态。
