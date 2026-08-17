# Classroom Frontend Page Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已有实验之上交付教师发布课堂方案、学生接受并从 40037 进入工作台、教师查看单份简报的可验证前端闭环。

**Architecture:** 先在当前 `classroom-platform` worktree 为同步服务增加最小的权限受控读取面，再在 `lab-platform-frontend` 新建隔离 worktree 实现独立 `classroom-monitoring` 模块。既有实验/BAMS API client 保持不变；课堂 API 走 `/classroom-api`，功能开关默认关闭。

**Tech Stack:** FastAPI、SQLAlchemy 2、Pytest、Vue 3.5、TypeScript 6、Vite 8、Vitest 4、Vue Test Utils、Axios、Element Plus 2。

## Global Constraints

- 不修改带有用户未提交文件的 `lab-platform-frontend` 工作区；执行前创建新的 `codex/classroom-ui` linked worktree。
- 当前 Python 工作在 `/Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/classroom-platform`；前端提交与它分开。
- `VITE_CLASSROOM_MONITORING_ENABLED=false` 为默认值，关闭时 `/admin/projects`、`/student/projects`、`/student/launch/...` 和创建实验流程不变。
- 教师方案绑定 `space_id + parent_algorithm_id`；学生只读取并操作自己的 assignment。
- 所有工作台链接强制为 `https://14.103.139.131:40037`，票据只能写入 fragment 的 `behavior_ticket`，不得写入 query 或页面正文。
- 同步服务是授权边界；前端不把用户名/隐藏按钮当作权限控制。
- 页面须有 loading、error、empty、partial 状态；交互可键盘完成，状态不能只靠颜色。
- 不部署、推送、创建真实工作台、调用真实 AI、执行真实数据迁移或开启真实课程开关。

---

## File Map

### 同步服务 worktree

```text
services/classroom-sync/src/classroom_sync/repositories.py         # 受限查询
services/classroom-sync/src/classroom_sync/services/read_models.py # 角色无关的数据库读服务
services/classroom-sync/src/classroom_sync/application.py          # 注入 read service
services/classroom-sync/src/classroom_sync/runtime.py              # 生产组合 read service
services/classroom-sync/src/classroom_sync/main.py                 # 注册课堂事件 router
services/classroom-sync/src/classroom_sync/routers/plans.py        # 教师读取实验绑定方案
services/classroom-sync/src/classroom_sync/routers/student.py      # 学生读取自己的任务/会话状态
services/classroom-sync/src/classroom_sync/routers/teacher.py      # 教师读取某版方案的全班状态
services/classroom-sync/src/classroom_sync/routers/events.py       # 教师 SSE monitoring snapshots
services/classroom-sync/tests/integration/test_classroom_read_models.py
```

### 新前端 worktree

```text
src/config/app-config.ts
src/constants/route-names.ts
src/router/index.ts
src/modules/classroom-monitoring/types.ts
src/modules/classroom-monitoring/feature.ts
src/modules/classroom-monitoring/api.ts
src/modules/classroom-monitoring/plan-draft.ts
src/modules/classroom-monitoring/workbench-ticket.ts
src/modules/classroom-monitoring/use-classroom-monitor.ts
src/modules/classroom-monitoring/components/PlanWizard.vue
src/modules/classroom-monitoring/components/ClassroomStudentTable.vue
src/modules/classroom-monitoring/components/StudentBriefPanel.vue
src/modules/classroom-monitoring/__tests__/*.test.ts
src/views/admin/AdminProjectsView.vue
src/views/admin/AdminClassroomPlanView.vue
src/views/admin/AdminClassroomMonitorView.vue
src/views/admin/AdminStudentBriefView.vue
src/views/student/StudentProjectsView.vue
src/views/student/StudentAssignmentView.vue
src/views/student/StudentSubmissionView.vue
src/views/student/StudentLaunchView.vue
src/views/**/__tests__/*.test.ts
README.md
```

