# Classroom AI Plan Suggestions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow an authorized teacher to generate an editable classroom title and knowledge-point draft from the teaching objective without exposing AI credentials to the browser.

**Architecture:** Add a small, synchronous OpenAI-compatible suggestion adapter to `classroom-sync`; its runtime configuration is server-only and optional. A new teacher-authorized route validates the request and adapter result before returning it. The Vue plan wizard requests, previews and explicitly applies the result while preserving manual authoring and the existing publication flow.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, httpx, Vue 3, TypeScript, Vitest, pytest.

## Global Constraints

- AI credentials are exclusively injected as `CLASSROOM_AI_API_KEY`; never return, log, persist or bundle them.
- Only `https` OpenAI-compatible provider addresses without userinfo, query or fragment are accepted.
- The existing FinColab bearer token and `require_teacher_owner` authorization are required before an AI request.
- AI output is transient: no database migration and no persistence before normal plan publication.
- The request and response limits are title ≤200, statement 1–10000, 1–10 knowledge points, point name ≤50 and description ≤500.
- A missing or unhealthy AI provider must leave manual authoring and publishing usable.
- Do not change the BAMS HTTPS 40037 student workbench flow, old 5179 container, current candidate container, or any existing `ai_policy` semantics.

---

### Task 1: Server-only AI settings and bounded provider adapter

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/config.py`
- Modify: `services/classroom-sync/src/classroom_sync/errors.py`
- Create: `services/classroom-sync/src/classroom_sync/services/plan_suggestions.py`
- Test: `services/classroom-sync/tests/unit/test_plan_suggestions.py`
- Test: `services/classroom-sync/tests/test_runtime.py`

**Interfaces:**
- Produces `AiSuggestionSettings.from_settings(settings) -> AiSuggestionSettings | None`.
- Produces `OpenAiPlanSuggestionService.generate(PlanSuggestionInput) -> PlanSuggestion`.
- Raises `AiSuggestionUnavailableError("ai_suggestion_not_configured")` for absent/partial settings and `UpstreamUnavailableError("ai_suggestion_upstream_unavailable")` for timeouts, network, 429/5xx or malformed provider output.
- `PlanSuggestion` is `{title: str, knowledge_points: tuple[SuggestedKnowledgePoint, ...]}`.

- [ ] **Step 1: Write failing settings and adapter tests**

```python
def test_ai_settings_require_all_three_server_values() -> None:
    settings = Settings(database_url="sqlite://", ai_base_url="https://ai.example", ai_model=None, ai_api_key="secret")
    with pytest.raises(AiSuggestionUnavailableError, match="ai_suggestion_not_configured"):
        AiSuggestionSettings.from_settings(settings)


def test_adapter_posts_only_bounded_teaching_text_and_validates_output() -> None:
    recorded: list[httpx.Request] = []
    service = OpenAiPlanSuggestionService(
        AiSuggestionSettings("https://ai.example/v1", "model-a", "secret", 15),
        httpx.Client(transport=httpx.MockTransport(responder(recorded))),
    )
    result = service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))
    assert result.knowledge_points[0].name == "字典读取"
    assert recorded[0].headers["authorization"] == "Bearer secret"
    assert "secret" not in recorded[0].content.decode("utf-8")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```sh
PYTHONPATH=src:. UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache uv run --no-project --with pytest --with httpx --with pydantic python -m pytest -q services/classroom-sync/tests/unit/test_plan_suggestions.py services/classroom-sync/tests/test_runtime.py
```

Expected: FAIL because the suggestion types and runtime AI settings do not exist.

- [ ] **Step 3: Add optional settings and stable safe errors**

Add optional `ai_base_url`, `ai_model`, `ai_api_key`, and `ai_timeout_seconds` fields to `Settings.from_env`; do not add them to `require_runtime_dependencies`, because manual classrooms must run without AI. Validate a complete triple and 1–30 second whole timeout in `AiSuggestionSettings`. Validate URLs with `urllib.parse.urlsplit`: HTTPS, hostname present, no username/password/query/fragment.

- [ ] **Step 4: Implement the provider adapter**

Use `httpx.Client.post` with a 15-second bounded timeout, `Authorization: Bearer <server secret>`, and a fixed JSON-only prompt that asks for `title` and `knowledge_points`. Send only `title` and `statement`; set deterministic temperature 0.2 and cap output tokens. Decode the OpenAI-compatible `choices[0].message.content`, remove an optional fenced JSON wrapper, parse JSON, strip fields and validate all limits with Pydantic. Never include provider bodies, request content or secrets in an exception message.

