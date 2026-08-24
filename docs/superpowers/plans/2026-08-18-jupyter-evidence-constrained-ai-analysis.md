# Jupyter 证据约束 AI 教学分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 让学生在 JupyterLab 明确授权后，提交私有、截断且脱敏的课堂证据给 AI，并让教师端获得逐知识点、带事件编号的安全教学分析，同时固定课堂简报始终可用。

**Architecture:** 保持现有固定简报和异步 job 的 revision 模型：Jupyter 本地后端仅在授权时构造 EvidenceConstrainedAnalysisInput，平台把它保存在 ClassroomBriefAnalysisJob.analysis_input 而不是 StudentBrief.payload。worker 以严格输入/输出模型、事件白名单和安全文本检查生成 ai_analysis；教师 API 和 Vue 面板仅读取该安全结论。

**Tech Stack:** JupyterLab TypeScript/Jest、Python 3.10+、Tornado、FastAPI、Pydantic 2、SQLAlchemy/Alembic、Vue 3、Vitest。

## Global Constraints

- 只操作 codex/classroom-main-integration 和 codex/classroom-ui worktree；保留现有未提交的课堂建议、自动评价和前端计划草稿改动。
- 固定课堂简报先生成；AI 不可用、失败或返回无效数据时不得覆盖其摘要、知识点和过程信息。
- request_ai_analysis 默认 false。值为 false 时，Jupyter 本地后端不得读取、构造、保存或上传任何代码快照分析输入。
- `PlanVersion.ai_policy` 是服务端强制门槛：只有不可变方案值为 `allowed`、学生 `request_ai_analysis` 为 true、且私有输入通过闭合结构校验时才可创建 job 或出站调用；`prohibited` 必须强制 `not_requested`、无 job、无 Provider 调用。
- 授权输入只能含课堂题目、教师确认的知识点、精选的编辑/运行/修正事件和最多 12,000 个 Unicode 字符的脱敏代码快照；不得含学生身份、文件/绝对路径、原始输出、诊断文本、token、Provider 响应或对象存储地址。
- 私有分析输入只能保存于本地重试状态和 ClassroomBriefAnalysisJob.analysis_input；绝不写入 StudentBrief.payload、教师读取 API、浏览器状态或教师 DOM。
- AI 不评分、不判定答案正确性；逐知识点状态仅为 observed、partial、not_observed、teacher_review_required，教师端显示为“已观察／部分观察／未观察／需教师复核”。
- 除 not_observed 外，每项必须引用至少一个输入中的事件编号；not_observed 必须没有事件编号。未知、重复或缺失知识点的输出必须失败。
- 不读取、记录或提交 AI 密钥；不运行本地数据重置脚本。Docker 重建/重启和真实 Provider 调用不属于本计划的实现阶段。

## Recovery Baseline

- 本工作区已保留一组未提交的新版实现和测试（任务 1、2、4 的大部分及迁移 0004）。它们是需要恢复和验证的基线，不得删除、重写或用旧 stash 覆盖。
- 开始每项恢复验证前先运行对应现有测试；只有本计划新增的 `ai_policy` 强制、稳定错误信封和教师端闭合 mapper 必须严格执行新的 RED → GREEN 循环。
- 两个 worktree 都含本轮范围外的既有改动。实现阶段不创建业务提交、不暂存、不合并、不推送；只在最终逐文件审查后向用户报告可提交范围。已存在的规格提交 `11b27ab` 是唯一例外。

---

## File Structure

| File | Responsibility |
| --- | --- |
| src/platform/classroomApi.ts | 浏览器仅传递学生授权布尔值给本地 Jupyter 后端。 |
| src/ui/behaviorAnalysisSidebar.ts | 默认未勾选的学生授权 UI，提交时传递状态。 |
| myextension/routes.py | 校验本地提交请求的授权布尔值，浏览器不能提交快照。 |
| myextension/classroom_ai_analysis_input.py | 从本地 session detail 白名单化并截断分析输入。 |
| myextension/submission_coordinator.py | 先生成固定简报，再按授权调用输入构造器并持久化可重试 payload。 |
| services/classroom-sync/.../models.py + migration | 私有 job 输入的持久化边界。 |
| services/classroom-sync/.../routers/plugin.py | 校验平台 plugin 提交：授权和私有输入必须一致，并将无效请求映射为无敏感细节的 422。 |
| services/classroom-sync/.../services/briefs.py | 从 session 绑定的不可变方案读取 ai_policy，创建固定 brief、pending/unavailable revision 与私有 job。 |
| services/classroom-sync/.../services/brief_analysis.py | 结构化 AI 输入/输出、prompt、白名单校验和 worker source。 |
| contracts/classroom/v1/student-brief.schema.json | 仅为教师安全结论定义新的 ai_analysis 契约。 |
| ../lab-platform-frontend-classroom-ui/... | 严格映射和渲染逐知识点结论。 |

