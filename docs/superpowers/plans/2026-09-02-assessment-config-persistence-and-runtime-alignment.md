# Assessment Config Persistence and Runtime Alignment Implementation Plan

> **Execution:** Apply strict RED → GREEN cycles. Backend and frontend live in separate Git repositories and must be committed and verified independently. Do not push or deploy.

**Goal:** Persist independent classroom assessment dimensions, freeze them into immutable plan versions, restore the approved one-card teacher UI, and prevent remote FinColab development from using the local-demo identity gateway.

**Architecture:** Add a one-to-one `AssessmentConfig` aggregate to plan drafts and a schema-v2 assessment snapshot to new plan versions. Reuse authoring sessions to recover an editable draft from the currently bound plan version. The Vue assessment page edits a wire-independent form, saves the plan draft and assessment config with optimistic locking, then uses the existing publish/sync pipeline. Development and local-demo run as separate complete identity domains.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, pytest, Vue 3, TypeScript, Axios, Vitest.

## Constraints

- Backend branch: `codex/assessment-config-persistence-20260902`.
- Frontend branch: `codex/student-experiment-ui-20260831`.
- Preserve `evaluation-policy`, ordinary experiment APIs, plan publication, assignment sync and Jupyter routing.
- Do not commit `.env.local-demo`, `.env.development.local`, tokens or passwords.
- Do not touch the dirty `classroom-resource-uploads-20260831` worktree.

## Task 1: Assessment config domain and API

**Backend files:**

- Create `services/classroom-sync/src/classroom_sync/services/assessment_configs.py`
- Create `services/classroom-sync/src/classroom_sync/routers/assessment_configs.py`
- Create `services/classroom-sync/tests/integration/test_assessment_config_routes.py`
- Modify `services/classroom-sync/src/classroom_sync/models.py`
- Modify `services/classroom-sync/src/classroom_sync/repositories.py`
- Modify `services/classroom-sync/src/classroom_sync/application.py`
- Modify `services/classroom-sync/src/classroom_sync/runtime.py`
- Modify `services/classroom-sync/src/classroom_sync/main.py`

### 1.1 RED: route behavior

Write integration tests against real SQLite models/services for:

- GET creates and persists the default five-dimension config.
- A second GET returns the same ids and revision.
- PUT accepts valid scopes/dimensions and returns `config_revision + 1`.
- PUT bumps the owning draft revision.
- stale draft or config revision returns 409 `assessment_config_stale`.
- wrong teacher returns 403; current parent ownership is rechecked.
- invalid total, duplicate id/order, extra scope, empty name and >10 dimensions return stable 422 codes.

Run the new test file and capture the expected import/404 failures.

### 1.2 GREEN: aggregate and router

Implement normalized dataclasses/Pydantic inputs and a transaction-owning service. Persist JSON only after validation. GET materializes one deterministic default config; ids are stable literals, not random on every read. PUT locks both draft and config and increments both revisions atomically.

Register the router and injected service. Run the focused tests until green, then run `ruff check` and `mypy` for the touched module.

## Task 2: Database migration

**Backend files:**

- Create `services/classroom-sync/migrations/versions/0010_assessment_configs.py`
- Modify `services/classroom-sync/tests/integration/test_migrations.py`

### 2.1 RED

Add migration tests that upgrade from 0009, inspect all new columns/constraints, and prove downgrade leaves the original classroom tables intact.

### 2.2 GREEN

Create `assessment_configs`, then add plan-version snapshot columns required by Task 3. Use named constraints and a linear down revision from 0009. Run migration tests on SQLite and the existing PostgreSQL path when available.

## Task 3: Immutable schema-v2 publication snapshot

**Backend files:**

- Modify `services/classroom-sync/src/classroom_sync/models.py`
- Modify `services/classroom-sync/src/classroom_sync/services/plans.py`
- Modify `services/classroom-sync/src/classroom_sync/services/read_models.py`
- Modify `contracts/classroom/v1/plan-version.schema.json`
- Modify `services/classroom-sync/tests/contract/test_schemas.py`
- Create `services/classroom-sync/tests/integration/test_assessment_config_snapshots.py`

### 3.1 RED

Test that a draft with a saved assessment config publishes schema v2, persists the exact normalized snapshot, and changes `content_hash` when only the config changes. Verify an already published version cannot be changed by later config PUT. Verify legacy drafts without the resource still publish schema v1.

### 3.2 GREEN

Add nullable `assessment_config` and non-null `content_schema_version` to `PlanVersion`; migration backfills 1. During publication, lock the config. If present, build and validate schema v2 and include the config in the hashed payload. If absent, preserve schema v1. Expose the snapshot on plan summaries without mutating it.

Run contract, publication, assignment and read-model regressions.

## Task 4: Idempotent assessment-draft recovery

**Backend files:**

- Modify `services/classroom-sync/src/classroom_sync/routers/assessment_configs.py`
- Modify `services/classroom-sync/src/classroom_sync/services/plans.py` only if a transaction helper is needed
- Extend `services/classroom-sync/tests/integration/test_assessment_config_routes.py`

### 4.1 RED

