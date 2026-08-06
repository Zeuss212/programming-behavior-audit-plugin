# AI Analysis Latency, Reliability, and BLUEDOT Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound whole-session AI analysis to a shared default 120-second budget, expose actionable safe failure reasons, reduce normal model output, and ship a verified `myextension 0.2.1` BLUEDOT offline image bundle.

**Architecture:** Keep the existing single-worker persisted job state machine. Add validated timeout configuration in the transport module, make one retrying recorder own a monotonic deadline shared by initial analysis, truncation recovery, and repair calls, map transport failures to stable analyzer/job error codes, and add frontend guidance for each code. Build the latest source into a separate wheel under `deploy/bluedot/release-0.2.1/` so the existing `dist/` wheel remains untouched.

**Tech Stack:** Python 3.12, `urllib.request`, pytest, TypeScript 5.5, Jest/jsdom, JupyterLab 4 prebuilt extension, Jupyter Server 2, POSIX shell, Docker.

## Global Constraints

- Package version remains exactly `0.2.1`; distinguish the new artifact by its directory and SHA-256.
- `JUPYTERLAB_BEHAVIOR_AUDIT_ANALYSIS_TIMEOUT_SEC` defaults to `120` and accepts only integer values from `60` through `180` inclusive.
- One Provider call receives at most `60` seconds and never more than the current shared budget.
- Retry network errors, Provider timeouts, HTTP 429, and HTTP 5xx at most once after a two-second backoff.
- Keep the existing explicit truncation recovery budgets `8192` then `16384`, but both calls consume the same shared budget.
- Keep one invalid-dimension repair call, but it consumes the same shared budget.
- Preserve `MAX_CANDIDATES = 20`, `MAX_CODE_CHARS = 300`, coverage rules, schema validation, private file permissions, restart recovery, and the single-worker queue.
- Never persist exception text, Provider response bodies, credentials, Jupyter tokens, Cookies, or absolute local paths in public errors.
- Do not call real/paid AI, stop the existing preview, push a container image, log into BLUEDOT, or deploy a real workspace.
- The working directory is not a Git repository. Commit steps are intentionally replaced with verification checkpoints; do not fabricate commit IDs.

---

## File Map

**Modify**

- `myextension/llm_transport.py`: timeout environment name, bounds, validated timeout loader, and transport constants.
- `myextension/analysis_worker.py`: shared monotonic budget, one transient retry, per-call remaining timeout, and accepted analyzer error codes.
- `myextension/dimension_analyzer.py`: compact response instructions and safe transport-to-analysis error mapping.
- `myextension/tests/test_ai_config_path.py`: timeout environment parsing tests alongside existing environment configuration tests.
- `myextension/tests/test_analysis_job_store.py`: shared-deadline, retry-count, per-call timeout, and persisted error-code tests.
- `myextension/tests/test_dimension_analyzer.py`: concise prompt and precise error mapping tests.
- `src/ui/behaviorAnalysisSidebar.ts`: actionable Chinese guidance for new error codes.
- `src/__tests__/behaviorAnalysisSidebar.spec.ts`: table-driven frontend error guidance and retry-state tests.
- `README.md`, `启动说明.md`, `项目交接文档.md`: new runtime setting, new release bundle, artifact identity, and remaining real-environment acceptance limits.

**Create**

- `deploy/bluedot/release-0.2.1/Dockerfile`: install and verify the fixed wheel in a caller-supplied BLUEDOT base image.
- `deploy/bluedot/release-0.2.1/.dockerignore`: admit only the Dockerfile and wheel artifact.
- `deploy/bluedot/release-0.2.1/build_image.sh`: verify the wheel checksum and build a caller-tagged image.
- `deploy/bluedot/release-0.2.1/verify_image.sh`: non-interactive installed-extension and writable-log-root verification.
- `deploy/bluedot/release-0.2.1/runtime.env.example`: non-secret runtime configuration example.
- `deploy/bluedot/release-0.2.1/README.md`: complete Chinese build, push, platform registration, acceptance, troubleshooting, and rollback instructions.
- `deploy/bluedot/release-0.2.1/SHA256SUMS`: checksum for the newly built wheel.
- `deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl`: wheel built from the fixed current source.

---

