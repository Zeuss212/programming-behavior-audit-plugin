# Versioned Classroom Assignments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a new classroom plan receives a new student assignment even when the same student already submitted work for an earlier plan in the same experiment.

**Architecture:** Keep the existing same-plan republish behavior unchanged. Change the assignment natural key from experiment-and-student scope to plan-and-student scope, then make the repository use that same key. A forward-only Alembic migration changes the database constraint without deleting historical assignments, briefs, or monitoring sessions.

**Tech Stack:** Python 3.10+, SQLAlchemy 2, Alembic, pytest, PostgreSQL, SQLite, Docker Compose.

## Global Constraints

- Work only in `codex/classroom-main-integration`; do not merge or push `main`.
- Preserve submitted classroom data; do not reset local Postgres or MinIO volumes.
- Never read, print, commit, or upload `deploy/classroom/local-demo/.env.ai` or its GLM key.
- Preserve the existing rule that an accepted assignment for the same `plan_id` is retained after that plan is republished.
- Final local validation may make exactly one authorized GLM analysis request.

---

### Task 1: Add the cross-plan regression test

**Files:**
- Modify: `services/classroom-sync/tests/integration/test_plan_assignment_flow.py`

**Interfaces:**
- Consumes: `PlanService.create_draft`, `PlanService.publish_draft`, `AssignmentService.sync_assignments`, and `AssignmentService.accept_assignment`.
- Produces: a regression test that fails while `ClassroomRepository.get_assignment` omits `plan_id` from its lookup.

- [ ] **Step 1: Write the failing test**

Add a test named `test_new_plan_creates_a_fresh_assignment_after_prior_plan_is_submitted`. Import `StudentAssignment`, reuse the existing SQLite setup and `profile_draft` helper, then create a first draft, publish it, sync the one-student roster, and accept the returned assignment. Persist the terminal state explicitly so the regression represents the real submitted-brief path:

```python
with session_factory.begin() as session:
    persisted = session.get(StudentAssignment, first_assignment.id)
    assert persisted is not None
    persisted.status = "submitted"
```

Create a separate second draft with the same `space_id`, `parent_algorithm_id`, and roster; publish and synchronize it.

```python
assert first_assignment.plan_id != second_plan.plan_id
assert second_assignment.id != first_assignment.id
assert second_assignment.plan_id == second_plan.plan_id
assert second_assignment.plan_version == 1
assert second_assignment.status == "pending_acceptance"
assert assignment_service.accept_assignment(
    second_assignment.id, student_id="student-1"
).status == "ready"
assert first_assignment.status == "submitted"
```

- [ ] **Step 2: Run the regression test and verify it fails for the intended reason**

Run:

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/integration/test_plan_assignment_flow.py::test_new_plan_creates_a_fresh_assignment_after_prior_plan_is_submitted -q
```

Expected before the implementation: failure because the second synchronization returns the first assignment, which is `submitted`, instead of a fresh `pending_acceptance` assignment.

### Task 2: Scope the assignment key to a classroom plan

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/models.py:80-87`
- Modify: `services/classroom-sync/src/classroom_sync/repositories.py:70-85`
- Modify: `services/classroom-sync/src/classroom_sync/services/assignments.py:53-59`
- Modify: `services/classroom-sync/tests/integration/test_plan_assignment_flow.py`

**Interfaces:**
- Consumes: `PlanVersion.plan_id`, `StudentAssignment.plan_id`, and the regression test from Task 1.
- Produces: `ClassroomRepository.get_assignment(..., plan_id: str, ...)`, which returns only an assignment for the specified plan.

- [ ] **Step 1: Make the smallest model and lookup change**

Change `StudentAssignment.__table_args__` so the unique constraint named `uq_student_assignments_plan_student_child` contains these columns in order:

```python
"plan_id",
"space_id",
"parent_algorithm_id",
"student_id",
"child_algorithm_id",
```