### Task 1: 建立学生显式授权的 Jupyter 提交契约

**Files:**

- Modify: src/platform/classroomApi.ts
- Modify: src/ui/behaviorAnalysisSidebar.ts
- Modify: src/__tests__/classroomApi.spec.ts
- Modify: src/__tests__/studentModeSidebar.spec.ts
- Modify: myextension/routes.py
- Modify: myextension/tests/test_platform_registration.py

**Interfaces:**

- Consumes: submitClassroomBrief(settings, sessionId, requestAiAnalysis) and JSON { schema_version: 1, reason: 'student_manual', request_ai_analysis: boolean }.
- Produces: PlatformSessionSubmitRouteHandler calls coordinator.submit(..., request_ai_analysis=body['request_ai_analysis']); no browser payload may contain analysis evidence or code.

- [ ] **Step 1: Write the failing TypeScript contract tests**

Add to src/__tests__/classroomApi.spec.ts a request-body assertion:

~~~ts
await submitClassroomBrief(settings, 'session-1', true);
expect(requestBody()).toEqual({
  schema_version: 1,
  reason: 'student_manual',
  request_ai_analysis: true,
});
expect(JSON.stringify(requestBody())).not.toContain('code_snapshots');
~~~

Add to src/__tests__/studentModeSidebar.spec.ts a DOM test that finds the unchecked #classroom-ai-analysis-consent checkbox, checks it, clicks “提交本节简报”, and asserts the mocked dependency receives true; add the unselected equivalent asserting false.

- [ ] **Step 2: Run the Jupyter tests to verify they fail for the missing third argument and checkbox**

Run: node_modules/.bin/jest --runInBand src/__tests__/classroomApi.spec.ts src/__tests__/studentModeSidebar.spec.ts

Expected: FAIL because the current API has two arguments and student mode renders no AI-analysis consent input.

- [ ] **Step 3: Write the failing local HTTP handler test**

In myextension/tests/test_platform_registration.py, construct the existing Tornado application with a fake coordinator and post these bodies:

~~~python
{"schema_version": 1, "reason": "student_manual", "request_ai_analysis": False}
{"schema_version": 1, "reason": "student_manual", "request_ai_analysis": True}
~~~

Assert each request returns the normal submission receipt and the fake records request_ai_analysis exactly. Post a body containing code_snapshots and assert HTTP 400 with platform_submission_validation_failed.

- [ ] **Step 4: Run the local route test to verify it fails for the extra field and missing coordinator argument**

Run: uv run --extra dev --extra test python -m pytest -q myextension/tests/test_platform_registration.py

Expected: FAIL because _closed_body only allows schema_version and reason, and the coordinator is never given authorization state.

- [ ] **Step 5: Write minimal implementation**

Change the public client function to:

~~~ts
export function submitClassroomBrief(
  settings: ServerConnection.ISettings,
  sessionId: string,
  requestAiAnalysis: boolean,
): Promise<IClassroomSubmission>
~~~

Serialize only the boolean. In BehaviorAnalysisSidebar, add a private studentAiAnalysisRequested = false, a labelled checkbox with id classroom-ai-analysis-consent, and explanatory text that source is sent only after opt-in. Pass this value to the dependency on submit; reset it when the classroom session changes or a submit completes. In PlatformSessionSubmitRouteHandler, permit and require the boolean in _closed_body, reject all additional keys, and pass it to coordinator.submit.

- [ ] **Step 6: Run focused tests to verify they pass**

Run: node_modules/.bin/jest --runInBand src/__tests__/classroomApi.spec.ts src/__tests__/studentModeSidebar.spec.ts && uv run --extra dev --extra test python -m pytest -q myextension/tests/test_platform_registration.py

Expected: PASS; the browser sends only a boolean and never code evidence.

- [ ] **Step 7: Commit only Task 1 files**

