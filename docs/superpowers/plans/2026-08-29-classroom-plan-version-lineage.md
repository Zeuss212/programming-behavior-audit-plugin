# Classroom Plan Version Lineage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make successive trusted classroom authoring sessions publish immutable v1/v2/v3 versions on one stable plan lineage without changing legacy new-plan semantics.

**Architecture:** Persist one `PlanSeries` row per actual plan lineage and lock it while allocating versions. Drafts store the stable lineage identity, while published versions store the exact source draft and revision for idempotency. Experiment bindings remain the current synchronized projection, and existing assignment state rules remain unchanged.

**Tech Stack:** Python 3.12, SQLAlchemy 2, Alembic, FastAPI domain services, SQLite/PostgreSQL-compatible migrations, pytest, Ruff, mypy.

## Global Constraints

- Start from `3037d06676ad9fcb891c28a0dd26d1ea83570f1a` in the isolated branch `codex/classroom-version-chain-fix-20260829`.
- Do not modify or clean `/Users/sxh/编程行为监控分析插件_交付版_20260727/.worktrees/classroom-ai-integration-ready`.
- Preserve all existing HTTP response fields and historical `PlanVersion.content_hash` values.
- Keep legacy drafts without an authoring session as fresh plan lineages beginning at version 1.
- Keep accepted/started/completed assignments on their original version; only pending assignments may move.
- Every production change follows RED → GREEN with the named targeted test.

---

### Task 1: Persist plan series and source-draft identity

**Files:**
- Create: `services/classroom-sync/migrations/versions/0009_plan_series.py`
- Modify: `services/classroom-sync/src/classroom_sync/models.py`
- Modify: `services/classroom-sync/tests/integration/test_migrations.py`

**Interfaces:**
- Produces: `PlanSeries(id, profile_id, space_id, parent_algorithm_id, latest_version)`.
- Produces: non-null `PlanDraft.plan_id` and `PlanVersion.source_draft_id`.
- Produces: unique key `uq_plan_versions_source_draft_revision`.

- [ ] **Step 1: Write the failing 0008-to-0009 migration test**

Add a test that upgrades to `0008_plan_authoring_sessions`, inserts a legacy draft plus two versions,
upgrades to head, and asserts these literal results:

```python
assert connection.execute(
    text("SELECT plan_id FROM plan_drafts WHERE id = 'legacy-draft'")
).scalar_one() == "legacy-draft"
assert connection.execute(
    text("SELECT source_draft_id FROM plan_versions WHERE id = 'legacy-version-2'")
).scalar_one() == "legacy-draft"
assert connection.execute(
    text("SELECT latest_version FROM plan_series WHERE id = 'legacy-draft'")
).scalar_one() == 2
```

Insert a second draft with `profile_id='legacy-profile'` and the same `plan_id`; it must succeed.
Insert a duplicate `(source_draft_id, source_draft_revision)` version; it must raise
`sqlalchemy.exc.IntegrityError`.

- [ ] **Step 2: Run the migration test and verify RED**

Run: `uv run --frozen --extra dev pytest tests/integration/test_migrations.py -q`

Expected: FAIL because revision `0009_plan_series` and the new columns/table do not exist.

- [ ] **Step 3: Implement the model and reversible empty-database migration**

Add this model shape and matching columns/constraints:

```python
class PlanSeries(Base):
    __tablename__ = "plan_series"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    space_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_algorithm_id: Mapped[str] = mapped_column(String(128), nullable=False)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False)
```

Use SQLAlchemy Core rows inside migration `0009` to build one series per historical `plan_id`.
Backfill drafts before making `plan_id` non-null; backfill versions before making
`source_draft_id` non-null. Remove the draft `profile_id` unique constraint while preserving all
other columns and constraints. The downgrade must query duplicate draft profile IDs first and raise
`RuntimeError("plan_series_downgrade_requires_backup")` when uniqueness cannot be restored.

- [ ] **Step 4: Run migration tests and verify GREEN**

Run: `uv run --frozen --extra dev pytest tests/integration/test_migrations.py -q`

Expected: PASS, including the existing `upgrade head -> downgrade base -> upgrade head` test.

- [ ] **Step 5: Commit the persistence slice**

```bash
git add services/classroom-sync/migrations/versions/0009_plan_series.py services/classroom-sync/src/classroom_sync/models.py services/classroom-sync/tests/integration/test_migrations.py
git commit -m "feat: persist classroom plan lineages"
```

