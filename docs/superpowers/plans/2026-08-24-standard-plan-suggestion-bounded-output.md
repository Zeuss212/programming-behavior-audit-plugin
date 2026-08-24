# Standard Plan Suggestion Bounded Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the standard classroom-plan Provider endpoint return a closed core JSON suggestion in one `1200`-token call while preserving the richer Coding Plan profile.

**Architecture:** `OpenAiPlanSuggestionService` keeps `_uses_coding_plan_profile` as the endpoint capability switch. Message construction becomes endpoint-aware: standard endpoints receive only the core `title` and `knowledge_points` contract, while Coding Plan endpoints retain the optional `automatic_evaluation` instructions and existing JSON/length-recovery profile.

**Tech Stack:** Python 3.12, Pydantic 2, HTTPX, pytest, Ruff, mypy, Docker Compose.

## Global Constraints

- Standard endpoints use `temperature=0.2`, `max_tokens=1200`, no `thinking`, no `response_format`, and at most one Provider call.
- Coding Plan endpoints retain `2048 → 4096` length recovery, disabled thinking, JSON object mode, and optional `automatic_evaluation`.
- Keep existing field bounds, sensitive-text filtering, absolute-path filtering, safe error codes, and teacher confirmation flow.
- Never output or persist API keys, Authorization headers, Provider response bodies, real course content, or student data.
- Do not reset or delete PostgreSQL or MinIO volumes.
- The worktree already contains unrelated and overlapping uncommitted work. Do not stage or commit the modified service or test files in this plan; preserve all existing changes.

---

### Task 1: Split Standard and Coding Plan Prompts

**Files:**
- Modify: `services/classroom-sync/tests/unit/test_plan_suggestions.py:79-190`
- Modify: `services/classroom-sync/src/classroom_sync/services/plan_suggestions.py:297-423`

**Interfaces:**
- Consumes: `OpenAiPlanSuggestionService._uses_coding_plan_profile: bool`, `PlanSuggestionInput`, and `OpenAiCompletionClient.complete_with_metadata(...)`.
- Produces: `OpenAiPlanSuggestionService._messages(suggestion_input: PlanSuggestionInput, *, include_automatic_evaluation: bool) -> list[dict[str, str]]`.
- Preserves: `OpenAiPlanSuggestionService.generate(...) -> PlanSuggestion`, standard `1200`/one-call behavior, and Coding Plan `2048 → 4096` behavior.

- [ ] **Step 1: Add the standard-endpoint failing assertion and Coding Plan regression assertion**

In `test_adapter_uses_the_fast_json_profile_for_bounded_teaching_text`, after decoding the first request body, assert the extended instruction remains present:

```python
    system_content = body["messages"][0]["content"]
    assert "automatic_evaluation" in system_content
    assert "dict_get_with_default" in system_content
```

In `test_adapter_uses_standard_chat_completions_profile_for_plan_endpoint`, after decoding the first request body, assert the standard prompt excludes the extended contract:

```python
    system_content = body["messages"][0]["content"]
    assert "automatic_evaluation" not in system_content
    assert "dict_get_with_default" not in system_content
```

The production change caught by the failing assertion is an unconditional extended prompt sent to standard endpoints. The Coding Plan assertion protects the capability that must remain.

- [ ] **Step 2: Run the focused tests and verify RED**

Run from `services/classroom-sync`:

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_plan_suggestions.py::test_adapter_uses_the_fast_json_profile_for_bounded_teaching_text \
  tests/unit/test_plan_suggestions.py::test_adapter_uses_standard_chat_completions_profile_for_plan_endpoint
```

Expected: one failure because the standard request still contains `automatic_evaluation`; the Coding Plan assertion passes.

- [ ] **Step 3: Make message construction endpoint-aware**

In `OpenAiPlanSuggestionService.generate`, pass the profile flag on both the initial request and the Coding Plan length-recovery request:

```python
        messages = self._messages(
            suggestion_input,
            include_automatic_evaluation=self._uses_coding_plan_profile,
        )

        try:
            completion = completion_client.complete_with_metadata(
                messages,
                temperature=0.2,
                max_tokens=max_tokens,
                thinking_mode=thinking_mode,
                json_mode=self._uses_coding_plan_profile,
            )
            if self._uses_coding_plan_profile and completion.finish_reason == "length":
                completion = completion_client.complete_with_metadata(
                    messages,
                    temperature=0.2,
                    max_tokens=4096,
                    thinking_mode=thinking_mode,
                    json_mode=self._uses_coding_plan_profile,
                )
