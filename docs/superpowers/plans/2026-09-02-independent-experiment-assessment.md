# Independent Experiment Assessment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow an existing experiment to create and persist assessment content without any classroom plan, schedule, participant, or resource prerequisite.

**Architecture:** Add an experiment-scoped assessment aggregate keyed by course and parent experiment ID with a display-name snapshot. Expose ensure/update endpoints, snapshot the independent config during later plan publication, and make the frontend editor save directly to this aggregate.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL/SQLite tests, Vue 3, TypeScript, Vitest.

## Global Constraints

- Keep `.env.local-demo` and every `*.local` file untracked and unmodified.
- Preserve historical plan-version hashes and existing draft assessment records.
- Do not require classroom publication to display or save assessment content.

---

### Task 1: Experiment assessment persistence and API

**Files:**
- Create: `services/classroom-sync/migrations/versions/0012_experiment_assessment_configs.py`
- Create: `services/classroom-sync/src/classroom_sync/services/experiment_assessment_configs.py`
- Create: `services/classroom-sync/src/classroom_sync/routers/experiment_assessment_configs.py`
- Modify: `services/classroom-sync/src/classroom_sync/models.py`
- Modify: `services/classroom-sync/src/classroom_sync/application.py`
- Modify: `services/classroom-sync/src/classroom_sync/main.py`
- Modify: `services/classroom-sync/src/classroom_sync/runtime.py`
- Test: `services/classroom-sync/tests/integration/test_experiment_assessment_config_routes.py`

**Interfaces:**
- Consumes: authenticated teacher ownership check for `(space_id, parent_algorithm_id)`.
- Produces: `ensure(...) -> ExperimentAssessmentConfigSnapshot` and `update(...) -> ExperimentAssessmentConfigSnapshot`.

- [ ] Write route tests for first-use defaults, persisted updates, ownership, validation, and stale revisions.
- [ ] Run focused tests and verify missing endpoint/model failures.
- [ ] Add the model, sequential migration after `0011_experiment_resources`, service, router, and runtime wiring.
- [ ] Run focused route and migration tests until green.

### Task 2: Publication compatibility

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/services/plans.py`
- Test: `services/classroom-sync/tests/integration/test_assessment_config_snapshots.py`

**Interfaces:**
- Consumes: independent config selected by draft `space_id + parent_algorithm_id`.
- Produces: the existing immutable `PlanVersion.assessment_config` snapshot and unchanged legacy fallback.

- [ ] Add a failing test proving a later classroom publication snapshots the independent config.
- [ ] Prefer the independent config and fall back to the legacy draft config.
- [ ] Verify legacy schema-v1/v2 hashes and assessment snapshot tests remain green.

### Task 3: Independent frontend editor

**Files:**
- Create: `src/modules/classroom-monitoring/experiment-assessment-config-api.ts`
- Modify: `src/modules/classroom-monitoring/components/LegacyPlanWizard.vue`
- Modify: `src/modules/classroom-monitoring/components/PlanWizard.vue`
- Modify: `src/views/admin/AdminClassroomPlanView.vue`
- Modify: `src/views/admin/AdminProjectsView.vue`
- Test: `src/views/admin/__tests__/AdminClassroomPlanView.test.ts`
- Test: `src/modules/classroom-monitoring/components/__tests__/LegacyPlanWizard.test.ts`

**Interfaces:**
- Consumes: course ID, parent experiment ID, and experiment display name.
- Produces: direct ensure/update calls with optimistic config revision; no plan publication.

- [ ] Replace missing-plan recovery expectations with an always-visible assessment editor test.
- [ ] Add API response validation and direct save tests.
- [ ] Remove plan schedule/knowledge-point publication behavior from the legacy editor.
- [ ] Pass experiment name from the experiment list route and retain parent ID as a safe fallback.
- [ ] Run focused tests and TypeScript checks.

### Task 4: Full verification and local preview

**Files:**
- Verify only; no new production files.

- [ ] Run backend `pytest`, Ruff, and Mypy.
- [ ] Run frontend Vitest, type-check, and production build.
- [ ] Rebuild only the isolated local `classroom-remote-development` services using the ignored remote-development config.
- [ ] Verify the new API route, Alembic head, and visible assessment page state.
- [ ] Stop before commit, merge, or push and report evidence.
