# Jupyter 学生监控接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Jupyter 插件改造成由平台任务驱动的学生执行端，安全领取固定方案、恢复误关会话、增量上传证据，并完成手动或自动单份简报提交。

**Architecture:** 浏览器扩展只负责当前页面的事件采集和 IndexedDB 耐久队列；Jupyter Server 扩展持有平台短期令牌、会话上下文、证据发件箱和截止状态。学生模式由服务端环境配置和已验证平台会话共同决定，前端不能自行切换；原本地 Pilot 模式保留在功能开关关闭时，便于回滚。

**Tech Stack:** TypeScript 5.5、JupyterLab 4、Jest、fake-indexeddb、Python 3.10+、Jupyter Server 2、Tornado、pytest、标准库 urllib/http.client 或已审核的服务端 HTTP 依赖。

## Global Constraints

- Consumes OpenAPI/Schema: `services/classroom-sync/openapi.json` 与 `contracts/classroom/v1/`。
- `JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE=student` 时服务端禁止方案写入、发布、AI Key 读写和教师分析辅助；不能只隐藏 UI。
- 一次性票据只从 URL fragment 读取，交换请求结束后立即调用 `history.replaceState` 清除；不得写 localStorage、日志或异常文本。
- 页面关闭不 finalize；证据截止前恢复已有 `collecting`/`temporarily_offline` 会话。
- 浏览器未获服务端 receipt 的事件保存在 IndexedDB；Jupyter Server 未获同步服务 receipt 的证据保存在持久卷 outbox。
- 证据截止后不接受新事件；只允许上传截止前已持久化的块。
- 手动和自动提交共用同一个幂等 finalize 协调器；AI 失败不阻塞基础简报。
- 保留原三类本地日志生成能力，但平台最终只提交一份结构化简报和证据清单。

---

## File Map

```text
src/platform/
  models.ts                         # 生成/薄封装的共享契约类型
  classroomApi.ts                   # 浏览器到本地 Jupyter API
  ticketBootstrap.ts                # fragment 读取、交换和清理
  platformSessionController.ts      # 接受、开始、恢复、提交状态机
  studentCapabilities.ts            # 学生 UI 能力投影
src/durableSegmentStore.ts          # IndexedDB 队列扩展
src/behaviorCapture.ts              # 恢复与截止拦截
src/behaviorEventUploader.ts        # receipt、补传和缺失区间
src/ui/behaviorAnalysisSidebar.ts   # 学生状态页；教师控件条件隔离
myextension/platform_config.py      # 环境配置、能力和截止规则
myextension/platform_client.py      # 同步服务 HTTP 客户端
myextension/platform_context_store.py # token、assignment、session 本地安全状态
myextension/evidence_outbox.py      # 持久证据发件箱
myextension/submission_coordinator.py # 统一手动/自动提交
myextension/platform_deadline_worker.py # 服务端截止触发
myextension/routes.py               # 本地 platform 路由和学生端写保护
myextension/api_schemas/platform-*.json
```

### Task 1: 生成契约类型和学生能力模型

**Files:**
- Create: `src/platform/models.ts`
- Create: `src/platform/studentCapabilities.ts`
- Create: `src/__tests__/studentCapabilities.spec.ts`
- Create: `myextension/platform_config.py`
- Create: `myextension/tests/test_platform_config.py`

**Interfaces:**
- Produces TS: `PlatformCapabilities` 与 `capabilitiesForMode(mode: 'local' | 'student'): PlatformCapabilities`。
- Produces Python: `PlatformConfig.from_env(env) -> PlatformConfig`；`student_mode`、`sync_base_url`、`log_root`、`deadline_poll_seconds`。
- Student capabilities fixed false: `canAuthorPlan`、`canPublishPlan`、`canConfigureAi`、`canUseAssessmentAssist`。

- [ ] **Step 1: 写前后端失败测试**

```ts
expect(capabilitiesForMode('student')).toEqual({
  canAuthorPlan: false,
  canPublishPlan: false,
  canConfigureAi: false,
  canUseAssessmentAssist: false,
  canCapture: true,
  canSubmit: true
});
```

Python 测试断言 student 模式缺少 HTTPS `sync_base_url` 时拒绝启动，测试环境只允许显式的 `http://127.0.0.1`。

- [ ] **Step 2: 验证测试失败**

Run: `jlpm test src/__tests__/studentCapabilities.spec.ts --runInBand && python -m pytest myextension/tests/test_platform_config.py -q`

Expected: FAIL，因为能力和配置模块不存在。

- [ ] **Step 3: 实现关闭式能力投影与严格配置**