### Task 2: Allocate stable versions and exact publish retries

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/repositories.py`
- Modify: `services/classroom-sync/src/classroom_sync/services/plans.py`
- Modify: `services/classroom-sync/tests/integration/test_plan_assignment_flow.py`

**Interfaces:**
- Produces: `ClassroomRepository.get_plan_series(plan_id, for_update=False)`.
- Produces: `ClassroomRepository.get_plan_version_for_source(draft_id, revision)`.
- Consumes: `PlanSeries` and the source-draft unique key from Task 1.

- [ ] **Step 1: Write a failing consecutive-authoring integration test**

Create two v3 authoring sessions for the same material scope. Publish and synchronize the first
draft, then create and publish the second. Assert hand-derived identity and version behavior:

```python
assert first.plan_id == second.plan_id
assert first.profile_id == second.profile_id
assert (first.version, second.version) == (1, 2)
assert second.source_draft_id == second_draft.id
```

Call `publish_draft(second_draft.id, ...)` again and assert that the returned `id` equals
`second.id` and the database still contains exactly two versions for the plan.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run --frozen --extra dev pytest tests/integration/test_plan_assignment_flow.py -q -k 'successive_authoring_sessions'`

Expected: FAIL because the second draft receives a new `plan_id` and publishes version 1.

- [ ] **Step 3: Implement repository locking and lineage-aware draft creation**

Add repository reads using `select(...).with_for_update()` when requested. In
`PlanService.create_draft`, generate the draft ID first, validate and lock the authoring session,
then apply this decision table:

```text
authoring session + current binding -> reuse binding.plan_id series
authoring session + no binding      -> create series with id=draft.id
no authoring session                -> create series with id=draft.id
```

Always set `draft.profile_id = series.profile_id` and `draft.plan_id = series.id`. A binding that
references no series raises `ConflictError("plan_series_not_found")`.

- [ ] **Step 4: Implement atomic publish allocation and exact idempotency**

Lock the draft and series in one transaction. Query the exact source key before allocating. For a
new publish use:

```python
version = series.latest_version + 1
plan_version = PlanVersion(
    plan_id=draft.plan_id,
    profile_id=draft.profile_id,
    version=version,
    source_draft_id=draft.id,
    source_draft_revision=draft.revision,
    # existing immutable fields remain unchanged
)
series.latest_version = version
```

Change authoring close/retry validation to compare `published_plan_id` with `draft.plan_id`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `uv run --frozen --extra dev pytest tests/integration/test_plan_assignment_flow.py -q -k 'successive_authoring_sessions or republish or new_plan or v3_publish'`

Expected: PASS; the existing legacy fresh-plan and pending-assignment tests remain green.

- [ ] **Step 6: Commit the service slice**

```bash
git add services/classroom-sync/src/classroom_sync/repositories.py services/classroom-sync/src/classroom_sync/services/plans.py services/classroom-sync/tests/integration/test_plan_assignment_flow.py
git commit -m "fix: keep classroom versions on one plan lineage"
```

### Task 3: Prove rollback and assignment-version preservation

**Files:**
- Modify: `services/classroom-sync/tests/integration/test_plan_assignment_flow.py`

**Interfaces:**
- Consumes: atomic `PlanSeries.latest_version` update from Task 2.
- Protects: existing `AssignmentService.sync_assignments` pending-versus-accepted behavior.

- [ ] **Step 1: Extend the insert-failure test and verify RED**

Before the forced `PlanVersion` insert, load the draft series and assert its version is 0. After the
expected `IntegrityError`, assert:

```python
assert series.latest_version == 0
assert authoring.status == "open"
assert authoring.published_plan_id is None
assert session.query(PlanVersion).filter_by(plan_id=draft.plan_id).count() == 0
```

The new counter assertion must fail if the implementation updates the series outside the publish
transaction.

- [ ] **Step 2: Add cross-draft assignment assertions**

In the successive-authoring test, accept student 1 on v1 and leave student 2 pending before v2
synchronization. Assert student 1 remains on v1 with the same assignment ID, while student 2 keeps
its assignment ID and moves to v2.

- [ ] **Step 3: Run the complete assignment flow file**

Run: `uv run --frozen --extra dev pytest tests/integration/test_plan_assignment_flow.py -q`

Expected: PASS with rollback, legacy new-plan, idempotency and assignment preservation covered.

- [ ] **Step 4: Commit the regression slice**

```bash
git add services/classroom-sync/tests/integration/test_plan_assignment_flow.py
git commit -m "test: lock classroom lineage rollback semantics"
```

### Task 4: Run backend quality gates and record evidence

**Files:**
- Modify only if required by an actual failing gate: files already listed in Tasks 1-3.

**Interfaces:**
- Produces: reproducible backend verification evidence.

- [ ] **Step 1: Run the full service suite**

Run: `uv run --frozen --extra dev pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Run static quality gates**

Run: `uv run --frozen --extra dev ruff check src tests migrations`

Expected: exit 0 with no diagnostics.

Run: `uv run --frozen --extra dev mypy src`

Expected: exit 0 with no diagnostics.

- [ ] **Step 3: Run migration and diff checks**

Run: `uv run --frozen --extra dev alembic upgrade head`

Expected: exit 0 on an isolated SQLite database.

Run: `git diff --check 3037d06676ad9fcb891c28a0dd26d1ea83570f1a..HEAD`

Expected: exit 0 with no output.

- [ ] **Step 4: Stop at the integration gate**

Report the branch, commits, test counts and migration result. Do not merge, deploy, alter the dirty
source worktree, or push the backend branch until the remote target is explicitly confirmed.

