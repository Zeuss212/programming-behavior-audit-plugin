# Complete Analysis Timeout Auto-Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make full behavior analysis tolerate two consecutive 60-second Provider timeouts by using a third bounded call within a 180-second default budget, while preserving audit semantics and presenting an accurate in-progress message.

**Architecture:** Keep retries inside the existing `_RecordingRetryingClient` and one persisted analysis attempt. Derive the maximum Provider calls from the configured whole-analysis budget, skip additional sleep only after a real Provider timeout, retain short backoff for fast retryable failures, and leave the public job API unchanged. Rebuild the same 0.2.1 wheel and delivery bundle after backend, frontend, documentation, and configuration are synchronized.

**Tech Stack:** Python 3.11, pytest, TypeScript, JupyterLab 4, Jest/jsdom, Hatch/uv wheel build, POSIX shell delivery scripts, Git.

## Global Constraints

- `JUPYTERLAB_BEHAVIOR_AUDIT_ANALYSIS_TIMEOUT_SEC` accepts only closed integer values from `60` through `180`; invalid values use the new default `180`.
- One Provider call remains bounded by `PROVIDER_CALL_TIMEOUT_SEC = 60`.
- The default `180`-second budget allows at most three calls; custom `60`/`120`/`180` budgets allow at most `1`/`2`/`3` calls.
- Retry only `provider_timeout`, `provider_network_error`, HTTP `429`, and HTTP `5xx`.
- Do not sleep after `provider_timeout`; retain a `2.0`-second wait after other retryable errors when the shared deadline permits it.
- Keep all automatic Provider calls inside one persisted job attempt; do not add API fields or job states.
- Do not change assessment-assist token budgets, JSON mode, thinking mode, prompts, model, or Provider configuration.
- Use fake Providers and fake clocks in tests; do not make another real Provider call.
- Do not alter, stop, or automate the user's active preview sessions on ports `18997` and `18998`.
- Do not build Docker images, deploy, push Git, or modify BLUEDOT.

## File Map

- `myextension/llm_transport.py`: validated whole-analysis timeout default and bounds.
- `myextension/analysis_worker.py`: shared deadline and retry loop around Provider calls.
- `myextension/tests/test_ai_config_path.py`: environment timeout parsing and fallback behavior.
- `myextension/tests/test_analysis_job_store.py`: call counts, wait policy, deadline sharing, audit state, and terminal errors.
- `src/ui/behaviorAnalysisSidebar.ts`: active-job state and bounded auto-retry explanation.
- `src/__tests__/behaviorAnalysisSidebar.spec.ts`: queued/running copy and terminal retry regression.
- `myextension/tests/test_labextension_artifact.py`: stale frontend wheel guard.
- Root README, two delivery README/config/Docker sets, and the complete bundle manifest: synchronized runtime contract.
- `docs/2026-08-06-assessment-assist-latency-reliability-verification.md`: consolidated evidence.
- `myextension-0.2.1-BLUEDOT-完整交付包-20260806-analysis-retry-fix.zip{,.sha256}`: new final archive.

---

### Task 1: Bound Full Analysis to Three Deadline-Aware Provider Calls

**Files:**
- Modify: `myextension/tests/test_ai_config_path.py:112-145`
- Modify: `myextension/tests/test_analysis_job_store.py:923-1045`
- Modify: `myextension/llm_transport.py:25-101`
- Modify: `myextension/analysis_worker.py:155-222`

**Interfaces:**
- Consumes: `analysis_timeout_sec() -> int`, `PROVIDER_CALL_TIMEOUT_SEC = 60`, `LlmTransportError.error_code`, and injected `clock`/`wait` seams.
- Produces: `DEFAULT_ANALYSIS_TIMEOUT_SEC = 180` and at most `ceil(total_timeout_sec / 60)` calls under one shared deadline.

- [ ] **Step 1: Update the timeout parser test to require the new default**

Use this exact expected table in `test_analysis_timeout_is_bounded`:

```python
[
    (None, 180),
    ("60", 60),
    ("120", 120),
    ("180", 180),
    ("59", 180),
    ("181", 180),
    ("120.0", 180),
    ("invalid", 180),
    ("", 180),
]
```

