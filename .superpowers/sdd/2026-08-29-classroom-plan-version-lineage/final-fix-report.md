# Final backend review fix report

## Scope and review verification

Branch: `codex/classroom-version-chain-fix-20260829`

The two Important findings were checked against the implementation before changes:

1. `PlanService.create_draft` and `AssignmentService.sync_assignments` both read the
   scope binding without a shared lock. An absent binding therefore had no row to
   lock, and either transaction could make a lineage decision from stale state.
2. `PlanDraft.plan_id` was a non-null string without an ORM or migration foreign key.
   Its Python default copied `draft.id`, which allowed direct writes to create a draft
   whose `PlanSeries` did not exist.

Both findings were confirmed and fixed within `services/classroom-sync`. No frontend,
deployment, source worktree, external system, or real database was changed.

## Implementation

### Shared scope transaction serialization

- Added `ClassroomRepository.lock_plan_scope(space_id, parent_algorithm_id)`.
- PostgreSQL uses `pg_advisory_xact_lock(bigint)` with a deterministic signed 64-bit
  key derived from a versioned, length-delimited SHA-256 encoding of the two scope
  identifiers. The lock is transaction-scoped and therefore releases on commit or
  rollback.
- SQLite uses `BEGIN IMMEDIATE`. This is intentionally coarser than the PostgreSQL
  scope lock, but it gives tests and SQLite deployments deterministic write
  serialization even when the binding row is absent.
- Trusted `PlanService.create_draft` and every `AssignmentService.sync_assignments`
  transaction acquire the shared scope lock before any binding read.
- Both paths then call `get_binding(..., for_update=True)`, so an existing PostgreSQL
  binding row is also locked.

The focused SQLite tests prove exclusion both with and without a binding row, release
after transaction completion, and observe that both business paths execute the same
lock protocol.

### Restrictive PlanDraft lineage foreign key

- `PlanDraft.plan_id` now has ORM metadata
  `ForeignKey("plan_series.id", ondelete="RESTRICT")`.
- Alembic `0009_plan_series` creates the named restrictive foreign key only after the
  historical `plan_id` backfill and non-null conversion; downgrade drops it before
  dropping the column.
- Removed the `PlanDraft` default shim. `PlanService.create_draft`, the supported
  writer, now assigns its generated draft ID explicitly as the provisional lineage ID
  and replaces it with the locked existing series ID when appropriate.
- Replaced the legacy direct-insert-default test with an invariant test that checks ORM
  metadata and proves a direct orphan draft insert fails with SQLite foreign keys on.
- The migration test now checks the migrated FK and proves an orphan `plan_id` insert
  fails after `0009`.
- The legacy migration fixture has distinct immutable version values and directly
  asserts preservation of `content_hash`, full `profile`, `published_at`, `space_id`,
  `parent_algorithm_id`, and `teacher_id`.

## TDD evidence

### Finding 1 RED

Command:

```text
UV_CACHE_DIR=/private/tmp/classroom-sync-uv-cache uv run --frozen --extra dev pytest tests/integration/test_plan_assignment_flow.py -q -k 'plan_scope_lock or same_scope_lock_protocol'
```

Expected failure after correcting test setup, before production changes:

```text
FFF [100%]
AttributeError: 'ClassroomRepository' object has no attribute 'lock_plan_scope'
AssertionError: assert [] == ['BEGIN IMMEDIATE', 'BEGIN IMMEDIATE']
3 failed, 20 deselected in 0.27s
```

This was the expected RED: no shared lock API existed and neither service path issued
the SQLite serialization statement.

### Finding 1 GREEN

Same command after the minimal repository and service changes:

```text
... [100%]
3 passed, 20 deselected in 0.27s
```

### Finding 2 RED

Command:

```text
UV_CACHE_DIR=/private/tmp/classroom-sync-uv-cache uv run --frozen --extra dev pytest tests/integration/test_migrations.py -q -k 'plan_draft_plan_id_requires or plan_series_migration_backfills'
```

Expected failure before model/migration changes:

```text
FF [100%]
AssertionError: assert set() == {'plan_series.id'}
AssertionError: assert False
2 failed, 7 deselected in 0.25s
```

The first assertion proved ORM metadata had no lineage FK; the second proved Alembic
`0009` had not created the restrictive FK. The new historical immutable-field
assertions had already passed up to that missing-FK assertion.

### Finding 2 GREEN

Same command after the minimal model, writer, and migration changes:

```text
.. [100%]
2 passed, 7 deselected, 4 warnings in 0.22s
```

The warnings are Python 3.12's deprecation warning for sqlite3's default datetime
adapter in the historical migration fixture; no functional warning or test failure was
reported.

## Fresh verification evidence

Focused regression files:

```text
pytest tests/integration/test_plan_assignment_flow.py -q
23 passed in 0.44s

pytest tests/integration/test_migrations.py -q
9 passed, 4 warnings in 0.57s
```

Full quality gates, using the locked environment through `uv run --frozen`:

```text
pytest -q
312 passed, 4 warnings in 2.61s

ruff check src tests migrations
All checks passed!

mypy src
Success: no issues found in 42 source files
```

Isolated migration round trip:

```text
alembic upgrade 0008_plan_authoring_sessions
alembic upgrade head
alembic downgrade 0008_plan_authoring_sessions
alembic upgrade head
```

All four commands exited 0 against
`/private/tmp/classroom-sync-final-fix-20260829.ETNcHE/migration.sqlite3`.

Whitespace gates before writing this report:

```text
git diff --check
git diff --check 3037d06676ad9fcb891c28a0dd26d1ea83570f1a
```

Both exited 0 with no output. They are rerun after the report and before commit.

## Files changed

- `services/classroom-sync/src/classroom_sync/repositories.py`
- `services/classroom-sync/src/classroom_sync/services/plans.py`
- `services/classroom-sync/src/classroom_sync/services/assignments.py`
- `services/classroom-sync/src/classroom_sync/models.py`
- `services/classroom-sync/migrations/versions/0009_plan_series.py`
- `services/classroom-sync/tests/integration/test_plan_assignment_flow.py`
- `services/classroom-sync/tests/integration/test_migrations.py`
- `.superpowers/sdd/2026-08-29-classroom-plan-version-lineage/final-fix-report.md`

## Self-review and concerns

- The shared lock is acquired before either path reads the binding, so it covers the
  absent-row race as well as updates to an existing binding.
- Advisory lock input is versioned and length-delimited, avoiding ambiguous scope
  concatenation. SHA-256 truncation has the usual negligible 64-bit advisory-key
  collision risk; a collision can only add serialization, not corrupt identity.
- SQLite serializes all writers rather than only equal scopes. This is safe and
  deterministic but reduces SQLite write concurrency; production PostgreSQL retains
  per-scope concurrency.
- No live PostgreSQL concurrency test was added because the repository has no stable
  PostgreSQL test service. The PostgreSQL SQL path is type/lint checked; the real
  transaction-exclusion tests use SQLite without mocks.
- Downgrade retains the existing duplicate-profile safety refusal and now removes the
  named FK before dropping `plan_id`.
- No push, merge, deploy, external write, or real migration was performed.
