# Jupyter 学生侧栏知识点 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** 在 myextension 0.4.0 的课堂学生模式中，只读显示教师发布快照中的知识点，并构建可验证的 0.4.0 wheel；不修改 BAMS 源码或数据库。

**Architecture:** 浏览器继续只调用本机 Jupyter 的去令牌课堂上下文接口。新增可单测的启动协调器，在携带课堂 ticket 而上下文不可用时初始化无作者权限的中性学生面板；侧栏消费 schema v2 profile 的知识点快照，刷新也通过同一受控接口完成。课堂资料同步不被导入或修改。

**Tech Stack:** TypeScript、Jest、JupyterLab 4 / Lumino、Python 3.12、pytest、uv、Yarn/jlpm、Hatch build。

## Global Constraints

- 基线提交为 2920f93，实现分支为 codex/jupyter-knowledge-points-20260902；不得触碰 classroom-resource-jupyter-sync-20260901 的未提交改动。
- 不修改 BAMS 源码、数据库、课堂资源服务或学生工作区文件。
- 浏览器不持有、记录、显示或转发课堂插件 token；只消费服务端 Schema 校验后的 classroom_session.profile。
- 学生模式不显示教师作者、AI 配置、发布、删除或评分控件。
- 知识点卡片和课堂资料同步互不依赖，任一失败均不能阻止编辑、运行、采集或简报提交。
- 已启动任务只显示发布快照；不要手动编辑 myextension/_version.py。
- 本计划不执行 Docker build/push、BAMS 模板替换或运行中工作台重建。远端发布需要单独授权、目标模板和 0.2.2 回滚 digest。

---

## File Structure

| File | Responsibility |
| --- | --- |
| src/platform/contextApi.ts | 提供无作者权限的学生 fallback 上下文；保留 POST platform/context 刷新。 |
| src/platform/ticketBootstrap.ts | 只判断 fragment 是否带 ticket，不泄露其字符串。 |
| src/platform/classroomUiBootstrap.ts | 新增可单测的课堂 UI 启动协调器。 |
| src/index.ts | 使用启动协调器初始化正确的侧栏。 |
| src/ui/behaviorAnalysisSidebar.ts | 渲染知识点、刷新快照、保留失败前的内容。 |
| style/base.css | 知识点列表的最小侧栏样式。 |
| src/__tests__/classroomUiBootstrap.spec.ts | 新增课堂启动失败不退回作者模式的测试。 |
| src/__tests__/ticketBootstrap.spec.ts | 验证 ticket 只从 fragment 观察。 |
| src/__tests__/studentModeSidebar.spec.ts | 覆盖知识点、刷新、失败保留、学生权限。 |
| myextension/tests/test_student_mode_routes.py | 维持服务端 profile 快照与 token 隔离契约。 |
| myextension/tests/test_labextension_artifact.py | 维持 wheel 内学生知识点 UI marker。 |
| deploy/bluedot/release-0.4.0 | 用已验证 wheel 和 SHA256SUMS 更新候选交付。 |
| docs/verification/2026-09-02-jupyter-student-knowledge-points.md | 记录实际测试、哈希和未执行远端发布。 |

### Task 1: Fail closed on unavailable classroom context

**Files:**
- Create: src/platform/classroomUiBootstrap.ts
- Create: src/__tests__/classroomUiBootstrap.spec.ts
- Modify: src/platform/contextApi.ts
- Modify: src/platform/ticketBootstrap.ts
- Modify: src/__tests__/ticketBootstrap.spec.ts
- Modify: src/index.ts:20-225

**Interfaces:**
- Consumes: IPlatformContext, getPlatformContext(settings), bootstrapClassroomTicket(...).
- Produces: createUnavailableStudentPlatformContext() and initializeClassroomUi(options).

- [ ] **Step 1: Write failing tests**

Add a fragment-only detector test to ticketBootstrap.spec.ts:

~~~ts
it('detects only a fragment classroom ticket without reading it', () => {
  expect(hasClassroomTicket({
    hash: '#view=lab&behavior_ticket=temporary-ticket',
    pathname: '/lab',
    search: '?behavior_ticket=unsafe-query-ticket'
  })).toBe(true);
  expect(hasClassroomTicket({
    hash: '#view=lab',
    pathname: '/lab',
    search: '?behavior_ticket=unsafe-query-ticket'
  })).toBe(false);
});
~~~

