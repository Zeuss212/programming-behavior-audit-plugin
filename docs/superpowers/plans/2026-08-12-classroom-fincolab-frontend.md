# FinColab 教师与学生端课堂闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Vue 前端中增加教师方案发布、课堂巡视、单份学生简报，以及学生接受任务、恢复和提交状态，同时保持现有演示流程在功能开关关闭时完全可用。

**Architecture:** 新功能放入独立 `modules/classroom-monitoring`，API、类型、组合式状态和展示组件分离；现有 Admin/Student 页面只增加小型入口与状态投影。同步服务使用独立 `VITE_CLASSROOM_SYNC_PREFIX`，认证沿用当前 Bearer Token；工作台 URL 仍由现有 BAMS API 解析，再追加 fragment 票据并统一规范为 `https://14.103.139.131:40037`。

**Tech Stack:** Vue 3.5、TypeScript 6、Vite 8、Vue Router 5、Pinia 3、Element Plus 2、Vitest 4、Vue Test Utils、Axios、CSS design tokens。

## Global Constraints

- 实施仓库：`.worktrees/lab-platform-frontend` 的独立 `codex/classroom-ui` worktree/分支；不得修改主仓库插件文件。
- `VITE_CLASSROOM_MONITORING_ENABLED=false` 是默认值；关闭时不增加可见菜单、按钮或启动阻塞。
- 同步服务授权由服务端校验；现有前端基于用户名的角色推断不能成为安全边界。
- 学生不能看到或调用方案创建、发布、知识点修改和 AI Key 配置。
- 教师发布方案绑定 parent experiment；学生任务绑定 child algorithm/workbench。
- 进入工作台统一使用 `40037` HTTPS，票据追加为 fragment `behavior_ticket`，不能放 query。
- 状态必须同时使用文字和图标，不只用颜色；交互可键盘完成，焦点顺序稳定。
- 所有页面实现 loading、error、empty、partial data 状态，并验证 320/768/1024/1440 px。
- 现有 `/admin/projects`、`/student/projects`、`/student/launch/...` 和实验创建/环境状态测试不得回归。

---

## File Map

```text
src/modules/classroom-monitoring/
  types.ts                          # 生成共享类型的前端薄封装
  api.ts                            # 同步服务 API
  feature.ts                        # 功能开关和能力判断
  workbench-ticket.ts               # 40037 URL + fragment
  plan-draft.ts                     # 三步向导本地校验/转换
  use-plan-editor.ts                # 草稿、发布和错误状态
  use-classroom-monitor.ts          # SSE、轮询降级、筛选
  use-student-assignment.ts         # 接受、ticket、提交状态
  components/PlanWizard.vue
  components/KnowledgePointEditor.vue
  components/ClassroomStatusSummary.vue
  components/ClassroomStudentTable.vue
  components/StudentBriefPanel.vue
  components/EvidenceDrawer.vue
src/views/admin/AdminClassroomPlanView.vue
src/views/admin/AdminClassroomMonitorView.vue
src/views/admin/AdminStudentBriefView.vue
src/views/student/StudentAssignmentView.vue
src/views/student/StudentSubmissionView.vue
```

### Task 1: 功能开关、同步服务客户端和生成类型

**Files:**
- Create: `src/modules/classroom-monitoring/types.ts`
- Create: `src/modules/classroom-monitoring/api.ts`
- Create: `src/modules/classroom-monitoring/feature.ts`
- Create: `src/modules/classroom-monitoring/__tests__/api.test.ts`
- Modify: `src/config/app-config.ts`

**Interfaces:**
- Produces: `classroomMonitoringEnabled: boolean`；`classroomSyncPrefix: string`。
- API methods: `getExperimentPlan`、`savePlanDraft`、`publishPlan`、`listStudentAssignments`、`acceptAssignment`、`issueWorkbenchTicket`、`getClassroomMonitoring`、`getStudentBrief`、`submitTeacherReview`。
- Errors: 保留同步服务稳定 `error.code`、`retryable` 和 `request_id`，不只转换成纯字符串。

- [ ] **Step 1: 写功能关闭与错误映射失败测试**

测试默认环境下 `isClassroomMonitoringEnabled() === false`；403 `student_capability_forbidden` 保留 code；503 显示可重试。

