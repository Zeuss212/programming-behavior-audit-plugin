# VS Code Plan Wizard and AI Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a recoverable three-step teacher plan wizard with editable AI suggestions, safe OpenAI-compatible fallback behavior, and explicit publish confirmation.

**Architecture:** Add a host-owned plan draft model and `workspaceState` store, plus a dedicated Webview Panel whose messages are parsed through an exact-key protocol. Keep `PlanRepository` and the published JSON contract unchanged. Extend `CompatibleAiClient` to parse bounded provider errors, retry once only when a 400 explicitly rejects JSON response formatting, and repair safe omissions before schema validation.

**Tech Stack:** TypeScript 5.9, VS Code Extension API 1.125, Webview HTML/CSS/JavaScript, AJV 8, Vitest 4, ESLint 10, esbuild 0.28, `@vscode/vsce` 3.9.

## Global Constraints

- Modify only `vscode-extension`, its tests, its documentation, and its VSIX delivery artifacts.
- Do not modify BAMS, Fincolab, Jupyter backends, student capture scope, or the published plan JSON schema.
- Keep API Keys only in VS Code SecretStorage; never log or display authorization headers, full prompts, absolute paths, or request bodies.
- Preserve the existing command IDs `behaviorAudit.publishPlan` and `behaviorAudit.suggestPlan` as compatibility entry points.
- AI suggestions remain optional and must never publish a plan without an explicit teacher action.
- Use VS Code theme variables, keyboard-accessible controls, visible labels, and `aria-live` for asynchronous status.
- A format-related HTTP 400 may trigger exactly one request without `response_format`; all other HTTP 400 responses must not be retried automatically.
- The final package version is `0.1.1`; rollback is installation of the previous `0.1.0` VSIX.

---

## File Structure

- Create `vscode-extension/src/plans/planDraft.ts`: draft types, normalization, completeness validation, and conversion to `PublishPlanInput`.
- Create `vscode-extension/src/plans/planDraftStore.ts`: versioned `workspaceState` persistence behind a testable Memento-like interface.
- Create `vscode-extension/src/ui/planWizardProtocol.ts`: exact-key parsing for host/webview messages and serializable wizard view state.
- Create `vscode-extension/src/ui/planWizardPanel.ts`: panel lifecycle, CSP-safe HTML generation, media URI wiring, and host message dispatch.
- Create `vscode-extension/media/plan-wizard.css`: responsive VS Code-themed three-step form styling.
- Create `vscode-extension/media/plan-wizard.js`: local wizard rendering, field editing, ordering, autosave signaling, and accessible progress/status behavior.
- Modify `vscode-extension/src/ai/aiClient.ts`: bounded provider error parsing, redaction, conditional response-format fallback, explicit JSON prompt, and safe suggestion normalization.
- Modify `vscode-extension/src/extension.ts`: instantiate the draft store/panel, route both legacy commands to the wizard, invoke AI without direct publish, and publish only validated drafts.
- Modify `vscode-extension/src/ui/sidebarProvider.ts`: replace duplicate teacher actions with one clear wizard entry while retaining export.
- Modify `vscode-extension/src/ui/protocol.ts`: add the dedicated `behaviorAudit.openPlanWizard` command ID and preserve old IDs.
- Modify `vscode-extension/package.json`: add the dedicated command, update copy, and bump to `0.1.1`.
- Modify `vscode-extension/README.md`: document the three-step teacher flow, AI fallback, draft recovery, and API error behavior.
- Create tests in `vscode-extension/src/__tests__/planDraft.spec.ts`, `planDraftStore.spec.ts`, `planWizardProtocol.spec.ts`, and `planWizardPanel.spec.ts`.
- Modify tests in `vscode-extension/src/__tests__/aiClient.spec.ts`, `commands.spec.ts`, `sidebarProvider.spec.ts`, `uiProtocol.spec.ts`, and `manifest.spec.ts`.
- Create `deploy/vscode/release-0.1.1/README.md`, `INSTALL.md`, `SHA256SUMS`, and the final `behavior-audit-vscode-0.1.1.vsix` only after verification passes.

---

### Task 1: AI Provider Compatibility and Safe Error Evidence

**Files:**
- Modify: `vscode-extension/src/ai/aiClient.ts`
- Modify: `vscode-extension/src/ai/sanitize.ts`
- Modify: `vscode-extension/src/__tests__/aiClient.spec.ts`
- Modify: `vscode-extension/src/__tests__/sanitize.spec.ts`

