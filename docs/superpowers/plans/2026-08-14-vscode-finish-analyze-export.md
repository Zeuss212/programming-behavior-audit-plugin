# VSCode One-Click Finish, Analyze, and Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a student finish an active local capture through one explicit command that creates the local classroom brief, optionally produces AI suggestions, and exports a portable package that always includes a safe `analysis_log.json`.

**Architecture:** Keep capture finalization and brief materialization in the command layer, add a small analysis-outcome service that persists a versioned, safe wrapper in the existing `ai_analysis` artifact, and make the exporter map that artifact to the stable public filename `analysis_log.json`. The extension wires the one-click command to the VS Code folder picker; the sidebar only owns the opt-in state and invokes typed commands.

**Tech Stack:** TypeScript 5.9, VS Code Extension API, Vitest, AJV-backed `CompatibleAiClient`, local file-session repository.

## Global Constraints

- Modify only `/Users/sxh/编程行为监控分析插件_交付版_20260727/vscode-extension` and this plan/spec documentation.
- Do not change the classroom platform frontends, BAMS/Jupyter, capture-event schema, remote services, existing session files, or any deploy artifact.
- Preserve the existing uncommitted changes in `src/ai/aiClient.ts`, `src/__tests__/aiClient.spec.ts`, and `.vscodeignore`; this implementation must not rewrite or stage them as part of this feature.
- Keep the legacy `finishCapture`, `analyzeSession`, and `exportSession` commands available as recovery paths.
- No API key, authorization header, workspace absolute path, provider response body, or arbitrary thrown-error message may be written to `analysis_log.json`, shown as a final success message, or added to a test fixture.
- A failed/disabled/cancelled optional AI step must not block exporting the already-generated local brief. Failure to finish capture, generate the local brief, or write the analysis artifact remains blocking.

---

## File Map

| File | Responsibility |
| --- | --- |
| `vscode-extension/src/reports/analysisLog.ts` (new) | Versioned analysis-outcome model, canonical serialization, legacy-artifact normalization, and safe error-to-status mapping. |
| `vscode-extension/src/reports/analysisService.ts` (new) | Reads the materialized brief, invokes the AI client when enabled, and persists one safe `ai_analysis` artifact for all outcomes. |
| `vscode-extension/src/workflows/finishAnalyzeExport.ts` (new) | Coordinates optional AI, export-folder cancellation, and export without importing VS Code. |
| `vscode-extension/src/reports/exporter.ts` | Always exports `analysis_log.json` and records its hash in the manifest. |
| `vscode-extension/src/commands/registerCommands.ts` | Adds the confirmed one-click command, finishes/materializes first, and invokes its injected post-brief workflow. |
| `vscode-extension/src/ui/protocol.ts` | Defines the one-click command and the strict `setAutoAnalyze` message. |
| `vscode-extension/src/ui/sidebarProvider.ts` | Adds accessible student controls: checked-by-default AI option, primary one-click action, and retained recovery actions. |
| `vscode-extension/media/sidebar.js` | Synchronizes the AI checkbox state with the typed webview protocol. |
| `vscode-extension/media/sidebar.css` | Gives the primary action and explanatory text adequate visual hierarchy without changing global workbench styles. |
| `vscode-extension/src/extension.ts` | Wires the analysis service, folder-picker cancellation, completion notice, test API, and post-command presentation refresh. |
| `vscode-extension/package.json` | Registers the new command title. |
| `vscode-extension/src/__tests__/analysisLog.spec.ts` (new) | Unit tests success, disabled/unconfigured, failed, malformed legacy artifact, and redaction behavior. |
| `vscode-extension/src/__tests__/exporter.spec.ts` | Verifies `analysis_log.json` is always exported and included in the manifest hash list. |
| `vscode-extension/src/__tests__/commands.spec.ts` | Verifies confirmation/cancel, terminal ordering, and post-brief workflow invocation. |
| `vscode-extension/src/__tests__/uiProtocol.spec.ts` | Verifies the new strict message/command forms and rejects injected fields. |
| `vscode-extension/src/__tests__/sidebarProvider.spec.ts` | Verifies the student UI exposes the default-on option and one-click command without inline handlers. |
| `vscode-extension/test/integration/suite/extension.test.ts` | Updates the expected command registration list and smoke-tests the narrow test API’s safe analysis artifact result. |