Create classroomUiBootstrap.spec.ts with a valid v2 student context and these three cases:

~~~ts
it('initializes the server context when it is available', async () => {
  const initialize = jest.fn();
  await initializeClassroomUi({
    classroomTicketObserved: true,
    getContext: async () => studentContext,
    initialize,
    reportUnavailable: jest.fn()
  });
  expect(initialize).toHaveBeenCalledWith(studentContext);
});

it('uses a no-authoring student context when classroom context is unavailable', async () => {
  const initialize = jest.fn();
  await initializeClassroomUi({
    classroomTicketObserved: true,
    getContext: async () => Promise.reject(new Error('offline')),
    initialize,
    reportUnavailable: jest.fn()
  });
  expect(initialize.mock.calls[0][0]).toMatchObject({
    mode: 'student',
    classroom_session: null,
    capabilities: { canAuthorPlan: false, canPublishPlan: false }
  });
});

it('does not invent a student UI for a non-classroom context failure', async () => {
  const initialize = jest.fn();
  const reportUnavailable = jest.fn();
  await initializeClassroomUi({
    classroomTicketObserved: false,
    getContext: async () => Promise.reject(new Error('offline')),
    initialize,
    reportUnavailable
  });
  expect(initialize).not.toHaveBeenCalled();
  expect(reportUnavailable).toHaveBeenCalledTimes(1);
});
~~~

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

~~~bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm jest \
  src/__tests__/ticketBootstrap.spec.ts \
  src/__tests__/classroomUiBootstrap.spec.ts \
  --runInBand --coverage=false
~~~

Expected: FAIL because the detector, fallback context factory, and UI bootstrap module do not yet exist.

- [ ] **Step 3: Implement the smallest safe bootstrap boundary**

In contextApi.ts add a fresh fallback object; do not reuse or mutate LOCAL_PLATFORM_CONTEXT:

~~~ts
export function createUnavailableStudentPlatformContext(): IPlatformContext {
  return {
    schema_version: 1,
    request_id: 'classroom-context-unavailable',
    mode: 'student',
    capabilities: capabilitiesForMode('student'),
    classroom_session: null
  };
}
~~~

In ticketBootstrap.ts add hasClassroomTicket(location). It must parse only location.hash using URLSearchParams and return parameters.has('behavior_ticket'), never return the value.

Create classroomUiBootstrap.ts:

~~~ts
export interface IClassroomUiBootstrapOptions {
  classroomTicketObserved: boolean;
  getContext: () => Promise<IPlatformContext>;
  initialize: (context: IPlatformContext) => void;
  reportUnavailable: () => void;
}

export async function initializeClassroomUi(
  options: IClassroomUiBootstrapOptions
): Promise<'context' | 'student-unavailable' | 'unavailable'> {
  try {
    options.initialize(await options.getContext());
    return 'context';
  } catch {
    if (options.classroomTicketObserved) {
      options.initialize(createUnavailableStudentPlatformContext());
      return 'student-unavailable';
    }
    options.reportUnavailable();
    return 'unavailable';
  }
}
~~~

In index.ts compute classroomTicketObserved before bootstrapClassroomTicket clears the fragment. Replace initializeAfterClassroomTicket with initializeClassroomUi, pass getPlatformContext and initializePlatformUi, and retain the existing console error only as reportUnavailable. Both ticket-registration success and failure paths call the same coordinator.

- [ ] **Step 4: Verify GREEN and TypeScript**

Run:

~~~bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm jest \
  src/__tests__/ticketBootstrap.spec.ts \
  src/__tests__/classroomUiBootstrap.spec.ts \
  --runInBand --coverage=false
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:lib:prod
~~~

Expected: PASS and no TypeScript error. The fallback capabilities must keep canAuthorPlan, canPublishPlan, canConfigureAi, and canUseAssessmentAssist false.

- [ ] **Step 5: Commit**

~~~bash
git add src/platform/contextApi.ts src/platform/ticketBootstrap.ts \
  src/platform/classroomUiBootstrap.ts src/__tests__/ticketBootstrap.spec.ts \
  src/__tests__/classroomUiBootstrap.spec.ts src/index.ts
