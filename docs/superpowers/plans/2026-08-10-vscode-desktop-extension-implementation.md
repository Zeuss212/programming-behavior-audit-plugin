# VS Code Desktop Programming Behavior Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and locally verify a standalone VS Code Desktop extension that lets teachers publish portable assessment plans, lets students durably capture supported Python/Notebook behavior, recovers interrupted sessions, and exports a deterministic classroom brief as `behavior-audit-vscode-0.1.0.vsix`.

**Architecture:** Add an independent `vscode-extension/` TypeScript project. The extension host owns typed domain services, append-only local storage under `ExtensionContext.globalStorageUri`, supported Python/Notebook adapters, deterministic reporting, optional AI, and a thin accessible sidebar. No Jupyter Server, local HTTP service, BAMS, or FinColab runtime dependency is introduced.

**Tech Stack:** VS Code API `^1.125.0`, TypeScript `5.9.3`, Node.js 22 build baseline, esbuild `0.28.2`, Vitest `4.1.10`, ESLint `10.8.1`, `typescript-eslint` `8.66.0`, `@vscode/test-electron` `3.1.0`, `@vscode/vsce` `3.9.2`, `@vscode/python-extension` `1.0.6`, Ajv 8, npm lockfile.

## Global Constraints

- Work only on branch `codex/vscode-extension`; do not modify, merge, or rebuild the JupyterLab 0.3.0 release.
- Source lives under `vscode-extension/`; the final local delivery lives under `deploy/vscode/release-0.1.0/`.
- The extension ID is `bluedot-ai.behavior-audit-vscode`, version `0.1.0`, with `engines.vscode` set to `^1.125.0`.
- Runtime targets are VS Code Desktop on Windows, macOS, and Linux; code-server and VS Code for Web are out of scope.
- Supported evidence sources are `.py` edits, the extension-owned Python run command, `.ipynb` edits, and stable Notebook execution summaries.
- Do not parse or store ordinary terminal commands, terminal output, environment variables, global keystrokes, or clipboard content.
- A standard paste keyboard shortcut may be classified as paste through an extension keybinding; other text changes remain ordinary edits and are never guessed to be paste.
- Runtime session data stays under `ExtensionContext.globalStorageUri`, never inside the source workspace.
- API keys stay only in `ExtensionContext.secrets`; HTTPS is mandatory except for `127.0.0.1` and `localhost`.
- Core plan, capture, recovery, finalize, brief, and export flows work without AI. Automated tests never call a paid or real AI provider.
- One VS Code instance permits only one collecting session. Closing VS Code stops new capture; reopening can recover only data already flushed locally.
- The deterministic brief never scores, ranks, disciplines, diagnoses ability/personality, or asserts knowledge mastery.
- Use test-driven development for every behavior task: observe RED, implement the minimum, observe GREEN, then commit.
- Do not publish to VS Code Marketplace, deploy BAMS/FinColab, or merge to GitHub `main` in this plan.

## Locked File Structure

```text
vscode-extension/
  package.json                    # VS Code manifest, commands, views, settings, scripts
  package-lock.json               # exact npm dependency graph
  tsconfig.json                   # strict TypeScript configuration
  tsconfig.test.json              # extension-host test compilation
  esbuild.mjs                     # single extension-host production bundle
  eslint.config.mjs               # TypeScript lint rules
  vitest.config.ts                # unit test configuration
  .vscodeignore                   # VSIX inclusion boundary
  media/activity.svg              # monochrome Activity Bar icon
  media/sidebar.css               # accessible sidebar styles
  media/sidebar.js                # CSP-safe webview client
  schemas/plan-v1.schema.json
  schemas/export-manifest-v1.schema.json
  src/extension.ts                # composition root only
  src/domain/types.ts             # stable domain types and constants
  src/domain/errors.ts            # stable error codes and AuditError
  src/domain/canonicalJson.ts      # canonical serialization and SHA-256
  src/domain/validation.ts         # Ajv-backed schema validation
  src/plans/planRepository.ts      # local plan publish/import/export
  src/storage/atomicFile.ts        # atomic JSON write helper
  src/storage/eventWriter.ts       # ordered 20-event/1-second JSONL writer
  src/storage/sessionRepository.ts # state transitions and recovery
  src/capture/workspaceIdentity.ts # non-path workspace hash
  src/capture/eventFactory.ts      # contiguous event IDs and bounded payloads
  src/capture/textCollector.ts     # TextDocument and supported paste events
  src/capture/captureController.ts # collecting lifecycle
  src/runners/pythonRunner.ts      # selected interpreter and shell-free child process
  src/notebooks/notebookCollector.ts # stable Notebook edit/execution summaries
  src/reports/briefGenerator.ts    # pure deterministic classroom brief
  src/reports/logGenerator.ts      # operation JSON and process Markdown
  src/reports/exporter.ts          # explicit export with manifest and hashes
  src/ai/aiSettings.ts             # settings and SecretStorage
  src/ai/aiClient.ts               # bounded compatible provider transport
  src/ai/sanitize.ts               # path/code/evidence request boundary
  src/ui/protocol.ts               # typed extension/webview messages
  src/ui/sidebarProvider.ts        # webview lifecycle and view models
  src/ui/statusBar.ts              # collecting status independent of sidebar
  src/commands/registerCommands.ts # command handlers and confirmations
  src/__tests__/                   # Vitest unit/contract tests matching modules
  test/integration/runTest.ts      # @vscode/test-electron launcher
  test/integration/suite/index.ts  # Mocha extension-host loader
  test/integration/suite/extension.test.ts
  test/fixtures/                   # synthetic Python, Notebook, and plan files only
  scripts/verify-vsix.mjs          # ZIP content and secret/path scanner
  scripts/accelerated-soak.mjs     # deterministic 40-minute simulated stream
  README.md                        # developer and feature documentation
deploy/vscode/release-0.1.0/
  behavior-audit-vscode-0.1.0.vsix
  SHA256SUMS
  README.md
  INSTALL.md
  demo/README.md
  demo/analyze_scores.py
docs/verification/
  2026-08-10-vscode-0.1.0-verification.md
.github/workflows/vscode-extension.yml
```

---

### Task 1: Extension Scaffold, Manifest, and Build Gate

**Files:**