~~~bash
Do not stage or commit recovery-baseline files. Record the focused test result in the final verification report and preserve all existing working-tree changes.
~~~

### Task 2: 构造受限的本地分析输入并保持固定简报优先

**Files:**

- Create: myextension/classroom_ai_analysis_input.py
- Create: myextension/tests/test_classroom_ai_analysis_input.py
- Modify: myextension/submission_coordinator.py
- Modify: myextension/tests/test_submission_coordinator.py

**Interfaces:**

- Consumes: profile: Mapping[str, object], detail: Mapping[str, object], delivered evidence chunk ranges.
- Produces: build_analysis_input(profile, detail, evidence_ranges) -> dict[str, object] with lesson, knowledge_points, evidence_events, and code_snapshots; the serialized snapshots total at most 12,000 characters.
- SubmissionCoordinator.submit(..., request_ai_analysis: bool) stores the flag and one durable payload on its first invocation; retries use the stored payload, not newly collected source.

- [ ] **Step 1: Write failing pure-builder tests**

In myextension/tests/test_classroom_ai_analysis_input.py, create a detail fixture with writing, failure-run, edit-after-failure, success-run, cell_source, error_message, document_name, and absolute-path literals. Assert an authorized build produces only:

~~~python
assert set(payload) == {"lesson", "knowledge_points", "evidence_events", "code_snapshots"}
assert [event["event_id"] for event in payload["evidence_events"]] == [
    "chunk-1#event-1", "chunk-1#event-2", "chunk-1#event-3", "chunk-1#event-4"
]
assert "error_message" not in json.dumps(payload, ensure_ascii=False)
assert "/Users/" not in json.dumps(payload, ensure_ascii=False)
assert sum(len(row["source"]) for row in payload["code_snapshots"]) <= 12_000
~~~

Add a 13,000-character source fixture asserting deterministic truncation, and an empty-evidence fixture asserting evidence_events == [] with no synthetic event id.

- [ ] **Step 2: Run builder tests to verify they fail because the module does not exist**

Run: uv run --extra dev --extra test python -m pytest -q myextension/tests/test_classroom_ai_analysis_input.py

Expected: FAIL with ModuleNotFoundError: myextension.classroom_ai_analysis_input.

- [ ] **Step 3: Write minimal pure allowlist builder**

Implement MAX_ANALYSIS_SNAPSHOT_CHARACTERS = 12_000 and MAX_ANALYSIS_EVENTS = 20. Select only code_writing, code_deletion, code_paste, and code_execution events; map their local session_seq into an actually delivered chunk-N#event-M range. Emit fixed category labels (edit, run_failure, run_success) and process descriptions without source, filenames, error strings, or output. For snapshots, retain only the allowed event_id and source, redact absolute-path patterns and secrets using the existing session-log sanitization conventions, truncate in event order within the shared 12,000-character budget, and never retain document metadata. Use the profile title and only id, name, description, question, and evidence criteria from each knowledge point.

- [ ] **Step 4: Extend coordinator tests before changing coordinator code**

In myextension/tests/test_submission_coordinator.py, add:

~~~python
result = coordinator.submit(
    session_id, reason="student_manual", cutoff_at=cutoff, request_ai_analysis=False
)
assert "analysis_input" not in client.submissions[0]

authorized = coordinator.submit(
    second_session_id, reason="student_manual", cutoff_at=cutoff, request_ai_analysis=True
)
assert authorized.status == "submitted"
assert client.submissions[1]["request_ai_analysis"] is True
assert client.submissions[1]["analysis_input"]["code_snapshots"]
~~~

Also assert the persisted retry state keeps exactly the originally bounded input and a later retry does not call the builder again.

- [ ] **Step 5: Run coordinator tests to verify they fail for the missing authorization parameter and payload**

Run: uv run --extra dev --extra test python -m pytest -q myextension/tests/test_submission_coordinator.py

Expected: FAIL because SubmissionCoordinator.submit has no request_ai_analysis parameter and all current payloads lack analysis_input.

- [ ] **Step 6: Write minimal coordinator integration**

Update SubmissionCoordinator.submit and _prepare_payload to accept the boolean. Always call export_classroom_brief first. Only in the authorized branch call get_detail, resolve delivered evidence ranges, call build_analysis_input, and include:

~~~python
{
    # existing summary / knowledge_points / process_overview / issues
    "request_ai_analysis": True,
    "analysis_input": bounded_input,
}
~~~