git commit -m "fix: keep classroom launch in student mode"
~~~

### Task 2: Render and refresh teacher knowledge points in the student sidebar

**Files:**
- Modify: src/ui/behaviorAnalysisSidebar.ts:66-130,362-458,699-780,1174-1260
- Modify: style/base.css:446-498
- Modify: src/__tests__/studentModeSidebar.spec.ts

**Interfaces:**
- Consumes: IPlatformContext.classroom_session.profile and refreshPlatformContext(settings).
- Produces: refreshStudentClassroomContext(): Promise<void>; a read-only “本次实验知识点” section.

- [ ] **Step 1: Write failing student sidebar tests**

Update studentContext in studentModeSidebar.spec.ts to use unsorted published points:

~~~ts
knowledge_points: [
  {
    id: 'KP_B2C3D4E5',
    name: '边界条件处理',
    description: '处理空输入与除零。',
    source: 'teacher',
    order: 1
  },
  {
    id: 'KP_A1B2C3D4',
    name: '平均值计算',
    description: '使用总和除以元素数量。',
    source: 'teacher',
    order: 0
  }
]
~~~

Add a createStudentSidebar helper that optionally overrides refreshPlatformContext. Add tests:

~~~ts
function findButton(
  sidebar: BehaviorAnalysisSidebar,
  label: string
): HTMLButtonElement {
  const found = Array.from(
    sidebar.node.querySelectorAll<HTMLButtonElement>('button')
  ).find(value => value.textContent === label);
  if (!found) throw new Error(`Missing button: ${label}`);
  return found;
}

async function flushPromises(): Promise<void> {
  for (let index = 0; index < 16; index += 1) {
    await Promise.resolve();
  }
  await new Promise(resolve => setTimeout(resolve, 0));
}
~~~

~~~ts
it('renders published knowledge points in teacher order', () => {
  const sidebar = createStudentSidebar(studentContext);
  const text = sidebar.node.textContent ?? '';
  expect(text).toContain('本次实验知识点');
  expect(text.indexOf('平均值计算')).toBeLessThan(text.indexOf('边界条件处理'));
  expect(text).toContain('使用总和除以元素数量。');
  expect(text).not.toContain('创建题目考核方案');
  sidebar.dispose();
});

it('keeps the latest snapshot when a refresh fails', async () => {
  const refreshPlatformContext = jest.fn(() =>
    Promise.reject(new Error('offline'))
  );
  const sidebar = createStudentSidebar(studentContext, {
    refreshPlatformContext
  });
  findButton(sidebar, '刷新课堂信息').click();
  await flushPromises();
  expect(sidebar.node.textContent).toContain('平均值计算');
  expect(sidebar.node.textContent).toContain('知识点暂时无法加载，请重试');
  expect(findButton(sidebar, '提交本节简报')).toBeDefined();
  sidebar.dispose();
});
~~~

Add a third test with a deliberately malformed profile cast through unknown; it must show the same neutral error and not throw.

- [ ] **Step 2: Run the sidebar test and verify RED**

Run:

~~~bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm jest \
  src/__tests__/studentModeSidebar.spec.ts \
  --runInBand --coverage=false
~~~

Expected: FAIL because the old sidebar does not render the knowledge-point section or expose a refresh dependency.

- [ ] **Step 3: Implement the read-only card and retry path**

Extend IBehaviorAnalysisSidebarDependencies and sidebarDependencies with:

~~~ts
refreshPlatformContext: typeof refreshPlatformContext;
~~~

Make platformContext mutable and add:
- studentContextRefreshInFlight: Promise<void> | null
- studentContextFeedback: string

Add orderedStudentKnowledgePoints(profile). It accepts only schema v2 points with finite non-negative order, non-empty name, and string description; otherwise return null. It must copy then sort the list, never mutate profile.knowledge_points.

In studentClassroomSection(), before monitoring and deadline information:
- render h2 “本次实验知识点”;
- render an ol.jp-BehaviorAudit-knowledgePointList in order, with each name and description set only through textContent;
- for no session render “尚未接入课堂任务，请从课程页面重新进入”;
- for malformed profile render “知识点暂时无法加载，请重试”;
- render “刷新课堂信息”; while a request is pending disable it and set aria-busy to true.

