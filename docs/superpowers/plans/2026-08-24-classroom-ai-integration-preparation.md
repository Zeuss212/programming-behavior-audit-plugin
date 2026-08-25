# Classroom AI Integration Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the accepted dirty classroom workspace while producing a local, reviewable branch based on the current `main`, with the bounded standard-endpoint Provider fix isolated from its pre-existing cross-stack dependencies.

**Architecture:** The original `codex/classroom-main-integration` worktree remains the evidence source and is never staged or cleaned. A new `codex/classroom-ai-integration-ready` worktree starts from the current local `main`, receives the existing design commits, then imports the source worktree snapshot. The snapshot is committed in its pre-bounded-output state; the already-tested standard-endpoint profile and prompt split are reapplied in a separate commit.

**Tech Stack:** Git worktrees, Python 3.12, FastAPI/Pydantic, pytest, Ruff, mypy, TypeScript/Jest.

## Global Constraints

- Do not mutate, stage, stash, reset, or clean the original dirty worktree.
- Do not pull, push, create a PR, merge into `main`, publish images, or call the Provider.
- Base the integration branch on the current local `main` commit; preserve unrelated dirty changes in the main checkout.
- Transfer only paths reported modified or untracked by the source worktree.
- Keep the accepted Provider behavior in a dedicated commit: standard endpoints use one `1200`-token core-only request; Coding Plan retains `2048 → 4096`, JSON mode, disabled thinking, and optional `automatic_evaluation`.
- Verification must load code from the new worktree, not editable-install paths from the source worktree.

---

### Task 1: Record the Source Boundary

**Files:**
- Read: all paths reported by `git status --short` in `.worktrees/classroom-main-integration`
- Create: `docs/superpowers/plans/2026-08-24-classroom-ai-integration-preparation.md`

**Interfaces:**
- Consumes: source HEAD `abd182c`, local `main`, the dirty tracked/untracked path inventory, and the previously verified container image IDs.
- Produces: a reproducible inventory and an explicit no-mutation boundary for the source worktree.

- [ ] **Step 1: Confirm source index and branch state**

Run:

```bash
git diff --cached --name-only
git status --short
git branch --show-current
```

Expected: the index is empty, the branch is `codex/classroom-main-integration`, and dirty files remain present.

- [ ] **Step 2: Confirm the local base and isolation directory**

Run from the repository root:

```bash
git branch --show-current
git rev-parse main
git check-ignore -q .worktrees
```

Expected: the root checkout is on `main`, `.worktrees` is ignored, and no checkout mutation is needed.

### Task 2: Create the Clean Integration Worktree

**Files:**
- Create worktree: `.worktrees/classroom-ai-integration-ready`
- Branch: `codex/classroom-ai-integration-ready`

**Interfaces:**
- Consumes: current local `main` and the seven source-only design commits.
- Produces: a clean named-branch worktree containing current `main` plus the approved classroom design history.

- [ ] **Step 1: Create the branch from local main**

Run:

```bash
git worktree add .worktrees/classroom-ai-integration-ready -b codex/classroom-ai-integration-ready main
```

Expected: the new worktree is clean and points at the current local `main`.

- [ ] **Step 2: Cherry-pick the source design commits in order**

Run in the new worktree:

```bash
git cherry-pick 0d4b0a4 06b262b 7b9d8fd c163082 98b3e9e 11b27ab abd182c
```

Expected: seven documentation commits apply without modifying application code.

### Task 3: Commit the Pre-Fix Coordinated Snapshot

**Files:**
- Import: only modified/untracked source-worktree paths
- Exclude from this commit: `docs/superpowers/plans/2026-08-24-standard-plan-suggestion-bounded-output.md`
- Exclude from this commit: `docs/superpowers/plans/2026-08-24-classroom-ai-integration-preparation.md`
- Temporarily revert in the new worktree: bounded endpoint profile/prompt code and its four test changes

**Interfaces:**
- Consumes: the exact source worktree snapshot.
- Produces: a local commit representing the pre-existing coordinated classroom AI work before the bounded standard-endpoint fix.

- [ ] **Step 1: Mechanically import only dirty source paths**

Run from the repository root, replacing neither path with a glob:

