# Session Log Experience and Platform Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Replace the confusing training-log folder workflow with three ordered, human-readable current-session logs, clarify observation-time semantics, and package a reproducible JupyterLab 4 wheel and BLUEDOT image-integration guide.

**Architecture:** Generate immutable session log artifacts from the already validated public `training_record.json` projection. The two deterministic logs are written synchronously when a session finalizes; the AI analysis log is written only after the linked analysis job reaches `ready`. Authenticated, allowlisted Jupyter Server routes expose list/view/download operations, while the JupyterLab frontend uses the server base URL so it works behind BLUEDOT's dynamic `/notebook_<uuid>/` prefix.

**Tech Stack:** Python 3, Jupyter Server/Tornado, JSON Schema/OpenAPI, TypeScript, JupyterLab 4 Lumino widgets and rendermime, Jest, pytest, Hatch/JupyterLab extension packaging.

**Design source:** `docs/superpowers/specs/2026-08-04-session-log-experience-platform-integration-design.md`

---

## Scope contract

- Modify only session log generation, authenticated log access, sidebar log presentation, observation-time wording, release metadata, tests, and deployment documentation.
- Preserve the existing custom-dimension workflow and historical/advanced data browsing.
- Do not deploy to BLUEDOT, call a paid AI service, expose API keys, change legacy session evidence semantics, or infer that every pause is student thinking.
- Accept only when operation and process logs exist immediately after finalize, the analysis log mirrors the real job state, ready logs open in a read-only JupyterLab tab and download through the dynamic base URL, all new security checks are covered, and existing quality commands remain green.
- Stop after producing and locally verifying the 0.2.1 wheel and updated demo/deployment instructions.

## Task 1: Add secure, deterministic session log artifacts

**Files:**

- Create: `myextension/session_log_artifacts.py`
- Modify: `myextension/session_store.py`
- Modify: `myextension/session_log_service.py`
- Test: `myextension/tests/test_session_log_artifacts.py`
- Test: `myextension/tests/test_session_log_service.py`

### Step 1: Write failing artifact tests

Add tests that construct a finalized public training record and assert:

- `operation_log.json` and `process_log.md` are emitted in `<session>/logs/` before any AI result exists.
- JSON is UTF-8, two-space indented, contains only public fields, and includes edit/paste/delete/run result/error records.
- Markdown contains a readable summary, chronological timeline, behavior detail, and the label `停顿（可能包含思考）`; it must not label every idle segment simply as `思考`.
- `analysis_log.json` is absent for `queued`, `running`, `partial`, and `error`, and is emitted only for a validated `ready` result.
- all writes are atomic and resulting files use mode `0600` where supported.
- session IDs and filenames are exact allowlist values; traversal, symlinks, directories, and oversized view reads are rejected.

Run:

```bash
python -m pytest myextension/tests/test_session_log_artifacts.py myextension/tests/test_session_log_service.py -q
```

Expected: FAIL because the artifact renderer/store does not exist.

### Step 2: Implement the minimum artifact module

In `myextension/session_log_artifacts.py`:

- define the fixed ordered kinds `operation`, `process`, `analysis` and filenames `operation_log.json`, `process_log.md`, `analysis_log.json`;
- render operation JSON from validated behavior events;
- render process Markdown as session summary → timeline → behavior detail;
- render analysis JSON only from the safe stored `ready` projection, including session summary, AI analysis, teacher reviews, and integrity metadata;
- never copy secret/configuration fields or unvalidated model output;
- expose metadata needed by the API: kind, filename, media type, state, size, and generated time.

In `SessionStore`, add narrow public artifact methods that canonicalize the UUID, enforce the three-filename allowlist, reject symlinks/non-regular files, enforce root containment, use atomic replacement, and cap inline reads while allowing full streamed download.

In `SessionLogService.export_training_record`, refresh artifacts only after the training record has passed its existing validation and been stored. This existing call site runs once at finalize and again after terminal analysis, which provides the required timing without a second state machine.