- [ ] **Step 5: Verify success and failure boundaries**

Run the Task 1 command and add tests for: missing setting, insecure/userinfo URL, timeout, 429, non-JSON response, zero/11 points, 51-character name and 501-character description. Expected: PASS; configuration failures are non-retryable and provider/transient failures are retryable 503s.

- [ ] **Step 6: Commit**

```sh
git add services/classroom-sync/src/classroom_sync/config.py services/classroom-sync/src/classroom_sync/errors.py services/classroom-sync/src/classroom_sync/services/plan_suggestions.py services/classroom-sync/tests/unit/test_plan_suggestions.py services/classroom-sync/tests/test_runtime.py
git commit -m "feat: add server-side classroom AI suggestions"
```

### Task 2: Teacher-authorized API route and runtime wiring

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/application.py`
- Modify: `services/classroom-sync/src/classroom_sync/runtime.py`
- Create: `services/classroom-sync/src/classroom_sync/routers/suggestions.py`
- Modify: `services/classroom-sync/src/classroom_sync/main.py`
- Test: `services/classroom-sync/tests/integration/test_plan_suggestions_route.py`
- Modify: `services/classroom-sync/tests/integration/test_classroom_routes.py`

**Interfaces:**
- Adds `ClassroomServices.plan_suggestion_service: PlanSuggestionService | None`.
- Adds `POST /v1/classroom/plan-suggestions` with `PlanSuggestionRequest(space_id, parent_algorithm_id, title, statement)`.
- Returns `{ "title": str, "knowledge_points": [{"name": str, "description": str}] }`.

- [ ] **Step 1: Write failing route tests**

```python
def test_teacher_owner_can_generate_a_transient_plan_suggestion() -> None:
    response = request(
        app_with_recording_suggestion_service(), "POST", "/v1/classroom/plan-suggestions",
        headers={"Authorization": "Bearer teacher-token"},
        json={"space_id": "space-1", "parent_algorithm_id": "parent-1", "title": "", "statement": "实现字典查询"},
    )
    assert response.status_code == 200
    assert response.json()["knowledge_points"][0]["name"] == "字典读取"


def test_unowned_experiment_is_rejected_before_ai_service_is_called() -> None:
    response = request(app_with_forbidden_owner(), "POST", "/v1/classroom/plan-suggestions", headers={"Authorization": "Bearer teacher-token"}, json=valid_payload())
    assert response.status_code == 403
    assert suggestion_service.calls == []