- Create: `vscode-extension/package.json`
- Create: `vscode-extension/package-lock.json`
- Create: `vscode-extension/tsconfig.json`
- Create: `vscode-extension/esbuild.mjs`
- Create: `vscode-extension/eslint.config.mjs`
- Create: `vscode-extension/vitest.config.ts`
- Create: `vscode-extension/.vscodeignore`
- Create: `vscode-extension/media/activity.svg`
- Create: `vscode-extension/src/extension.ts`
- Create: `vscode-extension/src/__tests__/manifest.spec.ts`
- Modify: `.gitignore`

**Interfaces:**

- Produces: `activate(context: vscode.ExtensionContext): Promise<void>` and `deactivate(): Promise<void>`.
- Produces npm commands: `build`, `typecheck`, `lint`, `test:unit`, `test:integration`, `test`, `package`, and `verify`.
- Later tasks register services through `activate`; this task must not add business behavior.

- [ ] **Step 1: Create tool configuration and the failing manifest contract test**

Create `package.json` with these locked manifest fields and dependency groups:

```json
{
  "name": "behavior-audit-vscode",
  "displayName": "编程行为分析",
  "publisher": "bluedot-ai",
  "version": "0.1.0",
  "engines": { "vscode": "^1.125.0" },
  "main": "./dist/extension.js",
  "extensionKind": ["workspace"],
  "extensionPack": ["ms-python.python"],
  "capabilities": {
    "untrustedWorkspaces": { "supported": "limited" },
    "virtualWorkspaces": { "supported": false }
  },
  "scripts": {
    "build": "node esbuild.mjs --production",
    "typecheck": "tsc --noEmit",
    "lint": "eslint .",
    "test:unit": "vitest run",
    "test:integration": "npm run build && npm run compile:test && node dist-test/test/integration/runTest.js",
    "test": "npm run test:unit",
    "verify": "npm run lint && npm run typecheck && npm run test:unit && npm run build",
    "package": "npm run verify && vsce package --out behavior-audit-vscode-0.1.0.vsix"
  },
  "dependencies": {
    "@vscode/python-extension": "1.0.6",
    "ajv": "^8.17.1"
  },
  "devDependencies": {
    "@eslint/js": "10.0.1",
    "@types/mocha": "10.0.10",
    "@types/node": "^22.0.0",
    "@types/vscode": "1.125.0",
    "@vscode/test-electron": "3.1.0",
    "@vscode/vsce": "3.9.2",
    "esbuild": "0.28.2",
    "eslint": "10.8.1",
    "mocha": "11.8.0",
    "prettier": "3.9.6",
    "typescript": "5.9.3",
    "typescript-eslint": "8.66.0",
    "vitest": "4.1.10"
  }
}
```

In `manifest.spec.ts`, load `package.json` and assert the ID fields, version, engine, desktop-only kind, limited untrusted support, Python extension pack recommendation, `main`, and absence of `browser`/hard `extensionDependencies`. Add a second test that dynamically imports `../extension` and asserts it exports callable `activate` and `deactivate` functions; this is the RED assertion before `src/extension.ts` exists.

- [ ] **Step 2: Install the exact dependency graph and verify RED**

Run:

```bash
cd vscode-extension
npm install
npm run test:unit -- src/__tests__/manifest.spec.ts
```

Expected: manifest assertions pass, then the activation import assertion fails because `src/extension.ts` does not exist.

- [ ] **Step 3: Implement the minimal composition root and production bundler**

Create `src/extension.ts`:

```ts
import type * as vscode from 'vscode';

export async function activate(_context: vscode.ExtensionContext): Promise<void> {
  return Promise.resolve();
}

export async function deactivate(): Promise<void> {
  return Promise.resolve();
}
```