The unselected branch includes request_ai_analysis false and no analysis_input. Add the flag to durable state validation so a retry cannot silently change consent. Preserve the existing automatic-evaluation dirty change exactly; it remains an independent producer of baseline knowledge-point fields.

- [ ] **Step 7: Run local builder and coordinator tests to verify they pass**

Run: uv run --extra dev --extra test python -m pytest -q myextension/tests/test_classroom_ai_analysis_input.py myextension/tests/test_submission_coordinator.py

Expected: PASS; the authorized payload is bounded and the unselected payload has no source-derived fields.

- [ ] **Step 8: Commit only Task 2 files**

~~~bash
Do not stage or commit recovery-baseline files. Record the focused test result in the final verification report and preserve all existing working-tree changes.
~~~

### Task 3: 持久化私有输入、强制方案 AI 政策并更新安全简报契约

**Files:**

- Create: services/classroom-sync/migrations/versions/0004_private_brief_analysis_input.py
- Modify: services/classroom-sync/src/classroom_sync/models.py
- Modify: services/classroom-sync/src/classroom_sync/routers/plugin.py
- Modify: services/classroom-sync/src/classroom_sync/services/briefs.py
- Modify: services/classroom-sync/src/classroom_sync/main.py
- Modify: contracts/classroom/v1/student-brief.schema.json
- Modify: services/classroom-sync/tests/integration/test_briefs.py
- Modify: services/classroom-sync/tests/integration/test_migrations.py
- Modify: services/classroom-sync/tests/contract/test_schemas.py
- Create: services/classroom-sync/tests/unit/test_plugin_submit_ai_contract.py

**Interfaces:**

- Consumes: plugin payload { ..., request_ai_analysis: bool, analysis_input?: EvidenceConstrainedAnalysisInput } and the session's immutable (plan_id, plan_version) pair.
- Produces: ClassroomBriefAnalysisJob.analysis_input: dict[str, object], with StudentBrief.payload["ai_analysis"] containing only a teacher-safe result or null.
- BriefService.submit(..., analysis_input: Mapping[str, object] | None) resolves the matching PlanVersion itself. It determines not_requested, pending, or immediate unavailable without ever embedding private input in the brief payload; router-supplied ai_policy is never trusted.

- [ ] **Step 1: Write failing service and route integration tests**

Add to services/classroom-sync/tests/integration/test_briefs.py:

~~~python
base = service.submit(IDS["session"], valid_content(), reason="student_manual")
assert base.payload["ai_analysis_status"] == "not_requested"
assert list(session.scalars(select(ClassroomBriefAnalysisJob))) == []

pending = service.submit(
    IDS["session"], valid_content(), reason="student_manual",
    request_ai_analysis=True, analysis_input=valid_analysis_input(),
)
assert pending.payload["ai_analysis_status"] == "pending"
assert "analysis_input" not in pending.payload
assert job.analysis_input == valid_analysis_input()
~~~

Seed `PlanVersion(plan_id=IDS["plan"], version=1, ai_policy="allowed")` in the shared fixture so the happy path relies on the same immutable lookup as production. Add a separate `ai_policy="prohibited"` fixture, submit true + valid input, and assert `not_requested`, no `ClassroomBriefAnalysisJob`, and no call to the fake analysis generator. Use the HTTP plugin route to assert false + input and true without input receive a stable 422 envelope with no echoed payload; true + malformed/over-limit input receives the same safe envelope; true + valid input creates one job; a teacher GET /brief response has no analysis_input, code_snapshots, source string, or path.

- [ ] **Step 2: Run integration tests to verify they fail**

Run: services/classroom-sync/.venv/bin/python -m pytest -q services/classroom-sync/tests/integration/test_briefs.py

Expected: FAIL because the current recovery baseline accepts a requested analysis regardless of the immutable plan ai_policy and FastAPI returns its default request-validation body for malformed plugin input.

- [ ] **Step 3: Write migration and JSON-schema failure tests**

In test_migrations.py, migrate an existing classroom_brief_analysis_jobs table through head and assert analysis_input is a non-null JSON object defaulting to {} for legacy rows. In test_schemas.py, validate a brief whose ai_analysis is null; add an invalid result containing source and assert schema validation fails once the structured result contract is introduced.

- [ ] **Step 4: Run migration and contract tests to verify they fail**