### Read API contract added in Task 1

The service returns only data already persisted in the existing tables; no migration is required.

```text
GET /v1/classroom/plans/experiments/{space_id}/{parent_algorithm_id}
  -> { plan_version_id, plan_id, version, title, profile, scheduled_start_at,
       scheduled_end_at, ai_policy, published_at } | 404

GET /v1/classroom/student/assignments
  -> { assignments: StudentAssignmentSummary[] }

GET /v1/classroom/student/assignments/{assignment_id}
  -> StudentAssignmentSummary

GET /v1/classroom/teacher/plans/{plan_version_id}/monitoring
  -> { plan_version_id, scheduled_end_at, students: TeacherStudentStatus[] }

GET /v1/classroom/classrooms/{plan_version_id}/events
  -> text/event-stream，首帧与每十秒一帧均为 allowlisted monitoring snapshot
```

`StudentAssignmentSummary` includes assignment identifiers, title/profile metadata, schedule,
assignment state, and an optional `session` with `id`/`status`/`last_activity_at`/
`submission_reason`. `TeacherStudentStatus` includes student identifier, assignment state, optional
session identifiers/status/last activity, and optional latest brief status/revision. It deliberately
excludes object keys, evidence bodies, ticket values, plugin access tokens and teacher reviews.

### Task 1: Add permission-scoped classroom read APIs

**Files:**
- Create: `services/classroom-sync/src/classroom_sync/services/read_models.py`
- Modify: `services/classroom-sync/src/classroom_sync/repositories.py`
- Modify: `services/classroom-sync/src/classroom_sync/application.py`
- Modify: `services/classroom-sync/src/classroom_sync/runtime.py`
- Modify: `services/classroom-sync/src/classroom_sync/main.py`
- Modify: `services/classroom-sync/src/classroom_sync/routers/plans.py`
- Modify: `services/classroom-sync/src/classroom_sync/routers/student.py`
- Modify: `services/classroom-sync/src/classroom_sync/routers/teacher.py`
- Create: `services/classroom-sync/src/classroom_sync/routers/events.py`
- Create: `services/classroom-sync/tests/integration/test_classroom_read_models.py`

**Interfaces:**
- Consumes: existing `PlanVersion`, `StudentAssignment`, `MonitorSession`, `StudentBrief` and
  trusted `Principal` checks.
- Produces: `ClassroomReadService.get_experiment_plan`, `list_student_assignments`,
  `get_student_assignment`, and `get_teacher_monitoring`; route JSON exactly matches the contract
  above.

- [ ] **Step 1: Write failing route tests for owner-scoped read models**

```python
def test_teacher_can_read_only_own_experiment_plan_and_monitoring(client, seeded):
    response = client.get(
        "/v1/classroom/plans/experiments/space-1/parent-1",
        headers={"Authorization": "Bearer teacher-token"},
    )
    assert response.status_code == 200
    assert response.json()["plan_version_id"] == seeded.plan_version.id
    assert "ticket" not in response.text

    monitoring = client.get(
        f"/v1/classroom/teacher/plans/{seeded.plan_version.id}/monitoring",
        headers={"Authorization": "Bearer teacher-token"},
    )
    assert monitoring.status_code == 200
    assert monitoring.json()["students"][0]["assignment_id"] == seeded.assignment.id

def test_student_lists_only_own_assignment_and_cannot_read_teacher_monitoring(client, seeded):
    own = client.get("/v1/classroom/student/assignments", headers={"Authorization": "Bearer student-token"})
    assert own.status_code == 200
    assert own.json()["assignments"] == [expected_student_assignment(seeded)]

    forbidden = client.get(
        f"/v1/classroom/teacher/plans/{seeded.plan_version.id}/monitoring",
        headers={"Authorization": "Bearer student-token"},
    )
    assert forbidden.status_code == 403

def test_teacher_event_stream_has_no_secret_fields(client, seeded):
    response = client.get(
        f"/v1/classroom/classrooms/{seeded.plan_version.id}/events",
        headers={"Authorization": "Bearer teacher-token"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data:" in response.text
    assert "access_token" not in response.text

def test_teacher_review_rejects_an_empty_reason(client, seeded):
    response = client.post(
        f"/v1/classroom/teacher/sessions/{seeded.session.id}/reviews",
        headers={"Authorization": "Bearer teacher-token"},
        json={"knowledge_point_reviews": [], "comment": ""},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run the new tests to verify they fail because the routes do not exist**

Run: `cd services/classroom-sync && python -m pytest tests/integration/test_classroom_read_models.py -q`

Expected: FAIL with HTTP 404 for each new read route.

- [ ] **Step 3: Add focused repository and read-service queries**

```python
def list_assignments_for_student(self, student_id: str) -> tuple[StudentAssignment, ...]:
    return tuple(self.session.scalars(
        select(StudentAssignment)
        .where(StudentAssignment.student_id == student_id)
        .order_by(StudentAssignment.scheduled_start_at.desc(), StudentAssignment.id)
    ))

