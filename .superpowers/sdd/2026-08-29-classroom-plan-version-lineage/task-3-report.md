# Task 3 report: rollback and assignment-version preservation

## Changes

- Extended `test_v3_publish_insert_failure_rolls_back_and_keeps_session_open` to verify the draft's `PlanSeries.latest_version` is `0` before publication and remains `0` after the forced `IntegrityError`. The existing assertions also verify the authoring session remains open, no plan is published, and no `PlanVersion` row remains.
- Extended `test_successive_authoring_sessions_reuse_plan_series_and_publish_exact_retry` to synchronize two students on v1, accept student 1, leave student 2 pending, then synchronize v2. The test verifies student 1 keeps the v1 assignment and ID while student 2 keeps its ID and moves to v2.

## TDD and mutation evidence

The new assertions passed immediately because Task 2's implementation already performs the series increment within the publication transaction, and the existing assignment service already preserves accepted assignments while moving pending assignments. No artificial production mutation was introduced to manufacture a RED result.

The rollback assertions fail if `latest_version` is committed outside the failed publish transaction. The assignment assertions fail if synchronization overwrites an accepted assignment, recreates either assignment, or fails to move a pending assignment to v2.

## Test evidence

Commands run from `services/classroom-sync` (with `UV_CACHE_DIR=/private/tmp/uv-cache-classroom-task3` because the default uv cache is not writable in the sandbox):

- `uv run --frozen --extra dev pytest tests/integration/test_plan_assignment_flow.py::test_v3_publish_insert_failure_rolls_back_and_keeps_session_open -q` — `1 passed`
- `uv run --frozen --extra dev pytest tests/integration/test_plan_assignment_flow.py::test_successive_authoring_sessions_reuse_plan_series_and_publish_exact_retry -q` — `1 passed`
- `uv run --frozen --extra dev pytest tests/integration/test_plan_assignment_flow.py -q` — `20 passed`

## Commit

`d0925a6dcb377655532f4fb3a11273151a3baa3a` — `test: lock classroom lineage rollback semantics`

## Concerns

None blocking. This slice changes only the requested integration test file; SQLite integration coverage does not independently model production database row-lock behavior.