**Interfaces:**
- Consumes: `AiRuntimeSettings`, `FetchLike`, `PlanSuggestion`, existing AJV schemas.
- Produces: `providerError(status: number, body: string): AuditError`, conditional one-time fallback behavior inside `CompatibleAiClient.request`, and a validated `PlanSuggestion` whose text fields are never empty.

- [ ] **Step 1: Add failing provider-error and fallback tests**

Add focused cases to `aiClient.spec.ts`:

```ts
it('redacts a bounded provider 400 message without exposing secrets or paths', async () => {
  const client = new CompatibleAiClient({
    runtime,
    fetch: () => Promise.resolve(new Response(JSON.stringify({
      error: {
        code: 'invalid_request_error',
        param: 'max_tokens',
        message: 'bad token sk-secret /Users/student/private.py',
      },
    }), { status: 400 })),
  });

  const error = await client.suggestPlan(planInput()).catch((reason: unknown) => reason);
  expect(error).toMatchObject({
    code: 'ai_provider_unavailable',
    message: expect.stringContaining('max_tokens'),
  });
  expect((error as Error).message).not.toContain('sk-secret');
  expect((error as Error).message).not.toContain('/Users/student/private.py');
});

it('retries once without response_format only when the provider rejects that field', async () => {
  const fetcher = vi.fn<FetchLike>()
    .mockResolvedValueOnce(new Response(JSON.stringify({
      error: { param: 'response_format', message: 'response_format is unsupported' },
    }), { status: 400 }))
    .mockResolvedValueOnce(providerResponse(planSuggestion));
  const client = new CompatibleAiClient({ runtime, fetch: fetcher });

  await expect(client.suggestPlan(planInput())).resolves.toEqual(planSuggestion);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(requestBody(fetcher, 0)).toContain('"response_format"');
  expect(requestBody(fetcher, 1)).not.toContain('"response_format"');
});

it('does not retry an unrelated HTTP 400', async () => {
  const fetcher = vi.fn<FetchLike>().mockResolvedValue(new Response(JSON.stringify({
    error: { param: 'model', message: 'model is unavailable' },
  }), { status: 400 }));
  const client = new CompatibleAiClient({ runtime, fetch: fetcher });

  await expect(client.suggestPlan(planInput())).rejects.toMatchObject({
    code: 'ai_provider_unavailable',
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
cd vscode-extension
npx vitest run src/__tests__/aiClient.spec.ts src/__tests__/sanitize.spec.ts
```

Expected: FAIL because response bodies are discarded and `response_format` is always sent.

- [ ] **Step 3: Implement bounded parsing, redaction, and conditional retry**

In `aiClient.ts`, add a provider detail parser with a 4 KiB read limit and 300-character displayed message limit:

```ts
interface ProviderFailureDetail {
  readonly param?: string;
  readonly code?: string;
  readonly type?: string;
  readonly message?: string;
  readonly rejectsResponseFormat: boolean;
}

function redactProviderText(value: string): string {
  return value
    .replace(/Bearer\s+\S+/giu, 'Bearer [REDACTED]')
    .replace(/\b(?:sk|ark)-[A-Za-z0-9_-]{8,}\b/gu, '[REDACTED]')
    .replace(/(?:[A-Za-z]:\\|\/Users\/|\/home\/)[^\s"']+/gu, '[PATH]')
    .replace(/[\u0000-\u001f\u007f]/gu, ' ')
    .slice(0, 300);
}
```

Read the failure body before throwing, detect format incompatibility only from `param`, `code`, `type`, or message tokens matching `response_format`, `json mode`, or `structured output`, and pass an `includeResponseFormat` boolean into the request-body builder. Retry once without the field only for that explicit case. Update the system prompt to include:

```ts
content: `你是课堂编程行为审计助手。生成${purpose}时只能依据引用数据，不评分、不排名、不判断能力或掌握程度。只返回一个有效 JSON 对象，不得包含 Markdown 或额外解释。`,
```

- [ ] **Step 4: Add safe omission repair before final schema validation**

Normalize plan suggestion objects before AJV validation. Only repair missing or blank textual fields; reject wrong container types:

