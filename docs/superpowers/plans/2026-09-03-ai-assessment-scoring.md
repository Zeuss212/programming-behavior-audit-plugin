# AI Assessment Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a 0–100 pure-AI score for every configured assessment dimension, calculate the weighted overall score on the server, and show the read-only result without changing teacher review workflows.

**Architecture:** Reuse the existing consented, bounded brief-analysis job and add a server-injected copy of the published assessment dimensions. The provider returns only per-dimension judgements; the service validates evidence references and score caps, then calculates weights deterministically and stores a separate `assessment_score` block in the student brief. Existing teaching-analysis and teacher-review payloads stay unchanged.

**Tech Stack:** Python 3.10, Pydantic 2, SQLAlchemy JSON payloads, JSON Schema, pytest, Vue 3, TypeScript, Vitest.

## Global Constraints

- Scores are integers from 0 through 100 for every published dimension.
- `partial` evidence caps the AI score at 79; `insufficient` evidence caps it at 59.
- The server, never the model, copies weights and calculates the one-decimal overall score.
- Keep old briefs and plan versions readable.
- Do not change teacher-review schemas, routes, or editing UI.
- Do not commit, push, publish, deploy, or call a paid AI provider.

---

### Task 1: Scoring contract and calculation

**Files:**
- Create: `services/classroom-sync/src/classroom_sync/services/assessment_scoring.py`
- Create: `services/classroom-sync/tests/unit/test_assessment_scoring.py`

**Interfaces:**
- Consumes: published dimensions shaped as `id`, `name`, `description`, `weight_bps`, `order` and provider judgements shaped as `dimension_id`, `score`, `evidence_level`, `confidence`, `reason`, `evidence_event_ids`.
- Produces: `build_assessment_score(dimensions, judgements)` with copied weights, per-row contribution, and one-decimal total.

- [ ] Write failing tests for dimension matching, score/evidence caps, unknown evidence references, and `75.9` weighted rounding.
- [ ] Run `uv run pytest tests/unit/test_assessment_scoring.py -q` and confirm the module is missing.
- [ ] Implement strict Pydantic models and the deterministic calculator.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Provider and brief persistence integration

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/services/brief_analysis.py`
- Modify: `services/classroom-sync/src/classroom_sync/services/briefs.py`
- Modify: `contracts/classroom/v1/student-brief.schema.json`
- Modify: `services/classroom-sync/tests/unit/test_brief_analysis.py`
- Modify: `services/classroom-sync/tests/integration/test_briefs.py`
- Modify: `services/classroom-sync/tests/contract/test_schemas.py`

**Interfaces:**
- Consumes: the existing private evidence input and the immutable `PlanVersion.assessment_config` snapshot.
- Produces: provider field `assessment_dimension_scores` and stored top-level field `assessment_score` while preserving the existing `ai_analysis` object.

- [ ] Write failing provider, persistence, and schema tests.
- [ ] Run the focused tests and confirm failures identify the missing scoring contract.
- [ ] Inject trusted dimensions into the durable job input, validate the provider output, and split the stored score from existing teaching analysis.
- [ ] Re-run focused unit, contract, and integration tests.

### Task 3: Bounded task statement input

**Files:**
- Modify: `myextension/classroom_ai_analysis_input.py`
- Modify: `myextension/tests/test_classroom_ai_analysis_input.py`
- Modify: `services/classroom-sync/src/classroom_sync/services/briefs.py`
- Modify: `services/classroom-sync/tests/unit/test_plugin_submit_ai_contract.py`

**Interfaces:**
- Consumes: `profile.problem_context.statement`.
- Produces: an optional, scrubbed, bounded `lesson.statement` accepted by old and new clients.

- [ ] Write a failing test proving the statement is included without leaking extra problem-context fields.
- [ ] Run the focused plugin tests and confirm failure.
- [ ] Add the bounded statement and server-side context comparison.
- [ ] Re-run the plugin and classroom contract tests.

### Task 4: Read models and frontend display

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/services/read_models.py`
- Modify: `services/classroom-sync/tests/integration/test_classroom_read_models.py`
- Modify: frontend `src/modules/classroom-monitoring/types.ts`
- Modify: frontend `src/modules/classroom-monitoring/api.ts`
- Modify: frontend `src/views/admin/AdminLearningAnalyticsView.vue`
- Modify: frontend `src/modules/classroom-monitoring/components/StudentBriefPanel.vue`
- Modify: related Vitest files beside those components.

**Interfaces:**
- Consumes: validated `assessment_score` wire payload.
- Produces: compact overall score in monitoring and a five-dimension read-only score table in the brief detail.

- [ ] Write failing backend mapper and frontend rendering tests.
- [ ] Run focused pytest and Vitest commands and confirm expected failures.
- [ ] Add strict wire mapping, safe rendering, evidence labels, weights, contributions, and total.
- [ ] Re-run focused tests.

### Task 5: Default dimensions and regression verification

**Files:**
- Modify: frontend `src/modules/classroom-monitoring/assessment-config.ts`
- Modify: frontend `src/modules/classroom-monitoring/__tests__/assessment-config.test.ts`

**Interfaces:**
- Produces: the approved default IDs `knowledge_mastery`, `debugging_ability`, `test_verification`, `requirement_alignment`, and `coding_fundamentals`, totaling 10,000 basis points.

- [ ] Write a failing default-dimension test.
- [ ] Run the focused test and confirm failure against the old defaults.
- [ ] Replace only the default list and descriptions.
- [ ] Run backend pytest/ruff/mypy and frontend Vitest/type-check/build, then inspect both Git diffs for unrelated changes.
