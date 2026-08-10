# 插件课堂长时监控可靠性（阶段 1）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** 在不改 BAMS 或 FinColab 后端的前提下，让插件持久保存未确认事件、网页重开后自动续接同一会话、按课堂超时生成基础简报，并在学生侧边栏展示简报。

**Architecture:** TypeScript 前端把带序列号的事件先写入 IndexedDB，再上传至现有 Jupyter Server API；只有收到回执才删除本地副本。重开时以服务端最后连续序号为准重放未确认事件。Python 服务端为 finalized 或 abandoned 会话生成确定性 \`classroom_brief.json\`；学生课堂镜像设置 300 秒超时，其他环境继续使用 1800 秒默认值。

**Tech Stack:** TypeScript 5.5、JupyterLab 4、IndexedDB、Jest、Python 3、Jupyter Server/Tornado、JSON Schema/OpenAPI、pytest。

**Design source:** \`docs/superpowers/specs/2026-08-10-fincolab-bams-classroom-monitoring-system-design.md\`

## Global Constraints

- 代码基线必须包含已验证的 \`a821218\` 0.2.2 自动补全修复；在隔离分支完成开发。
- 只修改当前插件仓库；不修改 BAMS 后端、FinColab 前端、FinColab 后端或真实平台数据。
- 本计划不实现教师端实时看板、跨学生同步、课堂票据或自动发送教师简报；它们需要共享后端。
- 保持现有事件 ID、批次哈希、上传路径、服务器回执和分析协议兼容。
- 未设置 \`JUPYTERLAB_BEHAVIOR_AUDIT_STALE_SESSION_TIMEOUT_SEC\` 时保持 1800 秒；课堂镜像显式设置为 300 秒。
- 自动超时只生成 \`partial\` 基础简报，不调用 AI，不伪造新操作，不标记为完整提交。
- 不使用“作弊”结论；不在代码、测试或文档中放 API Key、Cookie、Jupyter token、真实学生信息或真实平台 URL。
- 自动化测试不联网、不调用真实 AI、不启动真实 BAMS 容器。

---

## File Map

**Create**

- \`src/durableSegmentStore.ts\`：IndexedDB 未确认事件仓库。
- \`src/__tests__/durableSegmentStore.spec.ts\`：真实 IndexedDB 顺序、清理和隔离测试。
- \`myextension/classroom_brief.py\`：确定性一页简报渲染器。
- \`myextension/classroom_brief_automation.py\`：超时后的失败隔离简报回调。
- \`myextension/api_schemas/classroom-brief-v1.json\`：本地简报 schema。
- \`myextension/api_schemas/classroom-brief-response-v1.json\`：API 响应 schema。
- \`src/services/sessionBriefApi.ts\`：动态 base URL 简报客户端。
- \`src/services/__tests__/sessionBriefApi.spec.ts\`：简报 API 客户端测试。

**Modify**

- \`src/models/session.ts\`、\`src/behaviorEventUploader.ts\`、\`src/behaviorCapture.ts\`：持久队列和 collecting 会话恢复。
- \`src/ui/behaviorAnalysisSidebar.ts\`：自动恢复提示与学生本地简报。
- \`myextension/session_store.py\`、\`myextension/session_log_service.py\`、\`myextension/session_janitor.py\`、\`myextension/__init__.py\`：简报存储、超时配置和后台回调。
- \`myextension/routes.py\`、\`docs/openapi/myextension-v1.yaml\`：认证简报读取接口。
- 相关 Jest/pytest 测试、版本文件和部署文档。

---

### Task 1: 本地课堂简报领域闭环

**Files:**

- Create: \`myextension/classroom_brief.py\`
- Create: \`myextension/api_schemas/classroom-brief-v1.json\`
- Modify: \`myextension/session_store.py\`
- Modify: \`myextension/session_log_service.py\`
- Test: \`myextension/tests/test_session_store.py\`
- Test: \`myextension/tests/test_session_log_service.py\`

**Interfaces:**

- Produces: \`build_classroom_brief(detail: Mapping[str, object]) -> dict[str, object]\`。
- Produces: \`SessionStore.read_classroom_brief(session_id)\` and \`write_classroom_brief(session_id, brief)\`。
- Produces: \`SessionLogService.export_classroom_brief(session_id)\`。
- Brief fields: \`schema_version\`, \`session_id\`, \`status\`, \`data_completeness\`, \`active_duration_ms\`, \`run_summary\`, \`process_highlights\`, \`attention_message\`, \`generated_at\`。

- [ ] **Step 1: Write failing tests**

Add tests with independent literal expectations:

    def test_finalized_session_exports_complete_classroom_brief(tmp_path):
        brief = service_for(tmp_path).export_classroom_brief(finalized_session_id)
        assert brief["status"] == "complete"
        assert brief["data_completeness"] == "complete"
        assert brief["run_summary"] == "运行 2 次，其中 1 次成功、1 次失败"

    def test_abandoned_session_exports_partial_brief_without_ai(tmp_path):
        brief = service_for(tmp_path).export_classroom_brief(abandoned_session_id)
        assert brief["status"] == "partial"
        assert brief["attention_message"] == "监控中断，以下内容仅反映已保存的数据。"

    def test_classroom_brief_accepts_more_than_ten_thousand_events(tmp_path):
        assert service_for(tmp_path).export_classroom_brief(session_with_10_001_events)["status"] == "complete"

Also assert store methods reject non-canonical IDs, symlinks, and non-regular files.

Run:

    python -m pytest myextension/tests/test_session_store.py myextension/tests/test_session_log_service.py -q

Expected: FAIL because classroom brief code and storage do not exist.

- [ ] **Step 2: Implement minimum behavior**

Create a pure renderer that receives only the existing public session detail. Sum durations from writing/deletion/paste segments; count \`code_execution\` success/failure; emit no more than three fixed highlights. Mark abandoned sessions partial. Emit the exact attention message only for a \`page_away\` followed by a paste of at least 200 characters. Do not copy code text, error text, AI output, paths, configuration or identity.

Add \`classroom_brief.json\` store accessors that follow the existing \`training_record.json\` root-containment, private-permission and atomic-write rules. Generate the brief directly from \`get_detail()\`, not \`export_training_record()\`, so the training-record 10,000-item bound cannot prevent a brief.

- [ ] **Step 3: Verify GREEN**

Run:

    python -m pytest myextension/tests/test_session_store.py myextension/tests/test_session_log_service.py -q

Expected: new finalized, abandoned, 10,001-event and safe-file tests pass.

- [ ] **Step 4: Commit**

    git add myextension/classroom_brief.py myextension/api_schemas/classroom-brief-v1.json myextension/session_store.py myextension/session_log_service.py myextension/tests/test_session_store.py myextension/tests/test_session_log_service.py
    git commit -m "feat: add deterministic classroom session brief"

### Task 2: 可配置失活终结和简报回调

**Files:**

- Create: \`myextension/classroom_brief_automation.py\`
- Modify: \`myextension/session_janitor.py\`
- Modify: \`myextension/__init__.py\`
- Test: \`myextension/tests/test_analysis_job_store.py\`
- Test: \`myextension/tests/test_training_record_automation.py\`

**Interfaces:**

- Produces: \`stale_session_timeout() -> timedelta\`; accepts decimal 300–3600 seconds and defaults to 1800 seconds.
- Produces: \`ClassroomBriefRefresher.refresh(session_id: str) -> bool\`.
- Extends: \`SessionJanitor(..., on_abandoned: Callable[[str], None] | None = None)\`.

- [ ] **Step 1: Write failing tests**

    @pytest.mark.parametrize(
        ("configured", "expected_seconds"),
        [(None, 1800), ("300", 300), ("3600", 3600), ("299", 1800), ("3601", 1800), ("bad", 1800)],
    )
    def test_stale_session_timeout_is_bounded(monkeypatch, configured, expected_seconds):
        set_or_delete_timeout(monkeypatch, configured)
        assert stale_session_timeout().total_seconds() == expected_seconds

    def test_janitor_refreshes_one_brief_after_new_stale_abandonment(tmp_path):
        refreshed = []
        janitor = SessionJanitor(store, timeout=timedelta(minutes=5), on_abandoned=refreshed.append)
        assert janitor.run_once(now=stale_time) == [session_id]
        assert refreshed == [session_id]

    def test_janitor_does_not_repeat_brief_refresh_for_abandoned_session(tmp_path):
        janitor.run_once(now=stale_time)
        janitor.run_once(now=stale_time + timedelta(minutes=1))
        assert refreshed == [session_id]

Also prove a refresher exception returns \`False\`, logs only \`classroom_brief_refresh_failed\`, and preserves abandoned state.

Run:

    python -m pytest myextension/tests/test_analysis_job_store.py myextension/tests/test_training_record_automation.py -q

Expected: FAIL because timeout configuration, callback and refresher do not exist.

- [ ] **Step 2: Implement minimum behavior**

Add the bounded environment loader to \`session_janitor.py\`. Give \`SessionJanitor\` an optional callback and invoke it once for each ID returned by \`abandon_stale()\`; swallow callback failures. Implement the refresher parallel to the existing training-record refresher. In \`__init__.py\`, construct it from the existing \`SessionLogService\`, and construct the janitor with the configured timeout and callback.

- [ ] **Step 3: Verify GREEN**

    python -m pytest myextension/tests/test_analysis_job_store.py myextension/tests/test_training_record_automation.py -q

Expected: timeout bounds, one-time callback and failure isolation pass; existing janitor behavior remains green.

- [ ] **Step 4: Commit**

    git add myextension/classroom_brief_automation.py myextension/session_janitor.py myextension/__init__.py myextension/tests/test_analysis_job_store.py myextension/tests/test_training_record_automation.py
    git commit -m "feat: generate partial brief after classroom timeout"

### Task 3: IndexedDB 未确认事件仓库

**Files:**

- Create: \`src/durableSegmentStore.ts\`
- Create: \`src/__tests__/durableSegmentStore.spec.ts\`
- Modify: \`package.json\`
- Modify: lockfile if the project contains one after adding \`fake-indexeddb\`.

**Interfaces:**

    export interface IDurableSegmentStore {
      load(sessionId: string): Promise<IQueuedBehaviorSegment[]>;
      append(sessionId: string, segment: IQueuedBehaviorSegment): Promise<void>;
      removeThrough(sessionId: string, sessionSequence: number): Promise<void>;
      clear(sessionId: string): Promise<void>;
    }

- Production class: \`IndexedDbDurableSegmentStore\`.
- DB name: \`myextension-behavior-audit\`; store: \`unconfirmed-segments\`; composite key: \`[session_id, session_seq]\`.
- Error codes: \`durable_storage_unavailable\` and \`durable_storage_invalid\`.

- [ ] **Step 1: Write failing real-IndexedDB tests**

Use \`fake-indexeddb\` rather than a mocked store:

    it('returns queued segments in sequence order and keeps sessions isolated', async () => {
      await store.append(SESSION_ID, queued(2));
      await store.append(OTHER_SESSION_ID, queued(1, OTHER_SESSION_ID));
      await store.append(SESSION_ID, queued(1));
      await expect(store.load(SESSION_ID)).resolves.toEqual([queued(1), queued(2)]);
    });

    it('removes only server-confirmed sequences', async () => {
      await appendAll(store, SESSION_ID, [queued(1), queued(2), queued(3)]);
      await store.removeThrough(SESSION_ID, 2);
      await expect(store.load(SESSION_ID)).resolves.toEqual([queued(3)]);
    });

    it('rejects an event id from another session', async () => {
      await expect(store.append(SESSION_ID, { ...queued(1), event_id: 'other:1' }))
        .rejects.toMatchObject({ code: 'durable_storage_invalid' });
    });

Run:

    npm test -- --runInBand src/__tests__/durableSegmentStore.spec.ts

Expected: FAIL because the module does not exist.

- [ ] **Step 2: Implement minimum behavior**

Use one versioned IndexedDB database. Store exactly \`session_id\`, \`session_seq\` and the immutable segment. Validate the event ID matches the supplied session and sequence. Resolve only after transaction completion; reject abort/error as a stable error. \`load\` returns ascending sequence order. \`removeThrough\` and \`clear\` are restricted to the requested session. Do not fall back to localStorage or a memory-only queue.

- [ ] **Step 3: Verify GREEN**

    npm test -- --runInBand src/__tests__/durableSegmentStore.spec.ts

Expected: sequence ordering, session isolation, acknowledgement cleanup and invalid-ID tests pass.

- [ ] **Step 4: Commit**

    git add package.json yarn.lock src/durableSegmentStore.ts src/__tests__/durableSegmentStore.spec.ts
    git commit -m "feat: persist unconfirmed behavior segments in IndexedDB"

If no \`yarn.lock\` exists, omit it from \`git add\`; do not create a second lockfile.

### Task 4: 上传器先持久化、回执清理与自动续接

**Files:**

- Modify: \`src/models/session.ts\`
- Modify: \`src/behaviorEventUploader.ts\`
- Modify: \`src/behaviorCapture.ts\`
- Test: \`src/__tests__/behaviorEventUploader.spec.ts\`
- Test: \`src/__tests__/behaviorCapture.spec.ts\`

**Interfaces:**

- \`BehaviorEventUploader.start(session): Promise<void>\`
- \`BehaviorEventUploader.resume(session: ISessionState): Promise<void>\`
- \`IBehaviorCaptureController.resume(session: ISessionState): Promise<void>\`
- Resume accepts only \`session.status === 'collecting'\`.

- [ ] **Step 1: Write failing tests**

    it('does not upload a new segment before durable append completes', async () => {
      const appended = deferred<void>();
      const { uploader, upload } = createHarness({ durableStore: storeThatDefersAppend(appended) });
      await uploader.start(START_RESPONSE);
      uploader.enqueue(SEGMENT);
      await Promise.resolve();
      expect(upload).not.toHaveBeenCalled();
      appended.resolve();
      await uploader.flush();
      expect(upload).toHaveBeenCalledTimes(1);
    });

    it('removes only acknowledged durable segments after a receipt', async () => {
      await uploader.flush();
      expect(durableStore.removeThrough).toHaveBeenCalledWith(SESSION_ID, 1);
    });

    it('resumes only sequences after the server receipt', async () => {
      const { uploader, upload } = createHarness({ durableStore: storeWith([queued(1), queued(2), queued(3)]) });
      await uploader.resume({ ...STORED_COLLECTING_SESSION, last_contiguous_sequence: 1, received_event_count: 1 });
      await uploader.drain();
      expect(upload.mock.calls[0][2].segments.map(item => item.session_seq)).toEqual([2, 3]);
    });

    it('resumes a stored collecting session without starting a second server session', async () => {
      await capture.resume(STORED_COLLECTING_SESSION);
      expect(startSession).not.toHaveBeenCalled();
      expect(uploader.resume).toHaveBeenCalledWith(STORED_COLLECTING_SESSION);
      expect(capture.isEnabled()).toBe(true);
    });

Run:

    npm test -- --runInBand src/__tests__/behaviorEventUploader.spec.ts src/__tests__/behaviorCapture.spec.ts

Expected: FAIL because upload storage is memory-only and capture has no resume method.

- [ ] **Step 2: Implement minimum behavior**

Add \`durableStore\` as an uploader dependency with an IndexedDB default. Assign a sequence and event ID as today, then serialize append calls through a durable-write promise chain. Before creating a batch, wait for that chain. On durable write failure, stop accepting new segments and publish \`durable_storage_unavailable\`; do not upload a segment whose durable write failed.

On receipt, call \`removeThrough(sessionId, receipt.last_contiguous_sequence)\`. A cleanup failure must not reject the already-accepted server receipt; subsequent resume filters anything at or below server sequence. \`resume()\` loads durable entries, removes acknowledged entries, validates remaining sequences are continuous, restores them to memory, and never calls \`startSession\`. Successful finalization calls \`clear(sessionId)\`; clear failure remains safely replayable. Add a \`pagehide\` best-effort flush without claiming browser-close delivery is guaranteed.

- [ ] **Step 3: Verify GREEN**

    npm test -- --runInBand src/__tests__/behaviorEventUploader.spec.ts src/__tests__/behaviorCapture.spec.ts
    npm run build:lib

Expected: existing batching/retry/drain tests and new durability/recovery tests pass; TypeScript compiles.

- [ ] **Step 4: Commit**

    git add src/models/session.ts src/behaviorEventUploader.ts src/behaviorCapture.ts src/__tests__/behaviorEventUploader.spec.ts src/__tests__/behaviorCapture.spec.ts
    git commit -m "feat: resume durable behavior capture sessions"

### Task 5: 认证简报读取和学生侧边栏恢复

**Files:**

- Create: \`src/services/sessionBriefApi.ts\`
- Create: \`src/services/__tests__/sessionBriefApi.spec.ts\`
- Create: \`myextension/api_schemas/classroom-brief-response-v1.json\`
- Modify: \`myextension/routes.py\`
- Modify: \`docs/openapi/myextension-v1.yaml\`
- Modify: \`myextension/tests/test_pilot_api.py\`
- Modify: \`src/ui/behaviorAnalysisSidebar.ts\`
- Modify: \`src/ui/__tests__/behaviorAnalysisSidebar.spec.ts\`

**Interfaces:**

- \`GET /myextension/sessions/{session_id}/brief\`: authenticated; 200 for an existing brief, 409 \`classroom_brief_not_ready\` when absent, current public 400/404 handling for invalid/unknown IDs.
- \`getClassroomBrief(settings, sessionId): Promise<IClassroomBrief>\`: joins through \`URLExt.join(settings.baseUrl, ...)\`.
- Sidebar: stored collecting state calls \`capture.resume()\`; finalized/abandoned state loads the local brief.

- [ ] **Step 1: Write failing API and UI tests**

    async def test_classroom_brief_route_returns_valid_finalized_brief(jp_fetch):
        session_id = await finalize_fixture_session(jp_fetch)
        response = await jp_fetch(f"myextension/sessions/{session_id}/brief")
        assert response.code == 200
        openapi_validator("ClassroomBriefResponse").validate(response_json(response))

    async def test_classroom_brief_route_hides_unready_sessions(jp_fetch):
        assert (await jp_fetch(f"myextension/sessions/{collecting_id}/brief")).code == 409

    it('uses the dynamic Jupyter base url for classroom brief requests', async () => {
      await getClassroomBrief({ baseUrl: '/notebook_demo/' } as ServerConnection.ISettings, SESSION_ID);
      expect(request).toHaveBeenCalledWith(
        '/notebook_demo/myextension/sessions/' + encodeURIComponent(SESSION_ID) + '/brief',
        expect.anything()
      );
    });

    it('automatically resumes a stored collecting session and announces recovery', async () => {
      deps.getStoredActiveSession = jest.fn(async () => STORED_COLLECTING_SESSION);
      const sidebar = new BehaviorAnalysisSidebar(deps);
      await flush();
      expect(capture.resume).toHaveBeenCalledWith(STORED_COLLECTING_SESSION);
      expect(sidebar.node.textContent).toContain('已恢复本次监控');
    });

Run:

    python -m pytest myextension/tests/test_pilot_api.py -q
    npm test -- --runInBand src/services/__tests__/sessionBriefApi.spec.ts src/ui/__tests__/behaviorAnalysisSidebar.spec.ts

Expected: FAIL because the route, client and automatic-resume UI do not exist.

- [ ] **Step 2: Implement minimum behavior**

Register the exact \`/brief\` route before the broad session route. Read only from \`SessionLogService.get_classroom_brief()\`; never accept a path from the browser. Update JSON Schema and OpenAPI in the same change.

Create a small typed client that validates required fields and uses the authenticated Jupyter server request helper. In the sidebar, call resume for a stored collecting session; if recovery fails, retain the existing explicit abandon path. For finalized/abandoned sessions render only five visible summary items: completion state, completeness, active duration, run summary, and up to three highlights. Show attention text only when non-null. Copy must say “本地简报已保存，教师端同步将在后续接入”, never “已提交给教师”.

- [ ] **Step 3: Verify GREEN**

    python -m pytest myextension/tests/test_pilot_api.py -q
    npm test -- --runInBand src/services/__tests__/sessionBriefApi.spec.ts src/ui/__tests__/behaviorAnalysisSidebar.spec.ts
    npm run lint:check

Expected: API contract passes, page refresh resumes collecting sessions, terminated sessions show a concise local brief, and lint is clean.

- [ ] **Step 4: Commit**

    git add src/services/sessionBriefApi.ts src/services/__tests__/sessionBriefApi.spec.ts myextension/api_schemas/classroom-brief-response-v1.json myextension/routes.py docs/openapi/myextension-v1.yaml myextension/tests/test_pilot_api.py src/ui/behaviorAnalysisSidebar.ts src/ui/__tests__/behaviorAnalysisSidebar.spec.ts
    git commit -m "feat: show recovered classroom brief in sidebar"

### Task 6: 0.3.0 版本、课堂镜像说明和全量验证

**Files:**

- Modify: \`package.json\`
- Modify: \`pyproject.toml\`
- Modify: \`README.md\`
- Modify: \`启动说明.md\`
- Modify: \`项目交接文档.md\`
- Modify: \`deploy/bluedot/release-0.2.1/README.md\`

- [ ] **Step 1: Write failing release-boundary tests**

Add narrow metadata tests with literal expectations:

    def test_classroom_release_version_is_030():
        assert package_json_version() == "0.3.0"
        assert pyproject_version() == "0.3.0"

    def test_classroom_timeout_example_is_five_minutes():
        assert runtime_example_timeout_seconds() == 300

If the project has no safe metadata parser fixture, use its existing packaging metadata test location and keep prose validation for Step 3.

Run:

    python -m pytest -q

Expected: FAIL while the versions remain at their current values or no classroom deployment example exists.

- [ ] **Step 2: Update release metadata and docs**

Bump both package sources to \`0.3.0\`. Document that only a classroom student image sets \`JUPYTERLAB_BEHAVIOR_AUDIT_STALE_SESSION_TIMEOUT_SEC=300\`; a normal image keeps 1800 seconds. State exactly that recovery covers events already written to IndexedDB or BAMS, closure stops new capture, and this stage does not automatically send reports to FinColab. Mark \`/workspace/result/behavior-audit\` as a BAMS persistent-volume requirement, not a guarantee supplied by this plugin.

- [ ] **Step 3: Run complete quality gates**

    python -m pytest -q
    npm test -- --runInBand
    npm run lint:check
    npm run build:prod
    python -m build --wheel
    python -m zipfile -l dist/myextension-0.3.0-py3-none-any.whl
    shasum -a 256 dist/myextension-0.3.0-py3-none-any.whl
    rg -n "JUPYTERLAB_BEHAVIOR_AUDIT_STALE_SESSION_TIMEOUT_SEC|300|本地简报|FinColab" README.md 启动说明.md 项目交接文档.md deploy/bluedot/release-0.2.1/README.md

Expected: all local checks pass; wheel contains new Python modules, schemas and rebuilt labextension; documentation is explicit about the still-missing shared backend.

- [ ] **Step 4: Commit**

    git add package.json pyproject.toml README.md 启动说明.md 项目交接文档.md deploy/bluedot/release-0.2.1/README.md
    git commit -m "docs: prepare 0.3.0 classroom reliability release"

## Plan Self-Review

- **Spec coverage:** Tasks 1–2 add deterministic partial/complete briefs and five-minute classroom timeout without changing ordinary defaults. Tasks 3–4 add persisted upload and collecting-session recovery. Task 5 exposes a concise student-side brief. Task 6 validates and documents the local artifact.
- **Explicitly excluded:** FinColab identity/tickets, teacher real-time view, cross-container summary, automatic teacher delivery, BAMS backend changes and BAMS persistent-volume provisioning. No frontend mock may claim these are complete.
- **Consistency:** only collecting sessions can resume; finalized creates \`complete\`; janitor abandonment creates \`partial\`; the local brief is never described as teacher-submitted.
- **Security:** no path input, secret, role assertion or raw code is introduced. Browser storage contains only unconfirmed event records.

## Execution Stop Point

Stop after a locally verified \`0.3.0\` wheel and the documented classroom-image configuration. Begin the FinColab/shared-service phase only after the platform team supplies a backend integration repository and BAMS persistence/network confirmation.