```ts
function repairPlanSuggestion(value: unknown): unknown {
  if (!isRecord(value) || !Array.isArray(value.knowledge_points) || !Array.isArray(value.tests)) {
    return value;
  }
  return {
    ...value,
    schema_version: 1,
    knowledge_points: value.knowledge_points.map((item, index) => {
      if (!isRecord(item)) return item;
      const name = nonBlank(item.name) ?? `知识点 ${String(index + 1)}`;
      return {
        name,
        description: nonBlank(item.description) ?? `观察与“${name}”相关的代码实现与运行过程。`,
        observation_basis: nonBlank(item.observation_basis) ??
          `以“${name}”相关的代码编辑、运行结果或错误修正记录作为观察依据。`,
      };
    }),
  };
}
```

Add a test that blank `description` and missing `observation_basis` become non-empty, observable defaults, while non-array `knowledge_points` remains `ai_response_invalid`.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
cd vscode-extension
npx vitest run src/__tests__/aiClient.spec.ts src/__tests__/sanitize.spec.ts
npm run typecheck
```

Expected: all focused tests PASS and TypeScript reports no errors.

Commit:

```bash
git add vscode-extension/src/ai/aiClient.ts vscode-extension/src/ai/sanitize.ts vscode-extension/src/__tests__/aiClient.spec.ts vscode-extension/src/__tests__/sanitize.spec.ts
git commit -m "fix: harden compatible AI requests"
```

---

### Task 2: Versioned Plan Draft Model and Workspace Recovery

**Files:**
- Create: `vscode-extension/src/plans/planDraft.ts`
- Create: `vscode-extension/src/plans/planDraftStore.ts`
- Create: `vscode-extension/src/__tests__/planDraft.spec.ts`
- Create: `vscode-extension/src/__tests__/planDraftStore.spec.ts`

**Interfaces:**
- Consumes: `PublishPlanInput` and `PlanSuggestion`.
- Produces: `PlanDraft`, `emptyPlanDraft()`, `applySuggestion()`, `validateDraftForStep()`, `toPublishPlanInput()`, and `PlanDraftStore` with `load`, `save`, and `clear`.

- [ ] **Step 1: Write failing draft-domain tests**

Create tests for empty drafts, AI adoption, completeness, stable IDs, and publish conversion:

```ts
it('applies suggestions as editable reviewed fields without publishing data', () => {
  const draft = applySuggestion(emptyPlanDraft('2026-08-12T10:00:00.000Z'), planSuggestion);
  expect(draft.currentStep).toBe(2);
  expect(draft.knowledgePoints[0]).toMatchObject({
    localId: 'kp-1',
    name: '边界处理',
    needsReview: false,
  });
});

it('rejects step two when a knowledge point observation basis is blank', () => {
  const result = validateDraftForStep({
    ...emptyPlanDraft('2026-08-12T10:00:00.000Z'),
    currentStep: 2,
    problemText: '题目',
    knowledgePoints: [{
      localId: 'kp-1', name: '边界', description: '说明', observationBasis: '', needsReview: false,
    }],
  }, 3);
  expect(result).toEqual({ ok: false, field: 'knowledgePoints.0.observationBasis' });
});
```

- [ ] **Step 2: Run draft-domain tests and verify failure**

Run:

```bash
cd vscode-extension
npx vitest run src/__tests__/planDraft.spec.ts
```

Expected: FAIL because `planDraft.ts` does not exist.

- [ ] **Step 3: Implement the draft domain**

Define exact host-owned types:

```ts
export interface PlanDraftKnowledgePoint {
  readonly localId: string;
  readonly name: string;
  readonly description: string;
  readonly observationBasis: string;
  readonly needsReview: boolean;
}

export interface PlanDraft {
  readonly schemaVersion: 1;
  readonly currentStep: 1 | 2 | 3;
  readonly problemText: string;
  readonly knowledgePoints: readonly PlanDraftKnowledgePoint[];
  readonly tests: readonly PlanDraftTest[];
  readonly updatedAt: string;
}
```

Implement immutable normalization with maximum lengths matching the published-domain limits, sequential local IDs, and conversion to existing snake_case `PublishPlanInput`. `needsReview` remains internal and must not enter published JSON.

- [ ] **Step 4: Write failing workspace-store tests**

Use an in-memory Memento double:

```ts
class MemoryState {
  public value: unknown;
  get<T>(_key: string): T | undefined { return this.value as T | undefined; }
  update(_key: string, value: unknown): Promise<void> { this.value = value; return Promise.resolve(); }
}