Run: services/classroom-sync/.venv/bin/python -m pytest -q services/classroom-sync/tests/integration/test_migrations.py services/classroom-sync/tests/contract/test_schemas.py

Expected: FAIL because revision 0004 and the new safe analysis schema do not exist.

- [ ] **Step 5: Write minimal persistence, policy enforcement, and safe-base implementation**

Keep analysis_input as the non-null private JSON column introduced by migration 0004_private_brief_analysis_input. Type the plugin field as the closed `BriefAnalysisInput` Pydantic model, reject inconsistent authorization/input combinations before calling the service, and map `RequestValidationError` to one fixed 422 classroom error envelope without returning `detail` or request data. In `BriefService.submit`, query the PlanVersion matching the trusted MonitorSession plan_id and plan_version; compute `analysis_permitted = plan.ai_policy == "allowed" and request_ai_analysis`. When false, emit a fixed brief with ai_analysis_status="not_requested", no job and no private input; when true but AI runtime is absent, emit `unavailable`, no job and no private input; otherwise create one pending job with the validated private input. Extend the schema ai_analysis property to the new exact safe object shape, but do not add analysis_input anywhere in it.

- [ ] **Step 6: Run platform integration, migration, and contract tests to verify they pass**

Run: services/classroom-sync/.venv/bin/python -m pytest -q services/classroom-sync/tests/integration/test_briefs.py services/classroom-sync/tests/integration/test_migrations.py services/classroom-sync/tests/contract/test_schemas.py

Expected: PASS; route/service tests prove no unselected or plan-prohibited source leaves Jupyter, no job exists in either case, invalid submit bodies have a stable safe error, and no teacher payload includes job input.

- [ ] **Step 7: Commit only Task 3 files**

~~~bash
Do not stage or commit while either worktree contains pre-existing changes. List only the Task 3 files changed during this turn for the user's later review.
~~~

### Task 4: 实现逐知识点的证据约束 AI worker

**Files:**

- Modify: services/classroom-sync/src/classroom_sync/services/brief_analysis.py
- Modify: services/classroom-sync/src/classroom_sync/services/briefs.py
- Modify: services/classroom-sync/tests/unit/test_brief_analysis.py
- Modify: services/classroom-sync/tests/integration/test_briefs.py

**Interfaces:**

- Consumes: ClassroomBriefAnalysisJob.analysis_input validated as EvidenceConstrainedAnalysisInput.
- Produces: BriefAiAnalysis { knowledge_point_analyses: list[KnowledgePointAnalysis], teacher_note: str } where each analysis has knowledge_point_id, status, evidence_event_ids, teaching_suggestion.
- BriefAnalysisJobService._source_for_leased_job() no longer reads or derives model input from StudentBrief.payload.

- [ ] **Step 1: Write failing model-output and prompt-boundary tests**

Replace generic overview tests in test_brief_analysis.py with a valid source containing two knowledge points and allowed ids chunk-1#event-1 and chunk-1#event-2. Assert the provider request includes lesson, point ids/names, safe evidence descriptions and snapshots, but excludes absolute paths, output text, access_token, and student identity. Assert a valid provider response parses:

~~~python
{
  "knowledge_point_analyses": [{
    "knowledge_point_id": "KP_DICT0001",
    "status": "observed",
    "evidence_event_ids": ["chunk-1#event-2"],
    "teaching_suggestion": "请追问默认值分支的理由。",
  }],
  "teacher_note": "仅反映本次过程证据，仍需教师复核。",
}
~~~

- [ ] **Step 2: Run unit tests to verify they fail because the existing generic response schema is incompatible**

Run: services/classroom-sync/.venv/bin/python -m pytest -q services/classroom-sync/tests/unit/test_brief_analysis.py

Expected: FAIL because BriefAiAnalysis currently requires learning_overview, free-form observations, and suggestions instead of point-level results.

- [ ] **Step 3: Add failure cases for evidence and safety validation**

Add independent tests that reject: an unknown event id, observed with no ids, not_observed with ids, a missing or duplicated input knowledge-point id, a raw code fence, /Users/student/notebook.py, s3://private-bucket/..., https://storage.example/..., or a suggestion longer than 500 characters. Assert each becomes UpstreamUnavailableError("ai_brief_analysis_response_invalid", retryable=False) before persistence.

- [ ] **Step 4: Run rejection tests to verify they fail for the missing validator**