```bash
git -C .worktrees/classroom-main-integration diff --name-only -z HEAD | rsync -a --from0 --files-from=- .worktrees/classroom-main-integration/ .worktrees/classroom-ai-integration-ready/
git -C .worktrees/classroom-main-integration ls-files --others --exclude-standard -z | rsync -a --from0 --files-from=- .worktrees/classroom-main-integration/ .worktrees/classroom-ai-integration-ready/
```

Expected: only the source worktree's tracked modifications and non-ignored untracked files appear in the new worktree; `.git`, ignored files, and clean tracked files are untouched.

- [ ] **Step 2: Revert only the bounded-output delta in the new worktree**

Apply these exact reversions only in the new worktree:

- remove the `_uses_coding_plan_profile` assignment from `__init__`;
- replace the request-profile portion of `generate` with:

```python
        try:
            completion = completion_client.complete_with_metadata(
                self._messages(suggestion_input),
                temperature=0.2,
                max_tokens=2048,
                thinking_mode="disabled",
                json_mode=True,
            )
            if completion.finish_reason == "length":
                completion = completion_client.complete_with_metadata(
                    self._messages(suggestion_input),
                    temperature=0.2,
                    max_tokens=4096,
                    thinking_mode="disabled",
                    json_mode=True,
                )
```

- replace `_messages` with the one-argument version whose system content is exactly:

```python
    @staticmethod
    def _messages(suggestion_input: PlanSuggestionInput) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "你是课堂教学设计助手。只返回一个 JSON 对象，不要 Markdown。"
                    "对象必须含 title 和 knowledge_points；knowledge_points 是 1 到 10 项，"
                    "每项含 name、description，并且可以含 automatic_evaluation。"
                    "automatic_evaluation 只能含 mode=all、summary 和 requirements；"
                    "requirements 每项只能含 kind，kind 只能是 successful_execution、"
                    "dict_literal_assignment、dict_key_value_pairs、dict_subscript_access、"
                    "dict_get_with_default、print_call 或 input_call。"
                    "仅在能用这些本地、非执行性证据可靠判定时提供 automatic_evaluation，"
                    "否则省略该字段。内容必须是教师可继续编辑的中文课堂方案草稿。"
                ),
            },
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

- in `test_adapter_uses_the_fast_json_profile_for_bounded_teaching_text`, restore `configured_settings()` and `https://ai.example/v1/chat/completions`, and remove only the two system-prompt assertions;
- delete `test_adapter_uses_standard_chat_completions_profile_for_plan_endpoint` and `test_adapter_keeps_a_complete_standard_plan_response_without_a_second_provider_call`;
- in `test_adapter_retries_once_with_4096_tokens_after_a_length_response`, restore `configured_settings()`.

- [ ] **Step 3: Verify the pre-fix snapshot is internally consistent**

Run with the source worktree virtual environment and the new worktree `src` on `PYTHONPATH`:

```bash
PYTHONPATH=src /Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/classroom-main-integration/services/classroom-sync/.venv/bin/python -m pytest -q tests/unit/test_plan_suggestions.py
PYTHONPATH=src /Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/classroom-main-integration/services/classroom-sync/.venv/bin/python -m pytest -q tests/integration/test_plan_suggestions_route.py
```

Expected: the pre-fix unit and route suites pass; no Provider call occurs.

- [ ] **Step 4: Commit the snapshot without the two new plan documents**

Run:

```bash
git add -A
git restore --staged docs/superpowers/plans/2026-08-24-standard-plan-suggestion-bounded-output.md
git restore --staged docs/superpowers/plans/2026-08-24-classroom-ai-integration-preparation.md
git commit -m "feat: integrate classroom AI analysis workflow"
```

Expected: one honest coordinated snapshot commit; the two new plan files remain untracked.