it('restores a valid version-one draft and ignores corrupt state', async () => {
  const state = new MemoryState();
  const store = new PlanDraftStore(state, () => new Date('2026-08-12T10:00:00Z'));
  await store.save({ ...emptyPlanDraft('2026-08-12T10:00:00Z'), problemText: '长题目' });
  expect(store.load().problemText).toBe('长题目');
  state.value = { schemaVersion: 99, problemText: 'bad' };
  expect(store.load().problemText).toBe('');
});
```

- [ ] **Step 5: Implement `PlanDraftStore` and run focused tests**

Use a fixed key `behaviorAudit.planDraft.v1`, validate loaded data through `parsePlanDraft`, stamp `updatedAt` on save, and clear by writing `undefined`:

```ts
export interface DraftState {
  get<T>(key: string): T | undefined;
  update(key: string, value: unknown): PromiseLike<void>;
}

export class PlanDraftStore {
  public load(): PlanDraft;
  public save(draft: PlanDraft): Promise<void>;
  public clear(): Promise<void>;
}
```

Run:

```bash
cd vscode-extension
npx vitest run src/__tests__/planDraft.spec.ts src/__tests__/planDraftStore.spec.ts
npm run typecheck
```

Expected: PASS.

- [ ] **Step 6: Commit the draft vertical slice**

```bash
git add vscode-extension/src/plans/planDraft.ts vscode-extension/src/plans/planDraftStore.ts vscode-extension/src/__tests__/planDraft.spec.ts vscode-extension/src/__tests__/planDraftStore.spec.ts
git commit -m "feat: persist teacher plan drafts"
```

---

### Task 3: Three-Step Wizard Protocol and Webview Panel

**Files:**
- Create: `vscode-extension/src/ui/planWizardProtocol.ts`
- Create: `vscode-extension/src/ui/planWizardPanel.ts`
- Create: `vscode-extension/media/plan-wizard.css`
- Create: `vscode-extension/media/plan-wizard.js`
- Create: `vscode-extension/src/__tests__/planWizardProtocol.spec.ts`
- Create: `vscode-extension/src/__tests__/planWizardPanel.spec.ts`

**Interfaces:**
- Consumes: `PlanDraft`, local media URIs, nonce generator, and a host callback for parsed messages.
- Produces: `PlanWizardMessage`, `parsePlanWizardMessage`, `PlanWizardViewState`, `createPlanWizardHtml`, and `PlanWizardPanel` with `show`, `postState`, and `dispose`.

- [ ] **Step 1: Write exact-key protocol tests**

Cover accepted and rejected messages:

```ts
expect(parsePlanWizardMessage({
  type: 'saveDraft',
  draft: validDraft,
})).toEqual({ type: 'saveDraft', draft: validDraft });

expect(() => parsePlanWizardMessage({
  type: 'publishDraft',
  draft: validDraft,
  injected: true,
})).toThrowError(/消息格式无效/u);

expect(parsePlanWizardMessage({ type: 'requestSuggestion', problemText: '题目' }))
  .toEqual({ type: 'requestSuggestion', problemText: '题目' });
```

The protocol accepts only:

```ts
export type PlanWizardMessage =
  | { readonly type: 'ready' }
  | { readonly type: 'saveDraft'; readonly draft: PlanDraft }
  | { readonly type: 'requestSuggestion'; readonly problemText: string }
  | { readonly type: 'publishDraft'; readonly draft: PlanDraft }
  | { readonly type: 'exportPublishedPlan' }
  | { readonly type: 'closeWizard' };