Run: services/classroom-sync/.venv/bin/python -m pytest -q services/classroom-sync/tests/unit/test_brief_analysis.py -k rejects

Expected: FAIL because current validation accepts generic string lists and does not compare returned ids to job input.

- [ ] **Step 5: Write minimal strict worker implementation**

Define Pydantic models for lesson, point, evidence event, bounded snapshot, and point analysis. Use Literal for the four statuses; validate input text lengths and the 12,000-character snapshot aggregate. In OpenAiBriefAnalysisService.messages_for, instruct the provider to return only the new JSON contract, reason only from supplied evidence, never grade or claim answer correctness, and require valid event ids. After parsing, run a source-aware validation that exactly matches input point ids, checks unique IDs, enforces event rules, and sanitizes displayed text using path/URL/code-fence guards. Change _source_for_leased_job to validate job.analysis_input; malformed or empty legacy input becomes a terminal safe unavailable failure rather than reading a brief. Keep BriefService.record_analysis_failure behavior that appends an unavailable revision without changing base fields.

- [ ] **Step 6: Add worker persistence regression tests**

In test_briefs.py, run a fake valid point-level generator. Assert revision 2 has ai_analysis_status == "ready", preserves summary and knowledge_points from revision 1, stores only knowledge_point_analyses and teacher_note, and that the StudentBrief row contains neither analysis_input nor snapshot source. Add a fake invalid result test that ends at unavailable with the original fixed brief intact.

- [ ] **Step 7: Run unit and integration worker tests to verify they pass**

Run: services/classroom-sync/.venv/bin/python -m pytest -q services/classroom-sync/tests/unit/test_brief_analysis.py services/classroom-sync/tests/integration/test_briefs.py

Expected: PASS; all four rejected-output classes remain out of teacher-visible data.

- [ ] **Step 8: Commit only Task 4 files**

~~~bash
Do not stage or commit recovery-baseline files. List any new Task 4 test or implementation files in the final verification report.
~~~

### Task 5: 安全显示教师端逐知识点 AI 分析

**Files:**

- Modify: ../lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/types.ts
- Modify: ../lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/api.ts
- Modify: ../lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/components/StudentBriefPanel.vue
- Modify: ../lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/__tests__/api.test.ts
- Modify: ../lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts

**Interfaces:**

- Consumes: wire ai_analysis.knowledge_point_analyses[] and teacher_note.
- Produces: ClassroomBriefAiAnalysis { knowledgePointAnalyses, teacherNote }, where each presentation item has a typed status, non-sensitive evidenceEventIds, and teachingSuggestion.

- [ ] **Step 1: Write failing frontend mapper tests**

In api.test.ts, feed a ready brief with two valid structured analyses and assert the mapper returns camel-case fields and safe status literals. Add invalid wire inputs for an unknown status, duplicate event ids, observed with no evidence, not_observed with evidence, source code in teaching_suggestion, https://..., and /Users/...; assert aiAnalysis is null for each.

- [ ] **Step 2: Run focused mapper test to verify it fails for the obsolete generic shape**

Run: npm run test -- run src/modules/classroom-monitoring/__tests__/api.test.ts

Expected: FAIL because mapAiAnalysis expects learning_overview, observations, and suggestions.

- [ ] **Step 3: Write failing teacher-panel rendering tests**

Replace the generic ready-analysis fixture in StudentBriefPanel.test.ts with a structured result. Assert the ready card includes each point name, “已观察”/“部分观察”, chunk-1#event-2, its teaching suggestion, and “辅助分析，不自动评分”. Assert it does not render any source-like string, absolute path, object key, provider URL, or raw run-output string.

- [ ] **Step 4: Run the panel test to verify it fails for the missing point-level card**

Run: npm run test -- run src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts

Expected: FAIL because the template only displays generic overview, observation, and suggestion lists.

- [ ] **Step 5: Write minimal strict mapper and template implementation**

In types.ts, add ClassroomEvidenceAnalysisStatus and a label function mapping to the four Chinese labels. In api.ts, replace generic mapping with a closed-record mapper that validates point IDs, bounded event IDs matching chunk-<integer>#event-<integer>, uniqueness, status/evidence rules, and redacted safe text. In StudentBriefPanel.vue, render exactly the validated point-level list plus teacherNote; do not interpolate any unknown wire fields. Change not_requested label to “学生未申请 AI 分析” so it no longer implies unavailable configuration.