```

Replace `_messages` with an endpoint-aware implementation. Keep the existing Chinese core instruction unchanged and append the automatic-evaluation rules only for Coding Plan endpoints:

```python
    @staticmethod
    def _messages(
        suggestion_input: PlanSuggestionInput,
        *,
        include_automatic_evaluation: bool,
    ) -> list[dict[str, str]]:
        system_content = (
            "你是课堂教学设计助手。只返回一个 JSON 对象，不要 Markdown。"
            "对象必须含 title 和 knowledge_points；knowledge_points 是 1 到 10 项，"
            "每项含 name、description。"
        )
        if include_automatic_evaluation:
            system_content += (
                "每项可以含 automatic_evaluation。"
                "automatic_evaluation 只能含 mode=all、summary 和 requirements；"
                "requirements 每项只能含 kind，kind 只能是 successful_execution、"
                "dict_literal_assignment、dict_key_value_pairs、dict_subscript_access、"
                "dict_get_with_default、print_call 或 input_call。"
                "仅在能用这些本地、非执行性证据可靠判定时提供 automatic_evaluation，"
                "否则省略该字段。"
            )
        system_content += "内容必须是教师可继续编辑的简洁中文课堂方案草稿。"
        return [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "title": suggestion_input.title,
                        "statement": suggestion_input.statement,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_plan_suggestions.py::test_adapter_uses_the_fast_json_profile_for_bounded_teaching_text \
  tests/unit/test_plan_suggestions.py::test_adapter_uses_standard_chat_completions_profile_for_plan_endpoint \
  tests/unit/test_plan_suggestions.py::test_adapter_keeps_a_complete_standard_plan_response_without_a_second_provider_call \
  tests/unit/test_plan_suggestions.py::test_adapter_retries_once_with_4096_tokens_after_a_length_response
```

Expected: 4 passed. The standard request has a core-only prompt and one-call boundary; the Coding Plan request retains its extended profile and retry boundary.

- [ ] **Step 5: Preserve the dirty-worktree boundary**

Run:

```bash
git status --short -- \
  services/classroom-sync/src/classroom_sync/services/plan_suggestions.py \
  services/classroom-sync/tests/unit/test_plan_suggestions.py
```

Expected: both files remain modified and unstaged. Do not run `git add` or `git commit` for these files because they contain pre-existing work outside this fix.

---

### Task 2: Run Offline Quality Gates

**Files:**
- Verify: `services/classroom-sync/src/classroom_sync/services/plan_suggestions.py`
- Verify: `services/classroom-sync/tests/unit/test_plan_suggestions.py`
- Verify: `services/classroom-sync/tests/integration/test_plan_suggestions_route.py`

**Interfaces:**
- Consumes: endpoint-aware `_messages(...)` from Task 1.
- Produces: fresh unit, route, lint, type, and diff evidence required before image rebuild.

- [ ] **Step 1: Run the complete plan-suggestion unit suite**

Run from `services/classroom-sync`:

```bash
.venv/bin/python -m pytest -q tests/unit/test_plan_suggestions.py
```

Expected: all tests pass with no failures or warnings.

- [ ] **Step 2: Run the route integration suite**

Run:

```bash
.venv/bin/python -m pytest -q tests/integration/test_plan_suggestions_route.py
```

Expected: all route tests pass.

- [ ] **Step 3: Run static checks**

Run:

```bash
.venv/bin/ruff check \
  src/classroom_sync/services/plan_suggestions.py \
  tests/unit/test_plan_suggestions.py
.venv/bin/mypy src
git diff --check -- \
  src/classroom_sync/services/plan_suggestions.py \
  tests/unit/test_plan_suggestions.py
```

Expected: Ruff reports `All checks passed!`, mypy reports no issues, and `git diff --check` exits 0.

---

### Task 3: Rebuild and Recreate the Local Services

**Files:**
- Read: `deploy/classroom/local-demo/docker-compose.yml`
- Read without displaying contents: `deploy/classroom/local-demo/.env.ai`
- Build context: `services/classroom-sync`

**Interfaces:**
- Consumes: the verified source tree from Tasks 1–2 and the existing private `.env.ai`.
- Produces: new `classroom-local-demo-sync-api:latest` and `classroom-local-demo-deadline-worker:latest` images plus healthy recreated containers.

- [ ] **Step 1: Record the rollback image IDs**

Run from the worktree root:

```bash
docker image inspect classroom-local-demo-sync-api:latest --format '{{.Id}}'
docker image inspect classroom-local-demo-deadline-worker:latest --format '{{.Id}}'
```

Expected: two non-empty SHA-256 image IDs. Retain them in the task evidence without tagging, deleting, or modifying the images.

- [ ] **Step 2: Build only the two classroom service images**

Run from the repository root so the Compose paths remain explicit:

```bash
docker compose \
  --env-file .worktrees/classroom-main-integration/deploy/classroom/local-demo/.env.ai \
  -p classroom-local-demo \
  -f .worktrees/classroom-main-integration/deploy/classroom/local-demo/docker-compose.yml \
  build sync-api deadline-worker
```

Expected: both images build and export successfully. Do not run Compose `down`, `reset`, or any command that removes volumes.

- [ ] **Step 3: Recreate only the two services with the private environment file**

Run:

```bash
docker compose \
  --env-file .worktrees/classroom-main-integration/deploy/classroom/local-demo/.env.ai \
  -p classroom-local-demo \
  -f .worktrees/classroom-main-integration/deploy/classroom/local-demo/docker-compose.yml \
  up -d --no-deps --force-recreate sync-api deadline-worker
```

Expected: `sync-api` becomes healthy and `deadline-worker` is running. The command must include `--env-file`; omitting it clears the three AI settings in recreated containers.

- [ ] **Step 4: Verify the loaded behavior without invoking Provider**

Run a read-only container check that reports booleans only:

```bash
docker exec classroom-local-demo-sync-api-1 python -c '
import inspect
import os
from classroom_sync.services.plan_suggestions import OpenAiPlanSuggestionService
names = ("CLASSROOM_AI_BASE_URL", "CLASSROOM_AI_MODEL", "CLASSROOM_AI_API_KEY")
source = inspect.getsource(OpenAiPlanSuggestionService)
print("ai_config_present=" + str(all(bool(os.environ.get(name)) for name in names)))
print("core_prompt_split_loaded=" + str("include_automatic_evaluation" in source))
'
```

Expected: `ai_config_present=True` and `core_prompt_split_loaded=True`.

---

### Task 4: Run One Real Synthetic Acceptance Request

**Files:**
- Create temporarily: `/private/tmp/classroom-bounded-output-acceptance.py`
- Create temporarily inside container: `/tmp/classroom-bounded-output-acceptance.py`
- Create temporarily inside container: `/tmp/classroom-bounded-output-acceptance.json`

**Interfaces:**
- Consumes: the recreated `sync-api`, `AiSuggestionSettings`, `OpenAiPlanSuggestionService`, and configured Provider.
- Produces: a safe result with `outcome`, `calls`, `knowledge_points`, `error_code`, and `elapsed_sec`; it never includes request or response bodies.

- [ ] **Step 1: Create and syntax-check the one-shot acceptance probe**

Use `apply_patch` to create `/private/tmp/classroom-bounded-output-acceptance.py`. The probe must:

```python
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx

from classroom_sync.config import Settings
from classroom_sync.services.plan_suggestions import (
    AiSuggestionSettings,
    OpenAiPlanSuggestionService,
    PlanSuggestionInput,
)

RESULT_PATH = Path("/tmp/classroom-bounded-output-acceptance.json")


def main() -> None:
    calls = [0]
    started = time.monotonic()
    settings = AiSuggestionSettings.from_settings(Settings.from_env())
    if settings is None:
        result: dict[str, object] = {
            "outcome": "safe_failure",
            "calls": 0,
            "error_code": "ai_suggestion_not_configured",
        }
    else:
        client = httpx.Client()
        original_post = client.post

        def one_call_post(*args: object, **kwargs: object) -> httpx.Response:
            calls[0] += 1
            if calls[0] != 1:
                raise AssertionError("second_provider_call_blocked")
            return original_post(*args, **kwargs)

        client.post = one_call_post  # type: ignore[method-assign]
        try:
            suggestion = OpenAiPlanSuggestionService(settings, client).generate(
                PlanSuggestionInput(
                    title="合成课堂方案验收",
                    statement=(
                        "这是一次不含真实课程、学生身份、学生作业或课堂记录的合成验收。"
                        "请为虚构的 Python 字典查询练习生成简洁、可编辑的中文课堂方案草稿。"
                    ),
                )
            )
            result = {
                "outcome": "ok",
                "calls": calls[0],
                "knowledge_points": len(suggestion.knowledge_points),
                "error_code": "none",
            }
        except Exception as error:
            result = {
                "outcome": "safe_failure",
                "calls": calls[0],
                "knowledge_points": 0,
                "error_code": str(getattr(error, "code", type(error).__name__)),
            }
        finally:
            client.close()

    result["elapsed_sec"] = round(time.monotonic() - started, 3)
    with NamedTemporaryFile("w", encoding="utf-8", dir=RESULT_PATH.parent, delete=False) as output_file:
        json.dump(result, output_file, ensure_ascii=False, sort_keys=True)
        temporary_path = Path(output_file.name)
    os.replace(temporary_path, RESULT_PATH)


if __name__ == "__main__":
    main()
```

Copy it to the running container and syntax-check before removing any prior result:

```bash
docker cp /private/tmp/classroom-bounded-output-acceptance.py \
  classroom-local-demo-sync-api-1:/tmp/classroom-bounded-output-acceptance.py
docker exec classroom-local-demo-sync-api-1 \
  python -m py_compile /tmp/classroom-bounded-output-acceptance.py
docker exec classroom-local-demo-sync-api-1 \
  rm -f /tmp/classroom-bounded-output-acceptance.json
```

Expected: syntax check exits 0 and no Provider request has occurred.

- [ ] **Step 2: Start exactly one detached acceptance process**

Run:

```bash
docker exec -d classroom-local-demo-sync-api-1 \
  python /tmp/classroom-bounded-output-acceptance.py
```

Expected: the process starts once. Do not execute this command again in the same acceptance cycle.

- [ ] **Step 3: Poll only the safe result file**

Run:

```bash
docker exec classroom-local-demo-sync-api-1 sh -c '
for i in $(seq 1 55); do
  if test -f /tmp/classroom-bounded-output-acceptance.json; then
    cat /tmp/classroom-bounded-output-acceptance.json
    exit 0
  fi
  sleep 1
done
echo result_pending
'
```

Expected acceptance result: `outcome=ok`, `calls=1`, `error_code=none`, and `knowledge_points` between 1 and 10. A safe failure is not acceptance success; do not claim completion.

- [ ] **Step 4: Clean up temporary files and verify final health**

Delete only the three explicitly named temporary files:

```bash
docker exec classroom-local-demo-sync-api-1 rm -f \
  /tmp/classroom-bounded-output-acceptance.py \
  /tmp/classroom-bounded-output-acceptance.json
```

Use `apply_patch` to delete `/private/tmp/classroom-bounded-output-acceptance.py`, then run:

```bash
docker inspect --format '{{.State.Status}} {{.State.Health.Status}} {{.Image}}' \
  classroom-local-demo-sync-api-1
```

Expected: `running healthy` on the newly built image.

---

### Task 5: Final Verification and Handoff

**Files:**
- Verify: `services/classroom-sync/src/classroom_sync/services/plan_suggestions.py`
- Verify: `services/classroom-sync/tests/unit/test_plan_suggestions.py`
- Verify: `docs/superpowers/specs/2026-08-24-standard-plan-suggestion-bounded-output-design.md`
- Verify: `docs/superpowers/plans/2026-08-24-standard-plan-suggestion-bounded-output.md`

**Interfaces:**
- Consumes: successful Task 4 acceptance metadata and all offline quality evidence.
- Produces: an evidence-backed handoff without staging, committing, merging, pushing, or publishing existing code changes.

- [ ] **Step 1: Re-run the fresh offline verification gate**

Run from `services/classroom-sync`:

```bash
.venv/bin/python -m pytest -q tests/unit/test_plan_suggestions.py
.venv/bin/python -m pytest -q tests/integration/test_plan_suggestions_route.py
.venv/bin/ruff check \
  src/classroom_sync/services/plan_suggestions.py \
  tests/unit/test_plan_suggestions.py
.venv/bin/mypy src
git diff --check -- \
  src/classroom_sync/services/plan_suggestions.py \
  tests/unit/test_plan_suggestions.py
```

Expected: all tests and checks pass after the real acceptance result.

- [ ] **Step 2: Verify no task code was staged**

Run from the worktree root:

```bash
git diff --cached --name-only
git status --short -- \
  services/classroom-sync/src/classroom_sync/services/plan_suggestions.py \
  services/classroom-sync/tests/unit/test_plan_suggestions.py
```

Expected: the two modified files are unstaged. Any previously staged path must be reported and preserved; do not unstage user-owned work automatically.

- [ ] **Step 3: Report the exact acceptance evidence**

The final handoff must include:

- unit and integration pass counts;
- Ruff, mypy, and diff-check outcomes;
- the new sync image ID and `running healthy` status;
- real acceptance `calls`, `knowledge_points`, `elapsed_sec`, and safe error code;
- confirmation that no Provider body, key, real course content, or student data was retained;
- confirmation that no data reset, merge, push, or publication occurred;
- the remaining dirty-worktree boundary and recommended next integration step.
