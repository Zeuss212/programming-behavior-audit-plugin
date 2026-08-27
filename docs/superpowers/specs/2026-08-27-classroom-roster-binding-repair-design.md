# Classroom Roster Binding Repair Design

## Goal

Repair classroom assignment synchronization so teacher-created child experiments bind to the intended enrolled student without treating a child project's `username` (its workbench owner) as that binding. Any ambiguity, malformed data, unavailable required upstream field, or conflict fails closed before assignment synchronization writes the database.

## Current Failure

`FincolabIdentityGateway.list_student_children()` currently maps `project.username` to an enrolled student. `AdminProjectsView.vue` creates every child with the teacher Bearer token, so FinColab returns the teacher as child owner and the gateway raises `child_owner_not_student_member` (503). Existing children contain only `[FINCOLAB_PARENT_PROJECT_ID:<parent>]`; their names are `exp-<safe username>-<4 random>` and cannot be a durable primary identity protocol.

## Scope

The change is limited to roster discovery and the metadata written for future child projects. It creates, updates, deletes, repairs, and backfills no course, experiment, workbench, plan, assignment, or production row. Legacy data is read-only compatibility input. Real BAMS FileManager integration and the student Jupyter/image path are separate chains and explicit non-goals.

## V1 Binding Format

New child descriptions must start with exactly this combined first line, followed by the existing human-readable lines:

```text
[FINCOLAB_PARENT_PROJECT_ID:<parent>][FINCOLAB_STUDENT_BINDING_V1:<base64url-json>]
```

`base64url-json` is unpadded RFC 4648 URL-safe Base64 of UTF-8 canonical JSON. Canonical JSON is one object with exactly the four string keys below, sorted by key, no insignificant whitespace, and no escaped non-ASCII characters:

```json
{"parent_algorithm_id":"parent-1","space_id":"space-1","student_id":"student-1","student_username":"student-a"}
```

Its required golden value is:

```text
eyJwYXJlbnRfYWxnb3JpdGhtX2lkIjoicGFyZW50LTEiLCJzcGFjZV9pZCI6InNwYWNlLTEiLCJzdHVkZW50X2lkIjoic3R1ZGVudC0xIiwic3R1ZGVudF91c2VybmFtZSI6InN0dWRlbnQtYSJ9
```

The parser bounds description length at 4096, payload length at 2048, and each decoded field at 256 Unicode code points. It accepts only unpadded `[A-Za-z0-9_-]+`, valid UTF-8, a JSON object with no duplicate/extra/missing/non-string/empty fields, and requires re-canonicalizing and re-encoding to the original payload. Unknown `FINCOLAB_STUDENT_BINDING_*` versions, and any first line containing that tag family but not the exact paired grammar, are invalid markers. They must not return “no marker.” A canonical JSON/encoded/rejected vector file is checked in under `contracts/classroom/v1`; its identical frontend mirror is SHA-256 checked during cross-repository release verification.

## Resolution and Fail-closed Rules

The teacher-authorized gateway reads the complete space roster first and builds exact maps. For v1 it:

1. Requires marker space and parent values to equal the request.
2. Resolves `student_id` in the student roster. This ID is authoritative; absence is 409 and never falls back to username.
3. Requires the current roster username to exactly equal `student_username`; no trim, case fold, NFC, or legacy fallback is permitted.
4. Returns the current roster student ID/username, child algorithm ID, and workbench ID only after all checks pass.

Child owner is an ownership check, never a binding. It must exactly equal either the verified parent teacher username or the bound student username. If list output lacks owner, fetch child detail once; if still missing, fail 503. A different owner is a 409 contract conflict. Child ID and workbench ID are required list fields; their absence is 503. Track duplicate student ID, child ID, and workbench ID across candidates; any duplicate is 409. The router receives no roster on error, so it never calls `AssignmentService.sync_assignments` or starts its transaction.

## Legacy Compatibility

Legacy is available only when no binding marker family appears. The old parent marker must match the request and the name must match exactly:

```text
<configured-prefix>-<safe-key>-<4 lowercase base36 characters>
```

`CLASSROOM_FINCOLAB_STUDENT_PROJECT_PREFIX` defaults to `exp`; it is a literal 1--64 character ASCII `[A-Za-z0-9_-]` value. `safe-key` reproduces JavaScript `username.replace(/[^a-zA-Z0-9-_]/g, '-')` on UTF-16 code units: ASCII alphanumeric/hyphen/underscore units are retained and every other unit produces one hyphen. It performs no Unicode normalization or case folding. Build a safe-key-to-student map before scanning; any collision is 409. Valid legacy candidates still perform the same owner, required-field, and duplicate checks. A malformed/unknown/mismatched v1 marker can never use the legacy path.

## Error Contract

All failures use the existing classroom error envelope. Missing/invalid upstream required fields and request failures remain retryable 503 `UpstreamContractError` (`child_*_unverified`, `upstream_*`). Authoritative inconsistent data is non-retryable 409 `RosterConflictError`: `student_binding_marker_malformed`, `student_binding_marker_unknown_version`, `student_binding_*_mismatch`, `student_binding_student_not_in_roster`, `legacy_safe_key_collision`, `duplicate_student_child`, `duplicate_child_algorithm`, `duplicate_workbench`, and `child_owner_contract_conflict`.

## Deployment and Rollback

Release the backend parser compatible with both legacy and v1 first, then release the frontend writer. Verify through a dedicated test course without production mutation. A frontend rollback can continue creating legacy children while the compatibility backend remains. Never roll the backend back to a version that cannot recognize v1 after v1 children exist; roll forward to the compatibility backend instead. No automatic production backfill is permitted.

## Acceptance Criteria

1. A teacher-owned v1 child syncs to the marker's enrolled student.
2. Invalid markers, marker/roster mismatches, safe-key collisions, bad owners, duplicate identities, and missing child/workbench data fail as classified with zero assignment, binding, or audit write.
3. An unmarked exact legacy child remains readable only when its safe key is unique under JavaScript UTF-16 semantics.
4. Python and TypeScript generate the same golden markers and reject the same invalid vectors.