Add `plan_id: str` to `ClassroomRepository.get_assignment` and add `StudentAssignment.plan_id == plan_id` to its query. Pass `plan_version.plan_id` from `AssignmentService.sync_assignments` when resolving each roster entry. Do not change the existing branch that leaves a non-pending assignment attached to the same plan untouched.

- [ ] **Step 2: Run the focused tests and verify green**

Run:

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/integration/test_plan_assignment_flow.py -q
```

Expected: the new cross-plan test and the existing same-plan republish test both pass.

### Task 3: Migrate the durable Postgres constraint and test it

**Files:**
- Create: `services/classroom-sync/migrations/versions/0003_versioned_student_assignments.py`
- Modify: `services/classroom-sync/tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: legacy constraint `uq_student_assignments_student_child` and the model constraint from Task 2.
- Produces: Alembic head migration that preserves existing rows while allowing two assignments for the same student child under different plan IDs.

- [ ] **Step 1: Add a migration-specific failing assertion**

In `test_core_migration_round_trip_and_uniqueness`, after inserting `assignment-1`, add a second `student_assignments` insert that has the same space, parent experiment, student, and child fields but `id="assignment-plan-2"` and `plan_id="plan-2"`. The test must expect that insert to succeed. Keep the current duplicate-key assertion, but make its duplicate use `plan_id="plan-1"` so it still expects `IntegrityError`.

- [ ] **Step 2: Run the migration test and verify it fails**

Run:

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/integration/test_migrations.py::test_core_migration_round_trip_and_uniqueness -q
```

Expected before the migration: the second-plan insert raises `IntegrityError` under the old student-child constraint.

- [ ] **Step 3: Create the minimal Alembic migration**

Create revision `0003_versioned_student_assignments` with `down_revision = "0002_brief_analysis_jobs"`. Use `op.batch_alter_table("student_assignments")` so the migration also runs in the existing SQLite migration test. Its upgrade drops `uq_student_assignments_student_child` and creates `uq_student_assignments_plan_student_child` over `plan_id`, `space_id`, `parent_algorithm_id`, `student_id`, and `child_algorithm_id`. Its downgrade performs the inverse operations in reverse order. Document in the migration docstring that downgrading after multiple retained plan assignments requires a pre-migration database backup because the legacy key is stricter.

```python
with op.batch_alter_table("student_assignments") as batch_op:
    batch_op.drop_constraint("uq_student_assignments_student_child", type_="unique")
    batch_op.create_unique_constraint(
        "uq_student_assignments_plan_student_child",
        ["plan_id", "space_id", "parent_algorithm_id", "student_id", "child_algorithm_id"],
    )
```

- [ ] **Step 4: Run migration and full backend verification**

Run:

```bash
cd services/classroom-sync
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
uv run --extra dev mypy src
```

Expected: all commands exit 0.

### Task 4: Preserve the brief-to-analysis-job foreign key ordering

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/services/briefs.py`
- Modify: `services/classroom-sync/tests/integration/test_briefs.py`

**Interfaces:**
- Consumes: `BriefService.submit` and the `classroom_brief_analysis_jobs.source_brief_id` foreign key.
- Produces: an AI analysis job only after its source student brief has been inserted in the same transaction.

- [ ] **Step 1: Reproduce the production foreign-key contract in the integration test**

Enable SQLite foreign-key enforcement for `test_server_requested_analysis_writes_pending_brief_and_durable_job`, preserving the real model relationships in the fixture. Verify the test fails before the production change because the pending job references a source brief that has not yet been flushed.

- [ ] **Step 2: Flush only before enqueuing an AI analysis job**

After adding the `StudentBrief`, call `session.flush()` only in the `request_ai_analysis` branch before constructing `ClassroomBriefAnalysisJob`. Keep the brief, job, assignment, session, and audit event in the same transaction so a later error still rolls the complete submission back.