### Task 4: Apply the Bounded Standard-Endpoint Fix as a Separate Commit

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/services/plan_suggestions.py`
- Modify: `services/classroom-sync/tests/unit/test_plan_suggestions.py`
- Add: `docs/superpowers/plans/2026-08-24-standard-plan-suggestion-bounded-output.md`
- Add: `docs/superpowers/plans/2026-08-24-classroom-ai-integration-preparation.md`

**Interfaces:**
- Consumes: the pre-fix snapshot from Task 3.
- Produces: endpoint-aware `_messages(..., include_automatic_evaluation: bool)`, standard one-call `1200` behavior, and preserved Coding Plan recovery behavior.

- [ ] **Step 1: Reapply the exact accepted code and test delta**

Mechanically copy only the two already-verified files from the preserved source worktree:

```bash
rsync -a ../classroom-main-integration/services/classroom-sync/src/classroom_sync/services/plan_suggestions.py services/classroom-sync/src/classroom_sync/services/plan_suggestions.py
rsync -a ../classroom-main-integration/services/classroom-sync/tests/unit/test_plan_suggestions.py services/classroom-sync/tests/unit/test_plan_suggestions.py
```

Expected: `_uses_coding_plan_profile`, standard `1200`/single-call behavior, Coding Plan-only recovery, reusable messages, conditional prompt construction, both standard endpoint tests, and both prompt assertions exactly match the accepted source worktree.

- [ ] **Step 2: Verify focused and complete classroom-sync gates**

Run:

```bash
PYTHONPATH=src /Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/classroom-main-integration/services/classroom-sync/.venv/bin/python -m pytest -q tests/unit/test_plan_suggestions.py
PYTHONPATH=src /Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/classroom-main-integration/services/classroom-sync/.venv/bin/python -m pytest -q tests/integration/test_plan_suggestions_route.py
PYTHONPATH=src /Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/classroom-main-integration/services/classroom-sync/.venv/bin/python -m pytest -q
/Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/classroom-main-integration/services/classroom-sync/.venv/bin/ruff check src/classroom_sync/services/plan_suggestions.py tests/unit/test_plan_suggestions.py
MYPYPATH=src /Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/classroom-main-integration/services/classroom-sync/.venv/bin/mypy src
git diff --check
```

Expected: 21 unit tests, 6 route tests, the complete service suite, Ruff, mypy, and diff-check pass without a Provider call.

- [ ] **Step 3: Commit only the bounded fix and its plans**

Run:

```bash
git add services/classroom-sync/src/classroom_sync/services/plan_suggestions.py services/classroom-sync/tests/unit/test_plan_suggestions.py docs/superpowers/plans/2026-08-24-standard-plan-suggestion-bounded-output.md docs/superpowers/plans/2026-08-24-classroom-ai-integration-preparation.md
git commit -m "fix: bound standard plan provider output"
```

Expected: the bounded Provider change is isolated in one reviewable commit.

### Task 5: Verify the Local Integration Candidate

**Files:**
- Verify: all committed files in `.worktrees/classroom-ai-integration-ready`
- Preserve: all source and main-checkout dirty files

**Interfaces:**
- Consumes: the new integration branch and existing local toolchains.
- Produces: a local integration candidate with reproducible test evidence and no external side effects.

- [ ] **Step 1: Reuse the verified local toolchains without installing**

Create only these ignored symlinks in the integration worktree:

```bash
ln -s ../classroom-main-integration/.venv .venv
ln -s ../../../classroom-main-integration/services/classroom-sync/.venv services/classroom-sync/.venv
ln -s ../classroom-main-integration/node_modules node_modules
```

Expected: all three links resolve to existing local toolchains; no dependency download occurs.

- [ ] **Step 2: Run backend and plugin regression suites**

Run from the integration worktree root:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
```

Run from `services/classroom-sync`:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
MYPYPATH=src .venv/bin/mypy src
```

Expected: both complete Python suites, Ruff, and mypy exit 0 without a Provider call.

- [ ] **Step 3: Run TypeScript regression and build gates**

Run from the integration worktree root:

```bash
npm test -- --runInBand
npm run build:lib
npm run lint:check
```

Expected: Jest, TypeScript compilation, ESLint, Prettier, and Stylelint exit 0 using the linked `node_modules`.

- [ ] **Step 4: Verify branch, status, and source preservation**

Run:

```bash
git status --short
git log --oneline main..HEAD
```

Then re-run the source worktree status and compare its path inventory with Task 1. Expected: the integration worktree is clean; the source inventory remains present and unstaged.

- [ ] **Step 5: Stop locally**

Report the branch, worktree, commit IDs, verification results, and any baseline failures. Do not push, merge into `main`, publish, or call the Provider.