```

- [ ] **Step 2: Run protocol tests and verify failure**

Run:

```bash
cd vscode-extension
npx vitest run src/__tests__/planWizardProtocol.spec.ts
```

Expected: FAIL because the protocol module does not exist.

- [ ] **Step 3: Implement protocol parsing and serializable view state**

Validate exact keys, message types, problem length, and nested drafts with `parsePlanDraft`. Define host responses:

```ts
export interface PlanWizardViewState {
  readonly draft: PlanDraft;
  readonly aiConfigured: boolean;
  readonly busy: boolean;
  readonly published?: { readonly planId: string; readonly version: number };
  readonly notice?: { readonly kind: 'info' | 'warning' | 'error'; readonly message: string };
}
```

- [ ] **Step 4: Write failing HTML/panel tests**

Verify CSP, labels, three step names, local resources, and panel reuse:

```ts
const html = createPlanWizardHtml({
  cspSource: 'vscode-resource:',
  nonce: 'nonce-1',
  styleUri: 'style.css',
  scriptUri: 'script.js',
});
expect(html).toContain('输入题目');
expect(html).toContain('确认知识点');
expect(html).toContain('确认并发布');
expect(html).toContain('<textarea');
expect(html).toContain('aria-live="polite"');
expect(html).not.toContain('<script>');
```

- [ ] **Step 5: Implement the panel shell and themed responsive page**

`PlanWizardPanel.show()` creates or reveals one panel titled `创建考核方案`, with `enableScripts: true`, `retainContextWhenHidden: true`, media-only local roots, and a nonce-based CSP. The HTML contains semantic containers for all three steps, but state and user-provided text are rendered by `plan-wizard.js` through `textContent` or form values, never through interpolated HTML.

CSS requirements:

```css
.wizard-shell { max-width: 1120px; margin: 0 auto; padding: 24px; }
.problem-input { width: 100%; min-height: 300px; resize: vertical; }
.step[hidden] { display: none; }
.wizard-actions { position: sticky; bottom: 0; background: var(--vscode-editor-background); }
@media (max-width: 720px) { .review-grid { grid-template-columns: 1fr; } }
```

JavaScript requirements:

- Render only the current step.
- Debounce `saveDraft` messages by 300 ms.
- Disable AI and publish buttons while `busy`.
- Move keyboard focus to the step heading after navigation.
- Render knowledge-point cards with editable inputs and buttons for add, delete, move up, and move down.
- Keep a status node with `role="status" aria-live="polite"`.
- Never use `innerHTML` with draft or provider-derived values.

- [ ] **Step 6: Run panel tests, lint media, and commit**

Run:

```bash
cd vscode-extension
npx vitest run src/__tests__/planWizardProtocol.spec.ts src/__tests__/planWizardPanel.spec.ts
npm run lint
npm run typecheck
```

Expected: PASS.

Commit:

```bash
git add vscode-extension/src/ui/planWizardProtocol.ts vscode-extension/src/ui/planWizardPanel.ts vscode-extension/media/plan-wizard.css vscode-extension/media/plan-wizard.js vscode-extension/src/__tests__/planWizardProtocol.spec.ts vscode-extension/src/__tests__/planWizardPanel.spec.ts
git commit -m "feat: add teacher plan wizard UI"
```

---

### Task 4: Extension Wiring, Legacy Command Compatibility, and Explicit Publish

**Files:**
- Modify: `vscode-extension/src/extension.ts`
- Modify: `vscode-extension/src/ui/protocol.ts`
- Modify: `vscode-extension/src/ui/sidebarProvider.ts`
- Modify: `vscode-extension/src/commands/registerCommands.ts`
- Modify: `vscode-extension/src/__tests__/commands.spec.ts`
- Modify: `vscode-extension/src/__tests__/sidebarProvider.spec.ts`
- Modify: `vscode-extension/src/__tests__/uiProtocol.spec.ts`

**Interfaces:**
- Consumes: `PlanWizardPanel`, `PlanDraftStore`, `CompatibleAiClient`, `PlanRepository`, existing command registration.
- Produces: the new `behaviorAudit.openPlanWizard` command; legacy publish/suggest commands opening the same wizard; host handling that saves, suggests, publishes, and exports without changing the published contract.

- [ ] **Step 1: Add failing command and sidebar tests**

Assert all three commands are registered and route to one injected action:

```ts
expect(registeredCommands).toEqual(expect.arrayContaining([
  'behaviorAudit.openPlanWizard',
  'behaviorAudit.publishPlan',
  'behaviorAudit.suggestPlan',
]));
expect(createSidebarHtml(input)).toContain('data-command="behaviorAudit.openPlanWizard"');
expect(createSidebarHtml(input)).not.toContain('>生成 AI 建议（可选）</button>');
```

Add a host-flow test proving `requestSuggestion` updates the draft but never calls `planRepository.publish`, while `publishDraft` calls it exactly once with unchanged `problem_text`, `knowledge_points`, and `tests` field names.

- [ ] **Step 2: Run command/UI tests and verify failure**

Run:

```bash
cd vscode-extension
npx vitest run src/__tests__/commands.spec.ts src/__tests__/sidebarProvider.spec.ts src/__tests__/uiProtocol.spec.ts
```

Expected: FAIL because the command and consolidated sidebar entry do not exist.

- [ ] **Step 3: Wire the draft store and panel in `activate`**

Instantiate:

```ts
const planDraftStore = new PlanDraftStore(context.workspaceState, () => new Date());
const planWizard = new PlanWizardPanel({
  createPanel: () => new VsCodePlanWizardPanelAdapter(
    vscode.window.createWebviewPanel(
      'behaviorAudit.planWizard',
      '创建考核方案',
      vscode.ViewColumn.One,
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [mediaUri] },
    ),
  ),
  mediaRoot,
  nonce,
  onMessage: handlePlanWizardMessage,
});
```

`handlePlanWizardMessage` must:

- send the restored draft on `ready`;
- validate and persist on `saveDraft`;
- call `aiClient.suggestPlan`, apply the returned suggestion, save it, and post a non-published draft on `requestSuggestion`;
- call `toPublishPlanInput`, then `planRepository.publish`, update `selectedPlan`, clear the draft only after success, refresh sidebar/status, and post `planPublished` on `publishDraft`;
- delegate to the existing save dialog on `exportPublishedPlan`;
- preserve the draft on every error and post the existing `AuditError` message plus recovery hint.

- [ ] **Step 4: Replace input-box flows with wizard entry points**

Add `behaviorAudit.openPlanWizard` to `AUDIT_COMMAND_IDS` and `package.json`. Map commands:

```ts
const openPlanWizard = async (): Promise<void> => {
  await planWizard.show({
    draft: planDraftStore.load(),
    aiConfigured: await aiSettings.isConfigured(),
    busy: false,
  });
};