def latest_session_for_assignment(self, assignment_id: str) -> MonitorSession | None:
    return self.session.scalar(
        select(MonitorSession)
        .where(MonitorSession.assignment_id == assignment_id)
        .order_by(MonitorSession.created_at.desc())
        .limit(1)
    )
```

`ClassroomReadService` must serialize allowlisted public fields rather than ORM `__dict__`, resolve
the binding through `space_id + parent_algorithm_id`, and return no result when the requested plan
version/assignment does not exist. Add `read_service: ClassroomReadService | None = None` to
`ClassroomServices`, construct it in `create_runtime_services`, and make routes fail fast with a
configuration error if the dependency is missing in a partial test application.

- [ ] **Step 4: Expose routes after existing teacher/student ownership checks**

```python
@router.get("/assignments")
def list_own_assignments(request: Request, authorization: Annotated[str | None, Header()] = None):
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    return {"assignments": services.read_service.list_student_assignments(principal.user_id)}
```

For the experiment-plan and monitoring routes, call `require_teacher_owner` using the plan version's
stored `space_id` and `parent_algorithm_id` before returning any summary. For the assignment-detail
route, reject an assignment whose `student_id` differs from the principal with
`student_assignment_owner_mismatch`.

Change the existing `TeacherReviewRequest.comment` to
`Annotated[str, Field(min_length=1, max_length=1000)]`, so the front-end “复核原因” invariant is also
enforced for direct requests.

The events route uses `StreamingResponse` with `media_type="text/event-stream"`, immediately emits
one `event: monitoring\ndata: <snapshot-json>\n\n` frame, then re-reads the same allowlisted snapshot
every ten seconds until request disconnect. It must run the teacher ownership check before opening the
stream and must not serialize tickets, plugin access tokens, object keys, review payloads or evidence.
Update `deploy/classroom/nginx/classroom.conf` and its static test so this exact classroom event path
also has `proxy_buffering off` and the existing one-hour timeouts.

- [ ] **Step 5: Run route and existing authorization regressions**

Run: `cd services/classroom-sync && python -m pytest tests/integration/test_classroom_read_models.py tests/integration/test_authorization.py tests/integration/test_classroom_routes.py -q`

Expected: PASS; no summary response contains `ticket`, `access_token`, `object_key`,
`knowledge_point_reviews` or evidence bytes.

- [ ] **Step 6: Commit the service read surface**

```bash
git add services/classroom-sync/src/classroom_sync services/classroom-sync/tests/integration/test_classroom_read_models.py deploy/classroom/nginx scripts/__tests__/test_classroom_nginx_config.py
git commit -m "feat: expose classroom read models"
```

### Task 2: Create the isolated frontend worktree and baseline gate

**Files:**
- No product files change in this task.

**Interfaces:**
- Consumes: clean `version-2026-08-04` frontend baseline.
- Produces: a clean linked worktree on `codex/classroom-ui` under the frontend repository's ignored
  `.worktrees/` directory.

- [ ] **Step 1: Verify the target worktree is separate and the source worktree is not modified**

```bash
git -C /Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/lab-platform-frontend status --short
git -C /Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/lab-platform-frontend worktree list
```

Expected: retain the two user-owned untracked specification files untouched; never clean or stage
them.

- [ ] **Step 2: Create the new linked worktree only after confirming `.worktrees` is ignored**

```bash
cd /Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/lab-platform-frontend
git check-ignore -q .worktrees
git worktree add .worktrees/classroom-ui -b codex/classroom-ui version-2026-08-04
```

Expected: the new worktree has no changed or untracked files.

- [ ] **Step 3: Run the frontend baseline checks without auto-fixing source files**

```bash
cd .worktrees/classroom-ui
npm test -- --run src/views/admin/__tests__/AdminProjectsView.test.ts src/views/student/__tests__/StudentProjectsView.test.ts src/views/student/__tests__/StudentLaunchView.test.ts
npm run type-check
```

Expected: PASS before any product change. If either fails, capture its output and obtain direction
before implementing classroom UI.

### Task 3: Add classroom API client, types and default-off feature gate

**Files:**
- Create: `src/modules/classroom-monitoring/types.ts`
- Create: `src/modules/classroom-monitoring/feature.ts`
- Create: `src/modules/classroom-monitoring/api.ts`
- Create: `src/modules/classroom-monitoring/__tests__/api.test.ts`
- Modify: `src/config/app-config.ts`

**Interfaces:**
- Consumes: `getAccessToken()` from `@/utils/storage` and Task 1 JSON responses.
- Produces: `isClassroomMonitoringEnabled()`, `classroomApi.getExperimentPlan`,
  `classroomApi.createPlanDraft`, `publishPlan`, `syncAssignments`, `listOwnAssignments`,
  `acceptAssignment`, `issueWorkbenchTicket`, `getMonitoring`, `getStudentBrief`, and
  `submitTeacherReview`.

- [ ] **Step 1: Write failing API and feature-off tests**

```ts
const mockedClassroomClient = {
  get: vi.fn(), post: vi.fn(),
  interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
}
vi.mock('axios', () => ({ default: { create: vi.fn(() => mockedClassroomClient) } }))