`local` 保持当前功能；`student` 只开放采集、恢复、提交、查看状态。共享枚举由生成脚本输出，手写文件只添加 Jupyter 层辅助类型。

- [ ] **Step 4: 运行类型与配置测试**

Run: `jlpm test src/__tests__/studentCapabilities.spec.ts --runInBand && python -m pytest myextension/tests/test_platform_config.py -q`

Expected: 全部通过；未知 mode 默认拒绝而非回落到教师能力。

- [ ] **Step 5: 提交**

```bash
git add src/platform src/__tests__/studentCapabilities.spec.ts myextension/platform_config.py myextension/tests/test_platform_config.py
git commit -m "feat: define platform student capabilities"
```

### Task 2: 一次性票据引导和本地平台上下文

**Files:**
- Create: `src/platform/ticketBootstrap.ts`
- Create: `src/platform/classroomApi.ts`
- Create: `src/__tests__/ticketBootstrap.spec.ts`
- Create: `myextension/platform_context_store.py`
- Create: `myextension/platform_client.py`
- Create: `myextension/tests/test_platform_registration.py`
- Modify: `myextension/routes.py`

**Interfaces:**
- Browser: `consumeClassroomTicket(location, history) -> string | null`，只接受 fragment 参数 `behavior_ticket`。
- Local route: `POST /myextension/platform/register` body `{schema_version:1,ticket:string,plugin_instance_id:string}`。
- Context store: `save_registered_context(credentials)` 原子写 `platform-context.json`，权限 0600；不保存明文一次性票据。

- [ ] **Step 1: 写 fragment 清理和重放失败测试**

测试含其他 hash 参数时只移除 `behavior_ticket`；错误响应也必须清除 ticket；Python 测试断言客户端不在日志中输出 ticket，第二次注册映射稳定的 409。

- [ ] **Step 2: 运行并确认失败**

Run: `jlpm test src/__tests__/ticketBootstrap.spec.ts --runInBand && python -m pytest myextension/tests/test_platform_registration.py -q`

Expected: FAIL，因为 bootstrap、route 和 client 尚不存在。

- [ ] **Step 3: 实现注册链路**

浏览器先把 ticket 保存在函数局部变量，立即 `replaceState`，随后调用本地 route。本地 route 将 ticket 发送到同步服务 `/v1/monitor-sessions/register`，验证响应 Schema 后保存 assignment、plan version、session、截止时间和插件 JWT。

- [ ] **Step 4: 验证安全清理和上下文权限**

Run: `jlpm test src/__tests__/ticketBootstrap.spec.ts --runInBand && python -m pytest myextension/tests/test_platform_registration.py -q`

Expected: URL、localStorage、持久文件和日志均无 ticket；上下文文件 mode 为 0600。

- [ ] **Step 5: 提交**

```bash
git add src/platform src/__tests__/ticketBootstrap.spec.ts myextension
git commit -m "feat: register jupyter classroom sessions"
```

### Task 3: 服务端强制学生模式和方案只读快照

**Files:**
- Modify: `myextension/routes.py`
- Create: `myextension/api_schemas/platform-context-response-v1.json`
- Create: `myextension/tests/test_student_mode_routes.py`
- Modify: `src/index.ts`
- Modify: `src/ui/behaviorAnalysisSidebar.ts`
- Create: `src/__tests__/studentModeSidebar.spec.ts`

**Interfaces:**
- Local routes: `GET /myextension/platform/context`；`POST /myextension/platform/context/refresh`。
- In student mode returns fixed Profile v2 snapshot and capabilities; existing profile POST/PUT/publish、assessment-assist、AI config mutations return 403 `student_capability_forbidden`。
- UI consumes capabilities from server; no local query parameter or DOM state can enable teacher controls.

- [ ] **Step 1: 写服务端 403 和 UI 不渲染测试**

Python 参数化测试覆盖每个教师写接口；Jest 断言 student context 下 DOM 不含“创建考核方案”“发布”“保存 AI 配置”“API 密钥”。

- [ ] **Step 2: 验证现状失败**

Run: `python -m pytest myextension/tests/test_student_mode_routes.py -q && jlpm test src/__tests__/studentModeSidebar.spec.ts --runInBand`

Expected: FAIL，现有接口仍开放且侧边栏仍显示教师控件。

- [ ] **Step 3: 实现服务端 guard 和聚焦学生 UI**

在 `PilotAPIHandler` 增加 `require_capability(name)`，所有写接口显式调用。学生 UI 只显示任务标题、监控状态、最后同步、恢复提示、截止时间和提交按钮；本地模式 DOM 保持现有行为。

- [ ] **Step 4: 回归本地模式与学生模式**