Implement refreshStudentClassroomContext(): call deps.refreshPlatformContext(deps.settings); on success replace platformContext and clear feedback; on failure preserve the old platformContext and set the exact retry message. Do not alter capture, session IDs, submit flow, or any teacher controls.

Add the minimum CSS:

~~~css
.jp-BehaviorAudit-knowledgePointList {
  display: grid;
  gap: 8px;
  margin: 0;
  padding-left: 20px;
}

.jp-BehaviorAudit-knowledgePointList li {
  overflow-wrap: anywhere;
}
~~~

- [ ] **Step 4: Verify sidebar GREEN, style, and compilation**

Run:

~~~bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm jest \
  src/__tests__/studentModeSidebar.spec.ts \
  src/__tests__/behaviorAnalysisSidebar.spec.ts \
  --runInBand --coverage=false
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm stylelint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:lib:prod
~~~

Expected: PASS. The refresh button has aria-busy while pending; failure preserves the two visible points and the existing submit button.

- [ ] **Step 5: Commit**

~~~bash
git add src/ui/behaviorAnalysisSidebar.ts style/base.css \
  src/__tests__/studentModeSidebar.spec.ts
git commit -m "feat: show classroom knowledge points in Jupyter"
~~~

### Task 3: Lock the server classroom snapshot contract

**Files:**
- Modify: myextension/tests/test_student_mode_routes.py
- Modify: docs/openapi/myextension-v1.yaml only if it differs from platform-context-response-v1.json.

**Interfaces:**
- Consumes: persisted RegisteredPlatformContext and the platform-context response schema.
- Produces: a token-isolated, versioned profile-snapshot contract test.

- [ ] **Step 1: Write the classroom profile-snapshot contract test**

In test_student_mode_routes.py, extend the existing context test:

~~~py
profile = payload["classroom_session"]["profile"]
assert profile["schema_version"] == 2
assert profile["knowledge_points"] == context().profile["knowledge_points"]
assert "access_token" not in json.dumps(profile, ensure_ascii=False)
~~~

- [ ] **Step 2: Run the contract test**

Run:

~~~bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_student_mode_routes.py
~~~

Expected: PASS, or an explicit existing server-contract gap. This task must not rely on an unbuilt frontend artifact.

- [ ] **Step 3: Keep the server implementation unchanged**

Do not add a browser-to-classroom API. routes.py, platform_context_store.py, and platform_client.py already save the access token server-side and return the v2 profile snapshot without it. Update docs/openapi/myextension-v1.yaml only when it lacks the existing profile-version-v2 reference; otherwise leave it untouched.

- [ ] **Step 4: Verify the registration and snapshot route together**

~~~bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_platform_registration.py \
  myextension/tests/test_student_mode_routes.py
~~~

Expected: profile knowledge points match the persisted snapshot and no access token reaches HTTP output.

- [ ] **Step 5: Commit the contract gate**

~~~bash
git add myextension/tests/test_student_mode_routes.py
git commit -m "test: guard classroom knowledge point delivery"
~~~

If OpenAPI needs no edit, omit it from git add rather than making a cosmetic change.

### Task 4: Build and verify the 0.4.0 candidate package