it('keeps classroom UI disabled by default', () => {
  expect(isClassroomMonitoringEnabled()).toBe(false)
})

it('preserves code, retryability and request id from the classroom error envelope', async () => {
  mockedClassroomClient.get.mockRejectedValueOnce({ response: { status: 503, data: {
    error: { code: 'dependency_unavailable', retryable: true, request_id: 'request-1' },
  } } })
  await expect(classroomApi.listOwnAssignments()).rejects.toMatchObject({
    code: 'dependency_unavailable', retryable: true, requestId: 'request-1',
  })
})
```

- [ ] **Step 2: Run the test to verify it fails because the module is absent**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/api.test.ts`

Expected: FAIL with module resolution error.

- [ ] **Step 3: Implement a separate Axios client and normalized error type**

```ts
export class ClassroomApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly retryable: boolean,
    readonly requestId: string,
  ) { super(message) }
}

const classroomService = axios.create({ baseURL: appConfig.classroomSyncPrefix, timeout: 20_000 })
classroomService.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) config.headers = { ...config.headers, Authorization: `Bearer ${token}` }
  return config
})
```

Set `appConfig.classroomMonitoringEnabled` from exact string equality with `'true'`, and
`classroomSyncPrefix` from `VITE_CLASSROOM_SYNC_PREFIX || '/classroom-api'`. Do not import or modify
`src/api/request.ts`.