### Task 1: Validated analysis timeout configuration

**Files:**
- Modify: `myextension/llm_transport.py:22-31`
- Modify: `myextension/tests/test_ai_config_path.py:12-98`

**Interfaces:**
- Produces: `ANALYSIS_TIMEOUT_ENV_VAR: str`, `DEFAULT_ANALYSIS_TIMEOUT_SEC = 120`, `MIN_ANALYSIS_TIMEOUT_SEC = 60`, `MAX_ANALYSIS_TIMEOUT_SEC = 180`, `PROVIDER_CALL_TIMEOUT_SEC = 60`, and `analysis_timeout_sec() -> int`.
- Consumes: `os.environ`; no file reads and no secret values.

- [ ] **Step 1: Add failing configuration tests**

Add `transport.ANALYSIS_TIMEOUT_ENV_VAR` to the autouse fixture cleanup and add:

```python
@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, 120),
        ("60", 60),
        ("120", 120),
        ("180", 180),
        ("59", 120),
        ("181", 120),
        ("120.0", 120),
        ("invalid", 120),
        ("", 120),
    ],
)
def test_analysis_timeout_is_bounded(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv(transport.ANALYSIS_TIMEOUT_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(transport.ANALYSIS_TIMEOUT_ENV_VAR, configured)
    assert transport.analysis_timeout_sec() == expected
```

- [ ] **Step 2: Run the test and confirm RED**

Run:

```bash
.venv/bin/pytest -q myextension/tests/test_ai_config_path.py
```

Expected: failure because `ANALYSIS_TIMEOUT_ENV_VAR` and `analysis_timeout_sec()` do not exist.

- [ ] **Step 3: Implement the bounded loader**

Add to `myextension/llm_transport.py`:

```python
ANALYSIS_TIMEOUT_ENV_VAR = "JUPYTERLAB_BEHAVIOR_AUDIT_ANALYSIS_TIMEOUT_SEC"
DEFAULT_ANALYSIS_TIMEOUT_SEC = 120
MIN_ANALYSIS_TIMEOUT_SEC = 60
MAX_ANALYSIS_TIMEOUT_SEC = 180
PROVIDER_CALL_TIMEOUT_SEC = 60


def analysis_timeout_sec() -> int:
    raw = os.environ.get(ANALYSIS_TIMEOUT_ENV_VAR)
    if raw is None or not raw.isdecimal():
        return DEFAULT_ANALYSIS_TIMEOUT_SEC
    value = int(raw)
    if not MIN_ANALYSIS_TIMEOUT_SEC <= value <= MAX_ANALYSIS_TIMEOUT_SEC:
        return DEFAULT_ANALYSIS_TIMEOUT_SEC
    return value
```

Remove `WORKER_REQUEST_TIMEOUT_SEC = 90` after Task 2 switches all imports to `PROVIDER_CALL_TIMEOUT_SEC`.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run:

```bash
.venv/bin/pytest -q myextension/tests/test_ai_config_path.py
```

Expected: all existing path tests plus the timeout cases pass.

- [ ] **Step 5: Verification checkpoint**

Record the command, pass count, and absence of Git commit capability in the final handoff.

---

### Task 2: Shared monotonic deadline and single transient retry

**Files:**
- Modify: `myextension/analysis_worker.py:157-195`
- Modify: `myextension/analysis_worker.py:694-701`
- Modify: `myextension/tests/test_analysis_job_store.py:424-527`
- Modify: `myextension/tests/test_analysis_job_store.py:913-1033`

**Interfaces:**
- Consumes: `analysis_timeout_sec()`, `PROVIDER_CALL_TIMEOUT_SEC`, `LlmTransportError`, injected `wait(seconds)` and injected monotonic `clock()`.
- Produces: `_RecordingRetryingClient(..., total_timeout_sec: int, clock: Callable[[], float] = time.monotonic)`; it raises `LlmTransportError("analysis_deadline_exceeded")` when no shared time remains.
- Preserves: `responses: list[dict[str, object]]` contains only successful normalized Provider responses.

- [ ] **Step 1: Add a deterministic fake clock and RED tests**

Add beside the worker retry tests:

```python
class FakeMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds
```

Add tests proving:

```python
def test_worker_uses_sixty_second_provider_timeout(tmp_path):
    # A successful injected provider records timeout_sec == 60.


def test_worker_retries_transient_failure_only_once(tmp_path):
    # The provider always raises provider_timeout.
    # Assert exactly two calls, waits == [2.0], and terminal safe timeout code.


def test_worker_shares_budget_across_initial_truncation_and_repair(tmp_path):
    # Advance FakeMonotonic inside the provider.
    # Assert every timeout is <= min(60, remaining budget) and no call starts
    # after the 120-second deadline.
```

Update existing expectations from timeout `90` to timeout `60`, retry calls from three to two, and delays from `[2.0, 8.0]` to `[2.0]`.

- [ ] **Step 2: Run focused worker tests and confirm RED**

Run:

```bash
.venv/bin/pytest -q \
  myextension/tests/test_analysis_job_store.py::test_worker_ready_persists_private_artifacts_and_closed_public_result \
  myextension/tests/test_analysis_job_store.py::test_worker_recovers_truncated_response_in_same_attempt \
  myextension/tests/test_analysis_job_store.py::test_worker_retries_transient_provider_calls_only \
  myextension/tests/test_analysis_job_store.py::test_one_logical_repair_keeps_independent_transport_retry_budgets
```

Expected: failures show the old 90-second timeout, three-call retry loop, and independent budgets.

- [ ] **Step 3: Implement the shared deadline client**

Import `math`, `analysis_timeout_sec`, and `PROVIDER_CALL_TIMEOUT_SEC`. Change `_RecordingRetryingClient` to store:

```python
self._clock = clock
self._deadline = clock() + total_timeout_sec
```

Before each call calculate:

```python
remaining = self._deadline - self._clock()
if remaining <= 0:
    raise LlmTransportError("analysis_deadline_exceeded")
timeout_sec = min(
    PROVIDER_CALL_TIMEOUT_SEC,
    max(1, math.ceil(remaining)),
)
```

Use two attempts and one delay:

```python
for call_index in range(2):
    try:
        response = self._provider.chat_json(request_body, timeout_sec=timeout_sec)
        self.responses.append(response)
        return response
    except LlmTransportError as error:
        if call_index == 1 or not self._retryable(error):
            raise
        remaining = self._deadline - self._clock()
        if remaining <= 2.0:
            raise LlmTransportError("analysis_deadline_exceeded") from error
        self._wait(2.0)
```

Construct one recorder per job with `total_timeout_sec=analysis_timeout_sec()`. The same recorder is already passed through initial analysis, truncation recovery, and repair, so the deadline remains shared.

- [ ] **Step 4: Run focused worker tests and confirm GREEN**

Run:

```bash
.venv/bin/pytest -q myextension/tests/test_analysis_job_store.py -k 'worker or retry or repair or truncated'
```

Expected: all selected tests pass without real sleeping or networking.

- [ ] **Step 5: Verification checkpoint**

Confirm tests demonstrate at most two transient calls, one two-second delay, a maximum 60-second per-call timeout, and one shared deadline.

---

### Task 3: Precise safe error codes and concise model output

**Files:**
- Modify: `myextension/dimension_analyzer.py:17-35`
- Modify: `myextension/dimension_analyzer.py:390-432`
- Modify: `myextension/dimension_analyzer.py:662-835`
- Modify: `myextension/analysis_worker.py:50-66`
- Modify: `myextension/tests/test_dimension_analyzer.py:700-802`
- Modify: `myextension/tests/test_dimension_analyzer.py:1508-1616`
- Modify: `myextension/tests/test_analysis_job_store.py:881-1056`

**Interfaces:**
- Produces: `_safe_analysis_error_code(error: BaseException) -> str` returning only approved public codes.
- Consumes: `LlmTransportError.error_code` and optional `http_status`; no exception text.
- Produces job error codes listed in the approved design and accepted by `_ANALYZER_ERROR_CODES`.

- [ ] **Step 1: Add RED mapping and output-contract tests**

Add a parameterized analyzer test:

```python
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (LlmTransportError("analysis_deadline_exceeded"), "ai_analysis_timeout"),
        (LlmTransportError("provider_timeout"), "ai_analysis_timeout"),
        (LlmTransportError("provider_network_error"), "ai_provider_network_error"),
        (LlmTransportError("provider_http_error", http_status=429), "ai_provider_rate_limited"),
        (LlmTransportError("provider_http_error", http_status=401), "ai_provider_auth_failed"),
        (LlmTransportError("provider_http_error", http_status=403), "ai_provider_auth_failed"),
        (LlmTransportError("provider_http_error", http_status=400), "ai_provider_request_rejected"),
        (LlmTransportError("provider_http_error", http_status=503), "ai_provider_unavailable"),
        (LlmTransportError("provider_response_truncated"), "ai_response_truncated"),
        (LlmTransportError("provider_response_invalid"), "ai_response_invalid"),
        (RuntimeError("private detail"), "ai_analysis_failed"),
    ],
)
def test_analysis_failure_maps_to_safe_code(failure, expected):
    def client(_request):
        raise failure
    result = _analyze(client)
    assert result["status"] == "partial"
    assert result["error_code"] == expected
    assert "private detail" not in json.dumps(result)
```

Extend the invalid-repair test to assert `result["error_code"] == "ai_response_invalid"`. Add a prompt test asserting the user payload contains the exact constraints `最多 3 条` and `160`.

- [ ] **Step 2: Run focused analyzer tests and confirm RED**

Run:

```bash
.venv/bin/pytest -q myextension/tests/test_dimension_analyzer.py -k 'failure_maps or invalid_repair or output'
```

Expected: old code collapses failures to `ai_analysis_failed`, leaves invalid repair without a code, and lacks compact-output instructions.

- [ ] **Step 3: Implement safe mapping and compact instructions**

Add a pure mapping function that uses exact transport codes and HTTP status classes, returning `ai_analysis_failed` for all unknown exceptions. Catch `LlmTransportError as error` before the generic exception in `analyze_session()` and set the mapped code. If repair validation leaves any requested dimension unresolved without a transport exception, set `ai_response_invalid`.

Update `SYSTEM_PROMPT` and `_OUTPUT_SCHEMA` descriptions with:

```text
每个维度最多返回 3 条最强且不重复的证据，每条 claim 使用简洁中文，
explanation 不超过 160 个中文字符。不得输出分析过程、Markdown 或额外字段。
```

Add every new code to `analysis_worker._ANALYZER_ERROR_CODES`; retain `ai_not_configured`, `ai_analysis_failed`, and `invalid_profile`.

- [ ] **Step 4: Run analyzer and worker persistence tests**

Run:

```bash
.venv/bin/pytest -q \
  myextension/tests/test_dimension_analyzer.py \
  myextension/tests/test_analysis_job_store.py
```

Expected: all tests pass, public result remains closed, and job/attempt files contain only stable codes.

- [ ] **Step 5: Verification checkpoint**

Search test artifacts for the synthetic private marker and confirm it is absent from public job/result JSON.

---

### Task 4: Actionable frontend failure guidance

**Files:**
- Modify: `src/ui/behaviorAnalysisSidebar.ts:185-206`
- Modify: `src/__tests__/behaviorAnalysisSidebar.spec.ts:2260-2310`

**Interfaces:**
- Consumes: `IAnalysisJob.error_code: string | null` from the existing API.
- Produces: Chinese action text through the existing private `actionForError()` path; no API/model changes.

- [ ] **Step 1: Add RED table-driven UI expectations**

Extend the existing error-code matrix with exact fragments:

```typescript
[
  ['ai_analysis_timeout', '分析超时'],
  ['ai_provider_network_error', '网络、DNS 或 TLS'],
  ['ai_provider_rate_limited', '额度或并发'],
  ['ai_provider_auth_failed', 'API Key 和模型权限'],
  ['ai_provider_request_rejected', 'Base URL 和模型名'],
  ['ai_provider_unavailable', '服务暂不可用'],
  ['ai_response_truncated', '输出过长'],
  ['ai_response_invalid', '输出格式']
]
```

For every partial/error case, retain the assertion that the “重试分析” button is enabled unless the existing integrity-failure rule forbids it.

- [ ] **Step 2: Run the targeted Jest test and confirm RED**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm jest \
  src/__tests__/behaviorAnalysisSidebar.spec.ts \
  --runInBand --coverage=false -t 'error guidance'
