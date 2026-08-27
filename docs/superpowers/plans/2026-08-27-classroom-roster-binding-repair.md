# Classroom Roster Binding Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind classroom child experiments to enrolled students through versioned metadata, preserving only strict fail-closed legacy matching and preventing every database write when roster validation fails.

**Architecture:** A small Python codec owns canonical v1 parsing/encoding and JavaScript-compatible legacy names. The FinColab gateway resolves marker `student_id` against the roster and checks owners separately. The Vue writer encodes the identical v1 contract. Backend compatibility deploys before frontend creation changes.

**Tech Stack:** Python 3.10+, FastAPI, httpx, pytest, SQLAlchemy; Vue 3.5, TypeScript 6, Vitest; UTF-8 JSON/Base64url.

## Global Constraints

- Do not create or backfill courses, experiments, workbenches, plans, assignments, or production data.
- Do not use child/workbench owner as the primary binding; it must only match the teacher or bound student exactly.
- `student_id` is authoritative. Missing ID or username snapshot mismatch is 409; no username fallback.
- Unknown/malformed/inconsistent markers never fall back to legacy.
- Any roster failure must happen before `AssignmentService.sync_assignments`; no binding, assignment, or audit write is allowed.
- BAMS FileManager and student image/Jupyter chains are out of scope. Do not deploy, merge, or push.

---

## File Map

```text
contracts/classroom/v1/fincolab-student-binding-v1.golden.json
services/classroom-sync/src/classroom_sync/auth/student_binding.py
services/classroom-sync/src/classroom_sync/auth/fincolab.py
services/classroom-sync/src/classroom_sync/config.py
services/classroom-sync/src/classroom_sync/runtime.py
services/classroom-sync/tests/unit/test_student_binding.py
services/classroom-sync/tests/integration/test_authorization.py
services/classroom-sync/tests/integration/test_classroom_routes.py
services/classroom-sync/tests/test_runtime.py
deploy/classroom/mock-fincolab/app.py
deploy/classroom/local-demo/fincolab_demo.py
deploy/classroom/docker-compose.test.yml
deploy/classroom/local-demo/docker-compose.yml
../lab-platform-frontend/src/modules/student-binding/codec.ts
../lab-platform-frontend/src/modules/student-binding/__tests__/codec.test.ts
../lab-platform-frontend/src/modules/student-binding/fincolab-student-binding-v1.golden.json
../lab-platform-frontend/src/views/admin/AdminProjectsView.vue
../lab-platform-frontend/src/views/admin/__tests__/AdminProjectsView.test.ts
```

### Task 1: Define the canonical binding contract and Python RED tests

**Files:** Create `contracts/classroom/v1/fincolab-student-binding-v1.golden.json`; create `services/classroom-sync/tests/unit/test_student_binding.py`.

**Interfaces:** `StudentBindingV1(space_id, parent_algorithm_id, student_id, student_username)`; `encode_student_binding_v1`; `parse_student_binding_description`.

- [ ] **Step 1: Write failing vector tests**

Add the canonical JSON and encoded value from the design plus vectors exercising Base64url `-`/`_`. Add rejected payloads for padding, invalid Base64url, invalid UTF-8, duplicate/extra/missing JSON fields, empty/non-string fields, unknown version, and a first line with a binding tag but invalid paired grammar. Test that `safe_legacy_key("A😀B") == "A--B"`, and only `exp-student-1-a1b2` accepts among `exp-student-1-A1B2`, `exp-student-1-abc`, and `EXP-student-1-a1b2`.