```

- [ ] **Step 2: Run route tests to verify they fail**

Run:

```sh
PYTHONPATH=src:. UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache uv run --no-project --with pytest --with alembic --with boto3 --with httpx --with fastapi --with jsonschema --with pydantic --with 'psycopg[binary]' --with pyjwt --with sqlalchemy --with uvicorn python -m pytest -q services/classroom-sync/tests/integration/test_plan_suggestions_route.py
```

Expected: FAIL because the route and injected dependency do not exist.

- [ ] **Step 3: Add route dependency and authorization first**

Create the router under `/v1/classroom`, resolve the existing bearer with `resolve_bearer_principal`, then call `require_teacher_owner(principal, payload.space_id, payload.parent_algorithm_id)` before reading or calling the suggestion service. Reject blank statement and extra request fields with Pydantic 422. If the optional service is `None`, raise `AiSuggestionUnavailableError("ai_suggestion_not_configured")`.

- [ ] **Step 4: Wire runtime without leaking Secret values**

In `create_runtime_services`, derive the optional provider service from `Settings`; construct no provider client when AI is unconfigured. Include the router when `classroom_services` exists. Keep Uvicorn logging unchanged and do not print settings.

- [ ] **Step 5: Verify authorization, error envelope and non-persistence**

Run the Task 2 command plus `test_classroom_routes.py`. Assert 401 for missing bearer, 403 before model call for a non-owner, stable 503 envelope for unconfigured service, and no plan/draft rows created by a successful suggestion request.

- [ ] **Step 6: Commit**

```sh
git add services/classroom-sync/src/classroom_sync/application.py services/classroom-sync/src/classroom_sync/runtime.py services/classroom-sync/src/classroom_sync/main.py services/classroom-sync/src/classroom_sync/routers/suggestions.py services/classroom-sync/tests/integration/test_plan_suggestions_route.py services/classroom-sync/tests/integration/test_classroom_routes.py
git commit -m "feat: expose teacher AI plan suggestions"
```

### Task 3: Typed frontend API client

**Files:**
- Modify: `src/modules/classroom-monitoring/types.ts`
- Modify: `src/modules/classroom-monitoring/api.ts`
- Test: `src/modules/classroom-monitoring/__tests__/api.test.ts`

**Interfaces:**
- Adds `ClassroomPlanSuggestionInput` with `spaceId`, `parentAlgorithmId`, `title`, `statement`.
- Adds `ClassroomPlanSuggestion` with `title` and `knowledgePoints: Array<{name, description}>`.
- Adds `classroomApi.generatePlanSuggestion(input): Promise<ClassroomPlanSuggestion>`.

- [ ] **Step 1: Write the failing client test**

```ts
it('posts only plan context to the teacher AI suggestion endpoint', async () => {
  mocks.classroomClient.post.mockResolvedValueOnce({
    data: { title: '字典课堂练习', knowledge_points: [{ name: '字典读取', description: '按键读取并验证结果。' }] },
  })
  await expect(classroomApi.generatePlanSuggestion({
    spaceId: 'space-1', parentAlgorithmId: 'parent-1', title: '', statement: '实现字典查询',
  })).resolves.toEqual({ title: '字典课堂练习', knowledgePoints: [{ name: '字典读取', description: '按键读取并验证结果。' }] })
  expect(mocks.classroomClient.post).toHaveBeenCalledWith('/v1/classroom/plan-suggestions', {
    space_id: 'space-1', parent_algorithm_id: 'parent-1', title: '', statement: '实现字典查询',
  })
})
```

- [ ] **Step 2: Run the frontend test to verify it fails**

Run:

```sh
npm test -- --run src/modules/classroom-monitoring/__tests__/api.test.ts
```

Expected: FAIL because `generatePlanSuggestion` and its types do not exist.

- [ ] **Step 3: Implement typed mapping**

Post only the four request fields to `/v1/classroom/plan-suggestions`, map snake case knowledge points to camel case, and reuse the existing interceptor and `responseData` error normalization. Do not add any AI configuration, provider URL, model or API key to frontend code.

- [ ] **Step 4: Verify**

Run the Task 3 test and `npm run type-check`. Expected: PASS.

- [ ] **Step 5: Commit**

```sh
git add src/modules/classroom-monitoring/types.ts src/modules/classroom-monitoring/api.ts src/modules/classroom-monitoring/__tests__/api.test.ts
git commit -m "feat: request classroom AI suggestions from frontend"
```

### Task 4: Teacher plan wizard preview and explicit apply action

**Files:**
- Modify: `src/modules/classroom-monitoring/components/PlanWizard.vue`
- Modify: `src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts`

**Interfaces:**
- Adds `generateSuggestion(): Promise<void>` and `applySuggestion(): void` to the first wizard step.
- Uses `data-testid="generate-classroom-plan-suggestion"`, `data-testid="classroom-plan-suggestion"`, and `data-testid="apply-classroom-plan-suggestion"`.

- [ ] **Step 1: Write failing component tests**

```ts
it('previews an AI suggestion without overwriting manual fields until the teacher applies it', async () => {
  mocks.generatePlanSuggestion.mockResolvedValue({ title: '字典课堂练习', knowledgePoints: [{ name: '字典读取', description: '按键读取并验证结果。' }] })
  const wrapper = mount(PlanWizard, { props: { courseId: 'course-1', parentAlgorithmId: 'parent-1' } })
  await wrapper.get('#classroom-plan-statement').setValue('实现字典查询')
  await wrapper.get('[data-testid="generate-classroom-plan-suggestion"]').trigger('click')
  await flushPromises()
  expect(wrapper.get('[data-testid="classroom-plan-suggestion"]').text()).toContain('字典读取')
  expect((wrapper.get('#classroom-plan-knowledge-point-name-0').element as HTMLInputElement).value).toBe('')
  await wrapper.get('[data-testid="apply-classroom-plan-suggestion"]').trigger('click')
  expect((wrapper.get('#classroom-plan-knowledge-point-name-0').element as HTMLInputElement).value).toBe('字典读取')
})
```

- [ ] **Step 2: Run component tests to verify they fail**

Run:

```sh
npm test -- --run src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts
```

Expected: FAIL because the API mock, buttons and preview are absent.

- [ ] **Step 3: Implement minimal accessible UI**

Add a secondary “AI 生成建议” button after the teaching objective. Disable it when `statement.trim()` is empty or a request is in flight. Store the returned suggestion separately from `form`; render a clearly labelled preview with `aria-live="polite"`, an explicit “使用本次建议” button and a “继续手动编辑” dismissal. Applying a suggestion replaces the knowledge-point array, sets the suggested title and preserves statement, schedule and existing publish idempotency state. Cap the inserted list at 10 and retain existing field validation and accessibility attributes.

- [ ] **Step 4: Implement failure and repeat-click behavior**

Map `ai_suggestion_not_configured` to “AI 建议尚未由管理员配置，可继续手动填写。” Map retryable provider failures to “AI 建议暂时不可用，请稍后重试。” Keep current form values and suggestion preview on failed replacement; prevent a second request while the first is pending.

- [ ] **Step 5: Verify wizard regressions**

Run the Task 4 test, then:

```sh
npm test -- --run
npm run type-check
npm run build
```

Expected: all tests pass; manual publish and retry behavior remain unchanged.

- [ ] **Step 6: Commit**

```sh
git add src/modules/classroom-monitoring/components/PlanWizard.vue src/modules/classroom-monitoring/__tests__/PlanWizard.test.ts
git commit -m "feat: preview and apply AI classroom plan suggestions"
```

### Task 5: Deployment documentation and cross-service verification

**Files:**
- Modify: `deploy/classroom/docker-compose.test.yml`
- Modify: `deploy/bluedot/release-0.4.0/runtime.env.example`
- Modify: `deploy/bluedot/release-0.4.0/INSTALL.md`
- Create: `docs/runbooks/classroom-ai-operations.md`
- Modify: `README.md`

**Interfaces:**
- Documents all four `CLASSROOM_AI_*` variables as server Secret/runtime configuration, never sample values.
- Documents that `JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL` points to classroom sync HTTPS; no plugin AI key is added.

- [ ] **Step 1: Write a failing static deployment assertion**

```python
def test_release_docs_do_not_place_classroom_ai_key_in_student_runtime_template() -> None:
    values = Path("deploy/bluedot/release-0.4.0/runtime.env.example").read_text()
    assert "CLASSROOM_AI_API_KEY" not in values
    assert "JUPYTERLAB_BEHAVIOR_AUDIT_AI" not in values
