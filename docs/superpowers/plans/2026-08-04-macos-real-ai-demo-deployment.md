# macOS Real-AI Deployment Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible macOS Demo that installs the final wheel in isolation, opens authenticated JupyterLab, guides a manual real-AI create/publish/monitor/analyze flow, and safely verifies and exports the latest successful synthetic session.

**Architecture:** Shell scripts own deployment lifecycle and only pass non-secret configuration. One importable Python module owns authoritative session selection, success validation, whitelist export, and CLI output. A prepared Notebook and README drive the manual UI demonstration; unit tests use only synthetic filesystem fixtures and never call an AI provider.

**Tech Stack:** Bash 3.2-compatible shell, Python 3.12 standard library, `unittest`, JupyterLab 4.6.1, Jupyter Server 2.20.0, current `myextension 0.2.0` wheel.

## Global Constraints

- Target platform is macOS only; do not add Windows behavior in this plan.
- Final wheel is `dist/myextension-0.2.0-py3-none-any.whl` with SHA-256 `f95a375acf49947a9921cf688a6e4cd6854fe88da4efea3c57fc3e90421c516c`.
- Default Demo port is `18994`; port retries are disabled and existing `127.0.0.1:8899` is out of scope.
- Jupyter token/password authentication must remain enabled; no code may parse, persist, or print a token URL beyond Jupyter's own normal terminal output.
- API Key is entered manually in the product UI; it must not appear in `.env`, shell arguments, test fixtures, export archives, or documentation examples.
- Runtime data lives under a unique `$TMPDIR/myextension-real-ai-demo.XXXXXX` directory and is never deleted automatically.
- Export uses an explicit whitelist and must exclude `.ark_ai_config.json`, `jobs/`, provider raw responses, runtime files, tokens, and Cookies.
- A Demo passes only when both session and AI analysis are `ready`/finalized as specified; partial is a failure.
- Do not call real or paid AI during implementation or verification.
- This project directory is not a Git repository. Replace plan commit steps with SHA-256 checkpoint entries in `docs/2026-08-04-macos-real-ai-demo-verification.md`; do not invent commit IDs.

---

## File Map

- Create `demo/macos_real_ai/verify_demo.py`: latest-session selection, validation, export, CLI.
- Create `demo/macos_real_ai/tests/test_verify_demo.py`: synthetic TDD coverage for verifier/exporter.
- Create `demo/macos_real_ai/deploy_demo.sh`: safe config parsing, preflight, isolation, install, extension checks, Jupyter lifecycle.
- Create `demo/macos_real_ai/stop_demo.sh`: exact runtime/PID/port validation and graceful stop.
- Create `demo/macos_real_ai/export_latest_demo.sh`: locate current runtime and invoke verifier export.
- Create `demo/macos_real_ai/.env.example`: non-secret configuration examples only.
- Create `demo/macos_real_ai/demo_notebook.ipynb`: manual edit/error/fix/success flow.
- Create `demo/macos_real_ai/README.md`: full on-stage walkthrough and troubleshooting.
- Create `docs/2026-08-04-macos-real-ai-demo-verification.md` during Task 1, then append Task 2-4 evidence: RED/GREEN commands, hashes, exclusions, unexecuted real-AI boundary.
- Modify `项目交接文档.md`: link the Demo and state that real-AI execution remains user-owned.

---

### Task 1: Authoritative Verifier and Safe Export

**Files:**
- Create: `demo/macos_real_ai/verify_demo.py`
- Create: `demo/macos_real_ai/tests/test_verify_demo.py`
- Create: `docs/2026-08-04-macos-real-ai-demo-verification.md`

**Interfaces:**
- Produces `DemoVerificationError(message: str)`.
- Produces immutable `DemoVerification(session_id: str, session_dir: Path, event_count: int, analysis_status: str, model_name: str, legacy_projection: Path | None)`.
- Produces `verify_latest_demo(log_root: Path) -> DemoVerification`.
- Produces `export_verified_demo(log_root: Path, export_dir: Path) -> Path` returning the created zip path.
- CLI accepts `--log-root PATH`, optional `--export-dir PATH`, and `--export`; exit `0` on PASS, `2` on validation failure, `3` on invalid CLI/filesystem configuration.

- [ ] **Step 1: Write synthetic fixture helpers and failing latest-session tests**

Create test helpers that write `sessions/<uuid>/session.json`, `training_record.json`, `profile.json`, `signal_dictionary.json`, and `raw_events.jsonl`. A ready record must include:

```python
{
    "session": {"status": "finalized", "analysis_status": "ready", "event_count": 3},
    "integrity": {"complete": True, "missing_artifacts": [], "warnings": []},
    "behavior_events": [
        {"segment_type": "code_writing"},
        {"segment_type": "code_execution", "execution_result": "error"},
        {"segment_type": "code_execution", "execution_result": "success"},
    ],
    "ai_analysis": {
        "status": "ready",
        "dimension_results": [{"dimension_code": "DEMO"}],
        "provenance": {
            "model_name": "real-demo-model",
            "prompt_version": "teacher-dimensions-pilot-v1",
            "input_snapshot_hash": "a" * 64,
        },
    },
}
```

Tests must assert that the newest finalized `ended_at` wins, a newer partial session is not replaced by an older ready session, and a collecting session is ignored in favor of the newest finalized session.

- [ ] **Step 2: Run latest-session tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest -v demo.macos_real_ai.tests.test_verify_demo
```

Expected: import failure because `demo.macos_real_ai.verify_demo` does not exist.

- [ ] **Step 3: Implement strict loading, session selection, and verification**

Use standard-library `json`, `dataclasses`, `datetime`, and `pathlib`. Reject missing/non-object JSON, malformed timestamps, non-finalized latest records, `integrity.complete != True`, non-ready analysis, no dimensions, missing edit/error/success event types, count mismatch, and missing provenance strings. Resolve `legacy_projection_path` against `log_root` and accept it only when `resolved_path.is_relative_to(log_root.resolve())`.

`verify_latest_demo()` must select the maximum parsed `ended_at` among finalized session documents and validate that exact session; it must not search for an older passing record after failure.

- [ ] **Step 4: Add failing export whitelist and manifest tests**

Create sensitive fixture files at `log/.ark_ai_config.json`, `jobs/job/raw_response.json`, `sessions/<id>/batches/batch.json`, and `jupyter_cookie_secret`. Assert that the resulting zip names are exactly:

```python
{
    "manifest.json",
    "session/training_record.json",
    "session/session.json",
    "session/profile.json",
    "session/signal_dictionary.json",
    "session/raw_events.jsonl",
    "legacy/session.md",
}
```

Assert every manifest SHA-256 matches the extracted bytes and no archive name contains `config`, `key`, `token`, `cookie`, `job`, `batch`, `receipt`, or `raw_response`.

- [ ] **Step 5: Run export tests and verify RED**

Run the same `unittest` command.

Expected: verification tests pass; export tests fail because `export_verified_demo` is missing.

- [ ] **Step 6: Implement whitelist export and CLI**

Use `zipfile.ZipFile(..., mode="x", compression=ZIP_DEFLATED)`. Archive only existing required whitelist files plus the verified legacy Markdown. Build `manifest.json` with schema version `1`, session ID, UTC export time, and sorted entries of `{path, sha256}`. Refuse a pre-existing archive path rather than overwrite it. Print only session ID, statuses, event count, model name, and optional archive path; never print code, prompts, API config, or environment values.

- [ ] **Step 7: Run verifier/exporter tests and verify GREEN**

Run:

```bash
.venv/bin/python -m unittest -v demo.macos_real_ai.tests.test_verify_demo
```

Expected: all tests pass with no network calls.

- [ ] **Step 8: Record Task 1 checkpoint**

Append the exact test result and SHA-256 of both Task 1 files to `docs/2026-08-04-macos-real-ai-demo-verification.md`.

---

### Task 2: Safe macOS Deployment and Lifecycle Scripts

**Files:**
- Create: `demo/macos_real_ai/deploy_demo.sh`
- Create: `demo/macos_real_ai/stop_demo.sh`
- Create: `demo/macos_real_ai/export_latest_demo.sh`
- Create: `demo/macos_real_ai/.env.example`
- Create: `demo/macos_real_ai/tests/test_shell_safety.py`

**Interfaces:**
- `deploy_demo.sh [--preflight]` reads optional `.env` allowlist fields `DEMO_PORT`, `DEMO_BASE_URL`, `DEMO_MODEL`, `DEMO_WHEEL`.
- State file defaults to `${TMPDIR%/}/myextension-real-ai-demo-current`; tests may override only its path with `DEMO_STATE_FILE`.
- Runtime contains `server.pid` and `server.port`.
- `stop_demo.sh` exits `0` only after verified target termination or when the verified target is already stopped; unsafe/mismatched state exits non-zero without signaling.
- `export_latest_demo.sh` invokes `<runtime>/venv/bin/python verify_demo.py --log-root <runtime>/log --export --export-dir demo/macos_real_ai/exports`.

- [ ] **Step 1: Write failing shell safety tests**

Tests use temporary directories and subprocesses to assert:

1. `.env` containing `DEMO_PORT=18995` is accepted by `--preflight`, but `DEMO_API_KEY=secret` and `MALICIOUS=$(touch marker)` are rejected without creating `marker`.
2. Wrong wheel hash fails before `uv` is invoked.
3. A state file pointing outside `$TMPDIR/myextension-real-ai-demo.*` is rejected by stop/export scripts.
4. A PID whose command line does not contain the recorded runtime venv and port is never signaled.
5. Export wrapper passes only the current runtime log root and project export directory.

Use fake executables in a temporary `PATH` for `uv`, `lsof`, and Jupyter checks; do not install packages or bind ports.

- [ ] **Step 2: Run shell safety tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest -v demo.macos_real_ai.tests.test_shell_safety
```

