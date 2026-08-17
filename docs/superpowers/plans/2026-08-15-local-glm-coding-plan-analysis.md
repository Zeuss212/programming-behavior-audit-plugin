# Local GLM Coding Plan Classroom Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only GLM Coding Plan configuration, teacher plan suggestions, and durable asynchronous teaching analysis for submitted student briefs.

**Architecture:** `sync-api` keeps all provider credentials server-side and writes the deterministic student brief before it schedules an analysis job. The existing local worker leases durable jobs, submits only allowlisted brief text to the OpenAI-compatible GLM Coding Plan endpoint, then appends a validated analysis revision or an `unavailable` terminal revision. The frontend reads the persisted state through the existing teacher endpoints and never receives credentials.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, httpx, PostgreSQL/SQLite tests, Docker Compose, Vue 3, TypeScript, Vitest.

## Global Constraints

- Work only in the existing isolated branches `codex/classroom-main-integration` and `codex/classroom-ui`; do not modify, merge, reset, push, deploy, or stage files in root `main`.
- Coding Plan defaults are `https://open.bigmodel.cn/api/coding/paas/v4`, model `glm-5.2`, and a maximum service timeout of 30 seconds.
- Store the real Key only in ignored `deploy/classroom/local-demo/.env.ai`; never write it to Git, browser storage, Jupyter, logs, error messages, tests, or documentation.
- The plugin must not select `ai_analysis_status`; the server derives `pending` or `not_requested` solely from complete server configuration.
- Do not send raw code, raw logs, object keys, presigned URLs, tickets, bearer tokens, plugin tokens, or provider credentials to GLM.
- Student submission is successful even when AI is unconfigured, times out, returns invalid JSON, or exhausts retries.
- AI output is auxiliary teaching analysis, not an automatic score, disciplinary finding, or final evaluation.
- Real GLM calls require a later explicit user confirmation because they consume Coding Plan quota; all automated tests use `httpx.MockTransport`.

## File Structure

### Backend worktree: `.worktrees/classroom-main-integration`

| Path | Responsibility |
| --- | --- |
| `services/classroom-sync/src/classroom_sync/services/plan_suggestions.py` | Reuse a provider-safe settings object and OpenAI-compatible completion boundary for teacher suggestions. |
| `services/classroom-sync/src/classroom_sync/services/brief_analysis.py` | Build an allowlisted AI input, validate GLM JSON, lease/retry jobs, and commit terminal revisions. |
| `services/classroom-sync/src/classroom_sync/services/briefs.py` | Persist baseline `pending` briefs and append `ready`/`unavailable` analysis revisions atomically with job state. |
| `services/classroom-sync/src/classroom_sync/models.py` | Define durable `ClassroomBriefAnalysisJob`. |
| `services/classroom-sync/migrations/versions/0002_brief_analysis_jobs.py` | Create/drop the analysis job table and its idempotency/lease indexes. |
| `contracts/classroom/v1/student-brief.schema.json` | Permit a validated optional `ai_analysis` object without weakening existing brief fields. |
| `services/classroom-sync/src/classroom_sync/runtime.py` | Build the analysis service only from complete environment-derived settings. |
| `services/classroom-sync/src/classroom_sync/application.py` | Inject `brief_analysis_service` into the worker/runtime dependency graph. |
| `services/classroom-sync/src/classroom_sync/routers/plugin.py` | Ignore deprecated client status and request a job only when server AI is configured. |
| `services/classroom-sync/src/classroom_sync/services/read_models.py` | Include allowlisted AI status in the teacher monitoring DTO. |
| `services/classroom-sync/src/classroom_sync/worker.py`, `worker_main.py` | Poll deadline and AI jobs in the same restartable local worker without stopping on a failed model request. |
| `myextension/submission_coordinator.py` | Stop emitting an AI state; retain only the deterministic compact brief payload. |
| `deploy/classroom/local-demo/docker-compose.yml`, `scripts/start_local_classroom_demo.sh` | Load optional ignored provider configuration only into `sync-api` and `deadline-worker`. |
| `deploy/classroom/local-demo/.env.ai.example`, `deploy/classroom/local-demo/README.md` | Give a Key-free Coding Plan setup, restart, state meanings, and manual demo procedure. |

### Frontend worktree: `.worktrees/lab-platform-frontend-classroom-ui`