- [ ] **Step 2: Run RED**

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/unit/test_student_binding.py -q
```

Expected: import failure because `classroom_sync.auth.student_binding` does not exist.

- [ ] **Step 3: Commit test checkpoint**

```bash
git add contracts/classroom/v1/fincolab-student-binding-v1.golden.json services/classroom-sync/tests/unit/test_student_binding.py
git commit -m "test: define classroom student binding vectors"
```

### Task 2: Implement the bounded Python codec

**Files:** Create `services/classroom-sync/src/classroom_sync/auth/student_binding.py`; modify `services/classroom-sync/tests/unit/test_student_binding.py`.

**Interfaces:** `encode_student_binding_v1(binding) -> str`; `parse_student_binding_description(description) -> StudentBindingV1 | None`; `safe_legacy_key(username) -> str`; `parse_legacy_child_name(name, prefix) -> str | None`.

- [ ] **Step 1: Write minimal implementation**

Encode sorted-key JSON with `json.dumps(ensure_ascii=False, separators=(",", ":"), sort_keys=True)`, UTF-8, and `urlsafe_b64encode(...).rstrip(b"=")`. Accept only unpadded `[A-Za-z0-9_-]+`; reject duplicate JSON keys via `object_pairs_hook`, require exactly four nonempty strings of at most 256 Unicode code points, then re-encode and compare exact bytes. Limit description to 4096 and marker payload to 2048. Return `None` only when no binding tag family appears; malformed tags raise `RosterConflictError("student_binding_marker_malformed")`, unsupported versions raise `RosterConflictError("student_binding_marker_unknown_version")`. Implement safe keys by iterating UTF-16LE two-byte units, retaining only ASCII `[A-Za-z0-9_-]`; compile the literal escaped prefix plus `[0-9a-z]{4}` regex.

- [ ] **Step 2: Run GREEN**

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/unit/test_student_binding.py -q
uv run --extra dev ruff check src/classroom_sync/auth/student_binding.py tests/unit/test_student_binding.py
uv run --extra dev mypy src/classroom_sync/auth/student_binding.py
```

Expected: all commands exit 0 and no invalid marker returns `None`.

- [ ] **Step 3: Commit**

```bash
git add services/classroom-sync/src/classroom_sync/auth/student_binding.py services/classroom-sync/tests/unit/test_student_binding.py
git commit -m "feat: add strict classroom student binding codec"
```

### Task 3: Configure the strict legacy prefix

**Files:** Modify `services/classroom-sync/src/classroom_sync/config.py`, `services/classroom-sync/src/classroom_sync/runtime.py`, `services/classroom-sync/tests/test_runtime.py`, `deploy/classroom/docker-compose.test.yml`, and `deploy/classroom/local-demo/docker-compose.yml`.

**Interfaces:** `Settings.fincolab_student_project_prefix: str`; gateway constructor receives `student_project_name_prefix`.

- [ ] **Step 1: Write failing settings tests**

Assert absent `CLASSROOM_FINCOLAB_STUDENT_PROJECT_PREFIX` defaults to `exp`, explicit `lesson_x-1` works, and empty, 65-char, whitespace, and non-ASCII values raise a message naming the environment key.

- [ ] **Step 2: Run RED**

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/test_runtime.py -q
```

Expected: field and injection assertions fail.

- [ ] **Step 3: Implement and verify GREEN**

Read the raw environment mapping so an absent key defaults to `exp` but an explicitly blank key is rejected; then accept only a 1--64 character ASCII `[A-Za-z0-9_-]` literal and inject it only when runtime builds the gateway. Set explicit non-secret `CLASSROOM_FINCOLAB_STUDENT_PROJECT_PREFIX: exp` in both compose anchors.

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/test_runtime.py -q
uv run --extra dev ruff check src tests/test_runtime.py
uv run --extra dev mypy src
git add src/classroom_sync/config.py src/classroom_sync/runtime.py tests/test_runtime.py ../../deploy/classroom/docker-compose.test.yml ../../deploy/classroom/local-demo/docker-compose.yml
git commit -m "feat: configure classroom legacy child prefix"
```

Expected: all commands exit 0.

### Task 4: Resolve v1 and legacy children without owner-as-student inference

**Files:** Modify `services/classroom-sync/src/classroom_sync/auth/fincolab.py` and `services/classroom-sync/tests/integration/test_authorization.py`.

**Interfaces:** `list_student_children(...) -> tuple[StudentChildExperiment, ...]`; 409 `RosterConflictError` for data conflicts and retryable 503 `UpstreamContractError` for unavailable required upstream data.

- [ ] **Step 1: Write failing gateway tests**