- [ ] **Step 4: Run API tests and type check**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/api.test.ts && npm run type-check`

Expected: PASS; Bearer is sent, API errors remain structured, and default feature gate is false.

- [ ] **Step 5: Commit the client boundary**

```bash
git add src/config/app-config.ts src/modules/classroom-monitoring
git commit -m "feat: add classroom monitoring client"
```

### Task 4: Add the teacher plan route, wizard and experiment-card entry

**Files:**
- Create: `src/modules/classroom-monitoring/plan-draft.ts`
- Create: `src/modules/classroom-monitoring/components/PlanWizard.vue`
- Create: `src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts`
- Create: `src/views/admin/AdminClassroomPlanView.vue`
- Modify: `src/views/admin/AdminProjectsView.vue`
- Modify: `src/constants/route-names.ts`
- Modify: `src/router/index.ts`
- Modify: `src/views/admin/__tests__/AdminProjectsView.test.ts`

**Interfaces:**
- Consumes: `classroomApi` from Task 3 and a route `:courseId/:parentAlgorithmId`.
- Produces: `validatePlanDraft(form): FieldErrors`, `toPlanDraftPayload(form)`, and the teacher route
  `/admin/projects/:courseId/:parentAlgorithmId/classroom-plan`.

Add these exact route names before adding the route records:

```ts
ADMIN_CLASSROOM_PLAN: 'AdminClassroomPlan',
ADMIN_CLASSROOM_MONITOR: 'AdminClassroomMonitor',
ADMIN_STUDENT_BRIEF: 'AdminStudentBrief',
STUDENT_ASSIGNMENT: 'StudentAssignment',
STUDENT_SUBMISSION: 'StudentSubmission',
```

- [ ] **Step 1: Write a failing complete-input and feature-off regression test**

```ts
it('publishes exactly once when every reactive knowledge-point field is complete', async () => {
  const wrapper = mount(PlanWizard, { props: { courseId: 'course-1', parentAlgorithmId: 'parent-1' } })
  await fillCompleteProfile(wrapper)
  await wrapper.get('[data-testid="publish-classroom-plan"]').trigger('click')
  expect(mocks.createPlanDraft).toHaveBeenCalledTimes(1)
  expect(mocks.publishPlan).toHaveBeenCalledTimes(1)
  expect(mocks.syncAssignments).toHaveBeenCalledTimes(1)
})

it('does not render a classroom entry when the feature is disabled', async () => {
  const wrapper = mount(AdminProjectsView)
  expect(wrapper.text()).not.toContain('课堂方案')
})
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts src/views/admin/__tests__/AdminProjectsView.test.ts`

Expected: FAIL because the view/component and entry do not exist.

- [ ] **Step 3: Implement one reactive form source and three semantic steps**

```ts
const form = reactive<PlanDraftForm>(createEmptyPlanDraft())
const errors = computed(() => validatePlanDraft(form))
const canPublish = computed(() => Object.keys(errors.value).length === 0 && !isPublishing.value)

async function publish(): Promise<void> {
  if (!canPublish.value) return
  isPublishing.value = true
  try {
    const draft = await classroomApi.createPlanDraft(toPlanDraftPayload(form, props))
    const version = await classroomApi.publishPlan(draft.draftId)
    await classroomApi.syncAssignments(version.planVersionId)
    await router.replace({ name: ROUTE_NAMES.ADMIN_CLASSROOM_MONITOR, params: { planVersionId: version.planVersionId } })
  } finally { isPublishing.value = false }
}
```

Use one `reactive<PlanDraftForm>` as the sole validator input. Every visible form field has a `<label
for>` and an inline field error. The card entry must be a native button/link, remain absent when the
gate is false, and preserve the existing create/delete experiment controls.

Each new classroom route declares a `beforeEnter` guard that returns the original projects route name
when `isClassroomMonitoringEnabled()` is false. Thus a pasted classroom URL cannot reveal a new page
while rollout is disabled.

- [ ] **Step 4: Run form, project-card and type checks**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts src/views/admin/__tests__/AdminProjectsView.test.ts && npm run type-check`