- [ ] **Step 2: 验证测试失败**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/api.test.ts`

Expected: FAIL，因为 module 不存在。

- [ ] **Step 3: 实现独立 Axios 客户端**

复用 `getAccessToken()`，base URL 为 `VITE_CLASSROOM_SYNC_PREFIX`，默认 `/classroom-api`。不要改现有 BAMS `src/api/request.ts` 的 baseURL，以免混淆两个后端。

- [ ] **Step 4: 运行 API、类型和现有请求测试**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/api.test.ts src/api/__tests__/environment.test.ts && npm run type-check`

Expected: 开关关闭、Bearer Token、错误结构和共享类型全部通过。

- [ ] **Step 5: 提交**

```bash
git add src/config/app-config.ts src/modules/classroom-monitoring
git commit -m "feat: add classroom monitoring client"
```

### Task 2: 教师三步方案向导和发布入口

**Files:**
- Create: `src/modules/classroom-monitoring/plan-draft.ts`
- Create: `src/modules/classroom-monitoring/use-plan-editor.ts`
- Create: `src/modules/classroom-monitoring/components/PlanWizard.vue`
- Create: `src/modules/classroom-monitoring/components/KnowledgePointEditor.vue`
- Create: `src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts`
- Create: `src/views/admin/AdminClassroomPlanView.vue`
- Modify: `src/views/admin/AdminProjectsView.vue`

**Interfaces:**
- Route input: `courseId`、`parentAlgorithmId`。
- Wizard steps: `problem`、`knowledge_points`、`schedule_and_publish`。
- Produces Profile v2 draft with canonical knowledge point order, confirmations hashes and schedule metadata sent separately to sync service.

- [ ] **Step 1: 写完整输入仍可发布的回归测试**

该测试复现此前“明明输入完整却提示缺项”：知识点名称、说明、观察问题、支持表现、排除情况全部由响应式 model 更新后，`发布方案` 可用且提交一次；错误提示必须定位具体字段。

- [ ] **Step 2: 运行并确认失败**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts`

Expected: FAIL，因为向导不存在。

- [ ] **Step 3: 实现三步向导**

使用一个 `reactive<PlanDraftForm>` 作为唯一真源，不从 DOM 或不同子组件复制校验状态。AI 建议只写入草稿并标记 source，不自动发布。发布前显示不可变版本、上下课时间和学生自动提交规则。

- [ ] **Step 4: 验证表单、键盘与响应式**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts src/views/admin/__tests__/AdminProjectsView.test.ts && npm run type-check`

Expected: 完整输入发布、缺项定位、草稿恢复、Enter/Tab、重复点击和 409 冲突全部通过；功能开关关闭时 AdminProjects DOM 不变。

- [ ] **Step 5: 提交**

```bash
git add src/modules/classroom-monitoring src/views/admin
git commit -m "feat: let teachers publish classroom plans"
```

### Task 3: 教师课堂巡视、提前下课和 SSE 降级

**Files:**
- Create: `src/modules/classroom-monitoring/use-classroom-monitor.ts`
- Create: `src/modules/classroom-monitoring/components/ClassroomStatusSummary.vue`
- Create: `src/modules/classroom-monitoring/components/ClassroomStudentTable.vue`
- Create: `src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts`
- Create: `src/views/admin/AdminClassroomMonitorView.vue`
- Modify: `src/constants/menu.ts`
- Modify: `src/constants/route-names.ts`
- Modify: `src/router/index.ts`

**Interfaces:**
- Route: `/admin/classrooms/:classroomId/monitoring`。
- `useClassroomMonitor` uses authenticated `fetch` plus `ReadableStream` to consume SSE `/v1/classrooms/{id}/events`，从而携带现有 Bearer Token；不得使用不能设置 Authorization header 的原生 `EventSource`。连续三次连接失败后每 10 秒轮询 `GET monitoring`，成功重连流后停止轮询。
- `endClassroomEarly(actual_end_at)` requires confirmation and refreshes displayed cutoff.

- [ ] **Step 1: 写状态表和 SSE 降级测试**

覆盖八种状态、状态文字+图标、排序、带 Authorization 的 SSE 更新、三次失败后轮询、页面卸载 abort stream/清理 timer、提前下课二次确认。