```

Expected: the new fragments are missing because all codes fall through to the generic message.

- [ ] **Step 3: Implement explicit action text**

Add grouped branches in `actionForError()` for all eight new codes. Keep `ai_not_configured`, `invalid_profile`, integrity failures, and server persistence failures unchanged. Do not interpolate server text into the DOM.

- [ ] **Step 4: Run sidebar tests and confirm GREEN**

Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm jest \
  src/__tests__/behaviorAnalysisSidebar.spec.ts \
  --runInBand --coverage=false
```

Expected: all sidebar tests pass.

- [ ] **Step 5: Verification checkpoint**

Confirm every backend public error code has one frontend action and still exposes retry for non-integrity failures.

---

### Task 5: BLUEDOT offline image bundle and complete Chinese instructions

**Files:**
- Create: `deploy/bluedot/release-0.2.1/Dockerfile`
- Create: `deploy/bluedot/release-0.2.1/.dockerignore`
- Create: `deploy/bluedot/release-0.2.1/build_image.sh`
- Create: `deploy/bluedot/release-0.2.1/verify_image.sh`
- Create: `deploy/bluedot/release-0.2.1/runtime.env.example`
- Create: `deploy/bluedot/release-0.2.1/README.md`
- Modify: `README.md`
- Modify: `启动说明.md`
- Modify: `项目交接文档.md`

**Interfaces:**
- `build_image.sh <base-image> <target-image>` verifies `SHA256SUMS`, then runs Docker build with `BLUEDOT_BASE_IMAGE`.
- `verify_image.sh <image>` runs import/version, Jupyter extension, and writable tmpfs log-root checks.
- Dockerfile consumes exactly `artifacts/myextension-0.2.1-py3-none-any.whl`.

- [ ] **Step 1: Create shell contract checks before the scripts**

The scripts must start with `#!/bin/sh` and `set -eu`, reject missing or extra arguments with exit `64`, resolve their own directory without depending on the caller's current directory, and never execute login, push, platform API, or secret operations.

`build_image.sh` must support both macOS `shasum -a 256 -c` and Linux `sha256sum -c`, then call:

```bash
docker build \
  --build-arg "BLUEDOT_BASE_IMAGE=$1" \
  --tag "$2" \
  "$script_dir"
```

`verify_image.sh` must use `--entrypoint /bin/sh` and a writable tmpfs mounted at `/workspace/result`; it must not start JupyterLab or disable authentication.

- [ ] **Step 2: Write Dockerfile and non-secret runtime example**

Dockerfile requirements:

```dockerfile
ARG BLUEDOT_BASE_IMAGE
FROM ${BLUEDOT_BASE_IMAGE}

COPY artifacts/myextension-0.2.1-py3-none-any.whl /tmp/
RUN python -c "from importlib.metadata import version; assert version('jupyterlab').split('.')[0] == '4'; assert version('jupyter-server').split('.')[0] == '2'; import jsonschema" \
    && python -m pip install --no-cache-dir --no-deps --force-reinstall /tmp/myextension-0.2.1-py3-none-any.whl \
    && python -m jupyter server extension enable myextension --sys-prefix \
    && python -c "import myextension; assert myextension.__version__ == '0.2.1'; assert myextension._jupyter_labextension_paths()[0]['dest'] == 'myextension'" \
    && python -m jupyter labextension list \
    && python -m jupyter server extension list \
    && rm /tmp/myextension-0.2.1-py3-none-any.whl

ENV JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR=/workspace/result/behavior-audit \
    JUPYTERLAB_BEHAVIOR_AUDIT_ANALYSIS_TIMEOUT_SEC=120
```

`.dockerignore` must ignore everything, then re-include only `Dockerfile` and the exact wheel artifact.

- [ ] **Step 3: Write complete Chinese README**

Document exact prerequisites, checksum verification, script usage, manual Docker equivalent, registry login/push commands marked as administrator-executed actions, BLUEDOT framework registration, `/workspace/result` persistence, runtime environment injection, secret injection, no-AI functional acceptance, authorized real-AI acceptance with a 120-second terminal-state target, troubleshooting by the new stable error codes, and digest-based rollback.

Use explicit parameter names such as `BASE_IMAGE` and `TARGET_IMAGE`; never include a credential value or a live token URL.