Expected: failures because the scripts do not exist.

- [ ] **Step 3: Implement allowlisted config and deployment preflight**

Write Bash 3.2-compatible code with `set -euo pipefail`. Parse `.env` line-by-line without `source` or `eval`; accept only the four exact keys, reject `DEMO_API_KEY`, shell substitutions, backticks, embedded newlines, invalid ports, credential-bearing URLs, and non-HTTPS non-loopback Base URLs. `--preflight` checks macOS, commands, port, wheel existence/hash, and stale/current state but does not create a venv, install, open a browser, or call AI.

- [ ] **Step 4: Implement isolated install and authenticated launch**

Create runtime with `mktemp -d "${TMPDIR%/}/myextension-real-ai-demo.XXXXXX"`, store the path atomically in the state file, create named subdirectories, install fixed Jupyter versions and wheel with `uv`, and check both extension lists for enabled `myextension`. Copy `demo_notebook.ipynb` to runtime `workspace/成绩统计真实AI演示.ipynb`.

Launch the runtime Python with explicit environment directories and:

```text
-m jupyter lab
--ServerApp.root_dir=<runtime>/workspace
--ServerApp.ip=127.0.0.1
--ServerApp.port=<configured port>
--ServerApp.port_retries=0
--ServerApp.open_browser=True
```

Do not pass token/password overrides. Start the Python process as a child, write its PID and port, install `INT`/`TERM` traps that forward `SIGINT`, and wait for it.

- [ ] **Step 5: Implement exact-target stop and export wrappers**

For both scripts, resolve and validate state/runtime paths before use. Stop only when numeric PID is live and `ps -p <pid> -o command=` contains both `<runtime>/venv/bin/python` and the exact recorded `--ServerApp.port=<port>`. Send only `SIGINT`, poll for up to 10 seconds, and refuse automatic `SIGKILL`.

Export wrapper requires runtime venv Python, runtime log root, and verifier file; it never falls back to project `log/`.

- [ ] **Step 6: Run syntax and shell safety tests and verify GREEN**

Run:

```bash
bash -n demo/macos_real_ai/deploy_demo.sh
bash -n demo/macos_real_ai/stop_demo.sh
bash -n demo/macos_real_ai/export_latest_demo.sh
.venv/bin/python -m unittest -v demo.macos_real_ai.tests.test_shell_safety
```

Expected: all exit `0`; no ports bind and no network call occurs.

- [ ] **Step 7: Record Task 2 checkpoint**

Append syntax/test results and SHA-256 for the scripts, `.env.example`, and shell safety test to the verification document.

---

### Task 3: Demonstration Notebook and Operator Guide

**Files:**
- Create: `demo/macos_real_ai/demo_notebook.ipynb`
- Create: `demo/macos_real_ai/README.md`
- Test: `demo/macos_real_ai/tests/test_demo_assets.py`

**Interfaces:**
- Notebook kernelspec is Python 3 / `python3` and contains no outputs or execution counts.
- Initial code intentionally omits empty-list and range validation.
- README is the authoritative manual sequence from deployment through export and stop.

- [ ] **Step 1: Write failing asset tests**

Parse Notebook JSON and assert:

- exactly one Markdown instruction cell and three code cells;
- no `outputs`, no non-null `execution_count`, and no embedded `api_key`, token URL, or real personal data;
- initial implementation contains `def analyze_scores` but not `raise ValueError` and not `if not scores`;
- failing cell calls `analyze_scores([])`;
- final test cell checks normal, empty, and out-of-range behavior but is initially expected to fail until the function is edited.

