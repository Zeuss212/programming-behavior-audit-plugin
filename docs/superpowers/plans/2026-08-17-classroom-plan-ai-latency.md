# Classroom Plan AI Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make local classroom plan suggestions use the verified GLM Coding Plan JSON request settings without changing student brief analysis.

**Architecture:** `OpenAiCompletionClient` accepts a small, opt-in request profile. `OpenAiPlanSuggestionService` uses the profile with disabled thinking, JSON mode, and a bounded 2048-to-4096 recovery. The shared brief-analysis caller keeps its existing request body. Local Docker configuration changes only the non-secret base URL.

**Tech Stack:** Python 3.12, httpx, Pydantic, pytest, Ruff, Mypy, Docker Compose, Vue 3, Vitest.

## Global Constraints

- Work only in `codex/classroom-main-integration` and `codex/classroom-ui`; do not merge, push, deploy, reset, or stage root `main`.
- Keep API Keys only in ignored `deploy/classroom/local-demo/.env.ai`; never print, test, commit, log, or place them in a browser request.
- Use `https://ark.cn-beijing.volces.com/api/coding/v3` and retain the existing configured model.
- Apply disabled-thinking and JSON mode only to teacher plan suggestions; preserve the student brief-analysis request profile.
- A real Provider smoke test sends one synthetic teacher prompt only and stops after that request.

---

### Task 1: Add a plan-suggestion-only request profile

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/services/plan_suggestions.py`
- Test: `services/classroom-sync/tests/unit/test_plan_suggestions.py`
- Test: `services/classroom-sync/tests/unit/test_brief_analysis.py`

**Interfaces:**
- Consumes: `OpenAiCompletionClient.complete(messages, *, temperature, max_tokens)`.
- Produces: `OpenAiCompletionClient.complete(messages, *, temperature, max_tokens, thinking_mode: str | None = None, json_mode: bool = False) -> str`.
- Uses: `OpenAiPlanSuggestionService.generate()` with 2048 tokens, disabled thinking and JSON mode; `OpenAiBriefAnalysisService.generate()` remains at 1200 tokens with defaults.

- [ ] **Step 1: Write failing request-body tests**

```python
def test_adapter_sends_plan_profile_without_serializing_the_key() -> None:
    result = service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))
    body = json.loads(recorded[0].content)
    assert result.title == "字典课堂练习"
    assert body["max_tokens"] == 2048
    assert body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}

def test_brief_analysis_keeps_the_default_completion_profile() -> None:
    source = BriefAnalysisInput(
        summary="完成一次字典读取。",
        knowledge_points=(),
        process_overview=("运行一次",),
        issues=(),
    )
    service.generate(source)
    body = json.loads(recorded[0].content)
    assert "thinking" not in body
    assert "response_format" not in body
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run: `uv run --directory services/classroom-sync pytest tests/unit/test_plan_suggestions.py tests/unit/test_brief_analysis.py -q`  
Expected: the new plan-profile assertion fails because the request still sends 1200 tokens and no extra fields.

- [ ] **Step 3: Implement the smallest shared request-profile extension**

```python
def complete(
    self,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    thinking_mode: Literal["disabled"] | None = None,
    json_mode: bool = False,
) -> str:
    body: dict[str, object] = {
        "model": self._settings.model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if thinking_mode is not None:
        body["thinking"] = {"type": thinking_mode}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
```

The plan service calls `complete(self._messages(suggestion_input), temperature=0.2, max_tokens=2048, thinking_mode="disabled", json_mode=True)`. No other caller changes.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `uv run --directory services/classroom-sync pytest tests/unit/test_plan_suggestions.py tests/unit/test_brief_analysis.py -q`  
Expected: all tests pass.

### Task 2: Recover once from plan-output truncation

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/services/plan_suggestions.py`
- Test: `services/classroom-sync/tests/unit/test_plan_suggestions.py`

**Interfaces:**
- Consumes: the `finish_reason` field in the first OpenAI-compatible response.
- Produces: one automatic 4096-token retry only for `finish_reason == "length"`; no retry for 4xx, 5xx, timeout, network or invalid result.

- [ ] **Step 1: Write a failing truncation-recovery test**

```python
def test_adapter_retries_once_with_4096_tokens_only_after_length_finish_reason() -> None:
    responses = [truncated_response(), response_with(valid_suggestion_json())]
    result = service.generate(PlanSuggestionInput(title="", statement="实现字典查询"))
    assert result.title == "字典课堂练习"
    assert [json.loads(item.content)["max_tokens"] for item in recorded] == [2048, 4096]
```

- [ ] **Step 2: Run the focused test to verify RED**

Run: `uv run --directory services/classroom-sync pytest tests/unit/test_plan_suggestions.py::test_adapter_retries_once_with_4096_tokens_only_after_length_finish_reason -q`  
Expected: failure because the completion client returns text without exposing a bounded truncation signal.

- [ ] **Step 3: Implement one internal result path for plan suggestions**

`OpenAiCompletionClient` must expose the first choice's `finish_reason` without exposing Provider content in exceptions. `OpenAiPlanSuggestionService` requests 4096 only when that value is `"length"`, then strictly validates the second response as before.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `uv run --directory services/classroom-sync pytest tests/unit/test_plan_suggestions.py -q`  
Expected: all plan-suggestion unit tests pass.

### Task 3: Validate code, configure the local service, and smoke-test once

**Files:**
- Modify (ignored, non-secret line only): `deploy/classroom/local-demo/.env.ai`
- Modify: `.worktrees/lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/api.ts`
- Test: `.worktrees/lab-platform-frontend-classroom-ui/src/modules/classroom-monitoring/__tests__/api.test.ts`

**Interfaces:**
- Consumes: Docker Compose `--env-file deploy/classroom/local-demo/.env.ai` and the existing Vite proxy.
- Produces: 45-second browser request ceiling and local `sync-api` configured with `https://ark.cn-beijing.volces.com/api/coding/v3`.

- [ ] **Step 1: Run full automated checks**

Run:

```sh
uv run --directory services/classroom-sync pytest -q
uv run --directory services/classroom-sync ruff check src tests
uv run --directory services/classroom-sync mypy src
npm test
npm run type-check
npm run build
```

Expected: all commands exit 0.

- [ ] **Step 2: Change only the local non-secret base URL and recreate service containers**

Set `CLASSROOM_AI_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3` in the ignored `.env.ai`, then run:

```sh
docker compose --env-file deploy/classroom/local-demo/.env.ai -p classroom-local-demo \
  -f deploy/classroom/local-demo/docker-compose.yml \
  up -d --force-recreate sync-api deadline-worker classroom-nginx
```

Verify only set/unset state of AI variables, health endpoint, and URL path; never print the Key.

- [ ] **Step 3: Execute one synthetic Provider smoke test**

Use the teacher plan page with a short synthetic title and task description. Record elapsed time and only a safe outcome (`success` or stable failure code). Do not retry automatically. Stop after this request.

- [ ] **Step 4: Commit isolated code changes**

```sh
git add services/classroom-sync/src/classroom_sync/services/plan_suggestions.py \
  services/classroom-sync/tests/unit/test_plan_suggestions.py \
  services/classroom-sync/tests/unit/test_brief_analysis.py \
  docs/superpowers/specs/2026-08-17-classroom-plan-ai-latency-design.md \
  docs/superpowers/plans/2026-08-17-classroom-plan-ai-latency.md
git commit -m "fix: optimize classroom plan AI suggestions"
```

Commit the frontend timeout change separately in `codex/classroom-ui`; do not stage ignored `.env.ai`.