### Step 3: Run focused tests

```bash
python -m pytest myextension/tests/test_session_log_artifacts.py myextension/tests/test_session_log_service.py -q
```

Expected: PASS.

## Task 2: Expose authenticated list, view, and download APIs

**Files:**

- Modify: `myextension/routes.py`
- Modify: `myextension/schema_registry.py`
- Create: `myextension/api_schemas/session-log-list-response-v1.json`
- Modify: `docs/openapi/myextension-v1.yaml`
- Test: `myextension/tests/test_routes.py`
- Test: `myextension/tests/test_schema_registry.py`
- Test: `myextension/tests/test_openapi_contract.py`

### Step 1: Write failing route and contract tests

Cover:

- `GET /myextension/sessions/{session_id}/logs` always returns the fixed order operation → process → analysis.
- operation/process are `ready` after finalize; analysis maps linked job states to `queued`, `running`, `ready`, `partial`, or `error` and does not claim a file is ready when absent.
- `GET .../logs/{kind}` returns inline UTF-8 content with the correct media type and rejects content over the view limit.
- `GET .../logs/{kind}/download` returns the complete file with safe `Content-Disposition`, `nosniff`, and `no-store` headers.
- unauthenticated requests, invalid UUIDs, unknown kinds, traversal forms, symlink targets, and missing/not-ready artifacts fail with the documented status and public error body.
- OpenAPI and JSON schemas accept real responses and reject incorrect ordering/status/fields.

Run:

```bash
python -m pytest myextension/tests/test_routes.py myextension/tests/test_schema_registry.py myextension/tests/test_openapi_contract.py -q
```

Expected: FAIL because the new routes and schemas are missing.

### Step 2: Implement handlers and contracts

Add authenticated handlers for:

```text
GET /myextension/sessions/{session_id}/logs
GET /myextension/sessions/{session_id}/logs/{kind}
GET /myextension/sessions/{session_id}/logs/{kind}/download
```

Register them before any broader session patterns. Route all resolution through the allowlisted artifact service; do not accept filesystem paths from the browser. Return the actual status from the linked job and a stable public error if a requested artifact is not ready. Update JSON schemas and OpenAPI examples in the same change.

### Step 3: Run focused tests

```bash
python -m pytest myextension/tests/test_routes.py myextension/tests/test_schema_registry.py myextension/tests/test_openapi_contract.py -q
```

Expected: PASS.

## Task 3: Build a dynamic-prefix-safe frontend log client and viewer

**Files:**

- Create: `src/services/sessionLogApi.ts`
- Create: `src/ui/sessionLogViewer.ts`
- Modify: `src/index.ts`
- Modify: `package.json`
- Test: `src/services/__tests__/sessionLogApi.spec.ts`
- Test: `src/ui/__tests__/sessionLogViewer.spec.ts`

### Step 1: Write failing frontend tests

Assert that:

- every request joins paths against `ServerConnection.ISettings.baseUrl`, including a sample `/notebook_abc/` prefix;
- list response types preserve the server's fixed order and states;
- download fetches authenticated bytes and uses an object URL rather than navigating to a root-relative link;
- opening a ready log creates or activates one stable read-only main-area widget per session/kind;
- JSON is pretty displayed in `<pre>`, Markdown uses the Jupyter rendermime registry, and no log content is inserted as unsafe HTML;
- filename, session, generated time, and a download action are visible in the viewer header.

Run:

```bash
npm test -- --runInBand src/services/__tests__/sessionLogApi.spec.ts src/ui/__tests__/sessionLogViewer.spec.ts
```

Expected: FAIL because the client and viewer do not exist.

### Step 2: Implement the API client and read-only widget

- Use `URLExt.join(settings.baseUrl, ...)` and `ServerConnection.makeRequest` for all APIs.
- Validate response shape at the client boundary and surface actionable Chinese error messages.
- Download via authenticated fetch → Blob → temporary object URL → anchor, then revoke the URL.
- Inject `IRenderMimeRegistry` during plugin activation and render Markdown through a cloned safe renderer; render JSON as parsed, two-space-indented text.
- Give widgets a stable ID derived from canonical session ID and log kind and activate an existing widget on repeat clicks.
- Add the direct JupyterLab rendermime dependency required by the compile-time token/import.