## Task 1: Define a Safe, Versioned Analysis Artifact (TDD)

**Files:**
- Create: `vscode-extension/src/reports/analysisLog.ts`
- Create: `vscode-extension/src/__tests__/analysisLog.spec.ts`
- Modify: `vscode-extension/src/domain/types.ts`

- [ ] **Step 1: Write failing outcome-serialization tests.**

  Cover these concrete cases:
  - `completed` contains the validated `SessionAnalysis` object and has the current session ID/timestamp.
  - explicit student opt-out and `ai_not_configured` return `skipped` with a fixed reason code, never an API-key-shaped value or absolute workspace path.
  - provider/network/timeout/invalid-response failures return `failed` with a fixed public reason code/message; an arbitrary error message containing `Bearer secret`, an `ark-...` token, and `/Users/...` must not survive serialization.
  - a historical raw `SessionAnalysis` JSON payload is normalized into a `completed` log before export, while corrupt/unknown bytes become a safe `failed` log rather than being emitted as untyped data.

- [ ] **Step 2: Run the new test to confirm it fails before implementation.**

  Run: `npm run test:unit -- analysisLog.spec.ts`

  Expected: failing module/import assertions because `analysisLog.ts` and its types do not yet exist.

- [ ] **Step 3: Add the domain constants and public types.**

  In `src/domain/types.ts`, add an explicit `ANALYSIS_LOG_SCHEMA_VERSION = 1` and `AnalysisLog` model:

  ```ts
  type AnalysisLogStatus = 'completed' | 'skipped' | 'failed';
  interface AnalysisLog {
    readonly schema_version: 1;
    readonly session_id: string;
    readonly generated_at: string;
    readonly status: AnalysisLogStatus;
    readonly analysis?: SessionAnalysis-compatible JSON;
    readonly reason?: { readonly code: string; readonly message: string };
  }
  ```

  Keep `SessionAnalysis` owned by `aiClient.ts` to avoid a circular runtime dependency; use a structural JSON-safe analysis field in the domain layer or move only its shared structural type to a domain-only module if TypeScript requires it.

- [ ] **Step 4: Implement `analysisLog.ts`.**

  Export narrowly-scoped functions:

  ```ts
  createCompletedAnalysisLog(sessionId, generatedAt, analysis): AnalysisLog
  createSkippedAnalysisLog(sessionId, generatedAt, reasonCode): AnalysisLog
  createFailedAnalysisLog(sessionId, generatedAt, error): AnalysisLog
  serializeAnalysisLog(log): Uint8Array
  normalizeAnalysisArtifact(bytes, sessionId, generatedAt): Uint8Array
  ```

  Rules:
  - serialize only canonical JSON plus trailing newline;
  - allowlist the reason codes `disabled_by_student`, `ai_not_configured`, `ai_provider_unavailable`, `ai_provider_timeout`, `ai_provider_network_error`, `ai_provider_auth_failed`, `ai_response_invalid`, and `analysis_unavailable`;
  - map every unknown error to `analysis_unavailable` with a fixed Chinese recovery hint;
  - use `AuditError.code` only as an input to that allowlist, never preserve `error.message`, `error.cause`, or a provider body;
  - detect a complete existing `AnalysisLog` by schema/version/session/status shape; otherwise parse only the legacy `SessionAnalysis` shape and wrap it as `completed`; all other bytes normalize to safe `failed` output.

- [ ] **Step 5: Re-run focused tests.**

  Run: `npm run test:unit -- analysisLog.spec.ts`

  Expected: all new safety and compatibility tests pass.

## Task 2: Persist Optional Analysis without Blocking the Local Brief (TDD)