Expected: PASS; full inputs publish once, incomplete inputs name the exact field, and the feature-off
DOM assertion passes.

- [ ] **Step 5: Commit teacher plan publication UI**

```bash
git add src/modules/classroom-monitoring src/views/admin src/constants/route-names.ts src/router/index.ts
git commit -m "feat: let teachers publish classroom plans"
```

### Task 5: Add teacher monitoring and one-brief reader

**Files:**
- Create: `src/modules/classroom-monitoring/use-classroom-monitor.ts`
- Create: `src/modules/classroom-monitoring/components/ClassroomStudentTable.vue`
- Create: `src/modules/classroom-monitoring/components/StudentBriefPanel.vue`
- Create: `src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts`
- Create: `src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts`
- Create: `src/views/admin/AdminClassroomMonitorView.vue`
- Create: `src/views/admin/AdminStudentBriefView.vue`
- Modify: `src/constants/route-names.ts`
- Modify: `src/router/index.ts`

**Interfaces:**
- Consumes: `classroomApi.getMonitoring(planVersionId)`, `getStudentBrief(sessionId)`, and
  `submitTeacherReview(sessionId, comment, knowledgePointReviews)`.
- Produces: routes `/admin/classrooms/:planVersionId/monitoring` and
  `/admin/classrooms/:planVersionId/students/:sessionId/brief`.

- [ ] **Step 1: Write failing monitoring, polling and privacy tests**

```ts
it('falls back to ten-second polling after three event-stream failures and clears it on unmount', async () => {
  const monitor = useClassroomMonitor('plan-version-1', { streamFactory: failingStreamFactory })
  await monitor.start()
  expect(mockedGetMonitoring).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(10_000)
  expect(mockedGetMonitoring).toHaveBeenCalledTimes(2)
  monitor.stop()
  await vi.advanceTimersByTimeAsync(20_000)
  expect(mockedGetMonitoring).toHaveBeenCalledTimes(2)
})

it('shows one teaching brief and never renders object keys or presigned URLs', () => {
  const wrapper = mount(StudentBriefPanel, { props: { brief: completeBrief() } })
  expect(wrapper.text()).toContain('尚未证明')
  expect(wrapper.text()).not.toContain('object_key')
  expect(wrapper.text()).not.toContain('https://storage.example/')
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts`

Expected: FAIL because composable and components are absent.

- [ ] **Step 3: Implement monitoring with authenticated fetch stream and deterministic fallback**

Use `fetch` with `Authorization` and `ReadableStream`, never `EventSource`; start a 10-second polling
timer only after three failed stream attempts and cancel both `AbortController` and timer in `stop()`.
The table shows student label, assignment state, session state, last activity, brief state and an
accessible “查看简报” button. On narrow screens, use definition-list rows rather than a horizontally
compressed table.

`StudentBriefPanel` maps `not_demonstrated` to “尚未证明”, renders at most three issues, and requires a
nonempty “复核原因” field before a teacher review can submit; it maps that field to the existing
`comment` request property. It must not request evidence bodies automatically.