**Files:**
- Modify: myextension/tests/test_labextension_artifact.py
- Generate (not committed): myextension/labextension/**
- Generate (not committed): dist/myextension-0.4.0-py3-none-any.whl
- Modify (generated): deploy/bluedot/release-0.4.0/artifacts/myextension-0.4.0-py3-none-any.whl
- Modify: deploy/bluedot/release-0.4.0/SHA256SUMS
- Modify (generated): releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz and its .sha256
- Create: docs/verification/2026-09-02-jupyter-student-knowledge-points.md

**Interfaces:**
- Consumes: Task 1–3 source, existing release-0.4.0 scripts.
- Produces: byte-identical dist/release wheel, verified SHA-256 and deterministic handoff archive.

- [ ] **Step 1: Add a delivery-artifact marker RED test**

In myextension/tests/test_labextension_artifact.py, add these exact strings to REQUIRED_TASK_12_MARKERS:

~~~py
"本次实验知识点",
"刷新课堂信息",
"知识点暂时无法加载，请重试",
~~~

Run:

~~~bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_labextension_artifact.py::test_repository_and_delivery_wheel_load_the_same_task_11_extension
~~~

Expected: RED before production build because the clean worktree has no current labextension artifact or the old delivery wheel lacks the required strings.

- [ ] **Step 2: Run full source gates before packaging**

Run:

~~~bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm install --immutable
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand
.venv/bin/python -m pytest -q myextension/tests \
  --ignore=myextension/tests/test_labextension_artifact.py \
  --ignore=myextension/tests/test_classroom_release_040.py
~~~

Expected: source gates pass. If pytest-jupyter alone is blocked from binding 127.0.0.1, rerun exactly this test command with only local loopback permission; do not classify that sandbox error as a code failure.

- [ ] **Step 3: Build production frontend and wheel**

Run:

~~~bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm clean:all
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:prod
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m build --wheel
~~~

Expected: labextension static assets and dist/myextension-0.4.0-py3-none-any.whl exist. The version remains derived from package.json.

- [ ] **Step 4: Update release wheel and checksum**

Copy the verified dist wheel to deploy/bluedot/release-0.4.0/artifacts/. Calculate its SHA-256 with shasum -a 256, then use apply_patch to set SHA256SUMS to exactly one release-relative line:

~~~text
<actual-64-character-sha256>  artifacts/myextension-0.4.0-py3-none-any.whl
~~~

Run:

~~~bash
(cd deploy/bluedot/release-0.4.0 && shasum -a 256 -c SHA256SUMS)
cmp dist/myextension-0.4.0-py3-none-any.whl \
  deploy/bluedot/release-0.4.0/artifacts/myextension-0.4.0-py3-none-any.whl
~~~

Expected: checksum is OK and cmp returns 0. Do not hand-edit wheel contents or add secrets.

- [ ] **Step 5: Verify the package and create the handoff archive**

Run:

~~~bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_labextension_artifact.py \
  myextension/tests/test_classroom_release_040.py
.venv/bin/python -m zipfile -t dist/myextension-0.4.0-py3-none-any.whl
.venv/bin/python scripts/package_classroom_image_handoff.py \
  --source deploy/bluedot/release-0.4.0 \
  --output releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz
(cd releases && shasum -a 256 -c \
  behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256)
sh -n deploy/bluedot/release-0.4.0/build_image.sh
sh -n deploy/bluedot/release-0.4.0/verify_image.sh
sh -n deploy/bluedot/release-0.4.0/export_image.sh
~~~

Expected: all gates pass, zipfile prints Done testing, archive checksum is OK, and no Docker image is built, pushed, imported, or deployed.

- [ ] **Step 6: Write verification evidence and commit**

The verification doc must record actual commands/results, Jest and pytest counts, wheel SHA-256, archive SHA-256, JupyterLab version, and unexecuted remote work. It must state the future release preconditions: target test-template ID, current 0.2.2 image/wheel digest, rollback digest, Linux AMD64 base-image digest, running-workbench policy, and a separate deployment authorization.

~~~bash
git add myextension/tests/test_labextension_artifact.py \
  deploy/bluedot/release-0.4.0 \
  releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz \
  releases/behavior-audit-classroom-0.4.0-linux-amd64-buildkit.tar.gz.sha256 \
  docs/verification/2026-09-02-jupyter-student-knowledge-points.md
git commit -m "build: refresh classroom knowledge point wheel"
~~~

### Final Verification and Handoff

- [ ] **Step 1: Review scope and worktree state**

Run:

~~~bash
git diff --check 2920f93..HEAD
git diff --name-only 2920f93..HEAD
git status --short
~~~

Expected: no whitespace errors and no change under services/classroom-sync, BAMS source, or database migration paths.

- [ ] **Step 2: Run final local matrix**

Run:

~~~bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand
.venv/bin/python -m pytest -q myextension/tests
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:lib:prod
~~~

Expected: all source tests, lint, and compilation pass. Treat a business assertion failure as a blocker; only request narrowly-scoped local loopback permission for pytest-jupyter setup errors.

- [ ] **Step 3: Report and stop before remote replacement**

Report branch, commits, hashes, exact command outcomes, and unverified remote work. Do not change a remote BAMS template until the BAMS operator provides the target template, current and rollback digests, and a one-time deployment authorization.