**Files:**
- Create: `vscode-extension/src/reports/analysisService.ts`
- Modify: `vscode-extension/src/__tests__/analysisLog.spec.ts`

- [ ] **Step 1: Add failing service tests using only injected fakes.**

  Build a fake `SessionRepository` and fake `AiClient`, then assert:
  - enabled + successful AI reads `classroom_brief`, passes `sessionId`, the parsed brief, empty evidence and code-fragment arrays, then writes a completed `ai_analysis` artifact;
  - disabled writes `skipped/disabled_by_student` without invoking the AI client;
  - `ai_not_configured` writes `skipped/ai_not_configured` without rejecting;
  - timeout/provider/error writes `failed` and resolves normally;
  - no AI outcome may skip the repository write; an artifact-write failure rejects because export cannot meet its contract.

- [ ] **Step 2: Run the focused test to confirm the service is red.**

  Run: `npm run test:unit -- analysisLog.spec.ts`

  Expected: failures until `analysisService.ts` exists.

- [ ] **Step 3: Implement `FileSessionAnalysisService`.**

  Define dependency interfaces so it can be unit-tested without VS Code:

  ```ts
  interface SessionAnalysisService {
    materialize(sessionId: string, options: {
      readonly enabled: boolean;
      readonly workspaceRoot: string;
    }): Promise<AnalysisLog>;
  }
  ```

  Implementation order:
  1. Read and JSON-validate `classroom_brief`; if it is missing/corrupt, create a safe `failed/analysis_unavailable` artifact and return it.
  2. If disabled, create/persist `skipped/disabled_by_student`.
  3. Invoke `AiClient.analyzeSession` using the existing sanitizer-backed client and `workspaceRoot`; construct/persist `completed` on success.
  4. Catch only optional-AI errors and persist a safe `skipped`/`failed` outcome. Do not rethrow those errors.
  5. Write all outcomes through the existing `SessionRepository.writeArtifact(sessionId, 'ai_analysis', bytes)`.

  Do not edit `aiClient.ts`; the service is the boundary that makes its errors non-blocking and removes raw strings from exportable output.

- [ ] **Step 4: Re-run focused analysis tests.**

  Run: `npm run test:unit -- analysisLog.spec.ts`

  Expected: all service branches pass and no test asserts a raw provider error.

## Task 3: Make the Export Contract Mandatory and Backward Compatible (TDD)

**Files:**
- Modify: `vscode-extension/src/reports/exporter.ts`
- Modify: `vscode-extension/src/__tests__/exporter.spec.ts`

- [ ] **Step 1: Update exporter tests first.**

  Replace the optional raw `ai_analysis.json` expectation with three explicit cases:
  - a persisted completed outcome exports `analysis_log.json` and `manifest.files` contains its byte size/SHA-256;
  - no stored analysis artifact still exports a synthesized `skipped/analysis_unavailable` `analysis_log.json` and includes its hash;
  - a pre-feature raw AI analysis artifact is normalized to the new wrapper and exported under only `analysis_log.json`.

- [ ] **Step 2: Run the exporter test to verify red.**

  Run: `npm run test:unit -- exporter.spec.ts`

  Expected: current output is `ai_analysis.json` or omits analysis entirely, so expectations fail.

- [ ] **Step 3: Change `FileSessionExporter` only at its artifact boundary.**

  - Keep the local repository artifact name `ai_analysis` unchanged.
  - Remove the optional `['ai_analysis', 'ai_analysis.json', false]` source tuple.
  - Read `ai_analysis`, normalize it through `normalizeAnalysisArtifact`, and always append it as `analysis_log.json`.
  - If artifact bytes are absent, synthesize a fixed skipped log using the exporter’s existing `now()` and session ID.
  - Retain the `mkdir(..., recursive: false)` plus non-empty-directory refusal; never overwrite an export folder.

- [ ] **Step 4: Re-run exporter tests.**

  Run: `npm run test:unit -- exporter.spec.ts`

  Expected: all output files use the stable name, and every manifest hash matches written bytes.