```

- [ ] **Step 2: Run it to verify the intended documentation boundary**

Run:

```sh
PYTHONPATH=src:. UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache uv run --no-project --with pytest python -m pytest -q services/classroom-sync/tests/test_classroom_ai_release.py
```

Expected: FAIL because the release-boundary test does not exist.

- [ ] **Step 3: Document server-only configuration and add the static test**

Add an operator-only section listing the four `CLASSROOM_AI_*` variables, their bounds and health/failure behaviour. State that all values are Secret-injected into sync-api only; the deadline worker does not call the provider; the plugin and Vue image receive none. Keep test Compose provider configuration absent by default so deterministic tests do not call the internet.

- [ ] **Step 4: Execute complete verification**

Run:

```sh
PYTHONPATH=src:. UV_CACHE_DIR=/private/tmp/classroom-platform-uv-cache uv run --no-project --with pytest --with alembic --with boto3 --with httpx --with fastapi --with jsonschema --with pydantic --with 'psycopg[binary]' --with pyjwt --with sqlalchemy --with uvicorn python -m pytest -q --confcutdir=. tests
npm test -- --run
npm run type-check
npm run build
```

Expected: both repositories pass their relevant test, type and build checks. Do not deploy or replace the existing 5179 candidate as part of this task.

- [ ] **Step 5: Commit**

```sh
git add deploy/classroom/docker-compose.test.yml deploy/bluedot/release-0.4.0/runtime.env.example deploy/bluedot/release-0.4.0/INSTALL.md docs README.md services/classroom-sync/tests/test_classroom_ai_release.py
git commit -m "docs: configure classroom AI suggestions safely"
```

## Final Review Checklist

- [ ] API key, provider URL and model do not occur in the Vue bundle, browser storage or API response.
- [ ] Route authorization occurs before the provider service is invoked.
- [ ] AI output cannot exceed the immutable plan schema limits.
- [ ] Manual authoring works when the provider is unconfigured or fails.
- [ ] Candidate 5180 and production 5179 remain untouched until a separately approved release is packaged and tested.