- [ ] **Step 4: Run monitoring, brief and type checks**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts && npm run type-check`

Expected: PASS; timer/stream cleanup is verified, status is textual, and brief privacy assertions hold.

- [ ] **Step 5: Commit monitoring and brief UI**

```bash
git add src/modules/classroom-monitoring src/views/admin src/constants/route-names.ts src/router/index.ts
git commit -m "feat: add teacher classroom monitoring"
```

### Task 6: Add student assignment, ticketed 40037 launch and submission status

**Files:**
- Create: `src/modules/classroom-monitoring/workbench-ticket.ts`
- Create: `src/modules/classroom-monitoring/use-student-assignment.ts`
- Create: `src/modules/classroom-monitoring/__tests__/student-assignment.test.ts`
- Create: `src/views/student/StudentAssignmentView.vue`
- Create: `src/views/student/StudentSubmissionView.vue`
- Modify: `src/views/student/StudentProjectsView.vue`
- Modify: `src/views/student/StudentLaunchView.vue`
- Modify: `src/constants/route-names.ts`
- Modify: `src/router/index.ts`
- Modify: `src/views/student/__tests__/StudentProjectsView.test.ts`
- Modify: `src/views/student/__tests__/StudentLaunchView.test.ts`

**Interfaces:**
- Consumes: `classroomApi.listOwnAssignments`, `acceptAssignment`, `issueWorkbenchTicket`, and the
  existing `loadStudentExperimentRuntime` workbench URL.
- Produces: `buildTicketedWorkbenchUrl(rawUrl, ticket): string`, routes
  `/student/assignments/:assignmentId` and `/student/assignments/:assignmentId/submission`.

- [ ] **Step 1: Write failing URL, acceptance and feature-off regressions**

```ts
it.each(['/lab', 'http://14.103.139.131:40002/lab?token=old#view=1'])
  ('forces a ticketed workbench URL to HTTPS 40037 without query ticket: %s', (rawUrl) => {
    const url = new URL(buildTicketedWorkbenchUrl(rawUrl, 'ticket-123'))
    expect(url.origin).toBe('https://14.103.139.131:40037')
    expect(url.searchParams.has('behavior_ticket')).toBe(false)
    expect(url.hash).toContain('behavior_ticket=ticket-123')
  })