- [ ] **Step 2: Add the failing two-timeout/third-success worker test**

```python
def test_worker_recovers_after_two_provider_timeouts_in_one_attempt(tmp_path):
    session_store, job_store, job = create_worker_job(tmp_path)
    clock = FakeMonotonic()
    timeouts: list[int] = []
    waits: list[float] = []

    def provider(request, *, timeout_sec):
        timeouts.append(timeout_sec)
        if len(timeouts) <= 2:
            clock.advance(float(timeout_sec))
            raise LlmTransportError("provider_timeout")
        clock.advance(57.0)
        return provider_response(str(job["session_id"]))

    worker = AnalysisWorker(
        tmp_path,
        job_store=job_store,
        session_store=session_store,
        provider_client=provider,
        wait=waits.append,
        clock=clock,
        synchronous=True,
    )
    worker.enqueue(str(job["job_id"]))
    updated = job_store.get(str(job["job_id"]))
    assert updated["status"] == "ready"
    assert len(updated["attempt_ids"]) == 1
    assert timeouts == [60, 60, 60]
    assert waits == []
    worker.shutdown()
```

- [ ] **Step 3: Add failing call-ceiling and wait-policy cases**

Parameterize `("60", 1)`, `("120", 2)`, and `("180", 3)`. Set the timeout environment variable, advance `FakeMonotonic` by the received timeout, always raise `provider_timeout`, then assert the exact call count and final `partial`/`ai_analysis_timeout` state.

Update `test_worker_retries_transient_provider_calls_only` to expect three immediate failures at the default budget. Expected waits must be:

```python
{
    "provider_timeout": [],
    "provider_network_error": [2.0, 2.0],
    "provider_http_error_429": [2.0, 2.0],
    "provider_http_error_503": [2.0, 2.0],
}
```

- [ ] **Step 4: Run focused tests and capture RED**

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_ai_config_path.py \
  myextension/tests/test_analysis_job_store.py \
  -k 'analysis_timeout or provider_timeout or transient_provider or two_provider_timeouts'
```

Expected: the old default returns `120`, only two calls occur, and timeout retries still call `wait(2.0)`.

- [ ] **Step 5: Implement the minimal retry boundary**

In `llm_transport.py` set:

```python
DEFAULT_ANALYSIS_TIMEOUT_SEC = 180
```

In `_RecordingRetryingClient.__init__` add:

```python
self._max_calls = max(
    1,
    math.ceil(total_timeout_sec / PROVIDER_CALL_TIMEOUT_SEC),
)
```

Iterate over `range(self._max_calls)`. In the exception block, stop when the current call is the last or the error is non-retryable; raise `analysis_deadline_exceeded` when no time remains; `continue` immediately for `provider_timeout`; otherwise require more than 2 seconds remaining and call `self._wait(2.0)`. Do not change payloads, prompts, output budgets, or persistence.

- [ ] **Step 6: Run focused and adjacent backend tests for GREEN**

```bash
.venv/bin/python -m pytest -q \
  myextension/tests/test_ai_config_path.py \
  myextension/tests/test_analysis_job_store.py \
  myextension/tests/test_dimension_analyzer.py
```

Expected: all selected tests pass and the third-success test retains one persisted attempt.

- [ ] **Step 7: Commit the backend loop**

```bash
git add myextension/llm_transport.py myextension/analysis_worker.py \
  myextension/tests/test_ai_config_path.py \
  myextension/tests/test_analysis_job_store.py