- [ ] **Step 2: 验证测试失败**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts`

Expected: FAIL，因为 monitoring module 不存在。

- [ ] **Step 3: 实现聚焦课堂表格**

桌面显示学生、状态、最后活动、掌握进度、提交情况和操作；窄屏每名学生变为语义化 definition list，不用横向挤压所有列。活动刷新保留当前筛选和焦点。

- [ ] **Step 4: 验证路由、清理和无障碍**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts src/router/__tests__ && npm run type-check`

Expected: 功能开关控制菜单和路由；所有 button 可键盘操作；状态有可读文字；无 timer、fetch stream 或 AbortController 泄漏。

- [ ] **Step 5: 提交**

```bash
git add src/modules/classroom-monitoring src/views/admin/AdminClassroomMonitorView.vue src/constants src/router
git commit -m "feat: add teacher classroom monitoring"
```

### Task 4: 单份学生简报、教师复核和证据抽屉

**Files:**
- Create: `src/modules/classroom-monitoring/components/StudentBriefPanel.vue`
- Create: `src/modules/classroom-monitoring/components/EvidenceDrawer.vue`
- Create: `src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts`
- Create: `src/views/admin/AdminStudentBriefView.vue`
- Modify: `src/router/index.ts`

**Interfaces:**
- Route: `/admin/classrooms/:classroomId/students/:sessionId/brief`。
- Brief renders top summary, knowledge point matrix, process overview, up to three issues and teacher review overlay.
- Evidence drawer first calls directory endpoint; only after explicit click requests presigned item URL. It never preloads all raw logs.

- [ ] **Step 1: 写教学可读性和权限错误测试**

断言默认只出现一份简报，不显示三个下载卡片；`not_demonstrated` 中文为“尚未证明”而非“未掌握”；403 不显示对象 URL；教师修改必须填写理由。

- [ ] **Step 2: 运行并确认失败**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts`

Expected: FAIL，因为简报组件不存在。

- [ ] **Step 3: 实现分层简报**

顶部摘要先回答掌握数量、主要问题、完整度和是否复核；知识点按 Profile 顺序；证据引用使用按钮并带可读 `aria-label`；教师复核作为单独标记，不覆盖机器原结论。

- [ ] **Step 4: 验证空/部分/修订状态**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts && npm run type-check`

Expected: complete、partial、pending_upload、AI 未完成、revision 更新和证据 404/403 状态都能清楚表达。

- [ ] **Step 5: 提交**

```bash
git add src/modules/classroom-monitoring src/views/admin/AdminStudentBriefView.vue src/router/index.ts
git commit -m "feat: show classroom student briefs"
```

### Task 5: 学生任务接受与 40037 票据入口

**Files:**
- Create: `src/modules/classroom-monitoring/workbench-ticket.ts`
- Create: `src/modules/classroom-monitoring/use-student-assignment.ts`
- Create: `src/modules/classroom-monitoring/__tests__/student-assignment.test.ts`
- Create: `src/views/student/StudentAssignmentView.vue`
- Modify: `src/views/student/StudentProjectsView.vue`
- Modify: `src/views/student/StudentLaunchView.vue`
- Modify: `src/router/index.ts`

**Interfaces:**
- `buildTicketedWorkbenchUrl(rawUrl, ticket) -> string` preserves BAMS base path and non-ticket fragment entries, forces scheme/host/port to `https://14.103.139.131:40037`, adds `behavior_ticket` only to fragment.
- Student flow: assignment details -> explicit acceptance -> ticket -> `window.location.assign(url)`；ticket failure never opens bare student-mode workbench.

- [ ] **Step 1: 写 40037 URL 和接受流程失败测试**

覆盖 raw URL 为 40002、相对路径、已有 token query、已有 fragment、中文字符、空 ticket；断言生成 URL 不含 query `behavior_ticket`，且始终是 HTTPS 40037。

