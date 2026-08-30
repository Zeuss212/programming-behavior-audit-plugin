# Local Demo Short Login Credentials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local demo accept teacher `1 / 1` and student `2 / 2` while preserving the internal `teacher001` and `student001` identities.

**Architecture:** Separate disposable login aliases from stable demo principals in the local façade user table. Keep tokens, returned usernames, ownership records, classroom data, and the `student002` negative fixture unchanged.

**Tech Stack:** Python 3.12 standard-library HTTP façade, pytest, Docker Compose local demo.

## Global Constraints

- This change applies only to `deploy/classroom/local-demo` and its local scripts/tests/documentation.
- `1 / 1` must resolve to internal username `teacher001`; `2 / 2` must resolve to `student001`.
- The two old credential pairs must return HTTP `401`.
- Production authentication, tokens, stored classroom identifiers, and `student002` remain unchanged.
- Stop after local rebuild and verification; do not push or deploy.

---

### Task 1: Replace local demo login aliases without changing principals

**Files:**
- Modify: `scripts/__tests__/test_local_classroom_demo_facade.py`
- Modify: `deploy/classroom/local-demo/fincolab_demo.py`
- Modify: `scripts/local_classroom_demo_smoke.py`
- Modify: `scripts/__tests__/test_local_classroom_demo_smoke.py`
- Modify: `scripts/cpp_classroom_phase1_smoke.py`
- Modify: `scripts/__tests__/test_cpp_classroom_phase1_smoke.py`
- Modify: `deploy/classroom/local-demo/README.md`

**Interfaces:**
- Consumes: `DemoFincolabHandler._login()` and existing fixed tokens `teacher-token` / `student001-token`.
- Produces: login aliases `1 / 1` and `2 / 2`, returning existing internal usernames and tokens.

- [ ] **Step 1: Write failing façade contract tests**

Change the teacher login request to `{"username": "1", "password": "1"}` and assert the response username is `teacher001`. Add a student request with `{"username": "2", "password": "2"}` and assert username `student001`, role `student`, and token `student001-token`. Parametrize the rejection test with both old credential pairs and require `401` plus `{"detail": "demo_login_rejected"}`.

- [ ] **Step 2: Run the focused tests and confirm red**

Run:

```bash
python -m pytest scripts/__tests__/test_local_classroom_demo_facade.py -q
```

Expected: the new `1 / 1` and `2 / 2` login assertions fail with `401` before implementation.

- [ ] **Step 3: Implement alias-keyed local users**

Change only the first two keys and passwords in `USERS`:

```python
USERS = {
    "1": DemoUser("teacher001", "teacher001", "1", "teacher-token", ...),
    "2": DemoUser("student001", "student001", "2", "student001-token", ...),
    "student002": DemoUser(...),
}
```

Keep `_login()` lookup and its response format unchanged so the returned `username` remains the stable principal username.

- [ ] **Step 4: Update executable smoke callers and their expectations**

Use `1 / 1` and `2 / 2` in `local_classroom_demo_smoke.py`. Use `1 / 1` in `cpp_classroom_phase1_smoke.py`. Update test expectations and token-mismatch text from `teacher001 login` to `1 login` where it reflects the submitted login alias.

- [ ] **Step 5: Update the local operator README**

Document the login credentials as teacher `1 / 1` and student `2 / 2`, while noting that their internal identities remain `teacher001` and `student001`. Leave the negative fixture row unchanged.

- [ ] **Step 6: Run focused and local façade verification**

Run:

```bash
python -m pytest scripts/__tests__/test_local_classroom_demo_facade.py scripts/__tests__/test_local_classroom_demo_smoke.py scripts/__tests__/test_cpp_classroom_phase1_smoke.py -q
python -m ruff check deploy/classroom/local-demo/fincolab_demo.py scripts/local_classroom_demo_smoke.py scripts/cpp_classroom_phase1_smoke.py scripts/__tests__/test_local_classroom_demo_facade.py scripts/__tests__/test_local_classroom_demo_smoke.py scripts/__tests__/test_cpp_classroom_phase1_smoke.py
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 7: Rebuild the local façade and verify real HTTP login behavior**

Rebuild the `fincolab` local-demo service from the current worktree, then POST the four credential pairs. Expect `200` for `1 / 1` and `2 / 2`, and `401` for both old pairs. Confirm `/health/live` remains `200`.

- [ ] **Step 8: Commit the implementation**

```bash
git add deploy/classroom/local-demo/fincolab_demo.py deploy/classroom/local-demo/README.md scripts/local_classroom_demo_smoke.py scripts/cpp_classroom_phase1_smoke.py scripts/__tests__/test_local_classroom_demo_facade.py scripts/__tests__/test_local_classroom_demo_smoke.py scripts/__tests__/test_cpp_classroom_phase1_smoke.py
git commit -m "fix: simplify local classroom demo login"
```