### Step 3: Run focused tests

```bash
npm test -- --runInBand src/services/__tests__/sessionLogApi.spec.ts src/ui/__tests__/sessionLogViewer.spec.ts
```

Expected: PASS.

## Task 4: Replace the sidebar folder workflow and clarify observation time

**Files:**

- Modify: `src/ui/behaviorAnalysisSidebar.ts`
- Modify: `style/base.css`
- Modify: `src/observationProgress.ts` only if a presentation type needs a compatible field; do not change measurement semantics.
- Test: `src/ui/__tests__/behaviorAnalysisSidebar.spec.ts`
- Test: `src/__tests__/observationProgress.spec.ts`

### Step 1: Write failing UX/state tests

Add assertions for:

- the section always renders three rows in order: 操作日志, 过程日志, AI 分析日志;
- finalized operation/process rows become viewable/downloadable without waiting for AI;
- AI row visibly says `正在分析…` for queued/running, becomes actionable only for ready, and shows an incomplete/retry message for partial/error;
- view/download actions call the new client/viewer, not Finder or Contents Manager;
- the old `打开日志文件夹` action is no longer the primary training-log control but remains available under advanced diagnostics;
- the progress copy says what is counted and excluded: active-page input/delete/paste/action pauses count; page-away and code-execution duration do not; execution events still appear in logs;
- threshold copy says evidence coverage is sufficient and is unrelated to log generation or AI waiting;
- accessible names, keyboard activation, disabled states, and visible focus remain correct.

Run:

```bash
npm test -- --runInBand src/ui/__tests__/behaviorAnalysisSidebar.spec.ts src/__tests__/observationProgress.spec.ts
```

Expected: FAIL on the old folder-only UI and old `有效观察时间` wording.

### Step 2: Implement the sidebar state and presentation

- Replace the primary training-log block with the three compact status rows and filename/view/download controls.
- Refresh the list immediately after finalize, after session restore, and on existing analysis poll updates; avoid adding an independent high-frequency poller.
- Keep legacy log groups and folder opening in the advanced diagnostic section for backward compatibility.
- Change the label to `行为记录时长` and add concise explanatory text that does not equate pauses with thinking.
- Preserve the custom-dimension rendering, editing, persistence, and analysis logic byte-for-byte unless a type signature must be threaded through without behavioral change.
- Add responsive and accessible styles consistent with the existing JupyterLab theme variables.

### Step 3: Run focused tests

```bash
npm test -- --runInBand src/ui/__tests__/behaviorAnalysisSidebar.spec.ts src/__tests__/observationProgress.spec.ts
```

Expected: PASS.

## Task 5: Version, platform deployment, and demo handoff

**Files:**

- Modify: `package.json`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `启动说明.md`
- Modify: `项目交接文档.md`
- Create: `docs/2026-08-04-bluedot-platform-integration.md`
- Create: `deploy/bluedot/Dockerfile`
- Modify: `demo/macos_real_ai/README.md`
- Modify: `demo/macos_real_ai/run_demo.sh`
- Modify: `demo/macos_real_ai/tests/test_run_demo.py`

### Step 1: Write failing packaging/demo assertions

Add or update tests to require release version `0.2.1`, the 0.2.1 wheel name, and a configurable expected SHA-256. Assert the demo still verifies files without making an AI call during automated tests.

Run:

```bash
python -m pytest demo/macos_real_ai/tests -q
```

Expected: FAIL while metadata and scripts still target 0.2.0.

### Step 2: Update release and deployment assets