- [ ] **Step 3: Run the brief and worker tests**

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/integration/test_briefs.py -q
```

Expected: the pending job points at a persisted source brief and the worker still appends an analysis revision.

### Task 5: Apply the local migration and verify the GLM classroom path

**Files:**
- No tracked source changes.

**Interfaces:**
- Consumes: the built `classroom-local-demo-sync-api` and `classroom-local-demo-deadline-worker` images plus the ignored local `deploy/classroom/local-demo/.env.ai`.
- Produces: a new classroom brief whose teacher monitoring record reaches a terminal AI analysis status.

- [ ] **Step 1: Rebuild and recreate only API and worker**

Run from the integration worktree:

```bash
docker compose --env-file deploy/classroom/local-demo/.env.ai -p classroom-local-demo -f deploy/classroom/local-demo/docker-compose.yml build sync-api deadline-worker
docker compose --env-file deploy/classroom/local-demo/.env.ai -p classroom-local-demo -f deploy/classroom/local-demo/docker-compose.yml up -d --force-recreate sync-api deadline-worker
```

Verify `sync-api` health and Docker-internal FinColab direct access without revealing environment values:

```bash
docker compose -p classroom-local-demo -f deploy/classroom/local-demo/docker-compose.yml ps sync-api deadline-worker
docker exec classroom-local-demo-sync-api-1 python -c 'from urllib.request import urlopen; print(urlopen("http://demo-fincolab:8080/health/live", timeout=5).status)'
```

- [ ] **Step 2: Execute one authorized local brief submission**

Run:

```bash
PYTHONPATH=scripts uv run --no-project python scripts/local_classroom_demo_smoke.py
```

Expected: the script prints `{"status":"ok","phase":"submitted"}`. Do not repeat it if it succeeds.

- [ ] **Step 3: Poll teacher monitoring for a terminal AI outcome**

Read the plan ID returned by the smoke state only through local API responses. Poll the teacher monitoring endpoint at most once every five seconds for 60 seconds. Report only the allowlisted status (`ready`, `unavailable`, `not_requested`, or a timeout) and whether a teacher-visible analysis summary exists. Never print raw evidence, API keys, request headers, or model payloads.

- [ ] **Step 4: Commit the bug fix**

Run:

```bash
git add services/classroom-sync/src/classroom_sync/models.py \
  services/classroom-sync/src/classroom_sync/repositories.py \
  services/classroom-sync/src/classroom_sync/services/assignments.py \
  services/classroom-sync/migrations/versions/0003_versioned_student_assignments.py \
  services/classroom-sync/tests/integration/test_plan_assignment_flow.py \
  services/classroom-sync/tests/integration/test_migrations.py
git commit -m "fix: isolate classroom assignments by plan"
```

Commit only after the verification in Steps 1-3 has succeeded or its exact terminal failure is documented. Do not merge or push.

## Plan Self-Review

- Spec coverage: Tasks 1-3 implement the chosen key, compatibility rule, regression protection, and migration; Task 4 applies it locally and validates the teacher-visible AI outcome.
- Placeholder scan: no deferred implementation markers or unspecified interfaces remain.
- Type consistency: the repository accepts `plan_id: str`; the service passes `PlanVersion.plan_id`; the database unique key and migration use the same five columns.

## Local Execution Record — 2026-08-16

- The local database upgraded to `0003_versioned_assignments`; no classroom volume was reset.
- A previously collecting monitor session was resumed without creating a second plan, assignment, session, or evidence chunk. Its brief submission succeeded and became teacher-readable.
- The AI worker reached its configured retry limit with `ai_brief_analysis_upstream_unavailable`; teacher monitoring reported `unavailable` and the teacher brief contained no AI analysis. No follow-up submission was issued.
- The worker intentionally stores only a safe failure code, so the provider's raw response, credentials, and classroom evidence remain unavailable to logs and this record.