## Task 4: Add the Confirmed One-Click Command (TDD)

**Files:**
- Modify: `vscode-extension/src/ui/protocol.ts`
- Modify: `vscode-extension/src/commands/registerCommands.ts`
- Modify: `vscode-extension/src/__tests__/commands.spec.ts`
- Modify: `vscode-extension/src/__tests__/uiProtocol.spec.ts`
- Modify: `vscode-extension/package.json`

- [ ] **Step 1: Write failing protocol and command tests.**

  - `parseWebviewMessage({ type: 'setAutoAnalyze', value: false })` is accepted; extra fields, non-booleans, and unknown commands are rejected.
  - `AUDIT_COMMAND_IDS`, extension manifest commands, and command registration include `behaviorAudit.finishAnalyzeExport` exactly once.
  - the new command requires confirmation; cancelling invokes neither capture finish, brief materialization, nor post-brief workflow.
  - after confirmation it invokes `capture.finish('completed')`, then `reportService.materialize(sessionId)`, then injected `finishAnalyzeExport(sessionId)` in order.
  - if brief materialization fails and the user declines retry, it must not invoke export; if retry succeeds, it continues once.

- [ ] **Step 2: Run the focused command/UI tests to verify red.**

  Run: `npm run test:unit -- commands.spec.ts uiProtocol.spec.ts`

  Expected: tests fail because the command and option message do not exist.

- [ ] **Step 3: Extend the typed protocol and contribution manifest.**

  Add `behaviorAudit.finishAnalyzeExport` to `AUDIT_COMMAND_IDS`, register it in `package.json` with the title `编程行为分析: 结束、生成简报并导出`, and add `setAutoAnalyze` to the exact-key discriminated message union. Preserve the allowlist parser; do not accept arbitrary command IDs or UI objects.

- [ ] **Step 4: Extend `registerAuditCommands` with a narrow callback.**

  Add this service dependency rather than placing workflow logic into UI code:

  ```ts
  readonly finishAnalyzeExport: (sessionId: string) => Promise<void>;
  ```

  Put the new command in `CONFIRMATION_COMMAND_IDS`. Its execution path must finish the capture, materialize the brief via `materializeWithRetry`, and then call the callback with the terminal session ID. Make `materializeWithRetry` return `false` when retry is declined, preventing a misleading export attempt without a second duplicate error notification. Existing `finishCapture` behavior remains terminal-capture then brief only.

- [ ] **Step 5: Re-run focused command/UI tests.**

  Run: `npm run test:unit -- commands.spec.ts uiProtocol.spec.ts`

  Expected: confirmation, cancellation, ordering, retry, command/manifest synchronization, and strict message parsing pass.

## Task 5: Wire the Extension and Student Sidebar (TDD)

**Files:**
- Modify: `vscode-extension/src/extension.ts`
- Modify: `vscode-extension/src/ui/sidebarProvider.ts`
- Modify: `vscode-extension/media/sidebar.js`
- Modify: `vscode-extension/media/sidebar.css`
- Modify: `vscode-extension/src/__tests__/sidebarProvider.spec.ts`
- Modify: `vscode-extension/test/integration/suite/extension.test.ts`

- [ ] **Step 1: Write the UI/smoke assertions first.**

  - Sidebar HTML has a checked `#auto-analyze` checkbox with a clear “生成 AI 建议（可选）” label, a single primary `finishAnalyzeExport` button, and retained lower-priority “仅结束并生成简报” / “仅导出上次会话” recovery buttons.
  - It keeps CSP nonce protection and no inline event handlers.
  - Integration expected command list includes the new command; test-mode API can read a persisted analysis artifact without depending on a real API key or native folder picker.

- [ ] **Step 2: Run focused UI/smoke tests to verify red.**

  Run: `npm run test:unit -- sidebarProvider.spec.ts`

  Expected: HTML assertions fail until the control and command are added.