- Bump the dynamic extension/package version to `0.2.1` without rewriting historical 0.2.0 records.
- Document the three-log behavior, status timing, viewer/download workflow, and observation-time definition.
- Provide a platform Dockerfile based on `ARG BLUEDOT_BASE_IMAGE` that installs the built wheel, enables both extensions, and defaults log persistence to `/workspace/result/behavior-audit`.
- Document secret injection for `ARK_API_KEY` and non-secret `ARK_BASE_URL`/`ARK_MODEL`; explicitly state that the key must not be baked into the image or logs.
- Explain that integration belongs in the JupyterLab base image and that PyPI Manager is only a temporary validation route because workspaces may be rebuilt.
- Explain dynamic-prefix compatibility and current role/security limitations; do not describe the plugin as multi-tenant production-ready.
- After building the wheel, record its actual SHA-256 in the demo instructions/script.

### Step 3: Run documentation and demo checks

```bash
python -m pytest demo/macos_real_ai/tests -q
rg -n "0\.2\.1|operation_log|process_log|analysis_log|JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR|ARK_API_KEY|BLUEDOT_BASE_IMAGE" README.md 启动说明.md 项目交接文档.md docs deploy demo/macos_real_ai
```

Expected: tests pass and each deployment/logging contract is discoverable.

## Task 6: Full regression, build, wheel verification, and isolated smoke test

**Files:**

- Generated: `myextension/labextension/**`
- Generated: `myextension/_version.py`
- Generated: `dist/myextension-0.2.1-*.whl`
- Modify only generated packaging outputs produced by the project's normal build commands.

### Step 1: Run the complete source quality gates

```bash
python -m pytest -q
npm test -- --runInBand
npm run lint
npm run build:prod
```

Expected: all backend/frontend tests pass, lint passes, and the production frontend build succeeds.

### Step 2: Build and inspect the wheel offline

Use the project's documented Hatch/Python build entrypoint without downloading new dependencies, then run:

```bash
python -m zipfile -l dist/myextension-0.2.1-*.whl
sha256sum dist/myextension-0.2.1-*.whl
```

Expected: wheel contains the Python package, server extension metadata, schemas, and the rebuilt JupyterLab prebuilt extension; record the exact hash in the demo files.

### Step 3: Verify source/prebuilt parity and isolated installation

- compare the generated prebuilt extension metadata/version to `package.json`;
- install the wheel into a temporary isolated environment using only local artifacts;
- confirm `jupyter server extension list` and `jupyter labextension list` recognize and enable the package;
- run an unpaid smoke flow that finalizes a local fixture session, asserts immediate operation/process files, simulates ready analysis, asserts analysis JSON, and exercises authenticated list/view/download handlers.

Expected: the isolated install is enabled and the smoke flow passes without network or paid AI calls.

### Step 4: Final artifact audit

```bash
rg -n "TODO|TBD|PLACEHOLDER|有效观察时间|打开日志文件夹" myextension src style docs README.md 启动说明.md 项目交接文档.md deploy demo/macos_real_ai
```

Expected: no unresolved implementation placeholders; any remaining old wording/folder action is confined to historical docs or the explicitly retained advanced diagnostic control.

### Step 5: Final review hardening

- keep `ready` plus a temporarily missing analysis artifact in `generating` and continue the existing bounded frontend polling until the file is ready;
- freeze operation/process bytes after their first successful finalized-session generation;
- open artifacts with no-follow file descriptors, verify the opened descriptor against the directory entry, and stream downloads in 64 KiB chunks;
- document native JSON/Markdown response media types in OpenAPI;
- reject Demo exports whose human-facing logs have a mismatched session, event sequence, analysis state, or required Markdown structure;
- coalesce concurrent viewer opens and expose download failures through a visible `aria-live` status.

Expected: the review regression tests fail on the pre-hardening implementation, pass after the minimum changes, and remain green in the final full suite and isolated wheel smoke.

Record in the handoff:

- exact commands and pass counts;
- wheel absolute path, version, and SHA-256;
- files changed and generated;
- any unverified BLUEDOT-specific step;
- explicit stop point: local package ready, not deployed or published.