Read README and assert it includes the exact plan values, AI configuration/clear-key steps, minimum 30-second observation, `analysis ready` success criterion, export command, stop command, paid-call warning, and prohibition on sharing real student logs.

- [ ] **Step 2: Run asset tests and verify RED**

Run:

```bash
.venv/bin/python -m unittest -v demo.macos_real_ai.tests.test_demo_assets
```

Expected: failures because the Notebook and README do not exist.

- [ ] **Step 3: Create the Notebook**

The Markdown cell contains the problem and instructs the presenter not to paste a finished answer. Code cell 1 defines a deliberately incomplete implementation for normal non-empty inputs. Code cell 2 calls `analyze_scores([])` to produce `ZeroDivisionError`. Code cell 3 contains assertions for `[100, 80, 60, 40]`, `[]`, and `[-1]`; after presenter edits cell 1, it must print `Demo tests passed`.

- [ ] **Step 4: Write the complete operator guide**

Document:

1. optional `.env` copy and non-secret edits;
2. `./deploy_demo.sh --preflight` then `./deploy_demo.sh`;
3. real provider cost warning and manual UI Base URL/model/Key entry;
4. exact scheme title, problem ID, entrypoint, statement, knowledge-point review, test confirmation, and publish actions;
5. profile selection, consent, start, Notebook error/fix/success flow, 30-second gate, and stop;
6. polling until both UI task and training record are ready;
7. `./export_latest_demo.sh` and expected archive;
8. clearing saved Key in UI before stopping;
9. `./stop_demo.sh`, retained runtime location, and manual cleanup warning;
10. troubleshooting for wheel hash, port, dependency install, partial/timeout, old UI, and absent logs.

- [ ] **Step 5: Run asset tests and verify GREEN**

Run the Task 3 unittest command plus:

```bash
.venv/bin/python -m json.tool demo/macos_real_ai/demo_notebook.ipynb >/dev/null
```

Expected: all exit `0`.

- [ ] **Step 6: Record Task 3 checkpoint**

Append asset test results and Notebook/README hashes to the verification document.

---

### Task 4: Full No-Paid Regression and Handoff

**Files:**
- Modify/complete: `docs/2026-08-04-macos-real-ai-demo-verification.md`
- Modify: `项目交接文档.md`

**Interfaces:**
- Verification document distinguishes code readiness from unexecuted real-AI acceptance.
- Handoff links to `demo/macos_real_ai/README.md` and records the current wheel hash.

- [ ] **Step 1: Run all Demo tests**

Run:

```bash
.venv/bin/python -m unittest discover -v -s demo/macos_real_ai/tests
bash -n demo/macos_real_ai/deploy_demo.sh
bash -n demo/macos_real_ai/stop_demo.sh
bash -n demo/macos_real_ai/export_latest_demo.sh
```

Expected: all pass without provider calls, browser launch, installs, or port binding.

- [ ] **Step 2: Run product regression proportional to scope**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm lint:check
PATH="$PWD/.venv/bin:$PATH" .venv/bin/jlpm test --runInBand
```

Expected: backend `604 passed`, frontend `268 passed`, lint exit `0`.

- [ ] **Step 3: Verify current wheel and sensitive-string exclusions**

Run:

```bash
shasum -a 256 dist/myextension-0.2.0-py3-none-any.whl
.venv/bin/check-wheel-contents dist/myextension-0.2.0-py3-none-any.whl
.venv/bin/python -m zipfile -t dist/myextension-0.2.0-py3-none-any.whl
rg -n "API_KEY=|api_key=.*[^<]" demo/macos_real_ai
```

Expected: recorded SHA matches, wheel checks report `OK`/`Done testing`, and the string scan finds no secret assignment (documentation may mention the field name only).

- [ ] **Step 4: Complete verification and handoff documents**

Record every command and exact result, all Demo file hashes, runtime safety guarantees, and these explicit unexecuted items: real provider calls, paid AI, browser Demo, customer deployment, Windows, push, and release. Add the Demo README link to `项目交接文档.md` without claiming real-AI success.

- [ ] **Step 5: Final self-review**

Confirm there are no unresolved placeholders, no script reads API Key, no exporter reads outside the current runtime, no stop path targets 8899, and the Demo package can be understood from README alone. Report any remaining limitation instead of widening scope.

- [ ] **Step 6: Record final no-Git checkpoint**

Run `shasum -a 256` over every created/modified Demo and documentation file and place the results in the verification document. State that no commit SHA exists because the delivery directory is not a Git repository.
