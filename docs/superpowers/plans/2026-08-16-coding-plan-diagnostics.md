# Coding Plan Classroom Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Coding Plan classroom analysis diagnosable without leaking data and restrict the local validation run to one provider attempt.

**Architecture:** Classify failures at the OpenAI-compatible boundary, preserve their safe code through the brief-analysis adapter, and let the durable worker decide retryability from the typed domain error. Wire a bounded attempt setting through runtime configuration and Docker Compose; the ignored local AI environment opts into a single attempt.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy 2, httpx, pytest, Docker Compose, GLM Coding Plan OpenAI-compatible API.

## Global Constraints

- Work only on `codex/classroom-main-integration`; do not merge or push `main`.
- Keep `CLASSROOM_AI_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4` and `CLASSROOM_AI_MODEL=glm-5.2` for the local Coding Plan run.
- Never read, print, commit, or upload `deploy/classroom/local-demo/.env.ai` or its API key.
- Persist only allowlisted safe failure codes; never store raw provider responses, headers, prompts, or evidence.
- The local validation worker must make at most one provider attempt.

---

### Task 1: Preserve safe Coding Plan failure categories

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/errors.py`
- Modify: `services/classroom-sync/src/classroom_sync/services/plan_suggestions.py`
- Modify: `services/classroom-sync/src/classroom_sync/services/brief_analysis.py`
- Test: `services/classroom-sync/tests/unit/test_brief_analysis.py`

**Interfaces:**
- Produces `UpstreamUnavailableError(code, retryable=...)` outcomes consumed by the worker.

- [ ] Write failing tests for a 403 Coding Plan response and malformed completion payload; assert their safe codes differ and neither includes a response body.
- [ ] Run the focused test and observe the pre-change generic failure code.
- [ ] Add typed safe-code mapping in the provider client and preserve the code through the brief adapter.
- [ ] Rerun focused unit tests and commit the focused change.

### Task 2: Bound retries from runtime configuration

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/config.py`
- Modify: `services/classroom-sync/src/classroom_sync/runtime.py`
- Modify: `services/classroom-sync/src/classroom_sync/services/brief_analysis.py`
- Modify: `services/classroom-sync/tests/integration/test_briefs.py`
- Test: `services/classroom-sync/tests/unit/test_brief_analysis.py`

**Interfaces:**
- Consumes `CLASSROOM_AI_MAX_ATTEMPTS` as an integer from 1 through 3; defaults to 3.
- Produces a single terminal job attempt for non-retryable provider failures or a locally configured maximum of 1.

- [ ] Write failing tests for a one-attempt worker and non-retryable failure.
- [ ] Run the focused tests and observe existing three-attempt behavior.
- [ ] Implement the bounded setting and retry decision.
- [ ] Rerun focused tests, full tests, ruff, and mypy.

### Task 3: Configure and execute the local Coding Plan test

**Files:**
- Modify: `deploy/classroom/local-demo/docker-compose.yml`
- Modify: `deploy/classroom/local-demo/.env.ai` (ignored, never committed)
- Modify: `docs/superpowers/specs/2026-08-16-coding-plan-diagnostics-design.md`

**Interfaces:**
- The worker receives `CLASSROOM_AI_MAX_ATTEMPTS=1` only in the local demo environment.

- [ ] Rebuild only sync-api and deadline-worker, then verify health and boolean-only AI configuration.
- [ ] Use Computer Use to create/publish a classroom as teacher, accept and launch it as student, submit one brief, then return as teacher.
- [ ] Read only the allowlisted teacher AI status and safe database failure code; document the result without raw provider data.
- [ ] Commit the implementation after the terminal result is recorded; do not merge or push.

## Plan Self-Review

- Spec coverage: Tasks 1-2 implement diagnostic safety and bounded retry behavior; Task 3 validates the actual local Coding Plan integration.
- Placeholder scan: each task names the affected files, interfaces, tests, and terminal local behavior.
- Type consistency: the setting is an integer validated in `Settings`, passed by runtime into `BriefAnalysisJobService`, and consumed only by the retry policy.