- [ ] **Step 6: Run focused frontend tests to verify they pass**

Run: npm run test -- run src/modules/classroom-monitoring/__tests__/api.test.ts src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts

Expected: PASS; only supported fields render and malformed/provider-leaked data remains absent.

- [ ] **Step 7: Commit only Task 5 files from the frontend worktree**

~~~bash
Do not stage or commit. This protects the pre-existing Plan Wizard and automatic-evaluation changes in the same frontend worktree.
~~~

Run this command with working directory ../lab-platform-frontend-classroom-ui; do not stage its existing Plan Wizard changes.

### Task 6: 课堂建议回归、跨端质量门禁与交接

**Files:**

- Modify only if a test exposes a direct regression: services/classroom-sync/src/classroom_sync/services/plan_suggestions.py, services/classroom-sync/src/classroom_sync/routers/suggestions.py, and their existing tests.
- Modify: HANDOVER_CLASSROOM_AI_ANALYSIS_2026-08-18.md only to append actual executed verification evidence and remaining demo-stage work.

**Interfaces:**

- Consumes: existing teacher plan-suggestion API and UI tests plus completed Tasks 1–5.
- Produces: evidence that teacher “AI 生成建议” remains editable and that all three applications build/test without moving to Docker deployment.

- [ ] **Step 1: Run teacher AI-suggestion regression tests before any compatibility fix**

Run: services/classroom-sync/.venv/bin/python -m pytest -q services/classroom-sync/tests/unit/test_plan_suggestions.py services/classroom-sync/tests/integration/test_plan_suggestions_route.py

Expected: PASS. If a test fails, first reproduce it with its focused test, then make the smallest compatible fix only in the files listed above and rerun the same command. Do not alter AI evidence-analysis behavior to hide a suggestion failure.

- [ ] **Step 2: Run full Jupyter unit tests and production TypeScript build**

Run: node_modules/.bin/jest --runInBand && node_modules/.bin/tsc

Expected: PASS; the student consent UI compiles with the extension.

- [ ] **Step 3: Run classroom-service full checks**

Run: services/classroom-sync/.venv/bin/python -m pytest -q services/classroom-sync/tests && services/classroom-sync/.venv/bin/ruff check services/classroom-sync/src services/classroom-sync/tests && services/classroom-sync/.venv/bin/mypy services/classroom-sync/src

Expected: PASS; database, route, worker, schema, and static contracts remain consistent.

- [ ] **Step 4: Run teacher frontend tests and build in its own worktree**

Run from ../lab-platform-frontend-classroom-ui: npm run test -- run src/modules/classroom-monitoring/__tests__/api.test.ts src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts && npm run build

Expected: PASS; safe analysis rendering and the editable teacher AI-suggestion flow compile together.

- [ ] **Step 5: Inspect scope before reporting implementation completion**

Run in both worktrees:

~~~bash
git diff --check
git status --short
git log --oneline -5
~~~

Expected: no whitespace errors; only the explicit task files are newly staged/committed, while prior user changes remain unmodified and visible as pre-existing work.

- [ ] **Step 6: Append truthful verification evidence to the handover**

Append each exact command, exit status, test count where printed, current source revision(s), and the remaining separate stage: “requires explicit approval to rebuild/restart local Docker services, start port 5175, and run one real teacher/student/AI smoke session; no data reset.” Do not write any key, token, source snapshot, output, path, or Provider response into the handover.

- [ ] **Step 7: Commit the handover evidence only after the commands succeed**

~~~bash
Do not stage or commit the handover automatically. Present the exact evidence text and changed-file list for user review first.
~~~

---

## Plan Self-Review

- **Spec coverage:** Tasks 1–2 implement explicit consent, fixed-brief priority, bounded local input and retry safety. Task 3 isolates private input and migrations from teacher brief payload. Task 4 enforces per-knowledge-point, evidence-only AI outputs and failure isolation. Task 5 limits teacher visibility to safe conclusions. Task 6 verifies teacher suggestion generation, all builds, scope, and handover.
- **Placeholder scan:** 计划未保留待填内容或不确定接口；每个任务均列出精确文件、命令、预期失败/通过状态和提交范围。
- **Type consistency:** Browser uses requestAiAnalysis internally and serializes request_ai_analysis; coordinator and plugin route use request_ai_analysis; platform persistence uses analysis_input; only the job owns private input; teacher brief exposes ai_analysis.