git commit -m "fix: retry full analysis within 180 seconds"
```

---

### Task 2: Explain Bounded Automatic Retry While the Job Is Active

**Files:**
- Modify: `src/__tests__/behaviorAnalysisSidebar.spec.ts:1360-1435`
- Modify: `src/ui/behaviorAnalysisSidebar.ts:1318-1355`
- Modify: `myextension/tests/test_labextension_artifact.py:20-45`

**Interfaces:**
- Consumes: unchanged public `IAnalysisJob.status` values `queued` and `running`.
- Produces: exact visible copy `AI 正在分析；响应较慢时会自动重试，最长约 180 秒。` without an API change.

- [ ] **Step 1: Add failing queued/running message tests**

```typescript
it.each(['queued', 'running'] as const)(
  'explains bounded automatic retry while analysis is %s',
  async status => {
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => ({ ...job, status }));
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { job: IAnalysisJob }).job = { ...job, status };
    await sidebar.refreshAnalysis();
    expect(sidebar.node.textContent).toContain(
      'AI 正在分析；响应较慢时会自动重试，最长约 180 秒。'
    );
    sidebar.dispose();
  }
);
```

Add the same Chinese marker to `REQUIRED_MARKERS` in `test_labextension_artifact.py`.

- [ ] **Step 2: Run focused tests and capture RED**

```bash
.venv/bin/jlpm test src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand
.venv/bin/python -m pytest -q myextension/tests/test_labextension_artifact.py
```

Expected: Jest cannot find the message and the current prebuilt/delivery artifact lacks the marker.

- [ ] **Step 3: Render the active-job notice**

In `progressSection()`, create the paragraph only for `queued` or `running`:

```typescript
const activeAnalysisNotice =
  this.job?.status === 'queued' || this.job?.status === 'running'
    ? node('p', 'jp-BehaviorAudit-notice')
    : null;
if (activeAnalysisNotice) {
  activeAnalysisNotice.textContent =
    'AI 正在分析；响应较慢时会自动重试，最长约 180 秒。';
}
section.append(text);
if (activeAnalysisNotice) section.appendChild(activeAnalysisNotice);
section.appendChild(refresh);
```

Do not add a timer, retry counter, CSS rule, or job field.

- [ ] **Step 4: Run the focused Jest suite for GREEN**

```bash
.venv/bin/jlpm test src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand
```

Expected: the sidebar suite passes, including the existing explicit terminal retry behavior.

- [ ] **Step 5: Run formatting, lint, TypeScript, and prebuilt build**

```bash
.venv/bin/jlpm prettier:base --check
.venv/bin/jlpm stylelint:check
.venv/bin/jlpm eslint:check
.venv/bin/jlpm build:lib:prod
.venv/bin/jupyter-builder build .
```

Expected: every command exits `0`; the repository prebuilt bundle contains the new marker.

- [ ] **Step 6: Commit frontend behavior and tests**

```bash
git add src/ui/behaviorAnalysisSidebar.ts \
  src/__tests__/behaviorAnalysisSidebar.spec.ts \
  myextension/tests/test_labextension_artifact.py \
  myextension/labextension lib
git commit -m "fix: explain full analysis automatic retry"
```

---

### Task 3: Synchronize the 0.2.1 Runtime Contract and Guidance

**Files:**
- Modify: `README.md:116-135`
- Modify: both delivery `Dockerfile`, `runtime.env.example`, and `README.md` files.
- Modify: `myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json`.
- Modify: `docs/2026-08-06-assessment-assist-latency-reliability-verification.md`

**Interfaces:**
- Consumes: backend default `180`, one-call cap `60`, default maximum three calls, and unchanged assessment-assist fast configuration.
- Produces: consistent runtime defaults, install guidance, verification provenance, and rollback boundaries.

- [ ] **Step 1: Update only active runtime claims**

- Root README must distinguish the fast assessment-assist `2048 → 4096` request from full analysis `8192 → 16384`, three 60-second calls, and a 180-second total default.
- Both Dockerfiles must set `JUPYTERLAB_BEHAVIOR_AUDIT_ANALYSIS_TIMEOUT_SEC=180`.
- Both `runtime.env.example` files must set the same value to `180`.
- Both delivery READMEs must describe automatic retry within 180 seconds before manual retry and use 180 seconds in their smoke-test expectation.
- The verification doc must record the sanitized 120.13-second timeout and 57.82-second successful manual retry, and state that no extra real Provider validation was run.
- The complete bundle manifest retains Docker/BLUEDOT/real-course-data exclusions; final counts and hashes are filled only from Task 5 evidence.
- Earlier dated plans/specs remain historical and are not rewritten.

- [ ] **Step 2: Prove the two delivery copies match**

```bash
cmp deploy/bluedot/release-0.2.1/README.md \
  myextension-0.2.1-BLUEDOT-完整交付包/README.md
cmp deploy/bluedot/release-0.2.1/runtime.env.example \
  myextension-0.2.1-BLUEDOT-完整交付包/runtime.env.example