- [ ] **Step 4: Update top-level handoff links and limitations**

Add links to the release README and state that the release-directory wheel supersedes the old `dist/` wheel only for the BLUEDOT image handoff. Keep the old hash as historical evidence and add the new hash only after Task 6 builds it.

- [ ] **Step 5: Run static checks**

Run:

```bash
sh -n deploy/bluedot/release-0.2.1/build_image.sh
sh -n deploy/bluedot/release-0.2.1/verify_image.sh
rg -n 'ARK_API_KEY=.+' deploy/bluedot/release-0.2.1
```

Expected: both scripts parse; the secret scan returns no value assignment.

- [ ] **Step 6: Verification checkpoint**

List all bundle files and confirm no logs, notebooks, screenshots, `.ark_ai_config.json`, or unrelated source files are admitted by `.dockerignore`.

---

### Task 6: Full regression, independent wheel build, artifact identity, and final handoff

**Files:**
- Create: `deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl`
- Create: `deploy/bluedot/release-0.2.1/SHA256SUMS`
- Modify after hash generation: `deploy/bluedot/release-0.2.1/README.md`, `README.md`, `启动说明.md`, `项目交接文档.md`

**Interfaces:**
- Consumes: current source tree after Tasks 1–5.
- Produces: immutable wheel filename plus exact SHA-256 and a verification record in the final response.

- [ ] **Step 1: Run focused RED/GREEN evidence suites**

Run the exact Task 1–4 focused commands and preserve pass counts. Confirm no test uses network or real sleep.

- [ ] **Step 2: Run complete project quality gates**

Run:

```bash
.venv/bin/pytest -q
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm build:prod
```

Expected: all backend tests, frontend tests, lint, and production build pass. If an existing baseline fails, stop and diagnose it before building the wheel.

- [ ] **Step 3: Build to the independent release artifact directory**

First confirm `uv build --help` supports `--out-dir`, then run:

```bash
uv build --wheel --offline \
  --out-dir deploy/bluedot/release-0.2.1/artifacts
```

Expected: exactly one `myextension-0.2.1-py3-none-any.whl` appears in the release artifact directory and `dist/` remains unchanged.

- [ ] **Step 4: Verify wheel structure and latest logic**

Run:

```bash
.venv/bin/check-wheel-contents \
  deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
.venv/bin/python -m zipfile -t \
  deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
unzip -p \
  deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl \
  myextension/llm_transport.py | rg \
  'JUPYTERLAB_BEHAVIOR_AUDIT_(AI_CONFIG_PATH|ANALYSIS_TIMEOUT_SEC)|BLUEDOT_WORKSPACE_CODE_DIR'
```

Expected: wheel contents pass, ZIP integrity reports `Done testing`, and all latest config/budget markers are present.

- [ ] **Step 5: Perform an isolated no-dependency import**

Use `mktemp -d`, install the wheel with `uv pip install --target <temp>/site --no-deps`, prepend that target to `sys.path`, and assert:

```python
import myextension
import myextension.llm_transport as transport
assert myextension.__version__ == "0.2.1"
assert transport.analysis_timeout_sec() == 120
```

Remove only the explicit temporary directory after the assertion succeeds.

- [ ] **Step 6: Generate and backfill SHA-256**

Compute the hash with `shasum -a 256` on macOS. Write `SHA256SUMS` in the release-directory-relative form:

```text
<64-hex-digest>  artifacts/myextension-0.2.1-py3-none-any.whl
```

Backfill the exact digest into the release README and top-level handoff documents, then verify it with both the host checksum command and `build_image.sh`'s selected checksum implementation.

- [ ] **Step 7: Check Docker availability without pulling or pushing**

Run `docker version` read-only. If the daemon and the real BLUEDOT base image are unavailable, do not pull an unknown image; record Docker build/run and BLUEDOT acceptance as administrator-owned pending steps. If a locally cached explicitly identified base image exists, a local build may be run without registry or platform mutations.

- [ ] **Step 8: Final verification-before-completion audit**

Invoke `verification-before-completion`, rerun the smallest commands that directly prove every completion claim, list changed/created files, compare the old `dist/` hash to confirm it was not overwritten, and report all unverified real-environment items precisely.