- [ ] **Step 2: 运行并确认失败**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/student-assignment.test.ts src/router/__tests__/student-launch-route.test.ts`

Expected: FAIL，因为 ticket builder 和 assignment flow 不存在。

- [ ] **Step 3: 实现接受页和原流程兼容**

确认页展示题目、知识点、采集范围、上下课时间、AI 规则、教师查看范围和下课后 15 分钟自动提交。开关关闭时继续调用现有 `loadStudentExperimentRuntime` 和原打开逻辑；开关开启且存在任务时必须先接受。

- [ ] **Step 4: 验证入口和回归**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/student-assignment.test.ts src/views/student/__tests__/StudentProjectsView.test.ts src/views/student/__tests__/StudentLaunchView.test.ts src/router/__tests__/student-launch-route.test.ts && npm run type-check`

Expected: 40037、fragment、错误重试、功能关闭兼容和既有环境状态测试全部通过。

- [ ] **Step 5: 提交**

```bash
git add src/modules/classroom-monitoring src/views/student src/router/index.ts
git commit -m "feat: let students accept monitored assignments"
```

### Task 6: 学生提交状态与实验记录

**Files:**
- Create: `src/views/student/StudentSubmissionView.vue`
- Create: `src/modules/classroom-monitoring/__tests__/StudentSubmissionView.test.ts`
- Modify: `src/views/student/StudentRecordsView.vue`
- Modify: `src/router/index.ts`

**Interfaces:**
- Route: `/student/assignments/:assignmentId/submission`。
- Student sees `监控中`、`暂时离线`、`简报提交中`、`简报已提交，证据补传中`、`已提交`、`部分提交`，以及 last sync/submitted time；不能查看教师内部复核或其他学生信息。

- [ ] **Step 1: 写状态与隐私失败测试**

断言页面不会渲染其他 student ID、教师评语草稿、对象存储 key 或 presigned URL；终态解释手动/自动原因和完整度。

- [ ] **Step 2: 运行并确认失败**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/StudentSubmissionView.test.ts`

Expected: FAIL，因为提交状态页不存在。

- [ ] **Step 3: 实现轮询和记录入口**

活动会话 10 秒轮询，终态停止；网络错误保留最后成功快照并显示“状态暂未更新”，不把未知状态误显示为失败。实验记录只增加当前学生自己的简报入口。

- [ ] **Step 4: 验证终态停止与隐私**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/StudentSubmissionView.test.ts && npm run type-check`

Expected: 所有状态、timer 清理、错误恢复和数据最小化通过。

- [ ] **Step 5: 提交**

```bash
git add src/views/student src/modules/classroom-monitoring src/router/index.ts
git commit -m "feat: show student classroom submissions"
```

### Task 7: 前端完整门禁、视觉与功能关闭回归

**Files:**
- Modify: `src/assets/main.css`
- Create: `src/modules/classroom-monitoring/__tests__/feature-off-regression.test.ts`
- Modify: `README.md`

**Interfaces:**
- Produces documented env: `VITE_CLASSROOM_MONITORING_ENABLED`、`VITE_CLASSROOM_SYNC_PREFIX`。
- Visual: 复用现有颜色、间距、字体和圆角 token；不加入紫色渐变、重阴影或无意义卡片网格。

- [ ] **Step 1: 写功能关闭 DOM 快照测试**

开关关闭时教师菜单、学生项目、launch 路由行为与现有基线一致；同步服务不可用不影响旧实验入口。

- [ ] **Step 2: 运行只读质量门禁**

```bash
npx --no-install oxlint .
npx --no-install eslint .
npm run type-check
npm test -- --run
npm run build
```

Expected: 全部退出 0；不运行会自动修复文件的 `npm run lint` 作为最终证据。

- [ ] **Step 3: 执行浏览器尺寸和键盘检查**

在本地 mock 同步服务下依次验证 320、768、1024、1440 px；从页面标题开始只使用 Tab/Shift+Tab/Enter/Space 完成向导、接受、筛选、简报下钻和关闭抽屉；运行 axe 检查无 serious/critical 问题。

- [ ] **Step 4: 构建本地候选镜像但停止在部署门槛**

Run: `docker build --platform linux/amd64 -t lab-platform-frontend:classroom-candidate .`

Expected: 镜像构建成功。记录 digest，到此停止；不执行 save、scp、push、删除或替换 `5179` 容器。

- [ ] **Step 5: 提交**

```bash
git add src README.md
git commit -m "test: verify classroom frontend workflows"
```