const actions: CommandActions = {
  'behaviorAudit.openPlanWizard': openPlanWizard,
  'behaviorAudit.publishPlan': openPlanWizard,
  'behaviorAudit.suggestPlan': openPlanWizard,
  // existing actions unchanged
};
```

Remove the three sequential `showInputBox` calls and the direct-publish behavior from `suggestPlan`. Change the sidebar teacher actions to:

```html
<button type="button" data-command="behaviorAudit.openPlanWizard">创建考核方案</button>
<button type="button" data-command="behaviorAudit.exportPlan">导出已发布方案</button>
```

- [ ] **Step 5: Run integration-focused unit tests and full verification**

Run:

```bash
cd vscode-extension
npx vitest run src/__tests__/commands.spec.ts src/__tests__/sidebarProvider.spec.ts src/__tests__/uiProtocol.spec.ts src/__tests__/planWizardPanel.spec.ts src/__tests__/planDraft.spec.ts
npm run verify
```

Expected: all tests, lint, typecheck, and build PASS.

- [ ] **Step 6: Commit the connected wizard flow**

```bash
git add vscode-extension/src/extension.ts vscode-extension/src/ui/protocol.ts vscode-extension/src/ui/sidebarProvider.ts vscode-extension/src/commands/registerCommands.ts vscode-extension/src/__tests__/commands.spec.ts vscode-extension/src/__tests__/sidebarProvider.spec.ts vscode-extension/src/__tests__/uiProtocol.spec.ts vscode-extension/package.json
git commit -m "feat: connect explicit plan publishing workflow"
```

---

### Task 5: Versioned VSIX Delivery, Documentation, and Manual Smoke Evidence

**Files:**
- Modify: `vscode-extension/package.json`
- Modify: `vscode-extension/package-lock.json`
- Modify: `vscode-extension/README.md`
- Modify: `vscode-extension/scripts/verify-vsix.mjs`
- Create: `deploy/vscode/release-0.1.1/README.md`
- Create: `deploy/vscode/release-0.1.1/INSTALL.md`
- Create: `deploy/vscode/release-0.1.1/SHA256SUMS`
- Create: `deploy/vscode/release-0.1.1/behavior-audit-vscode-0.1.1.vsix`
- Create: `docs/verification/2026-08-12-vscode-plan-wizard-verification.md`
- Modify: `vscode-extension/src/__tests__/manifest.spec.ts`

**Interfaces:**
- Consumes: completed wizard flow and all project verification commands.
- Produces: a reproducible `0.1.1` VSIX, matching installation/demo instructions, and evidence of automated plus manual acceptance.

- [ ] **Step 1: Add failing manifest/release assertions**

Update `manifest.spec.ts` to require version `0.1.1`, the new command, and media inclusion assumptions. Update `verify-vsix.mjs` to accept or assert `behavior-audit-vscode-0.1.1.vsix` and to require these entries:

```js
const requiredEntries = [
  'extension/dist/extension.js',
  'extension/media/plan-wizard.css',
  'extension/media/plan-wizard.js',
  'extension/package.json',
  'extension/README.md',
];
```

- [ ] **Step 2: Run manifest and packaging checks to verify failure**

Run:

```bash
cd vscode-extension
npx vitest run src/__tests__/manifest.spec.ts
npm run verify:vsix
```

Expected: FAIL because the manifest and package script still identify `0.1.0`, or because the new VSIX does not exist.

- [ ] **Step 3: Bump version and synchronize scripts and documentation**

Set `package.json` and `package-lock.json` to `0.1.1`. Update scripts:

```json
"verify:vsix": "node scripts/verify-vsix.mjs behavior-audit-vscode-0.1.1.vsix",
"package": "npm run verify && vsce package --out behavior-audit-vscode-0.1.1.vsix && npm run verify:vsix"
```

Document the exact teacher demo:

1. Open a local folder.
2. Open `编程行为分析` and select `创建考核方案`.
3. Enter a multiline problem and continue.
4. Generate AI suggestions or add a knowledge point manually.
5. Review/edit every field, continue, and explicitly publish.
6. Export the published JSON for the student side.

Also document that a failed AI request preserves the draft, the provider error is redacted, and manual authoring remains available.

- [ ] **Step 4: Run the complete automated quality gate and package**

Run:

```bash
cd vscode-extension
npm run verify
npm run package
```

Expected: lint, typecheck, unit tests, build, VSIX creation, and archive verification all PASS.

- [ ] **Step 5: Install in Extension Development Host and perform manual smoke acceptance**

Use VS Code's `Run Extension` or install the local VSIX. Record these checks without using a real student identity or sensitive prompt:

- multiline problem remains after closing and reopening the wizard;
- AI suggestion appears in step two and does not publish automatically;
- every knowledge-point field can be edited, reordered, and deleted;
- blank required fields block the next step with focus on the field;
- HTTP 400 shows a redacted actionable message and preserves the draft;
- explicit publish creates version 1 and export produces a plan accepted by the existing import test;
- dark theme at 1024 px and narrow panel at 640 px remain readable and keyboard-operable.

Write command output summaries and manual results to `docs/verification/2026-08-12-vscode-plan-wizard-verification.md`. If the real AI service is not called, mark only the external-provider smoke as unverified; do not claim it passed from mocks.

- [ ] **Step 6: Copy the verified artifact and commit delivery files**

Copy the already verified VSIX into `deploy/vscode/release-0.1.1/` without rebuilding it, then generate the checksum file and compare both artifacts:

```bash
mkdir -p deploy/vscode/release-0.1.1
cp vscode-extension/behavior-audit-vscode-0.1.1.vsix deploy/vscode/release-0.1.1/behavior-audit-vscode-0.1.1.vsix
cd deploy/vscode/release-0.1.1
shasum -a 256 behavior-audit-vscode-0.1.1.vsix > SHA256SUMS
cd ../../..
shasum -a 256 vscode-extension/behavior-audit-vscode-0.1.1.vsix deploy/vscode/release-0.1.1/behavior-audit-vscode-0.1.1.vsix
```

Expected: the two SHA-256 values are identical.

Commit:

```bash
git add vscode-extension/package.json vscode-extension/package-lock.json vscode-extension/README.md vscode-extension/scripts/verify-vsix.mjs vscode-extension/src/__tests__/manifest.spec.ts deploy/vscode/release-0.1.1 docs/verification/2026-08-12-vscode-plan-wizard-verification.md
git commit -m "build: package VS Code extension 0.1.1"
```

---

## Final Verification Gate

Run from the worktree root:

```bash
git status --short
cd vscode-extension
npm run verify
npm run verify:vsix
shasum -a 256 behavior-audit-vscode-0.1.1.vsix ../deploy/vscode/release-0.1.1/behavior-audit-vscode-0.1.1.vsix
```

Expected:

- Git contains only intentionally committed changes.
- `npm run verify` exits 0.
- `npm run verify:vsix` exits 0 and confirms the wizard media files are packaged.
- Both VSIX SHA-256 values match.
- The verification document distinguishes automated evidence, manual evidence, and any external-provider check not performed.
