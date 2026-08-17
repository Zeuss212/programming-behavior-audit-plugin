# Versioned Classroom Assignments Design

## Goal

Allow a teacher to publish a new classroom plan for an existing experiment after a student has submitted an earlier plan, without reusing the earlier student assignment or brief.

## Current Failure

`student_assignments` is unique only by experiment and student child environment. When a new plan is published for the same experiment, synchronization finds the prior assignment. If that assignment is already `submitted`, the service intentionally preserves it, so the student cannot accept the new plan and the local end-to-end flow stops before evidence collection and AI analysis.

The existing `test_republish_moves_only_unaccepted_assignments_to_the_new_plan_version` establishes a required compatibility rule: a republish of the *same* plan must retain an already accepted v1 assignment rather than replacing it with v2.

## Chosen Design

Assignments are unique per **plan ID plus student child environment**. Their database constraint and repository lookup will include `plan_id` in addition to the existing space, parent experiment, student, and child experiment fields.

- A newly created plan has a new `plan_id`, so synchronization creates a new `pending_acceptance` assignment even when the prior plan's assignment is `ready` or `submitted`.
- A republish of the same plan keeps its `plan_id`. Existing behavior remains: pending assignments update to the new version, while accepted or submitted assignments remain attached to their original version.
- Briefs and monitor sessions remain attached to their existing assignment IDs. No historic rows are edited or deleted.

## Database Migration

Add an Alembic migration after `0002_brief_analysis_jobs` that drops `uq_student_assignments_student_child` and creates a replacement unique constraint over:

`plan_id, space_id, parent_algorithm_id, student_id, child_algorithm_id`

The migration changes only a constraint. All existing assignment, session, and brief data stays in place. Its downgrade restores the former constraint only when no two retained rows differ solely by `plan_id`; after new multi-plan assignments exist, rollback requires restoring a pre-migration backup rather than deleting or merging classroom history.

## Validation

1. A failing integration regression test creates and submits a first plan, then publishes a separate second plan for the same experiment and student. Before the fix it receives the first assignment; after the fix it receives a new assignment in `pending_acceptance` and can accept it to `ready`.
2. The existing same-plan republish test continues to pass, proving v1 accepted work remains stable.
3. Migration tests run on SQLite and the local Postgres container applies the new migration at startup.
4. The local smoke submits a fresh brief. Teacher monitoring is then polled until the analysis is `ready`, `unavailable`, or a documented timeout.

## Non-goals

- No changes to teacher/student UI, GLM payload shape, or remote production data.
- No deletion or reset of the local classroom database as a workaround.
- No merge or push to `main`.