- [ ] **Step 3: Instantiate and wire `FileSessionAnalysisService` in `extension.ts`.**

  - Create it from `sessionRepository`, `aiClient`, and `now`; it must not access VS Code directly.
  - Store `autoAnalyze = true` in extension state; process `setAutoAnalyze` in `onWebviewMessage`, then refresh state.
  - Add `autoAnalyze` to `SidebarViewModel`/posted state.
  - Make the retained manual `analyzeSession` call the same service with `enabled: true`, so it persists a safe outcome instead of leaking/throwing an optional-provider error.
  - In the new `finishAnalyzeExport(sessionId)` callback, call the analysis service using `autoAnalyze`, then open the existing folder dialog. If selection is cancelled, show a neutral notice that the brief/analysis are saved locally and that “导出上次会话” can be used later; do not delete or regenerate data.
  - If a directory is selected, call `sessionExporter.exportSession`, then show a success notice derived only from `analysisLog.status`: `AI 建议已生成`, `未生成 AI 建议（已跳过）`, or `AI 建议失败，本地简报已导出`.
  - Update `lastSessionId` when the one-click command finishes, just as the existing finish/abandon commands do.
  - Extend the test-only API with a `readAnalysisLog(sessionId)` method. It must read only the local artifact and never attempt a provider call.

- [ ] **Step 4: Update sidebar rendering and behavior.**

  - The `autoAnalyze` checkbox starts checked and sends only `{ type: 'setAutoAnalyze', value: boolean }`.
  - The primary button is visibly distinct but honors VS Code theme variables and keyboard focus; no spinner/polling loop is introduced.
  - Keep independent recovery commands visible and accurately labelled.
  - In `sidebar.js`, set checkbox state from extension state before enabling interactions and keep `aria-live` notice behavior.

- [ ] **Step 5: Run unit and extension-host smoke tests.**

  Run:
  ```bash
  npm run test:unit -- sidebarProvider.spec.ts commands.spec.ts uiProtocol.spec.ts exporter.spec.ts analysisLog.spec.ts
  npm run test:integration
  ```

  Expected: unit suite covers all branch outcomes; extension host starts, registers the command, captures/finishes a fixture session, reads brief and safe analysis artifact, and still detects interruption. The integration test does not use a real API key or invoke the native export picker.

## Task 6: Full Regression, Packaging, and Handoff

**Files:**
- Modify only if validation requires a documented command adjustment: `vscode-extension/README.md`

- [ ] **Step 1: Run the full local quality gate from the extension directory.**

  ```bash
  npm run verify
  npm run test:integration
  ```

  Expected: lint, typecheck, all unit tests, production bundle, and VS Code extension-host smoke all pass.

- [ ] **Step 2: Inspect the package contents without overwriting any existing VSIX.**

  Run `npm run package` only after selecting a fresh, explicitly named output path or after confirming the existing release artifact is allowed to change. Then run the repository’s `verify:vsix` against that new file.

- [ ] **Step 3: Review the change boundary.**

  Run:

  ```bash
  git diff --check
  git status --short
  git diff -- vscode-extension/src/reports vscode-extension/src/commands vscode-extension/src/ui vscode-extension/media/sidebar.js vscode-extension/media/sidebar.css vscode-extension/package.json vscode-extension/test
  ```

  Confirm that no pre-existing dirty AI/config/release files were changed by this task and that no secrets are present in diffs or generated artifacts.

- [ ] **Step 4: Handoff evidence.**

  Report the exact commands/results, the new command title, output file set, AI failure behavior, test coverage versus excluded live-provider/native-dialog scenarios, and the location of any newly created VSIX. Do not claim deployment or live-provider validation unless separately performed and evidenced.

## Definition of Done

- An active session can reach local brief, safe AI outcome, folder choice, and portable export from one confirmed student command.
- `analysis_log.json` is always exported and is hash-listed in `manifest.json` for completed, skipped, failed, and historical-artifact cases.
- Optional AI never blocks local report/export and no sensitive provider material is output.
- Cancellation leaves completed local artifacts intact; existing independent commands remain usable.
- `npm run verify` and `npm run test:integration` pass in the user-authorized implementation workspace.