Test `POST /v1/classroom/experiments/{space}/{parent}/assessment-drafts`:

- creates/resumes one open authoring session;
- copies profile, schedule, AI policy, title and plan series from the active binding;
- returns the same draft on a retry;
- returns 404 `experiment_plan_binding_not_found` when no immutable source exists;
- does not invent schedule or roster data.

### 4.2 GREEN

Authorize the parent, call `PlanAuthoringService.create_or_return_open`, and create a draft from the current plan summary only when that session has no draft. Reuse `PlanService.create_draft` with the authoring session so the existing series is retained.

## Task 5: Frontend assessment config types and API

**Frontend files:**

- Create `src/modules/classroom-monitoring/assessment-config.ts`
- Create `src/modules/classroom-monitoring/assessment-config-api.ts`
- Create focused tests beside both files
- Modify `src/modules/classroom-monitoring/types.ts`
- Modify `src/modules/classroom-monitoring/api.ts`

### 5.1 RED

Test literal wire fixtures for strict keys, fixed scopes, BPS total, unique ids/orders, datetime/draft response validation, 409 mapping, and `POST assessment-drafts` URL encoding.

### 5.2 GREEN

Implement UI types that do not expose snake_case wire objects. Provide deterministic default dimensions and pure `validateAssessmentConfig`. Add `classroomApi.createAssessmentDraft` and config GET/PUT clients.

## Task 6: Restore the approved independent-dimension UI

**Frontend files:**

- Modify `src/modules/classroom-monitoring/components/LegacyPlanWizard.vue`
- Modify `src/modules/classroom-monitoring/__tests__/LegacyPlanWizard.test.ts`
- Reference commit `7ae2381` for layout only

### 6.1 RED

Component tests must observe:

- all five monitoring scopes including paste;
- independent dimensions with editable name, description, percentage and student visibility;
- add/delete bounded at 1–10;
- total not 100% prevents a save;
- fixed defaults affect only the local form;
- restore returns to the last successful GET/PUT snapshot;
- 409 shows refresh guidance;
- service failure does not loop or expose internal messages.

### 6.2 GREEN

On mount, recover assessment draft and GET config. Map the recovered draft profile into teaching-goal/knowledge-point inputs and keep schedule hidden. On save:

1. update the recovered plan draft with `expectedRevision`;
2. PUT assessment config with the returned draft revision and snapshot config revision;
3. publish through `usePlanPublication`;
4. sync assignments;
5. replace the local restore snapshot with the successful response.

Remove `KnowledgePointWeight` and `classroomEvaluationPolicyApi` from this page only. Preserve compatibility modules and trusted-course UI.

## Task 7: Page state and student-home fault isolation

**Frontend files:**

- Modify `src/views/admin/AdminClassroomPlanView.vue`
- Modify `src/views/admin/__tests__/AdminClassroomPlanView.test.ts`
- Modify `src/views/student/StudentHomeView.vue`
- Modify `src/views/student/__tests__/StudentHomeView.test.ts`

### 7.1 RED

Add tests that classroom assignment failure leaves successful courses and records visible. Add teacher-page tests distinguishing 401/403, unavailable, no recoverable binding and retryable failure.

### 7.2 GREEN

Load ordinary home data independently from classroom assignments. Use `teacherReadErrorMessage` and explicit error codes on the teacher page. Keep the one-card evaluation layout and prevent nested duplicate cards.

## Task 8: Runtime topology guard

**Backend/front-end files:**

- Add a non-secret remote-development compose/env example and launcher checks in the backend repo.
- Modify frontend `vite.config.ts` and its tests.
- Keep actual `.env.development.local` untracked.

### 8.1 RED

Test that Vite rejects or disables classroom monitoring when a remote FinColab proxy is paired with the local-demo classroom target. Test that explicit local-demo remains all-local and keeps 8888 isolated to that mode.

### 8.2 GREEN

Require a deliberate `VITE_CLASSROOM_PROXY_TARGET` for remote development. Provide an isolated local classroom service launcher that takes FinColab origin/organization from an untracked env file and refuses the local-demo origin. Do not change local-demo compose.

## Task 9: Verification

Backend:

```bash
cd services/classroom-sync
python -m pytest tests/integration/test_assessment_config_routes.py tests/integration/test_assessment_config_snapshots.py tests/contract/test_schemas.py tests/integration/test_migrations.py
python -m pytest
ruff check src tests
mypy
```

Frontend:

```bash
npm test -- --run src/modules/classroom-monitoring/__tests__/assessment-config.test.ts src/modules/classroom-monitoring/__tests__/assessment-config-api.test.ts src/modules/classroom-monitoring/__tests__/LegacyPlanWizard.test.ts src/views/admin/__tests__/AdminClassroomPlanView.test.ts src/views/student/__tests__/StudentHomeView.test.ts
npm run type-check
npm run build
git diff --check
```

Runtime smoke uses one coherent environment only. Verify save → refresh → same config, restore snapshot, conflict response, publish/sync, student home with classroom service stopped, and no navigation to 8888 in development. Record any unavailable remote ingress as “implementation complete, remote runtime verification blocked”; never substitute local-demo credentials.