cmp deploy/bluedot/release-0.2.1/Dockerfile \
  myextension-0.2.1-BLUEDOT-完整交付包/Dockerfile
```

Expected: all three comparisons exit `0`.

- [ ] **Step 3: Validate documentation and manifests**

```bash
.venv/bin/jlpm exec prettier README.md --write
.venv/bin/jlpm prettier:base --check
.venv/bin/python -m json.tool \
  myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json >/dev/null
git diff --check
```

Expected: both JSON documents parse and formatting passes.

- [ ] **Step 4: Commit synchronized runtime documentation**

```bash
git add README.md \
  docs/2026-08-06-assessment-assist-latency-reliability-verification.md \
  deploy/bluedot/release-0.2.1/Dockerfile \
  deploy/bluedot/release-0.2.1/runtime.env.example \
  deploy/bluedot/release-0.2.1/README.md \
  myextension-0.2.1-BLUEDOT-完整交付包/Dockerfile \
  myextension-0.2.1-BLUEDOT-完整交付包/runtime.env.example \
  myextension-0.2.1-BLUEDOT-完整交付包/README.md \
  myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json
git commit -m "docs: document full analysis auto retry"
```

---

### Task 4: Rebuild the 0.2.1 Wheel and Current Delivery Archive

**Files:**
- Rebuild: `dist/myextension-0.2.1-py3-none-any.whl`
- Replace: both delivery `artifacts/myextension-0.2.1-py3-none-any.whl` files.
- Modify: both delivery `SHA256SUMS` files and the complete bundle `MANIFEST.json`.
- Create: `myextension-0.2.1-BLUEDOT-完整交付包-20260806-analysis-retry-fix.zip`
- Create: `myextension-0.2.1-BLUEDOT-完整交付包-20260806-analysis-retry-fix.zip.sha256`

**Interfaces:**
- Consumes: the production JupyterLab bundle, synchronized delivery sources, and package version `0.2.1`.
- Produces: one byte-identical wheel in both directories, exact SHA records, a fresh non-overwriting ZIP, and an isolated-installable artifact.

- [ ] **Step 1: Re-run production and wheel builds**

```bash
.venv/bin/jlpm build:lib:prod
.venv/bin/jupyter-builder build .
uv build --wheel --offline
```

Expected: all exit `0` and the 0.2.1 wheel is recreated. If uv needs its existing cache outside the sandbox, grant only the narrow read permission and rerun the identical offline command.

- [ ] **Step 2: Replace both wheel copies and update exact SHAs**

```bash
shasum -a 256 dist/myextension-0.2.1-py3-none-any.whl
cp dist/myextension-0.2.1-py3-none-any.whl \
  deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
cp dist/myextension-0.2.1-py3-none-any.whl \
  myextension-0.2.1-BLUEDOT-完整交付包/artifacts/myextension-0.2.1-py3-none-any.whl
cmp deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl \
  myextension-0.2.1-BLUEDOT-完整交付包/artifacts/myextension-0.2.1-py3-none-any.whl
```

Expected: copies match byte-for-byte. Update both `SHA256SUMS` and manifest artifact SHA fields with the printed digest using `apply_patch`, not shell redirection.

- [ ] **Step 3: Verify wheel structure and frontend identity**

```bash
.venv/bin/check-wheel-contents dist/myextension-0.2.1-py3-none-any.whl
.venv/bin/python -m zipfile -t dist/myextension-0.2.1-py3-none-any.whl
.venv/bin/python -m pytest -q \
  myextension/tests/test_labextension_artifact.py \
  myextension/tests/test_release_artifact.py
```

Expected: structure, ZIP integrity, the 180-second marker, remote-entry identity, and both release copies pass.

- [ ] **Step 4: Install into a fresh private target**

Create `mktemp -d /private/tmp/myextension-analysis-retry.XXXXXX`, install with `pip --no-deps --target <target>/site`, then use Python `-P` with only that target on `PYTHONPATH` to assert version `0.2.1`. Verify `jupyter labextension list` reports `myextension v0.2.1 enabled OK`. Record the concrete temporary path; do not modify project or system Python.

- [ ] **Step 5: Create and checksum the new archive**

```bash
/usr/bin/zip -qr \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-analysis-retry-fix.zip \
  myextension-0.2.1-BLUEDOT-完整交付包 -x '*/.DS_Store'