Configure strict TypeScript (`strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `moduleResolution: Node`, `target: ES2022`), esbuild with `vscode` external, and ESLint without disabling unsafe/no-floating-promise checks. Add `/vscode-extension/dist/`, `/vscode-extension/dist-test/`, `/vscode-extension/.vscode-test/`, `/vscode-extension/coverage/`, and `/vscode-extension/*.vsix` to `.gitignore`.

- [ ] **Step 4: Verify GREEN and production output**

Run:

```bash
cd vscode-extension
npm run lint
npm run typecheck
npm run test:unit -- src/__tests__/manifest.spec.ts
npm run build
test -f dist/extension.js
```

Expected: all commands exit 0 and the bundle exists without bundling a `vscode` shim.

- [ ] **Step 5: Commit the scaffold**

```bash
git add .gitignore vscode-extension
git commit -m "build: scaffold VS Code desktop extension"
```

---

### Task 2: Stable Domain Types, Validation, and Content Hashes

**Files:**

- Create: `vscode-extension/src/domain/types.ts`
- Create: `vscode-extension/src/domain/errors.ts`
- Create: `vscode-extension/src/domain/canonicalJson.ts`
- Create: `vscode-extension/src/domain/validation.ts`
- Create: `vscode-extension/schemas/plan-v1.schema.json`
- Create: `vscode-extension/schemas/export-manifest-v1.schema.json`
- Create: `vscode-extension/src/__tests__/domain.spec.ts`

**Interfaces:**

- Produces: `PublishedPlan`, `AuditEvent`, `SessionState`, `ClassroomBrief`, `ExportManifest`, and their exact version constants.
- Produces: `canonicalJson(value: JsonValue): string`, `sha256Hex(value: string | Uint8Array): string`, `validatePlan(value: unknown): PublishedPlan`, and `AuditError`.
- Consumes: Node `crypto`; no VS Code API dependency is allowed in domain files.

- [ ] **Step 1: Write failing domain and validation tests**

Use fixed timestamps and assert:

```ts
expect(canonicalJson({ b: 1, a: 2 })).toBe('{"a":2,"b":1}');
expect(sha256Hex('abc')).toBe(
  'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
);
expect(() => validatePlan({ schema_version: 1 })).toThrowError(
  expect.objectContaining({ code: 'import_invalid' })
);
expect(SESSION_STATUSES).toEqual([
  'collecting', 'interrupted', 'finalizing', 'completed', 'partial', 'abandoned'
]);
```

Also assert duplicate knowledge-point IDs, empty problem text, unknown schema versions, and mismatched `content_sha256` are rejected.

- [ ] **Step 2: Run tests to verify RED**

Run: `cd vscode-extension && npm run test:unit -- src/__tests__/domain.spec.ts`

Expected: FAIL because the domain modules do not exist.

- [ ] **Step 3: Implement the exact domain contracts**

Define these core shapes:

```ts
export type SessionStatus =
  | 'collecting' | 'interrupted' | 'finalizing'
  | 'completed' | 'partial' | 'abandoned';

export interface PublishedPlan {
  schema_version: 1;
  plan_id: string;
  version: number;
  problem_text: string;
  knowledge_points: readonly KnowledgePoint[];
  tests: readonly TestDraft[];
  published_at: string;
  content_sha256: string;
}

export interface AuditEvent {
  schema_version: 1;
  event_id: string;
  session_id: string;
  session_seq: number;
  occurred_at: string;
  monotonic_ms: number;
  kind: AuditEventKind;
  document?: DocumentRef;
  payload: Readonly<Record<string, JsonValue>>;
}
```

Lock `AuditEventKind` to `edit`, `paste_shortcut`, `save`, `document_focus`, `window_focus`, `python_run`, `notebook_edit`, `notebook_run`, and `external_terminal_activity`. Lock `AuditErrorCode` to the exact codes in design section 12. Ajv validation must use `additionalProperties: false`, length limits, unique IDs, and schema version `1`.

- [ ] **Step 4: Run domain tests and the full unit suite**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/domain.spec.ts
npm run typecheck
npm run test:unit
```

Expected: all pass; hashing the same normalized plan twice produces the same digest.

- [ ] **Step 5: Commit domain contracts**

```bash
git add vscode-extension/src/domain vscode-extension/schemas vscode-extension/src/__tests__/domain.spec.ts
git commit -m "feat: define VS Code audit domain contracts"
```

---

### Task 3: Local Teacher Plan Repository

**Files:**

- Create: `vscode-extension/src/plans/planRepository.ts`
- Create: `vscode-extension/src/__tests__/planRepository.spec.ts`
- Modify: `vscode-extension/src/domain/types.ts`

**Interfaces:**

- Consumes: `PublishedPlan`, `validatePlan`, `canonicalJson`, `sha256Hex`, and a root storage path.
- Produces:

```ts
export interface PlanRepository {
  list(): Promise<readonly PublishedPlan[]>;
  publish(input: PublishPlanInput): Promise<PublishedPlan>;
  import(bytes: Uint8Array): Promise<PublishedPlan>;
  export(planId: string, version: number): Promise<Uint8Array>;
  get(planId: string, version: number): Promise<PublishedPlan | undefined>;
}
```

- Published files are immutable at `plans/<plan_id>/v<version>.json`; a new publish increments the previous version.

- [ ] **Step 1: Write failing real-filesystem repository tests**

Use a fresh `fs.mkdtemp` directory for each test. Assert that publish creates version 1, a second publish creates version 2 without changing version 1, list ordering is newest first, export/import round-trips bytes, duplicate imported content is idempotent, and a tampered hash throws `import_invalid`.

```ts
const first = await repository.publish(input('题目一'));
const second = await repository.publish(input('题目二', first.plan_id));
expect([first.version, second.version]).toEqual([1, 2]);
expect((await repository.get(first.plan_id, 1))?.problem_text).toBe('题目一');
```

- [ ] **Step 2: Run tests to verify RED**

Run: `cd vscode-extension && npm run test:unit -- src/__tests__/planRepository.spec.ts`

Expected: FAIL because `FilePlanRepository` is missing.

- [ ] **Step 3: Implement immutable plan persistence**

Implement `FilePlanRepository` with constructor `(plansRoot: string, now: () => Date, randomId: () => string)`. Serialize only canonical JSON plus a final newline, write with exclusive creation, and validate every read. Compute `content_sha256` over the plan with `content_sha256` omitted. Never overwrite a published version; on collision reload and retry the version calculation once, then throw `storage_write_failed`.

- [ ] **Step 4: Verify plan behavior**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/planRepository.spec.ts
npm run typecheck
npm run test:unit
```

Expected: publish, immutability, import idempotency, validation, and ordering tests pass.

- [ ] **Step 5: Commit the teacher plan store**

```bash
git add vscode-extension/src/plans vscode-extension/src/domain/types.ts vscode-extension/src/__tests__/planRepository.spec.ts
git commit -m "feat: persist portable assessment plans"
```

---

### Task 4: Atomic Session Repository and Ordered Event Writer

**Files:**

- Create: `vscode-extension/src/storage/atomicFile.ts`
- Create: `vscode-extension/src/storage/eventWriter.ts`
- Create: `vscode-extension/src/storage/sessionRepository.ts`
- Create: `vscode-extension/src/__tests__/atomicFile.spec.ts`
- Create: `vscode-extension/src/__tests__/eventWriter.spec.ts`
- Create: `vscode-extension/src/__tests__/sessionRepository.spec.ts`
- Modify: `vscode-extension/src/domain/types.ts`

**Interfaces:**

- Produces `writeJsonAtomic(path, value): Promise<void>` using same-directory temp file, file sync, rename, and parent-directory sync where supported.
- Produces `OrderedEventWriter.append(event): Promise<void>`, `flush(): Promise<void>`, and `close(): Promise<void>`.
- Produces:

```ts
export interface SessionRepository {
  create(plan: PublishedPlan, workspaceId: string): Promise<SessionState>;
  append(sessionId: string, events: readonly AuditEvent[]): Promise<void>;
  transition(
    sessionId: string,
    expected: SessionStatus,
    next: SessionStatus,
    reason?: string
  ): Promise<SessionState>;
  readEvents(sessionId: string): AsyncIterable<AuditEvent>;
  writeArtifact(
    sessionId: string,
    kind: 'operation_log' | 'process_log' | 'classroom_brief' | 'ai_analysis',
    bytes: Uint8Array
  ): Promise<void>;
  readArtifact(
    sessionId: string,
    kind: 'operation_log' | 'process_log' | 'classroom_brief' | 'ai_analysis'
  ): Promise<Uint8Array | undefined>;
  findActive(workspaceId: string): Promise<SessionState | undefined>;
  get(sessionId: string): Promise<SessionState | undefined>;
}
```

- [ ] **Step 1: Write failing storage and recovery tests**

Assert atomic writes never expose partial JSON, append order remains `[1,2,3]` when promises resolve out of order, flush occurs at 20 records and at one fake-timer second, event gaps/duplicates reject with `session_sequence_invalid`, two active sessions reject with `session_conflict`, and a persisted `collecting` session loads as `interrupted` on a new repository instance.

```ts
await Promise.all([writer.append(event(1)), writer.append(event(2))]);
await writer.flush();
expect(readJsonl(path).map(row => row.session_seq)).toEqual([1, 2]);
```

- [ ] **Step 2: Run storage tests to verify RED**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/atomicFile.spec.ts src/__tests__/eventWriter.spec.ts src/__tests__/sessionRepository.spec.ts
```

Expected: FAIL because the storage modules do not exist.

- [ ] **Step 3: Implement append-only durability and state transitions**

Use a per-session promise chain; do not perform concurrent appends. Set constants exactly:

```ts
export const EVENT_BATCH_SIZE = 20;
export const EVENT_FLUSH_INTERVAL_MS = 1_000;
export const SESSION_CHECKPOINT_INTERVAL_MS = 5_000;
export const MAX_EVENT_JSON_BYTES = 64 * 1024;
export const MAX_SESSION_EVENT_BYTES = 10 * 1024 * 1024;
```

Create `plan_snapshot.json`, `session_state.json`, and `events.jsonl` before returning from `create`. Maintain `last_persisted_seq` only after append completion. Store the active-session pointer under the workspace hash. Restrict `writeArtifact` to the four declared kinds and use atomic replacement; it cannot write arbitrary paths. Recovery must never silently skip an invalid JSONL row; preserve the file and throw `storage_corrupt` with the failing line number.

- [ ] **Step 4: Verify durability and regression suite**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/atomicFile.spec.ts src/__tests__/eventWriter.spec.ts src/__tests__/sessionRepository.spec.ts
npm run typecheck
npm run test:unit
```

Expected: ordering, timed flush, capacity, state machine, crash recovery, and corruption tests pass.

- [ ] **Step 5: Commit durable session storage**

```bash
git add vscode-extension/src/storage vscode-extension/src/domain/types.ts vscode-extension/src/__tests__
git commit -m "feat: add durable local audit sessions"
```

---

### Task 5: Text Editing Capture and Session Lifecycle

**Files:**

- Create: `vscode-extension/src/capture/workspaceIdentity.ts`
- Create: `vscode-extension/src/capture/eventFactory.ts`
- Create: `vscode-extension/src/capture/textCollector.ts`
- Create: `vscode-extension/src/capture/captureController.ts`
- Create: `vscode-extension/src/__tests__/workspaceIdentity.spec.ts`
- Create: `vscode-extension/src/__tests__/eventFactory.spec.ts`
- Create: `vscode-extension/src/__tests__/textCollector.spec.ts`
- Create: `vscode-extension/src/__tests__/captureController.spec.ts`

**Interfaces:**

- Consumes: `SessionRepository`, `PublishedPlan`, VS Code document/window events, and injected clock/UUID functions.
- Produces:

```ts
export interface CaptureController {
  start(plan: PublishedPlan, consent: true): Promise<SessionState>;
  resume(sessionId: string): Promise<SessionState>;
  record(input: AuditEventInput): Promise<AuditEvent>;
  finish(outcome: 'completed' | 'partial' | 'abandoned', reason?: string): Promise<SessionState>;
  flush(): Promise<void>;
  current(): SessionState | undefined;
}
```

- Produces `TextCollector.start(controller): vscode.Disposable` and a context key `behaviorAudit.collecting` used by the supported paste shortcut.

- [ ] **Step 1: Write failing collector and lifecycle tests**

Use faked VS Code event emitters. Assert workspace IDs are SHA-256 hashes without path fragments; edit events contain counts and content hash rather than clipboard contents; relative document refs never contain the absolute workspace root; sequence IDs are contiguous; start requires literal `true` consent and a trusted workspace; finish flushes before terminal transition; write failure clears the collecting context and raises `storage_write_failed`.

```ts
emitter.fire(change('/private/course/main.py', [{ text: 'x = 1\n', rangeLength: 0 }]));
expect(recorded[0]).toMatchObject({
  kind: 'edit',
  document: { relative_uri: 'main.py', language_id: 'python' },
  payload: { inserted_chars: 6, deleted_chars: 0 }
});
expect(JSON.stringify(recorded[0])).not.toContain('/private/course');
```

Test the custom paste handler records only `inserted_chars`, `line_count`, and resulting document SHA-256; it must not read or store clipboard text itself.

- [ ] **Step 2: Run capture tests to verify RED**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/workspaceIdentity.spec.ts src/__tests__/eventFactory.spec.ts src/__tests__/textCollector.spec.ts src/__tests__/captureController.spec.ts
```

Expected: FAIL because capture modules are missing.

- [ ] **Step 3: Implement bounded supported capture**

Normalize edit changes to aggregate character counts and line deltas. Record `.py` and Notebook-cell text documents only. Register `behaviorAudit.pasteAndRecord`, and contribute `ctrl+v`/`cmd+v` keybindings with `when: behaviorAudit.collecting && editorTextFocus`; the handler invokes `editor.action.clipboardPasteAction` and marks the resulting supported change as `paste_shortcut`. Context-menu paste remains an ordinary `edit`.

Listen to `window.onDidChangeActiveTextEditor`, `window.onDidChangeWindowState`, `workspace.onDidSaveTextDocument`, and `window.onDidOpenTerminal`/terminal activity only as boolean external activity. Do not subscribe to terminal data or shell integration command lines.

- [ ] **Step 4: Verify capture and durability integration**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/captureController.spec.ts src/__tests__/textCollector.spec.ts
npm run typecheck
npm run test:unit
```

Expected: trusted start, single-session guard, edit/save/focus capture, supported paste classification, ordered persistence, flush-before-finalize, and failure-stop tests pass.

- [ ] **Step 5: Commit capture lifecycle**

```bash
git add vscode-extension/src/capture vscode-extension/src/__tests__ vscode-extension/package.json
git commit -m "feat: capture durable VS Code editing events"
```

---

### Task 6: Shell-Free Python Runner

**Files:**

- Create: `vscode-extension/src/runners/pythonRunner.ts`
- Create: `vscode-extension/src/__tests__/pythonRunner.spec.ts`
- Modify: `vscode-extension/src/domain/types.ts`
- Modify: `vscode-extension/package.json`

**Interfaces:**

- Consumes `PythonExtension.api()`, active document URI, `CaptureController`, and injectable `spawn`.
- Produces:

```ts
export interface PythonRunResult {
  readonly exitCode: number | null;
  readonly signal: NodeJS.Signals | null;
  readonly durationMs: number;
  readonly stdout: string;
  readonly stderr: string;
  readonly stdoutTruncated: boolean;
  readonly stderrTruncated: boolean;
}

export interface PythonRunner {
  run(document: vscode.TextDocument): Promise<PythonRunResult>;
}
```

- [ ] **Step 1: Write failing interpreter and process tests**

Mock the official API exactly:

```ts
const active = api.environments.getActiveEnvironmentPath(document.uri);
const resolved = await api.environments.resolveEnvironment(active);
expect(resolved?.executable.uri?.fsPath).toBe('/venv/bin/python');
```

Assert the runner saves dirty documents, rejects non-Python documents, calls `spawn(interpreter, [document.uri.fsPath], { shell: false, cwd })`, records a single `python_run` event after process close, limits each output stream to 16 KiB, reports truncation, and returns `python_interpreter_missing` when no executable URI is available. Assert logged payloads never include the interpreter absolute path or environment variables.

- [ ] **Step 2: Run runner tests to verify RED**

Run: `cd vscode-extension && npm run test:unit -- src/__tests__/pythonRunner.spec.ts`

Expected: FAIL because `VsCodePythonRunner` is missing.

- [ ] **Step 3: Implement selected-interpreter execution**

Implement:

```ts
if (!vscode.extensions.getExtension('ms-python.python')) {
  throw new AuditError('python_interpreter_missing', '未安装 Microsoft Python 扩展');
}
const python = await PythonExtension.api();
await python.ready;
const active = python.environments.getActiveEnvironmentPath(document.uri);
const resolved = await python.environments.resolveEnvironment(active);
const executable = resolved?.executable.uri?.fsPath;
```

Set `MAX_RUN_OUTPUT_BYTES = 16 * 1024` per stream and `MAX_RUN_DURATION_MS = 120_000`. Use an `AbortSignal`/timer to terminate the direct child process, report `python_run_failed`, and preserve the timeout evidence without running a shell. The command must be disabled in untrusted workspaces or without an active collecting session.

- [ ] **Step 4: Verify Python runner behavior**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/pythonRunner.spec.ts
npm run typecheck
npm run test:unit
```

Expected: interpreter, save, process argument, success/failure, timeout, truncation, path privacy, and session guard tests pass.

- [ ] **Step 5: Commit the supported Python run path**

```bash
git add vscode-extension/src/runners vscode-extension/src/domain/types.ts vscode-extension/src/__tests__/pythonRunner.spec.ts vscode-extension/package.json
git commit -m "feat: record supported Python executions"
```

---

### Task 7: Stable Notebook Edit and Execution Summaries

**Files:**

- Create: `vscode-extension/src/notebooks/notebookCollector.ts`
- Create: `vscode-extension/src/__tests__/notebookCollector.spec.ts`
- Modify: `vscode-extension/src/capture/textCollector.ts`

**Interfaces:**

- Consumes `workspace.onDidChangeNotebookDocument`, `NotebookDocumentChangeEvent.cellChanges[].executionSummary`, and `CaptureController.record`.
- Produces `NotebookCollector.start(controller): vscode.Disposable`.
- Does not use proposed VS Code APIs and does not create a Notebook controller.

- [ ] **Step 1: Write failing Notebook contract tests**

Create fake cell changes with stable summaries and assert:

```ts
cellChange.executionSummary = {
  executionOrder: 3,
  success: false,
  timing: { startTime: 1_000, endTime: 1_250 }
};
```

The collector must emit one `notebook_run` event with `success: false`, `duration_ms: 250`, notebook-relative URI, cell index, language ID, and source SHA-256. Repeating the identical summary emits no duplicate. A summary without `success` records `outcome: 'unknown'`. Text cell changes remain handled through `onDidChangeTextDocument` and must not double-count as `notebook_edit`.

- [ ] **Step 2: Run Notebook tests to verify RED**

Run: `cd vscode-extension && npm run test:unit -- src/__tests__/notebookCollector.spec.ts`

Expected: FAIL because `StableNotebookCollector` is missing.

- [ ] **Step 3: Implement stable API-only Notebook evidence**

Use `workspace.onDidChangeNotebookDocument`. For every cell change with non-undefined `executionSummary`, compute a fingerprint from notebook URI hash, cell index, execution order, success, start, and end. Keep only the latest fingerprint per cell for the active session. Output bodies are not copied; store output item count and total byte count capped at `MAX_NOTEBOOK_OUTPUT_METADATA_BYTES = 4 * 1024`.

Record structural added/removed cell counts as `notebook_edit`; detailed cell source edits continue through the text collector's `vscode-notebook-cell` documents.

- [ ] **Step 4: Verify Notebook and full unit suites**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/notebookCollector.spec.ts
npm run typecheck
npm run test:unit
```

Expected: success/failure/unknown, deduplication, structural edits, cell identity, privacy, and no-double-count tests pass.

- [ ] **Step 5: Commit Notebook capture**

```bash
git add vscode-extension/src/notebooks vscode-extension/src/capture/textCollector.ts vscode-extension/src/__tests__/notebookCollector.spec.ts
git commit -m "feat: capture stable Notebook execution evidence"
```

---

### Task 8: Deterministic Brief, Human-Readable Logs, and Export

**Files:**

- Create: `vscode-extension/src/reports/briefGenerator.ts`
- Create: `vscode-extension/src/reports/logGenerator.ts`
- Create: `vscode-extension/src/reports/exporter.ts`
- Create: `vscode-extension/src/__tests__/briefGenerator.spec.ts`
- Create: `vscode-extension/src/__tests__/logGenerator.spec.ts`
- Create: `vscode-extension/src/__tests__/exporter.spec.ts`
- Modify: `vscode-extension/src/domain/types.ts`
- Modify: `vscode-extension/schemas/export-manifest-v1.schema.json`

**Interfaces:**

- Consumes a terminal-state `SessionState`, immutable plan snapshot, and ordered `AuditEvent[]`.
- Produces:

```ts
export function generateClassroomBrief(input: BriefInput): ClassroomBrief;
export function generateOperationLog(input: ReportInput): Uint8Array;
export function generateProcessLog(input: ReportInput): Uint8Array;
export interface ReportService {
  materialize(sessionId: string): Promise<ClassroomBrief>;
}
export interface SessionExporter {
  exportSession(sessionId: string, destination: vscode.Uri): Promise<ExportManifest>;
}
```

- [ ] **Step 1: Write failing deterministic reporting tests**

Use fixed events and assert normal, partial, and abandoned briefs contain exactly these semantic categories: `session_result`, `effective_observation`, `run_statistics`, `evidence_summary`, and nullable `attention_point`. Assert no fields named `score`, `rank`, `mastery`, `ability`, or `personality` exist.

Define effective observation time as the sum of gaps between qualifying edit/save/run events while the VS Code window is focused, with each positive gap capped at 30 seconds. Assert run statistics distinguish `success`, `failure`, and `unknown`. Generate the same input twice and compare canonical bytes.

Exporter tests must assert a fresh destination contains only `plan_snapshot.json`, `operation_log.json`, `process_log.md`, `classroom_brief.json`, optional `ai_analysis.json`, and `manifest.json`; every manifest SHA-256 must match bytes on disk.

- [ ] **Step 2: Run report tests to verify RED**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/briefGenerator.spec.ts src/__tests__/logGenerator.spec.ts src/__tests__/exporter.spec.ts
```

Expected: FAIL because the report modules are missing.

- [ ] **Step 3: Implement pure reporting and explicit export**

Sort evidence by `session_seq`; reject gaps rather than silently reordering invalid data. Bound the evidence summary to 20 objective entries and 8 KiB canonical JSON. Use plain Chinese descriptions such as “记录到 2 次失败运行，随后记录到 1 次成功运行”; do not infer motivation or knowledge.

`ReportService.materialize` reads the terminal session and ordered events, generates all three local reports, and stores them through the restricted `SessionRepository.writeArtifact` method. It is idempotent so a report-write failure can be retried without changing the terminal session.

Write exports to a newly created session-named child directory under the user-selected destination. Refuse a non-empty conflicting child directory with `export_failed`; never recursively overwrite. Build `manifest.json` last with schema version, extension version, session ID, creation time, relative file names, byte sizes, and SHA-256 hashes.

- [ ] **Step 4: Verify reports and storage regression**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/briefGenerator.spec.ts src/__tests__/logGenerator.spec.ts src/__tests__/exporter.spec.ts
npm run typecheck
npm run test:unit
```

Expected: deterministic categories, effective time, run statistics, bounded evidence, forbidden-language scan, exact file set, no-overwrite, and manifest hash tests pass.

- [ ] **Step 5: Commit deterministic local reports**

```bash
git add vscode-extension/src/reports vscode-extension/src/domain/types.ts vscode-extension/schemas/export-manifest-v1.schema.json vscode-extension/src/__tests__
git commit -m "feat: generate and export classroom briefs"
```

---

### Task 9: Optional AI Settings, Sanitization, and Bounded Transport

**Files:**

- Create: `vscode-extension/src/ai/aiSettings.ts`
- Create: `vscode-extension/src/ai/sanitize.ts`
- Create: `vscode-extension/src/ai/aiClient.ts`
- Create: `vscode-extension/schemas/ai-plan-suggestion-v1.schema.json`
- Create: `vscode-extension/schemas/ai-session-analysis-v1.schema.json`
- Create: `vscode-extension/src/__tests__/aiSettings.spec.ts`
- Create: `vscode-extension/src/__tests__/sanitize.spec.ts`
- Create: `vscode-extension/src/__tests__/aiClient.spec.ts`
- Modify: `vscode-extension/package.json`

**Interfaces:**

- Consumes VS Code configuration namespace `behaviorAudit.ai`, `ExtensionContext.secrets`, and injected `fetch`/clock.
- Produces:

```ts
export interface AiSettingsService {
  getPublic(): Readonly<{ baseUrl: string; model: string; hasApiKey: boolean }>;
  saveApiKey(value: string): Promise<void>;
  clearApiKey(): Promise<void>;
  requireRuntime(): Promise<Readonly<{ baseUrl: URL; model: string; apiKey: string }>>;
}

export interface AiClient {
  suggestPlan(input: PlanSuggestionInput): Promise<PlanSuggestion>;
  analyzeSession(input: SessionAnalysisInput): Promise<SessionAnalysis>;
}
```

- [ ] **Step 1: Write failing secrets, URL, sanitizer, and retry tests**

Assert the API key is stored under exactly `behaviorAudit.ai.apiKey`, never returned from `getPublic`, and removed by `clearApiKey`. Accept `https://...`, `http://127.0.0.1/...`, and `http://localhost/...`; reject other HTTP, credentials in URLs, fragments, and non-HTTP schemes with `ai_provider_request_rejected`.

Sanitizer tests must replace workspace absolute paths with relative URIs, bound each code fragment to 32 KiB, bound evidence to 20 items, and treat code/comments/errors as quoted untrusted data. Transport tests use fake fetch responses for success, 401/403, 429, 5xx, abort timeout, invalid JSON, and length truncation. Spy on request bodies and assert no API key, absolute temp path, or raw environment variable appears.

- [ ] **Step 2: Run AI tests to verify RED**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/aiSettings.spec.ts src/__tests__/sanitize.spec.ts src/__tests__/aiClient.spec.ts
```

Expected: FAIL because AI modules are missing.

- [ ] **Step 3: Implement optional bounded AI behavior**

Add manifest settings with defaults:

```json
{
  "behaviorAudit.ai.baseUrl": {
    "type": "string",
    "default": "https://ark.cn-beijing.volces.com/api/coding/v3"
  },
  "behaviorAudit.ai.model": {
    "type": "string",
    "default": "glm-5-2-260617"
  }
}
```

Use native `fetch` and `AbortController`. Suggestions use a 60-second request timeout and one 2048-to-4096 truncation recovery. Session analysis uses a shared 180-second budget with at most three 60-second provider calls; timeout retries immediately, network/429/5xx waits two seconds while budget remains. Map failures to the stable AI codes defined in Task 2. Validate returned JSON with `ai-plan-suggestion-v1.schema.json` and `ai-session-analysis-v1.schema.json` before returning it. Do not call AI automatically from capture/finalize. The explicit analysis command writes canonical response bytes through `SessionRepository.writeArtifact(sessionId, 'ai_analysis', bytes)` only after validation succeeds.

- [ ] **Step 4: Verify AI remains optional**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/aiSettings.spec.ts src/__tests__/sanitize.spec.ts src/__tests__/aiClient.spec.ts
npm run typecheck
npm run test:unit
```

Expected: all provider/error/privacy tests pass; a missing key produces `ai_not_configured` without changing plan or session files.

- [ ] **Step 5: Commit optional AI services**

```bash
git add vscode-extension/src/ai vscode-extension/src/__tests__ vscode-extension/schemas/ai-plan-suggestion-v1.schema.json vscode-extension/schemas/ai-session-analysis-v1.schema.json vscode-extension/package.json
git commit -m "feat: add optional private AI assistance"
```

---

### Task 10: Accessible Sidebar, Status Bar, and Commands

**Files:**

- Create: `vscode-extension/src/ui/protocol.ts`
- Create: `vscode-extension/src/ui/sidebarProvider.ts`
- Create: `vscode-extension/src/ui/statusBar.ts`
- Create: `vscode-extension/src/commands/registerCommands.ts`
- Create: `vscode-extension/media/sidebar.css`
- Create: `vscode-extension/media/sidebar.js`
- Create: `vscode-extension/src/__tests__/uiProtocol.spec.ts`
- Create: `vscode-extension/src/__tests__/sidebarProvider.spec.ts`
- Create: `vscode-extension/src/__tests__/statusBar.spec.ts`
- Create: `vscode-extension/src/__tests__/commands.spec.ts`
- Modify: `vscode-extension/package.json`
- Modify: `vscode-extension/src/extension.ts`

**Interfaces:**

- Consumes all domain services through a `ServiceContainer`; the webview never receives filesystem paths or API keys.
- Produces command IDs:

```text
behaviorAudit.openTeacher
behaviorAudit.openStudent
behaviorAudit.publishPlan
behaviorAudit.suggestPlan
behaviorAudit.importPlan
behaviorAudit.exportPlan
behaviorAudit.startCapture
behaviorAudit.resumeCapture
behaviorAudit.finishCapture
behaviorAudit.abandonCapture
behaviorAudit.runPython
behaviorAudit.analyzeSession
behaviorAudit.exportSession
behaviorAudit.openDataLocation
behaviorAudit.configureAiKey
behaviorAudit.clearAiKey
behaviorAudit.pasteAndRecord
```

- Produces webview messages as a discriminated union validated by `parseWebviewMessage(value: unknown)`.

- [ ] **Step 1: Write failing UI protocol and command tests**

Assert unknown message types/extra fields reject with `import_invalid`; rendered HTML has a nonce CSP, no inline event handlers, `localResourceRoots` limited to `media`, Chinese headings for teacher/student, and a visible no-permission-isolation notice. Assert every destructive/AI command, including `suggestPlan` and `analyzeSession`, calls a confirmation prompt and stops on cancel. Assert start requires a selected published plan, consent `true`, trusted workspace, and no active session.

Status tests must show `$(record) 正在监控 · 00:12:34 · 42 个事件` and tooltip `最近保存：<time>` while collecting, display interrupted recovery state, and hide when idle.

- [ ] **Step 2: Run UI tests to verify RED**

Run:

```bash
cd vscode-extension
npm run test:unit -- src/__tests__/uiProtocol.spec.ts src/__tests__/sidebarProvider.spec.ts src/__tests__/statusBar.spec.ts src/__tests__/commands.spec.ts
```

Expected: FAIL because UI and command modules are missing.

- [ ] **Step 3: Implement thin accessible presentation and composition**

Contribute one Activity Bar container and one Webview View. Use semantic `h1`/`h2`, native buttons/inputs, labels, focus order, `aria-live="polite"` for status, and VS Code theme variables. Keep teacher and student routes in a single CSP-safe `sidebar.js`; all state arrives as typed view models. Never interpolate user text into HTML; assign it through `textContent`.

In `activate`, construct repositories from `context.globalStorageUri`, set up collectors, register commands, create status bar, and inspect `findActive(workspaceId)`. If an interrupted session exists, show exactly two non-destructive choices: continue, or end and generate a partial brief. Normal/partial/abandoned finish commands call `capture.finish(...)` and then `reportService.materialize(sessionId)`; a materialization error exposes a retry action and never reopens the terminal session. `behaviorAudit.openDataLocation` opens exactly `context.globalStorageUri` and does not accept a caller-supplied path. `deactivate` calls `capture.flush()` and disposes listeners.

- [ ] **Step 4: Verify UI, extension build, and unit regression**

Run:

```bash
cd vscode-extension
npm run lint
npm run typecheck
npm run test:unit
npm run build
```

Expected: all exit 0; manifest command/view IDs match implementation; output bundle contains no API key or absolute workspace path.

- [ ] **Step 5: Commit the complete local workflow UI**

```bash
git add vscode-extension/package.json vscode-extension/src/extension.ts vscode-extension/src/ui vscode-extension/src/commands vscode-extension/media vscode-extension/src/__tests__
git commit -m "feat: add teacher and student VS Code workflows"
```

---

### Task 11: Extension-Host Integration, Cross-Platform CI, and Accelerated Soak

**Files:**

- Create: `vscode-extension/test/integration/runTest.ts`
- Create: `vscode-extension/test/integration/suite/index.ts`
- Create: `vscode-extension/test/integration/suite/extension.test.ts`
- Create: `vscode-extension/test/fixtures/plan-v1.json`
- Create: `vscode-extension/test/fixtures/analyze_scores.py`
- Create: `vscode-extension/test/fixtures/demo.ipynb`
- Create: `vscode-extension/scripts/accelerated-soak.mjs`
- Create: `vscode-extension/tsconfig.test.json`
- Create: `.github/workflows/vscode-extension.yml`
- Modify: `vscode-extension/tsconfig.json`
- Modify: `vscode-extension/package.json`

**Interfaces:**

- Consumes the packaged composition root and only synthetic fixtures.
- Produces `npm run test:integration`, `npm run test:soak`, and the shared `npm run verify` quality gate.
- CI runs unit/build/package checks on `ubuntu-latest`, `windows-latest`, and `macos-latest`; the Linux extension-host test uses `xvfb-run`.

- [ ] **Step 1: Write failing extension-host and accelerated-soak tests**

Extension-host tests must activate `bluedot-ai.behavior-audit-vscode`, assert all command IDs exist, open synthetic `analyze_scores.py`, start a session through a test-only service seam, apply an edit and save, run the capture finalization path, and assert a classroom brief with five categories exists under a test-scoped storage URI. Reload the service container against the same storage and assert an unfinished session is reported as interrupted.

The accelerated soak script must generate the equivalent of 40 minutes of deterministic edit/save/run/focus events with a fake clock, append through the real `OrderedEventWriter`, recreate the repository halfway, finish partial, and assert continuous sequences, bounded event JSON, expected observation time, and no growth in pending queue count.

- [ ] **Step 2: Compile and run tests to verify RED**

Run:

```bash
cd vscode-extension
npm run test:integration
npm run test:soak
```

Expected: FAIL because the integration launcher/scripts and test seams are not configured.

- [ ] **Step 3: Implement official extension-host launcher and CI matrix**

Compile test files to `dist-test` without bundling `vscode`. Set `tsconfig.test.json` to `rootDir: "."`, `outDir: "dist-test"`, CommonJS output, and include only `test/integration/**/*.ts`. Add package scripts `"compile:test": "tsc -p tsconfig.test.json"` and `"test:integration": "npm run build && npm run compile:test && node dist-test/test/integration/runTest.js"`. Use `@vscode/test-electron` with VS Code `1.125.0`, a temporary user-data directory, extension development path `vscode-extension`, and workspace fixture directory. Add a narrow test seam activated only when `BEHAVIOR_AUDIT_TEST_MODE=1`; production activation must not expose it as a contributed command.

Add workflow commands:

```yaml
- run: npm ci
  working-directory: vscode-extension
- run: npm run lint
  working-directory: vscode-extension
- run: npm run typecheck
  working-directory: vscode-extension
- run: npm run test:unit
  working-directory: vscode-extension
- run: npm run build
  working-directory: vscode-extension
- run: npm run package
  working-directory: vscode-extension
```

Run `test:integration` on macOS and Windows, plus Linux under `xvfb-run`; run the accelerated soak on all three. Do not configure publishing tokens or Marketplace actions.

- [ ] **Step 4: Verify local integration and all quality gates**

Run:

```bash
cd vscode-extension
npm run test:integration
npm run test:soak
npm run verify
```

Expected: extension activation, commands, edit/save persistence, interruption recovery, brief generation, 40-minute accelerated stream, lint, types, units, and production bundle all pass.

- [ ] **Step 5: Commit integration and CI gates**

```bash
git add vscode-extension/test vscode-extension/test/fixtures vscode-extension/scripts/accelerated-soak.mjs vscode-extension/package.json vscode-extension/tsconfig.json vscode-extension/tsconfig.test.json .github/workflows/vscode-extension.yml
git commit -m "test: verify VS Code extension workflows"
```

---

### Task 12: VSIX Verification, Installation Package, and Real 40-Minute Acceptance

**Files:**

- Create: `vscode-extension/scripts/verify-vsix.mjs`
- Create: `vscode-extension/README.md`
- Create: `deploy/vscode/release-0.1.0/README.md`
- Create: `deploy/vscode/release-0.1.0/INSTALL.md`
- Create: `deploy/vscode/release-0.1.0/demo/README.md`
- Create: `deploy/vscode/release-0.1.0/demo/analyze_scores.py`
- Create: `deploy/vscode/release-0.1.0/behavior-audit-vscode-0.1.0.vsix`
- Create: `deploy/vscode/release-0.1.0/SHA256SUMS`
- Create: `docs/verification/2026-08-10-vscode-0.1.0-verification.md`
- Modify: `README.md`

**Interfaces:**

- Consumes the verified extension source and npm package command.
- Produces a self-contained local delivery folder; a recipient needs only VS Code Desktop, the Microsoft Python extension, a Python interpreter, and the files in that folder.
- Produces no Marketplace publication, BAMS/FinColab deployment, API key, or real student data.

- [ ] **Step 1: Write the failing VSIX content verifier**

`verify-vsix.mjs <vsix>` must open the ZIP and assert:

- extension manifest ID/version/engine are exact;
- `extension/dist/extension.js`, `extension/media/activity.svg`, `extension/media/sidebar.css`, `extension/media/sidebar.js`, and both schemas exist;
- source maps, test files, fixture data, `.env`, `.git`, node_modules sources, and root project paths are absent;
- text entries do not contain `ARK_API_KEY=`, `gho_`, `github_pat_`, `/Users/`, `C:\\Users\\`, or synthetic secret marker `must-not-ship-secret`;
- exactly one production extension entry point exists.

Run it against a missing VSIX first and observe a non-zero exit with `VSIX not found`.

- [ ] **Step 2: Build the VSIX and verify package RED-to-GREEN**

Run:

```bash
cd vscode-extension
npm run package
node scripts/verify-vsix.mjs behavior-audit-vscode-0.1.0.vsix
```

Expected: the first verifier run exposes any packaging leaks; tighten `.vscodeignore` and bundling until it prints `VSIX verification: OK` without weakening secret/path checks.

- [ ] **Step 3: Create the clear delivery folder and installation instructions**

Copy the verified binary into `deploy/vscode/release-0.1.0/`, calculate SHA-256, and write `SHA256SUMS` with the relative VSIX path. `INSTALL.md` must contain these exact supported installation paths:

```text
VS Code -> Extensions -> ... -> Install from VSIX...
code --install-extension behavior-audit-vscode-0.1.0.vsix
```

Document verification, Python prerequisite, trusted workspace, optional AI SecretStorage entry, teacher plan export, student import/start/run/finish/export, data location through the VS Code command “打开本地数据位置”, uninstall, reinstall, and rollback. Explicitly state that closing VS Code stops new capture and that no report is automatically sent to FinColab.

- [ ] **Step 4: Execute a real Desktop acceptance with synthetic data**

Install the VSIX into an isolated VS Code profile and use only `demo/analyze_scores.py`. Record start/end timestamps covering at least 40 wall-clock minutes. During the session perform edits, saves, one failed extension-owned Python run, one successful run, close/reopen the VS Code window once, choose resume, then finish and export.

Write actual evidence to `docs/verification/2026-08-10-vscode-0.1.0-verification.md`:

```text
VS Code version:
OS/architecture:
VSIX SHA-256:
session_id:
wall-clock start/end:
interruption/recovery result:
event sequence first/last and gap count:
brief five-category result:
export manifest verification:
```

If the 40-minute run or recovery is not executed successfully, label the state exactly `implementation complete, real soak verification incomplete` and do not claim 0.1.0 ready for classroom delivery.

- [ ] **Step 5: Run final fresh verification**

Run:

```bash
cd vscode-extension
npm ci
npm run lint
npm run typecheck
npm run test:unit
npm run test:integration
npm run test:soak
npm run build
npm run package
node scripts/verify-vsix.mjs behavior-audit-vscode-0.1.0.vsix
cd ../deploy/vscode/release-0.1.0
shasum -a 256 -c SHA256SUMS
```

Then inspect `git diff --check`, `git status --short`, the VSIX file list, and the verification record. Expected: every automated command exits 0, checksum is `OK`, real acceptance fields contain actual values, and only intended source/docs/release files are changed.

- [ ] **Step 6: Commit the verified 0.1.0 delivery**

```bash
git add README.md vscode-extension deploy/vscode/release-0.1.0 docs/verification/2026-08-10-vscode-0.1.0-verification.md
git commit -m "build: deliver VS Code extension 0.1.0"
```

Stop after the local commit and verified delivery folder. Pushing the final branch, opening/updating a PR, publishing to Marketplace, merging, and platform integration require separate authorization.