Run: `python -m pytest myextension/tests/test_student_mode_routes.py myextension/tests/test_assessment_assist_api.py myextension/tests/test_routes.py -q && jlpm test src/__tests__/studentModeSidebar.spec.ts src/__tests__/assessmentPlanEditor.spec.ts --runInBand`

Expected: student 全部禁用，本地 Pilot 原功能测试不回归。

- [ ] **Step 5: 提交**

```bash
git add myextension src/index.ts src/ui/behaviorAnalysisSidebar.ts src/__tests__/studentModeSidebar.spec.ts
git commit -m "feat: enforce jupyter student mode"
```

### Task 4: 恢复同一会话和证据截止拦截

**Files:**
- Create: `src/platform/platformSessionController.ts`
- Create: `src/__tests__/platformSessionController.spec.ts`
- Modify: `src/behaviorCapture.ts`
- Modify: `src/behaviorEventUploader.ts`
- Modify: `src/durableSegmentStore.ts`
- Modify: `myextension/session_store.py`
- Create: `myextension/tests/test_platform_session_recovery.py`

**Interfaces:**
- Produces: `PlatformSessionController.bootstrap() -> Promise<SessionBootstrapResult>`；结果 `created | resumed | terminal`。
- Server: `recover_platform_session(assignment_id, plan_hash, now) -> session`；只在 `now <= evidence_cutoff_at` 且身份/方案相同时恢复。
- IndexedDB key changes from单一 active session to `assignment_id + monitor_session_id`，迁移现有 key 时不丢队列。

- [ ] **Step 1: 写误关恢复和截止后拒绝测试**

模拟 37 个未回执事件、页面重新加载和同一服务端会话；断言不创建第二个 session、从序号 38 继续。模拟截止后重开，断言返回 terminal 且新事件不入队。

- [ ] **Step 2: 运行并确认失败**

Run: `jlpm test src/__tests__/platformSessionController.spec.ts src/__tests__/durableSegmentStore.spec.ts --runInBand && python -m pytest myextension/tests/test_platform_session_recovery.py -q`

Expected: FAIL，因为平台恢复键和截止规则不存在。

- [ ] **Step 3: 实现恢复与队列迁移**

服务端是会话身份真源；浏览器队列只在 session ID、assignment ID 和 plan hash 全部匹配时接续。时间比较统一使用 UTC ISO-8601，发生时钟偏差时以同步服务下发的 server time 为准。

- [ ] **Step 4: 验证刷新、断网和截止边界**

Run: `jlpm test src/__tests__/platformSessionController.spec.ts src/__tests__/durableSegmentStore.spec.ts src/__tests__/behaviorEventUploader.spec.ts --runInBand && python -m pytest myextension/tests/test_platform_session_recovery.py myextension/tests/test_session_store.py -q`

Expected: 同一会话恢复、未回执事件补传、不同任务隔离、截止后只读全部通过。

- [ ] **Step 5: 提交**

```bash
git add src myextension/session_store.py myextension/tests/test_platform_session_recovery.py
git commit -m "feat: resume interrupted classroom sessions"
```

### Task 5: 服务端持久证据发件箱

**Files:**
- Create: `myextension/evidence_outbox.py`
- Create: `myextension/tests/test_evidence_outbox.py`
- Modify: `myextension/session_log_artifacts.py`
- Modify: `myextension/__init__.py`

**Interfaces:**
- Produces: `EvidenceOutbox.enqueue(session_id, chunk) -> OutboxEntry`；`flush_once(limit=20) -> FlushReport`；`recover_pending() -> list[OutboxEntry]`。
- Layout: `<log_root>/platform-outbox/{session_id}/{sequence:08d}-{sha256}.json` + 原子状态文件。
- Retry: 1、2、4、8、16、30 秒上限并加入 0–20% jitter；认证失败暂停该会话并刷新 token，4xx 非重试错误进入 quarantine，不删除源日志。

- [ ] **Step 1: 写崩溃恢复和幂等 receipt 测试**

覆盖临时文件、重复 enqueue、进程在上传成功但写 receipt 前退出、S3/API 503、401 刷新和永久 409。

- [ ] **Step 2: 确认测试失败**

Run: `python -m pytest myextension/tests/test_evidence_outbox.py -q`

Expected: FAIL，因为 outbox 不存在。

- [ ] **Step 3: 实现原子发件箱和后台线程**

文件先写临时路径、fsync、rename；同步服务的相同序号/哈希幂等语义允许崩溃后重试。后台线程启动时扫描 pending，关闭时只停止领取新任务，不删除未完成项。

- [ ] **Step 4: 验证重启与背压**

Run: `python -m pytest myextension/tests/test_evidence_outbox.py myextension/tests/test_session_log_artifacts.py -q`