| Path | Responsibility |
| --- | --- |
| `src/modules/classroom-monitoring/types.ts` | Type `ClassroomBriefAiAnalysis` and monitoring-level AI status. |
| `src/modules/classroom-monitoring/api.ts` | Parse/redact the analysis payload and map the monitoring status. |
| `src/modules/classroom-monitoring/components/ClassroomStudentTable.vue` | Show the AI lifecycle in desktop and narrow monitoring layouts. |
| `src/modules/classroom-monitoring/components/StudentBriefPanel.vue` | Render the auxiliary analysis card only when ready, with safe empty/error states. |
| existing Vitest files | Lock API mapping, redaction, AI states, and teacher-visible table behavior. |

---

### Task 1: Harden the reusable Coding Plan provider boundary

**Files:**
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/src/classroom_sync/config.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/src/classroom_sync/services/plan_suggestions.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/tests/test_runtime.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/tests/unit/test_plan_suggestions.py`

**Interfaces:**
- Consumes: `Settings.ai_base_url`, `Settings.ai_model`, `Settings.ai_api_key`, and `Settings.ai_timeout_seconds`.
- Produces: `AiProviderSettings.from_settings(settings) -> AiProviderSettings | None` and `OpenAiCompletionClient.complete(messages, *, temperature, max_tokens) -> str`.
- Compatibility: retain `AiSuggestionSettings` as an import alias or update every internal import in this task; existing plan-suggestion route behavior stays unchanged.

- [ ] **Step 1: Write failing configuration and completion-boundary tests**

  Add tests that require 30 to be accepted, 31 rejected, verify the Coding Plan URL receives `/chat/completions` exactly once, and assert credentials are present only in the Authorization header:

  ```python
  def test_coding_plan_settings_build_the_openai_completion_url() -> None:
      settings = configured_settings(
          ai_base_url="https://open.bigmodel.cn/api/coding/paas/v4",
          ai_model="glm-5.2",
          ai_timeout_seconds=30,
      )
      provider = AiProviderSettings.from_settings(settings)

      assert provider is not None
      assert OpenAiCompletionClient.completion_url(provider.base_url) == (
          "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
      )
  ```

- [ ] **Step 2: Run the focused tests and verify they fail for the new boundary**

  Run:

  ```sh
  uv run --directory services/classroom-sync pytest tests/test_runtime.py tests/unit/test_plan_suggestions.py -q
  ```

  Expected: FAIL because `AiProviderSettings` and `OpenAiCompletionClient` do not exist yet.

- [ ] **Step 3: Extract provider settings and a no-log completion client**

  In `plan_suggestions.py`, extract the common validated provider configuration and completion method. Keep provider response parsing at each feature layer, but do not duplicate URL or HTTP exception logic:

  ```python
  @dataclass(frozen=True)
  class AiProviderSettings:
      base_url: str
      model: str
      api_key: str = field(repr=False)
      timeout_seconds: int = 15

      @classmethod
      def from_settings(cls, settings: Settings) -> AiProviderSettings | None:
          values = (settings.ai_base_url, settings.ai_model, settings.ai_api_key)
          if all(value is None for value in values):
              return None
          if any(value is None for value in values) or not cls._is_safe_base_url(settings.ai_base_url):
              raise AiSuggestionUnavailableError("ai_suggestion_not_configured")
          if not isinstance(settings.ai_timeout_seconds, int) or not 1 <= settings.ai_timeout_seconds <= 30:
              raise AiSuggestionUnavailableError("ai_suggestion_not_configured")
          return cls(settings.ai_base_url.rstrip("/"), settings.ai_model, settings.ai_api_key,
                     settings.ai_timeout_seconds)

  class OpenAiCompletionClient:
      def complete(self, messages: list[dict[str, str]], *, temperature: float, max_tokens: int) -> str:
          try:
              response = self._client.post(self.completion_url(self._settings.base_url),
                  headers={"Authorization": f"Bearer {self._settings.api_key}"},
                  json={"model": self._settings.model, "temperature": temperature,
                        "max_tokens": max_tokens, "messages": messages},
                  timeout=self._settings.timeout_seconds)
              payload = response.json()
              content = payload["choices"][0]["message"]["content"]
          except (httpx.RequestError, ValueError, KeyError, IndexError, TypeError) as error:
              raise UpstreamUnavailableError("ai_provider_unavailable") from error
          if response.status_code >= 400 or not isinstance(content, str):
              raise UpstreamUnavailableError("ai_provider_unavailable")
          return content
  ```

  Make `OpenAiPlanSuggestionService` call this client and retain its existing strict plan JSON validation. Do not log the request headers, request body, error detail, or raw provider response.

- [ ] **Step 4: Run focused tests and static checks**

  Run:

  ```sh
  uv run --directory services/classroom-sync pytest tests/test_runtime.py tests/unit/test_plan_suggestions.py -q
  uv run --directory services/classroom-sync ruff check src tests
  uv run --directory services/classroom-sync mypy src
  ```

  Expected: all commands pass; current plan suggestions remain structurally unchanged.

- [ ] **Step 5: Commit the provider boundary**

  ```sh
  git add services/classroom-sync/src/classroom_sync/config.py \
    services/classroom-sync/src/classroom_sync/services/plan_suggestions.py \
    services/classroom-sync/tests/test_runtime.py \
    services/classroom-sync/tests/unit/test_plan_suggestions.py
  git commit -m "refactor: share classroom AI provider boundary"
  ```

### Task 2: Add the versioned brief-analysis contract and durable job table

**Files:**
- Modify: `.worktrees/classroom-main-integration/contracts/classroom/v1/student-brief.schema.json`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/src/classroom_sync/models.py`
- Create: `.worktrees/classroom-main-integration/services/classroom-sync/migrations/versions/0002_brief_analysis_jobs.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/tests/contract/test_schemas.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: current `student_brief` v1 JSON payload and `StudentBrief.id`.
- Produces: nullable `ai_analysis` brief property and one `ClassroomBriefAnalysisJob` per source `StudentBrief` row.
- Invariants: no existing v1 field changes; `source_brief_id` is unique; job references cascade only with its source brief; attempts starts at 0.

- [ ] **Step 1: Write failing contract/migration tests**

  Extend the student-brief contract fixture with a valid AI result and an invalid over-length entry. Extend migration assertions with the new table and the unique source-brief constraint:

  ```python
  def test_student_brief_contract_accepts_only_bounded_ai_analysis(registry) -> None:
      payload = valid_student_brief_payload()
      payload["ai_analysis"] = {
          "learning_overview": "已完成基础字典读取并进行了两次验证。",
          "evidence_based_observations": ["提交摘要显示已完成一次修正。"],
          "teaching_suggestions": ["追问缺失键的处理方式。"],
      }
      registry.validate("student-brief", payload)

      payload["ai_analysis"]["learning_overview"] = "长" * 1001
      with pytest.raises(Exception):
          registry.validate("student-brief", payload)
  ```

- [ ] **Step 2: Run contract/migration tests and verify they fail**

  Run:

  ```sh
  uv run --directory services/classroom-sync pytest tests/contract/test_schemas.py tests/integration/test_migrations.py -q
  ```

  Expected: FAIL because the schema has no `ai_analysis` field and the migration has no job table.

- [ ] **Step 3: Add the JSON schema, SQLAlchemy model, and reversible migration**

  Add optional `ai_analysis` to the brief schema. It is either absent/null for historical and baseline records, or an object with three bounded fields:

  ```json
  "ai_analysis": {
    "type": ["object", "null"],
    "additionalProperties": false,
    "required": ["learning_overview", "evidence_based_observations", "teaching_suggestions"],
    "properties": {
      "learning_overview": {"type": "string", "minLength": 1, "maxLength": 1000},
      "evidence_based_observations": {"type": "array", "minItems": 1, "maxItems": 5,
        "items": {"type": "string", "minLength": 1, "maxLength": 500}},
      "teaching_suggestions": {"type": "array", "minItems": 1, "maxItems": 5,
        "items": {"type": "string", "minLength": 1, "maxLength": 500}}
    }
  }
  ```

  Define `ClassroomBriefAnalysisJob` with `source_brief_id` foreign-keyed to `student_briefs.id` and unique, `run_at`, `status`, `lease_owner`, `lease_expires_at`, `attempts`, `failure_code`, `completed_at`, `created_at`, and `updated_at`. Use the same `pending → leased → completed` lease convention as `ClassroomDeadlineJob`; a retry returns to `pending` with a later `run_at`.

  Migration `0002_brief_analysis_jobs` must create the table, create an index on `(status, run_at)`, and drop its index/table in `downgrade()`.

- [ ] **Step 4: Run contract/migration tests and verify upgrade/downgrade**

  Run:

  ```sh
  uv run --directory services/classroom-sync pytest tests/contract/test_schemas.py tests/integration/test_migrations.py -q
  ```

  Expected: PASS, including SQLite upgrade → downgrade → upgrade and duplicate `source_brief_id` rejection.

- [ ] **Step 5: Commit the data contract**

  ```sh
  git add contracts/classroom/v1/student-brief.schema.json \
    services/classroom-sync/src/classroom_sync/models.py \
    services/classroom-sync/migrations/versions/0002_brief_analysis_jobs.py \
    services/classroom-sync/tests/contract/test_schemas.py \
    services/classroom-sync/tests/integration/test_migrations.py
  git commit -m "feat: persist classroom brief analysis jobs"
  ```

### Task 3: Implement analysis input filtering, leasing, retries, and revisioned results

**Files:**
- Create: `.worktrees/classroom-main-integration/services/classroom-sync/src/classroom_sync/services/brief_analysis.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/src/classroom_sync/services/briefs.py`
- Create: `.worktrees/classroom-main-integration/services/classroom-sync/tests/unit/test_brief_analysis.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/tests/integration/test_briefs.py`

**Interfaces:**
- Consumes: `StudentBrief.payload`, `ClassroomBriefAnalysisJob`, `AiProviderSettings`, and `OpenAiCompletionClient`.
- Produces: `OpenAiBriefAnalysisService.generate(source: BriefAnalysisInput) -> BriefAiAnalysis` and `BriefAnalysisJobService.run_due_jobs(worker_id) -> int`.
- Produces: `BriefService.submit(session_id: str, content: BriefContent, *, reason: str, request_ai_analysis: bool = False) -> StudentBrief`, `BriefService.complete_analysis_job(job_id, worker_id, analysis) -> StudentBrief`, and `BriefService.fail_analysis_job(job_id, worker_id, failure_code) -> None`.

- [ ] **Step 1: Write failing unit and integration tests**

  Use `httpx.MockTransport` to assert that the provider body excludes sensitive fields and that the service writes `pending` before network work:

  ```python
  def test_analysis_request_omits_evidence_addresses_and_tokens() -> None:
      source = BriefAnalysisInput.from_brief_payload({
          "summary": "完成一次字典读取。",
          "knowledge_points": [{"name": "字典读取", "status": "partial",
                                "demonstrated": "完成读取", "gap": "未测空键",
                                "teacher_suggestion": "补充测试",
                                "evidence_refs": ["chunk-1#event-1"]}],
          "process_overview": ["运行两次"],
          "issues": ["缺少空键测试"],
          "access_token": "must-not-leave-server",
          "object_key": "private/evidence.jsonl",
      })

      body = OpenAiBriefAnalysisService.request_payload(source)
      encoded = json.dumps(body, ensure_ascii=False)
      assert "chunk-1#event-1" not in encoded
      assert "access_token" not in encoded
      assert "object_key" not in encoded
  ```

  Add integration tests proving: complete configuration creates one pending job; invalid provider JSON retries with a delayed `run_at`; the third failure appends a `unavailable` revision; a valid response appends a `ready` revision without changing original deterministic fields.

- [ ] **Step 2: Run the focused tests and verify they fail**

  Run:

  ```sh
  uv run --directory services/classroom-sync pytest tests/unit/test_brief_analysis.py tests/integration/test_briefs.py -q
  ```

  Expected: FAIL because analysis classes, job creation, and terminal revision methods do not exist.

- [ ] **Step 3: Implement the analysis service and atomic brief transitions**

  In `brief_analysis.py`, define strict Pydantic response models and construct a data-only provider input. Use a system message that requires JSON, says the output is auxiliary/not automatically scored, forbids invention of student attributes, and limits analysis to supplied summary fields.

  ```python
  class BriefAiAnalysis(BaseModel):
      model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
      learning_overview: str = Field(min_length=1, max_length=1000)
      evidence_based_observations: list[str] = Field(min_length=1, max_length=5)
      teaching_suggestions: list[str] = Field(min_length=1, max_length=5)

  RETRY_DELAYS = (timedelta(seconds=5), timedelta(seconds=30), timedelta(seconds=120))
  MAX_ATTEMPTS = len(RETRY_DELAYS)
  ```

  In `BriefService.submit`, derive `ai_analysis_status` itself and insert the baseline brief and one job in the same transaction:

  ```python
  ai_status = "pending" if request_ai_analysis else "not_requested"
  payload["ai_analysis_status"] = ai_status
  payload["ai_analysis"] = None
  session.add(brief)
  if request_ai_analysis:
      session.add(ClassroomBriefAnalysisJob(
          id=str(uuid4()), source_brief_id=brief.id, run_at=now, status="pending",
          lease_owner=None, lease_expires_at=None, attempts=0, failure_code=None,
          completed_at=None, created_at=now, updated_at=now,
      ))
  ```

  `claim_due_jobs` must use `with_for_update(skip_locked=True)`, lease only due/non-completed jobs, increment attempts once per lease, and reject a mismatched worker when committing. `complete_analysis_job` copies the newest logical brief payload, increments its revision, sets `ai_analysis_status="ready"`, attaches only the validated three-field analysis, and marks the job completed in the same transaction. `fail_analysis_job` either reschedules its current leased job with `RETRY_DELAYS[attempts - 1]` or appends exactly one `unavailable` revision and completes it after the third failure.

- [ ] **Step 4: Run focused backend tests and code quality checks**

  Run:

  ```sh
  uv run --directory services/classroom-sync pytest tests/unit/test_brief_analysis.py tests/integration/test_briefs.py -q
  uv run --directory services/classroom-sync ruff check src tests
  uv run --directory services/classroom-sync mypy src
  ```

  Expected: PASS. Confirm the request payload test proves that only compact teaching text reaches the mock provider.

- [ ] **Step 5: Commit asynchronous analysis behavior**

  ```sh
  git add services/classroom-sync/src/classroom_sync/services/brief_analysis.py \
    services/classroom-sync/src/classroom_sync/services/briefs.py \
    services/classroom-sync/tests/unit/test_brief_analysis.py \
    services/classroom-sync/tests/integration/test_briefs.py
  git commit -m "feat: analyze classroom briefs asynchronously"
  ```

### Task 4: Wire the server-only service into submission, monitoring, and the local worker

**Files:**
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/src/classroom_sync/application.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/src/classroom_sync/runtime.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/src/classroom_sync/routers/plugin.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/src/classroom_sync/services/read_models.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/src/classroom_sync/worker.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/src/classroom_sync/worker_main.py`
- Modify: `.worktrees/classroom-main-integration/myextension/submission_coordinator.py`
- Modify: `.worktrees/classroom-main-integration/myextension/tests/test_submission_coordinator.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/tests/integration/test_classroom_read_models.py`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/tests/integration/test_briefs.py`

**Interfaces:**
- Consumes: a complete `AiProviderSettings` at runtime, plugin submit fields, and stored job state.
- Produces: `ClassroomServices.brief_analysis_service: BriefAnalysisJobService | None`, `run_due_brief_analyses(service, worker_id) -> int`, monitoring brief `{status, revision, ai_analysis_status}`.
- Security: client `ai_analysis_status` is accepted only as an optional deprecated field for one release, ignored by all server state transitions, then omitted by the plugin.

- [ ] **Step 1: Write failing wiring and read-model tests**

  Add tests that submit a plugin brief with a forged `"ai_analysis_status": "ready"` and verify the persisted status is `not_requested` when no service exists and `pending` when a configured fake service exists. Expand monitoring assertions:

  ```python
  assert monitoring.json()["students"][0]["brief"] == {
      "status": "completed",
      "revision": 1,
      "ai_analysis_status": "pending",
  }
  ```

  Add a worker test with one deadline job and one analysis job where the analysis service raises internally; assert the loop reaches its next tick rather than terminating.

- [ ] **Step 2: Run the focused tests and verify they fail**

  Run:

  ```sh
  uv run --directory services/classroom-sync pytest tests/integration/test_briefs.py tests/integration/test_classroom_read_models.py tests/unit/test_deadlines.py -q
  pytest myextension/tests/test_submission_coordinator.py -q
  ```

  Expected: FAIL because the service is not injected, monitoring lacks AI status, and the client controls the old field.

- [ ] **Step 3: Inject configuration and run both durable job families**

  `runtime.py` must create the shared `AiProviderSettings` once. When it exists, create both the existing plan suggestion adapter and `BriefAnalysisJobService`; otherwise both are absent. Add the nullable dependency to `ClassroomServices`.

  In the plugin route, leave the old Pydantic field optional for backward compatibility but never pass it into `BriefContent`:

  ```python
  class SubmitBriefRequest(BaseModel):
      model_config = ConfigDict(extra="forbid")
      # existing deterministic fields
      ai_analysis_status: str | None = None  # deprecated and deliberately ignored

  brief = services.brief_service.submit(
      session_id,
      BriefContent(summary=payload.summary, knowledge_points=tuple(payload.knowledge_points),
                   process_overview=tuple(payload.process_overview), issues=tuple(payload.issues)),
      reason=payload.reason,
      request_ai_analysis=services.brief_analysis_service is not None,
  )
  ```

  Change the Jupyter coordinator to omit `ai_analysis_status` from its remote payload. Add `ai_analysis_status` to `ClassroomReadService._brief_summary`; it must default to `not_requested` for legacy JSON rows that lack the key.

  Run both worker paths without exposing an upstream exception:

  ```python
  def run_due_classroom_jobs(services: ClassroomServices, worker_id: str) -> int:
      deadline_count = run_due_deadlines(services.deadline_service, worker_id)
      analysis_count = 0
      if services.brief_analysis_service is not None:
          analysis_count = services.brief_analysis_service.run_due_jobs(worker_id)
      return deadline_count + analysis_count
  ```

  Catch/record provider failures inside `BriefAnalysisJobService`; do not use a bare catch that would hide programmer or database errors.

- [ ] **Step 4: Run server/plugin regression checks**

  Run:

  ```sh
  uv run --directory services/classroom-sync pytest tests/integration/test_briefs.py tests/integration/test_classroom_read_models.py tests/unit/test_deadlines.py tests/test_runtime.py -q
  pytest myextension/tests/test_submission_coordinator.py myextension/tests/test_platform_registration.py -q
  ```

  Expected: PASS; a spoofed client status has no authority and monitoring provides only the allowlisted status.

- [ ] **Step 5: Commit runtime and plugin wiring**

  ```sh
  git add services/classroom-sync/src/classroom_sync/application.py \
    services/classroom-sync/src/classroom_sync/runtime.py \
    services/classroom-sync/src/classroom_sync/routers/plugin.py \
    services/classroom-sync/src/classroom_sync/services/read_models.py \
    services/classroom-sync/src/classroom_sync/worker.py \
    services/classroom-sync/src/classroom_sync/worker_main.py \
    services/classroom-sync/tests/integration/test_briefs.py \
    services/classroom-sync/tests/integration/test_classroom_read_models.py \
    services/classroom-sync/tests/unit/test_deadlines.py \
    myextension/submission_coordinator.py \
    myextension/tests/test_submission_coordinator.py
  git commit -m "feat: wire local classroom AI analysis"
  ```

### Task 5: Render analysis lifecycle and safe AI content in the teacher UI

**Files:**
- Modify: `.worktrees/lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/types.ts`
- Modify: `.worktrees/lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/api.ts`
- Modify: `.worktrees/lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/components/ClassroomStudentTable.vue`
- Modify: `.worktrees/lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/components/StudentBriefPanel.vue`
- Modify: `.worktrees/lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/__tests__/api.test.ts`
- Modify: `.worktrees/lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts`
- Modify: `.worktrees/lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts`

**Interfaces:**
- Consumes: teacher brief wire `ai_analysis_status` plus nullable `ai_analysis`, and monitoring brief `ai_analysis_status`.
- Produces: `ClassroomBriefAiAnalysis | null`, typed/redacted mapping, an AI column in both table layouts, and a ready-only `AI 教学分析` card.
- Rendering rule: no analysis card is shown from untyped/partial payloads; no status is inferred from content.

- [ ] **Step 1: Write failing frontend tests**

  Add an API mapping test with an analysis response containing an object-storage URL and assert it is redacted. Add a table test and a panel test:

  ```ts
  it('shows pending AI analysis in the classroom table', () => {
    const wrapper = mount(ClassroomStudentTable, { props: { students: [
      { studentId: 'student001', assignmentId: 'assignment-1', assignmentStatus: 'submitted',
        session: { id: 'session-1', status: 'completed', lastActivityAt: null, submissionReason: 'student_manual' },
        brief: { status: 'completed', revision: 1, aiAnalysisStatus: 'pending' } },
    ] } })

    expect(wrapper.text()).toContain('AI 分析生成中')
  })
  ```

  Require the panel to show all three validated analysis sections plus `辅助分析，不自动评分` only when `aiAnalysisStatus === 'ready'` and `aiAnalysis !== null`.

- [ ] **Step 2: Run frontend tests and verify they fail**

  Run:

  ```sh
  npm test -- --run src/modules/classroom-monitoring/__tests__/api.test.ts src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts
  ```

  Expected: FAIL because monitoring brief types lack AI state and the analysis card does not exist.

- [ ] **Step 3: Add strict wire mapping and teacher-only presentation**

  In `types.ts`, define:

  ```ts
  export type ClassroomAiAnalysisStatus = 'not_requested' | 'pending' | 'ready' | 'unavailable'

  export interface ClassroomBriefAiAnalysis {
    learningOverview: string
    evidenceBasedObservations: string[]
    teachingSuggestions: string[]
  }
  ```

  Parse `ai_analysis` only when it is a record containing all three expected fields with string arrays; otherwise map it to `null`. Apply `redactTeachingText` to every AI string. Extend `ClassroomBriefStatus` with `aiAnalysisStatus` and make the status label a shared pure function so the table and panel use the same Chinese labels.

  Add an `AI 分析` column in the wide table and an `AI 分析` row in the narrow card. In `StudentBriefPanel.vue`, add the card below the deterministic brief sections:

  ```vue
  <section v-if="brief.aiAnalysisStatus === 'ready' && brief.aiAnalysis" class="ai-analysis-card">
    <h3>AI 教学分析</h3>
    <p>辅助分析，不自动评分。</p>
    <p>{{ displayText(brief.aiAnalysis.learningOverview) }}</p>
    <ul><li v-for="item in brief.aiAnalysis.evidenceBasedObservations" :key="item">{{ displayText(item) }}</li></ul>
    <ul><li v-for="item in brief.aiAnalysis.teachingSuggestions" :key="item">{{ displayText(item) }}</li></ul>
  </section>
  ```

- [ ] **Step 4: Run targeted and full frontend validation**

  Run:

  ```sh
  npm test -- --run src/modules/classroom-monitoring/__tests__/api.test.ts src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts
  npm run type-check
  npm run build
  ```

  Expected: PASS. Inspect tests to confirm raw storage URLs are absent from rendered output.

- [ ] **Step 5: Commit frontend AI visibility**

  ```sh
  git add src/modules/classroom-monitoring/types.ts \
    src/modules/classroom-monitoring/api.ts \
    src/modules/classroom-monitoring/components/ClassroomStudentTable.vue \
    src/modules/classroom-monitoring/components/StudentBriefPanel.vue \
    src/modules/classroom-monitoring/__tests__/api.test.ts \
    src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts \
    src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts
  git commit -m "feat: show classroom AI analysis status"
  ```

### Task 6: Add optional local Key configuration, documentation, and non-billed verification

**Files:**
- Modify: `.worktrees/classroom-main-integration/deploy/classroom/local-demo/docker-compose.yml`
- Modify: `.worktrees/classroom-main-integration/scripts/start_local_classroom_demo.sh`
- Create: `.worktrees/classroom-main-integration/deploy/classroom/local-demo/.env.ai.example`
- Modify: `.worktrees/classroom-main-integration/deploy/classroom/local-demo/README.md`
- Modify: `.worktrees/classroom-main-integration/services/classroom-sync/tests/integration/test_plan_suggestions_route.py`
- Modify: `.worktrees/classroom-main-integration/scripts/local_classroom_demo_smoke.py`

**Interfaces:**
- Consumes: optional ignored `.env.ai` values from the local demo directory.
- Produces: `CLASSROOM_AI_*` only in `sync-api` and `deadline-worker`; no Key reaches façade, nginx, Vue, Jupyter, or smoke-test output.
- Validation: baseline demo continues to start with no `.env.ai`; mocked provider tests cover AI behavior without credentials.

- [ ] **Step 1: Write failing configuration/README checks**

  Add a script-level test or focused textual assertion that the Compose runtime environment contains exactly the four `CLASSROOM_AI_*` interpolation entries and that `demo-fincolab` has none. Add a smoke assertion that monitoring brief responses include one of the four safe status values but do not contain `api_key`, `access_token`, `object_key`, or `evidence_refs`.

- [ ] **Step 2: Run the safe local configuration checks and verify they fail**

  Run:

  ```sh
  rg -n "CLASSROOM_AI_|api_key|access_token" deploy/classroom/local-demo scripts/start_local_classroom_demo.sh scripts/local_classroom_demo_smoke.py
  PYTHONPATH=scripts uv run --no-project python scripts/local_classroom_demo_smoke.py
  ```

  Expected: the source check lacks the optional AI wiring; the smoke check lacks the new monitoring assertion. Do not create an actual `.env.ai` and do not call GLM.

- [ ] **Step 3: Implement opt-in-only Compose loading and key-free instructions**

  Keep the local demo operational without an AI file. In the Compose runtime anchor used only by `sync-api` and `deadline-worker`, add:

  ```yaml
  CLASSROOM_AI_BASE_URL: ${CLASSROOM_AI_BASE_URL:-}
  CLASSROOM_AI_MODEL: ${CLASSROOM_AI_MODEL:-}
  CLASSROOM_AI_API_KEY: ${CLASSROOM_AI_API_KEY:-}
  CLASSROOM_AI_TIMEOUT_SECONDS: ${CLASSROOM_AI_TIMEOUT_SECONDS:-30}
  ```

  In the start script, select the Key-free compose invocation unless the ignored file exists:

  ```sh
  ai_env="$root/deploy/classroom/local-demo/.env.ai"
  if [ -f "$ai_env" ]; then
    docker compose --env-file "$ai_env" -p "$project" -f "$compose" up --build -d
  else
    docker compose -p "$project" -f "$compose" up --build -d
  fi
  ```

  Add `.env.ai.example` with blank `CLASSROOM_AI_API_KEY=` and the approved Coding Plan base URL/model. README instructions must tell the user to copy it to `.env.ai`, paste the Key locally, restart the demo, submit as `student001`, then refresh the teacher monitoring page until `AI 分析已完成`. Document that `unavailable` preserves the base brief, and that no Key should be pasted into chat or committed.

- [ ] **Step 4: Run complete no-Key regression and build artifacts**

  Run from the backend worktree:

  ```sh
  uv run --directory services/classroom-sync pytest -q
  uv run --directory services/classroom-sync ruff check src tests
  uv run --directory services/classroom-sync mypy src
  pytest myextension/tests/test_submission_coordinator.py myextension/tests/test_platform_registration.py -q
  uv build --wheel
  PYTHONPATH=scripts uv run --no-project python scripts/local_classroom_demo_smoke.py
  ```

  Run from the frontend worktree:

  ```sh
  npm test -- --run
  npm run type-check
  npm run build
  ```

  Expected: all Mock/no-Key tests and builds pass. The no-Key smoke path returns `not_requested` and makes no external network request.

- [ ] **Step 5: Commit safe local configuration and hand off billed verification**

  ```sh
  git add deploy/classroom/local-demo/docker-compose.yml \
    deploy/classroom/local-demo/.env.ai.example \
    deploy/classroom/local-demo/README.md \
    scripts/start_local_classroom_demo.sh \
    scripts/local_classroom_demo_smoke.py \
    services/classroom-sync/tests/integration/test_plan_suggestions_route.py
  git commit -m "docs: configure local GLM classroom analysis"
  ```

  Do not create `.env.ai`, restart Docker, or call GLM in this task. Report the exact manual commands and ask the user to authorize the quota-consuming test separately.

## Final Verification Checklist

- [ ] Backend source, migration, contract, plugin, and worker tests pass with `httpx.MockTransport`.
- [ ] `ruff`, `mypy`, frontend tests, TypeScript check, and frontend build pass.
- [ ] Empty local AI config preserves the existing classroom flow and produces `not_requested` without an external request.
- [ ] Teacher monitoring shows `pending`, `ready`, `unavailable`, and `not_requested`; the brief page shows AI content only for a validated `ready` payload.
- [ ] A forged plugin `ai_analysis_status` cannot mark analysis ready.
- [ ] Git status is clean in both feature worktrees; root `main` remains untouched; no merge, push, deployment, or real GLM request occurred.
- [ ] After explicit user approval only, create ignored `.env.ai`, restart the known local stack, and perform one real teacher-suggestion and one student-submission smoke test while treating any Coding Plan usage as billable quota consumption.