it('does not open a bare workbench when ticket issuance fails', async () => {
  mocks.issueWorkbenchTicket.mockRejectedValue(new ClassroomApiError('服务暂不可用', 'dependency_unavailable', true, 'r1'))
  await wrapper.get('[data-testid="accept-and-enter"]').trigger('click')
  expect(window.location.assign).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('可重试')
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/student-assignment.test.ts src/views/student/__tests__/StudentProjectsView.test.ts src/views/student/__tests__/StudentLaunchView.test.ts`

Expected: FAIL because the ticket builder, assignment route and gated project projection are absent.

- [ ] **Step 3: Implement ticket URL normalization and student views**

```ts
export function buildTicketedWorkbenchUrl(rawUrl: string, ticket: string): string {
  if (!ticket.trim()) throw new Error('课堂工作台票据为空')
  const source = new URL(rawUrl, 'https://14.103.139.131:40037')
  const target = new URL(`${source.pathname}${source.search}`, 'https://14.103.139.131:40037')
  const hash = new URLSearchParams(source.hash.replace(/^#/, ''))
  hash.set('behavior_ticket', ticket)
  target.hash = hash.toString()
  return target.toString()
}
```

When the gate is off, retain existing student project and launch behavior exactly. When it is on and
an assignment matches the selected child algorithm, route to assignment acceptance before opening the
workbench. The submission view polls assignment details every 10 seconds only while session status is
non-terminal, retains the last successful snapshot after retryable failures, and shows no teacher
reviews, other student identifiers, object keys or ticket.

The two student classroom routes use the same feature-off `beforeEnter` redirect to
`ROUTE_NAMES.STUDENT_PROJECTS`. `StudentLaunchView` only requests a classroom assignment after the
feature gate is true; otherwise it calls the current `loadStudentExperimentRuntime` path unchanged.

- [ ] **Step 4: Run student route, privacy and type checks**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/student-assignment.test.ts src/views/student/__tests__/StudentProjectsView.test.ts src/views/student/__tests__/StudentLaunchView.test.ts && npm run type-check`

Expected: PASS; ticket stays in fragment, 40037 is enforced, no bare workbench opens on failure, and
existing feature-off tests pass.

- [ ] **Step 5: Commit student classroom workflow**

```bash
git add src/modules/classroom-monitoring src/views/student src/constants/route-names.ts src/router/index.ts
git commit -m "feat: let students accept monitored assignments"
```

### Task 7: Run the full frontend quality gate and document local configuration

**Files:**
- Create: `src/modules/classroom-monitoring/__tests__/feature-off-regression.test.ts`
- Modify: `README.md`

**Interfaces:**
- Consumes: all UI tasks and feature-off environment.
- Produces: documented `VITE_CLASSROOM_MONITORING_ENABLED` and `VITE_CLASSROOM_SYNC_PREFIX`; no
  deployment artifact or remote change.

- [ ] **Step 1: Write a failing feature-off regression test**

```ts
it('keeps existing teacher and student route targets when classroom monitoring is disabled', async () => {
  vi.stubEnv('VITE_CLASSROOM_MONITORING_ENABLED', 'false')
  vi.resetModules()
  const { default: router } = await import('@/router')
  expect(router.resolve('/admin/projects').matched.at(-1)?.name).toBe(ROUTE_NAMES.ADMIN_PROJECTS)
  expect(router.resolve('/student/projects').matched.at(-1)?.name).toBe(ROUTE_NAMES.STUDENT_PROJECTS)
  expect(router.resolve('/student/launch/course-1/child-1').matched.at(-1)?.name).toBe(ROUTE_NAMES.STUDENT_LAUNCH)
})
```

- [ ] **Step 2: Run the test to verify it fails before the explicit regression guard exists**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/feature-off-regression.test.ts`

Expected: FAIL because the regression test/module is absent.

- [ ] **Step 3: Implement the regression guard and concise environment documentation**

The README must document `VITE_CLASSROOM_MONITORING_ENABLED=false` as default,
`VITE_CLASSROOM_SYNC_PREFIX=/classroom-api`, the fact that 40037 remains a BAMS entry, and that a
build does not authorize deployment. It must not contain secrets, real API tokens, `40002` as a
student target, or remote shell commands.

- [ ] **Step 4: Run all read-only quality gates**

Run:

```bash
npx --no-install oxlint .
npx --no-install eslint .
npm run type-check
npm test -- --run
npm run build
```

Expected: every command exits 0. Do not use `npm run lint` for final evidence because it contains
auto-fix flags.

- [ ] **Step 5: Perform local visual and keyboard verification without deployment**

Run the local Vite server with the classroom feature enabled and use a local/mock classroom API. At
320, 768, 1024 and 1440 px, use only Tab/Shift+Tab/Enter/Space to complete teacher configuration,
student acceptance, retry and brief navigation. Record loading, empty, error and partial screenshots
without tokens in the URL. Stop the local server after the check.

- [ ] **Step 6: Commit the frontend quality gate**

```bash
git add src/modules/classroom-monitoring/__tests__/feature-off-regression.test.ts README.md
git commit -m "test: verify classroom frontend workflows"
```

## Plan Self-Review

| Specification requirement | Implementing task |
| --- | --- |
| Existing experiment is classroom carrier | Task 4 |
| Teacher publish, monitor and one brief | Tasks 4 and 5 |
| Student owns only acceptance/status/entry | Tasks 1 and 6 |
| 40037 HTTPS fragment ticket | Task 6 |
| Default-off feature gate and rollback | Tasks 3 and 7 |
| API permission boundary and privacy | Task 1 plus API tests in Task 3 |
| Browser-close recovery and manual/automatic states | Task 1 summaries and Task 6 status UI |
| Loading/error/empty/partial, keyboard and responsive checks | Tasks 4–7 |
| No deployment/push/real-data actions | Global constraints and Task 7 stop point |

The plan contains no migrations because existing models already store the plan binding, assignment,
session and brief fields needed by the proposed read responses. The events stream is a read-only
periodic snapshot over the existing monitoring query, so it introduces no event table or schema field.
The only cross-repository dependency is Task 1's committed JSON response and SSE shape; Task 3 starts
after that commit is available to the isolated frontend worktree.