Replace the current teacher-owned-child failure fixture with a v1 child owned by `teacher-a` and assert `StudentChildExperiment("student-1", "student-a", "child-1", "workbench-1")`. Add focused cases: absent marker student ID; username snapshot mismatch; space/parent mismatch; malformed and unknown marker do not fallback; valid legacy; `student.a`/`student-a` safe-key collision; teacher/student owner allowed; other owner 409; list owner missing then detail owner allowed; owner still missing 503; missing child/workbench 503; and duplicate student/child/workbench 409.

- [ ] **Step 2: Run RED**

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/integration/test_authorization.py -q
```

Expected: teacher-owned v1 still produces `child_owner_not_student_member` and new resolution tests fail.

- [ ] **Step 3: Implement all-or-nothing roster construction**

Build exact student ID/username/safe-key maps before candidates. Use v1 `student_id` only; use legacy only if codec returns `None` and old parent marker plus exact name match. Validate list child ID/workbench; fetch detail at most once only for absent owner. Permit owner only if equal to `principal.username` or bound roster username. Maintain seen student, child, and workbench sets and raise before returning any roster. Never select a student from owner.

- [ ] **Step 4: Run GREEN and commit**

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/integration/test_authorization.py tests/unit/test_student_binding.py -q
uv run --extra dev ruff check src tests/integration/test_authorization.py tests/unit/test_student_binding.py
uv run --extra dev mypy src
git add src/classroom_sync/auth/fincolab.py tests/integration/test_authorization.py
git commit -m "fix: bind classroom roster children explicitly"
```

Expected: all tests pass; 409 is non-retryable and required upstream omissions are retryable 503.

### Task 5: Prove route errors occur before DB writes and upgrade fixtures

**Files:** Modify `services/classroom-sync/tests/integration/test_classroom_routes.py`, `deploy/classroom/mock-fincolab/app.py`, `deploy/classroom/local-demo/fincolab_demo.py`, `scripts/__tests__/test_local_classroom_demo_facade.py`, and `scripts/__tests__/test_classroom_mock_fincolab.py`.

**Interfaces:** assignment sync preserves the current error envelope and does not invoke the assignment service when gateway discovery raises.

- [ ] **Step 1: Write failing no-write integration tests**

Use a fake gateway that raises `RosterConflictError("student_binding_username_mismatch")`; POST sync with fixed `X-Request-ID`, assert 409/non-retryable/request ID and zero `ExperimentPlanBinding`, `StudentAssignment`, and `AuditEvent` rows. Make fake assignment sync fail if called. Add the same zero-write assertions for `UpstreamContractError("child_workbench_unverified")` as 503/retryable.