Expected: 重启后所有 pending 可恢复，队列上限触发本地告警但原始会话日志不丢失。

- [ ] **Step 5: 提交**

```bash
git add myextension
git commit -m "feat: persist classroom evidence uploads"
```

### Task 6: 统一手动/自动提交协调器

**Files:**
- Create: `myextension/submission_coordinator.py`
- Create: `myextension/platform_deadline_worker.py`
- Create: `myextension/tests/test_submission_coordinator.py`
- Modify: `myextension/routes.py`
- Modify: `myextension/__init__.py`
- Modify: `src/platform/platformSessionController.ts`
- Modify: `src/ui/behaviorAnalysisSidebar.ts`

**Interfaces:**
- Produces: `SubmissionCoordinator.submit(session_id, reason, cutoff_at) -> SubmissionResult`。
- Local route: `POST /myextension/platform/sessions/{id}/submit` body `{schema_version:1,reason:'student_manual'}`。
- Deadline worker polls local contexts every 30 seconds and calls the same coordinator with `system_deadline`; duplicate calls return existing result.

- [ ] **Step 1: 写提交竞态和 AI 故障测试**

覆盖学生点击与 deadline 同时触发、最后队列 draining、缺失区间、AI worker 超时、同步服务暂时不可用和重复提交。

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest myextension/tests/test_submission_coordinator.py -q && jlpm test src/__tests__/platformSessionController.spec.ts --runInBand`

Expected: FAIL，因为统一协调器不存在。

- [ ] **Step 3: 实现单锁幂等收口**

协调器按 session 文件锁串行：冻结 cutoff、flush 本地段、finalize SessionStore、生成确定性 classroom brief、固化证据清单、enqueue 未上传证据、提交简报。AI job 仅在基础提交事务后异步更新 revision。

- [ ] **Step 4: 验证所有终态**

Run: `python -m pytest myextension/tests/test_submission_coordinator.py myextension/tests/test_session_store.py myextension/tests/test_classroom_release.py -q && jlpm test src/__tests__/platformSessionController.spec.ts src/__tests__/behaviorCapture.spec.ts --runInBand`

Expected: 每个 session 一个逻辑提交；网络失败进入 pending_upload；不可恢复缺失进入 partial；AI 失败仍 completed/partial。

- [ ] **Step 5: 提交**

```bash
git add myextension src/platform src/ui/behaviorAnalysisSidebar.ts
git commit -m "feat: submit classroom sessions reliably"
```

### Task 7: 插件完整回归、构建和本地课堂镜像

**Files:**
- Modify: `README.md`
- Create: `deploy/bluedot/release-0.4.0/Dockerfile`
- Create: `deploy/bluedot/release-0.4.0/runtime.env.example`
- Create: `deploy/bluedot/release-0.4.0/build_image.sh`
- Create: `deploy/bluedot/release-0.4.0/verify_image.sh`
- Create: `deploy/bluedot/release-0.4.0/README.md`

**Interfaces:**
- Produces: 本地候选 wheel 和 Linux AMD64 classroom image；版本号由实际发布门槛确认，计划以 `0.4.0` 作为下一兼容版本。
- Runtime env includes `PLATFORM_MODE`、`SYNC_BASE_URL`、`LOG_DIR`、`DEADLINE_POLL_SEC`；JWT/AI/S3 Secret 不进入镜像或示例文件。

- [ ] **Step 1: 增加镜像 smoke 失败测试**

`verify_image.sh` 必须验证 JupyterLab 4、Jupyter Server 2、插件版本、学生模式 capability、持久目录可写和无内置 Secret。

- [ ] **Step 2: 执行完整源代码门禁**

```bash
python -m pytest myextension/tests -q
jlpm lint:check
jlpm test --runInBand
jlpm build:prod
```

Expected: 全部退出 0；若基线失败，保存与实施前基线的差异并停止扩大范围。

- [ ] **Step 3: 构建 wheel 和本地镜像**

Run: `python -m build && deploy/bluedot/release-0.4.0/build_image.sh "$BAMS_BASE_IMAGE" behavior-audit:0.4.0-classroom`

Expected: wheel 与镜像构建成功，构建日志不包含 Secret。

- [ ] **Step 4: 验证镜像但不推送部署**

Run: `deploy/bluedot/release-0.4.0/verify_image.sh behavior-audit:0.4.0-classroom`

Expected: 版本、能力、extension list 和持久目录全部通过。到此停止，不执行 `docker push`、`scp` 或 BAMS 模板替换。

- [ ] **Step 5: 提交**

```bash
git add README.md deploy/bluedot/release-0.4.0 package.json myextension src
git commit -m "build: package classroom student plugin"
```
