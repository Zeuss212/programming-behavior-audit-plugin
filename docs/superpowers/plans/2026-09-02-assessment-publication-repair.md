# Assessment Publication Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make saving an experiment assessment publish an immutable classroom plan, bind it to the experiment, and synchronize students for learning analytics.

**Architecture:** Persist creation-time publication context independently from assessment content. A teacher-authorized classroom endpoint consumes the latest valid assessment and context, reuses the current version when its immutable inputs match, otherwise publishes through `PlanService` and then synchronizes the verified roster.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, Vue 3, TypeScript, Vitest, pytest.

## Global Constraints

- Modify only the isolated classroom service and lab-platform frontend branches.
- Never modify BAMS source, BAMS data, BAMS deployment, or production data.
- All plan versions remain immutable; a changed assessment creates a new version.
- A transient roster-sync failure must be retryable without duplicating a plan version.

---

### Task 1: Persist experiment publication context

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/models.py`
- Create: `services/classroom-sync/migrations/versions/0013_experiment_publication_contexts.py`
- Create: `services/classroom-sync/src/classroom_sync/services/experiment_publications.py`
- Test: `services/classroom-sync/tests/integration/test_experiment_publication_routes.py`

- [ ] Write a failing integration test that upserts a teacher-owned context and reads the original statement and schedule.
- [ ] Add the model, migration, validation, service snapshot, and teacher-authorized context route.
- [ ] Run the new test until it passes.

### Task 2: Publish and synchronize from saved assessment

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/application.py`
- Modify: `services/classroom-sync/src/classroom_sync/runtime.py`
- Modify: `services/classroom-sync/src/classroom_sync/routers/experiment_assessment_configs.py`
- Modify: `services/classroom-sync/src/classroom_sync/repositories.py`
- Test: `services/classroom-sync/tests/integration/test_experiment_publication_routes.py`

- [ ] Write failing tests for first publication, exact retry idempotency, assessment revision publication, and roster-sync retry.
- [ ] Add the publication service that maps evaluation dimensions to a v2 profile, delegates immutable publication to `PlanService`, and synchronizes the existing verified roster.
- [ ] Expose `POST /v1/classroom/experiments/{space_id}/{parent_algorithm_id}/assessment-publication` with teacher authorization.
- [ ] Run the focused integration tests until they pass.

### Task 3: Connect creation and assessment save in the frontend

**Files:**
- Create: `src/modules/classroom-monitoring/experiment-publication-api.ts`
- Modify: `src/views/admin/AdminProjectsView.vue`
- Modify: `src/modules/classroom-monitoring/components/LegacyPlanWizard.vue`
- Modify: `src/views/admin/AdminClassroomPlanView.vue`
- Test: `src/modules/classroom-monitoring/__tests__/LegacyPlanWizard.test.ts`
- Test: `src/views/admin/__tests__/AdminProjectsView.test.ts`

- [ ] Write failing tests that creation persists its schedule/context and that a successful save calls assessment update followed by assessment publication.
- [ ] Implement strict wire mapping, save-context call after successful creation, and publication call after assessment update.
- [ ] Refresh the bound-plan status and report publication/sync outcomes without automatic navigation.
- [ ] Run the focused frontend tests until they pass.

### Task 4: Prove analytics and release quality

**Files:**
- Test: `src/views/admin/__tests__/AdminLearningAnalyticsView.test.ts`
- Test: `services/classroom-sync/tests/integration/test_plan_assignment_flow.py`

- [ ] Add a regression asserting a save-created binding leaves analytics in its data view and supports the existing student-detail route.
- [ ] Run backend pytest and Ruff, frontend test/type-check/build, and `git diff --check` in both worktrees.
- [ ] Commit backend contract/database changes and frontend integration changes separately; do not push or deploy.