- [ ] **Step 2: Run RED**

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/integration/test_classroom_routes.py -q
```

Expected: fixture/full-marker assertions fail before mock metadata changes.

- [ ] **Step 3: Update fixtures, then run GREEN and commit**

Make mock/demo child first lines v1 canonical marker descriptions matching roster ID/username while retaining teacher owner. Assert entire paired first line in facade tests; add no migration/backfill code.

```bash
cd services/classroom-sync
uv run --extra dev pytest tests/integration/test_classroom_routes.py ../../scripts/__tests__/test_local_classroom_demo_facade.py ../../scripts/__tests__/test_classroom_mock_fincolab.py -q
git add tests/integration/test_classroom_routes.py ../../deploy/classroom/mock-fincolab/app.py ../../deploy/classroom/local-demo/fincolab_demo.py ../../scripts/__tests__/test_local_classroom_demo_facade.py ../../scripts/__tests__/test_classroom_mock_fincolab.py
git commit -m "test: guard classroom sync before roster writes"
```

Expected: any roster exception occurs before a database transaction write.

### Task 6: Write v1 child metadata in the frontend worktree

**Files:** Create `../lab-platform-frontend/src/modules/student-binding/codec.ts`, `../lab-platform-frontend/src/modules/student-binding/fincolab-student-binding-v1.golden.json`, `../lab-platform-frontend/src/modules/student-binding/__tests__/codec.test.ts`; modify `../lab-platform-frontend/src/views/admin/AdminProjectsView.vue` and `../lab-platform-frontend/src/views/admin/__tests__/AdminProjectsView.test.ts`.

**Interfaces:** `encodeStudentBindingV1(binding: StudentBindingV1): string`; child description builder accepts parent ID plus `{ space_id, parent_algorithm_id, student_id, student_username }`.

- [ ] **Step 1: Write TypeScript RED tests**

Copy canonical vectors byte-for-byte and assert exact first-line output. Require `TextEncoder`, sorted insertion order, UTF-8 byte Base64 conversion, URL-safe substitutions, and no `=`; prohibit raw-Unicode `btoa`. In `AdminProjectsView` tests give each student an `id`, trigger creation, assert the child payload has paired marker/human lines and the parent payload has no binding marker. Include a username whose original value differs from its trimmed value and assert the identity marker preserves the exact roster value.

- [ ] **Step 2: Run RED**

```bash
cd ../lab-platform-frontend
npm test -- --run src/modules/student-binding/__tests__/codec.test.ts src/views/admin/__tests__/AdminProjectsView.test.ts
```

Expected: codec import and child-description assertions fail.

- [ ] **Step 3: Implement, verify, and commit in that worktree**

At the existing per-student loop, build the marker with course ID, parent project ID, student ID, and the exact unmodified roster `student.username`; reject an empty ID or an all-whitespace username locally. Existing trimmed/display/name behavior may remain, but it must not alter the identity snapshot. Keep `buildStudentExperimentName()` and parent creation otherwise untouched.

```bash
npm test -- --run src/modules/student-binding/__tests__/codec.test.ts src/views/admin/__tests__/AdminProjectsView.test.ts
npm run type-check
npx --no-install oxlint .
npx --no-install eslint .
git add src/modules/student-binding src/views/admin/AdminProjectsView.vue src/views/admin/__tests__/AdminProjectsView.test.ts
git commit -m "feat: mark classroom child student bindings"
```

Expected: frontend and Python golden strings agree exactly.

### Task 7: Document and verify the backend-first release gate

**Files:** Modify `docs/runbooks/bams-classroom-api-ingress.md`, `README.md`; create `scripts/__tests__/test_classroom_roster_binding_docs.py`.

**Interfaces:** operator documentation states backend-first rollout, frontend rollback safety, 409/503 action, and non-goals.

- [ ] **Step 1: Add documentation RED test**

Test that runbook/design/README contain `backend first`, `CLASSROOM_FINCOLAB_STUDENT_PROJECT_PREFIX`, `no production backfill`, `do not roll back the backend`, `BAMS FileManager`, and `student image`.

- [ ] **Step 2: Run RED**

```bash
uv run --no-project pytest scripts/__tests__/test_classroom_roster_binding_docs.py -q
```

Expected: required rollout wording is initially absent.

- [ ] **Step 3: Document, fully verify, and commit**

Document parser-compatible backend, test-course read-only check, then frontend writer. State frontend can revert to legacy only while compatibility backend remains; no older parser after v1 exists. Do not include a deploy command.

```bash
cd services/classroom-sync
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
uv run --extra dev mypy src
cd ../..
uv run --no-project pytest scripts/__tests__/test_classroom_roster_binding_docs.py -q
git diff --check
git add docs/runbooks/bams-classroom-api-ingress.md README.md scripts/__tests__/test_classroom_roster_binding_docs.py
git commit -m "docs: define classroom roster binding rollout"
```

Expected: all checks exit 0; stop without Docker, deployment, merge, or push.

## Plan Self-Review

- Spec coverage: Tasks 1--2 cover bounded codec and cross-language vectors; 3 prefix configuration; 4 explicit/legacy gateway resolution; 5 zero-write API proof; 6 future frontend marker writing; 7 deployment ordering and non-goals.
- Type consistency: codecs use `space_id`, `parent_algorithm_id`, `student_id`, `student_username`; gateway output remains `StudentChildExperiment(student_id, student_username, child_algorithm_id, workbench_id)`.
- Scope: no task creates data, backfills production, changes FileManager/student image, deploys, merges, or pushes.