.venv/bin/python -m zipfile -t \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-analysis-retry-fix.zip
shasum -a 256 \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-analysis-retry-fix.zip
```

Create `.zip.sha256` with the exact digest via `apply_patch`, then verify it with `shasum -a 256 -c` from the repository root.

- [ ] **Step 6: Move only the superseded archive generated during this session**

After the new ZIP passes, move the known untracked pre-retry ZIP and `.sha256` into a fresh `/private/tmp/myextension-superseded-archive.<random>/` directory. They remain recoverable for this session. Do not move or remove any other archive.

- [ ] **Step 7: Commit artifacts and archive**

```bash
git add deploy/bluedot/release-0.2.1/artifacts \
  deploy/bluedot/release-0.2.1/SHA256SUMS \
  myextension-0.2.1-BLUEDOT-完整交付包/artifacts \
  myextension-0.2.1-BLUEDOT-完整交付包/SHA256SUMS \
  myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-analysis-retry-fix.zip \
  myextension-0.2.1-BLUEDOT-完整交付包-20260806-analysis-retry-fix.zip.sha256
git commit -m "build: deliver full analysis auto retry"
```

---

### Task 5: Run Final Gates, Record Evidence, and Tag the Rollback Point

**Files:**
- Modify: `docs/2026-08-06-assessment-assist-latency-reliability-verification.md`
- Modify: the complete bundle `MANIFEST.json` only when fresh counts or hashes require it.

**Interfaces:**
- Consumes: the exact source tree, wheel, delivery directories, and archive being handed off.
- Produces: fresh evidence and local tag `analysis-auto-retry-delivery-0.2.1`; no merge or push.

- [ ] **Step 1: Run the full backend gate**

```bash
.venv/bin/python -m pytest -q myextension/tests
```

Run with local-loopback permission because pytest-jupyter binds temporary `127.0.0.1` ports. Expected: all tests pass; record the exact count and duration.

- [ ] **Step 2: Run the full frontend and quality gates**

```bash
.venv/bin/jlpm test --runInBand
.venv/bin/jlpm stylelint:check
.venv/bin/jlpm prettier:base --check
.venv/bin/jlpm eslint:check
.venv/bin/jlpm build:lib:prod
.venv/bin/jupyter-builder build .
```

Expected: all Jest suites, tests, and coverage thresholds pass; every quality/build command exits `0`.

- [ ] **Step 3: Verify both delivery directories and the ZIP**

Run `shasum -a 256 -c SHA256SUMS` from each delivery directory. Then run README/runtime/Dockerfile `cmp`, all four install/verify script `sh -n` checks, and the final ZIP `.sha256` check from the repository root. Expected: both wheels and the archive report `OK`, all comparisons match, and every script parses.

- [ ] **Step 4: Record fresh evidence only**

Update the verification document and complete bundle manifest with exact backend/frontend totals, retry RED/GREEN evidence, wheel SHA, remote entry, archive SHA, and isolated install path. State that the observed real first/second attempt timings motivated the fix, while the new third-call path was validated with fake time and no additional real Provider request. Retain exclusions for Docker, BLUEDOT, registry/Git push, and real course/student data. Validate the JSON file and run `git diff --check`.

- [ ] **Step 5: Commit final evidence**

```bash
git add docs/2026-08-06-assessment-assist-latency-reliability-verification.md \
  myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json
git commit -m "docs: record full analysis retry verification"
```

- [ ] **Step 6: Create the local tag and inspect final state**

```bash
test -z "$(git tag --list analysis-auto-retry-delivery-0.2.1)"
git tag analysis-auto-retry-delivery-0.2.1
git status --short
git log -8 --oneline
```

Expected: the tag is new, no unintended files remain, and nothing is pushed.

- [ ] **Step 7: Stop at local handoff**

Report the folder, wheel, ZIP, checksum, install/restart/verify commands, test totals, branch, commit, and rollback tag. Keep the worktree and both active preview services intact. Present the finishing-a-development-branch integration menu; do not merge or push without an explicit choice.
