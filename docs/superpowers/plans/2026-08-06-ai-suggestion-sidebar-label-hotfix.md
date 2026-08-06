# AI Suggestion and Sidebar Label Hotfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make adopted AI knowledge-point suggestions immediately confirmable, show exact missing observation fields when teachers clear them, render the left “行为分析” label upright from top to bottom, and ship a newly verified `0.2.1` delivery artifact and preview.

**Architecture:** Normalize the three runtime-only evidence fields at the single AI-adoption boundary while keeping publication validation strict. Reuse one pure missing-field locator in validation and rendering so the global error, expanded advanced section, field error text, and ARIA state cannot drift. Add one Lumino title class and a JupyterLab-left-sidebar-scoped CSS override, then rebuild one wheel and copy it byte-for-byte to both delivery directories.

**Tech Stack:** TypeScript 5.5, Jest 29/jsdom, Lumino/JupyterLab 4, CSS/stylelint, Python 3.10+, pytest, Hatch/Jupyter Builder, uv wheel build, POSIX shell, local JupyterLab preview.

## Global Constraints

- Package version remains exactly `0.2.1`; the new artifact is identified by its new SHA-256 and hotfix delivery commit/tag.
- Preserve valid Provider values after trimming; only a missing, non-string, or whitespace-only evidence field receives `defaultEvidence(name)`.
- Keep Profile v2, backend schema, confirmation hashes, assessment tests, and publication validation strict.
- Keep “行为分析” as the visible label and “编程行为分析” as the caption; display the Chinese characters upright from top to bottom.
- Scope CSS to the left, vertical JupyterLab activity bar and `jp-BehaviorAudit-sidebarTab`; do not affect any other tab or the right sidebar.
- Do not alter analysis algorithms, the default 120-second whole-analysis budget, the 60-second Provider-call ceiling, retry behavior, AI prompts, or Provider contracts.
- Do not call real or paid AI, build or push a Docker image, log in to a registry, deploy BLUEDOT, or stop the existing mixed preview on port `18995`.
- Preserve the pre-existing untracked `myextension-0.2.1-BLUEDOT-完整交付包.zip`; create a separately named hotfix archive instead of overwriting it.
- Every implementation task follows RED → minimal GREEN → focused regression → commit. Full verification and artifact synchronization happen only after the source tasks are green.

---

## File Map

**Modify**

- `src/ui/assessmentPlanForm.ts`: normalize adopted evidence fields, expose required-field labels, and produce precise first-error validation.
- `src/__tests__/assessmentPlanForm.spec.ts`: cover malformed runtime AI fields, preservation of valid fields, and exact missing-field messages.
- `src/ui/knowledgePointStep.ts`: render field-level errors, `aria-invalid`, and automatically open invalid advanced observation settings.
- `src/__tests__/assessmentPlanSteps.spec.ts`: verify advanced-section expansion and accessible error state.
- `src/ui/behaviorAnalysisSidebar.ts`: attach the plugin-specific Lumino title class.
- `src/__tests__/behaviorAnalysisSidebar.spec.ts`: verify title metadata and the static scoped CSS contract.
- `style/base.css`: override the left vertical label transform only for this plugin.
- `myextension/tests/test_labextension_artifact.py`: require the new tab-class marker in the compiled delivery extension.
- `deploy/bluedot/release-0.2.1/README.md`: identify the UI hotfix and its new wheel digest.
- `deploy/bluedot/release-0.2.1/SHA256SUMS`: record the new wheel digest.
- `deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl`: replace with the wheel built from this hotfix.
- `myextension-0.2.1-BLUEDOT-完整交付包/README.md`: remain byte-identical to the release README.
- `myextension-0.2.1-BLUEDOT-完整交付包/00_从这里开始.md`: identify this folder as the latest UI-hotfix delivery.
- `myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json`: record the code commit, new wheel digest, actual verification counts, and remaining external checks.
- `myextension-0.2.1-BLUEDOT-完整交付包/SHA256SUMS`: record the same wheel digest.
- `myextension-0.2.1-BLUEDOT-完整交付包/artifacts/myextension-0.2.1-py3-none-any.whl`: remain byte-identical to the release wheel.

**Create**

- `docs/2026-08-06-ai-suggestion-sidebar-label-hotfix-verification.md`: reproducible RED/GREEN, full-suite, artifact, isolated-install, and preview evidence.
- `myextension-0.2.1-BLUEDOT-完整交付包-20260806-ui-hotfix.zip`: new archive; the old untracked archive remains untouched.
- `myextension-0.2.1-BLUEDOT-完整交付包-20260806-ui-hotfix.zip.sha256`: transfer checksum for the new archive.

**Generated but ignored**

- `lib/`, `myextension/labextension/`, `dist/myextension-0.2.1-py3-none-any.whl`: local production-build outputs used to construct and verify the tracked wheel.
- `/private/tmp/myextension-ui-hotfix-preview.*`: isolated wheel install and preview workspace; never committed.

---

### Task 1: Normalize AI evidence and locate strict validation failures

**Files:**
- Modify: `src/ui/assessmentPlanForm.ts:118-128,215-253,457-484`
- Modify: `src/__tests__/assessmentPlanForm.spec.ts:1-134,290-300`

**Interfaces:**
- Consumes: `IKnowledgePointSuggestion` at runtime, including values that may violate its compile-time string fields because of stale frontends, old drafts, or Provider compatibility paths.
- Produces: `KnowledgePointRequiredField`, `KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS`, and `missingKnowledgePointFields(point): KnowledgePointRequiredField[]`.
- Preserves: duplicate ID/name rejection, ten-point cap, teacher points, AI source markers, confirmation invalidation, and trimmed valid Provider values.

- [ ] **Step 1: Write failing tests for malformed runtime suggestions and precise validation**

Extend the existing merge test so valid Provider evidence values are asserted exactly, then add:

```typescript
it('fills only invalid AI evidence fields with deterministic defaults', async () => {
  const runtimeSuggestion = {
    id: 'KP_B1C2D3E4',
    name: '空序列处理',
    description: '先判断列表是否为空。',
    evidence_question: '   ',
    support_statement: undefined,
    exclusion_statement: 7,
    source: 'ai_suggestion',
    order: 0
  } as unknown as Parameters<typeof mergeKnowledgeSuggestions>[1][number];

  const merged = mergeKnowledgeSuggestions(baseState(), [runtimeSuggestion]);

  expect(merged.knowledgePoints[0]).toMatchObject({
    evidenceQuestion: '学生是否通过代码、运行和修改过程正确应用“空序列处理”？',
    supportStatement: '代码与验证过程显示学生正确应用了“空序列处理”。',
    exclusionStatement:
      '只出现一次偶然正确输出，或缺少与“空序列处理”相关的验证，不计入。'
  });
  expect(validateAssessmentPlanState(merged)).not.toHaveProperty(
    'knowledgePoints'
  );
  await expect(confirmKnowledgePoints(merged, subtle)).resolves.toMatchObject({
    confirmations: {
      knowledge_points_hash: expect.stringMatching(/^[0-9a-f]{64}$/)
    }
  });
});

it('reports the first incomplete point and its exact missing fields', () => {
  const incomplete = updateKnowledgePoint(withPoint(), 'KP_A1B2C3D4', {
    supportStatement: ' ',
    exclusionStatement: ''
  });

  expect(validateAssessmentPlanState(incomplete)).toMatchObject({
    knowledgePoints: '知识点 1 缺少：支持表现、排除情况'
  });
});
```

Change the existing incomplete-evidence expectation from the generic sentence to `知识点 1 缺少：过程观察问题`.

Also extend the existing valid suggestion assertion with the exact Provider values:

```typescript
expect(merged.knowledgePoints[1]).toMatchObject({
  evidenceQuestion: '是否正确完成平均值计算？',
  supportStatement: '使用多个样例验证平均值。',
  exclusionStatement: '固定输出单个结果不计入。'
});
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/assessmentPlanForm.spec.ts --runInBand
```

Expected: the runtime case throws on a non-string `.trim()` or stores blanks, and the validation assertions receive the old generic message.

- [ ] **Step 3: Add the pure missing-field contract and defensive normalizer**

Add beside `defaultEvidence`:

```typescript
export type KnowledgePointRequiredField =
  | 'name'
  | 'evidenceQuestion'
  | 'supportStatement'
  | 'exclusionStatement';

export const KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS: Record<
  KnowledgePointRequiredField,
  string
> = {
  name: '知识点名称',
  evidenceQuestion: '过程观察问题',
  supportStatement: '支持表现',
  exclusionStatement: '排除情况'
};

export function missingKnowledgePointFields(
  point: IAssessmentKnowledgePointEditor
): KnowledgePointRequiredField[] {
  return (
    Object.keys(KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS) as KnowledgePointRequiredField[]
  ).filter(field => !point[field].trim());
}

function normalizedSuggestionText(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}
```

Inside `mergeKnowledgeSuggestions`, calculate `const evidence = defaultEvidence(name);` before `added.push`, then replace the three direct `.trim()` calls with:

```typescript
evidenceQuestion: normalizedSuggestionText(
  suggestion.evidence_question,
  evidence.evidenceQuestion
),
supportStatement: normalizedSuggestionText(
  suggestion.support_statement,
  evidence.supportStatement
),
exclusionStatement: normalizedSuggestionText(
  suggestion.exclusion_statement,
  evidence.exclusionStatement
)
```

Replace the generic `some(...)` validation branch with:

```typescript
const incompletePointIndex = state.knowledgePoints.findIndex(
  point => missingKnowledgePointFields(point).length > 0
);
if (state.knowledgePoints.length === 0) {
  errors.knowledgePoints = '请至少确认一个知识点';
} else if (incompletePointIndex >= 0) {
  const labels = missingKnowledgePointFields(
    state.knowledgePoints[incompletePointIndex]
  ).map(field => KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS[field]);
  errors.knowledgePoints = `知识点 ${incompletePointIndex + 1} 缺少：${labels.join('、')}`;
}
```

- [ ] **Step 4: Run focused and adjacent state tests and confirm GREEN**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/assessmentPlanForm.spec.ts src/__tests__/assessmentPlanEditor.spec.ts --runInBand
```

Expected: both suites pass; valid AI values remain unchanged after trimming, malformed hidden values receive deterministic defaults, and publication remains strict after a teacher clears a field.

- [ ] **Step 5: Commit the state-layer hotfix**

```bash
git add src/ui/assessmentPlanForm.ts src/__tests__/assessmentPlanForm.spec.ts
git commit -m "fix: normalize adopted AI knowledge evidence"
```

---

### Task 2: Expose hidden observation errors accessibly

**Files:**
- Modify: `src/ui/knowledgePointStep.ts:1-129`
- Modify: `src/__tests__/assessmentPlanSteps.spec.ts:1-170`

**Interfaces:**
- Consumes: `missingKnowledgePointFields(point)` and `KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS` from Task 1.
- Produces: one inline error per missing required field, `aria-invalid="true"` on that field, and `details.open = true` when any hidden observation field is missing.
- Preserves: native `<details>` behavior, labels, focus order, callbacks, source badge, and editing semantics.

- [ ] **Step 1: Write the failing rendered-state test**

Import `updateKnowledgePoint` in `assessmentPlanSteps.spec.ts` and add:

```typescript
it('opens advanced settings and identifies every missing observation field', () => {
  const content = document.createElement('div');
  const state = updateKnowledgePoint(withPoint(), 'KP_A1B2C3D4', {
    supportStatement: '',
    exclusionStatement: '   '
  });

  renderKnowledgePointStep(
    content,
    'synthetic-invalid-knowledge',
    state,
    [],
    { status: 'idle' },
    {
      onAdoptSuggestion: jest.fn(),
      onIgnoreSuggestion: jest.fn(),
      onAddPoint: jest.fn(),
      onUpdatePoint: jest.fn(),
      onRemovePoint: jest.fn(),
      onMovePoint: jest.fn(),
      onRequestSuggestions: jest.fn(),
      onBack: jest.fn(),
      onConfirm: jest.fn()
    }
  );

  expect(
    content.querySelector<HTMLDetailsElement>(
      'details.jp-BehaviorAudit-advancedSettings'
    )?.open
  ).toBe(true);
  expect(
    inputByLabel(content, '支持表现').getAttribute('aria-invalid')
  ).toBe('true');
  expect(
    inputByLabel(content, '排除情况').getAttribute('aria-invalid')
  ).toBe('true');
  expect(
    inputByLabel(content, '过程观察问题').hasAttribute('aria-invalid')
  ).toBe(false);
  expect(content.textContent).toContain('请填写支持表现');
  expect(content.textContent).toContain('请填写排除情况');
});
```

- [ ] **Step 2: Run the step suite and confirm RED**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/assessmentPlanSteps.spec.ts --runInBand
```

Expected: `<details>` remains closed, the blank textareas lack `aria-invalid`, and no inline error text is rendered.

- [ ] **Step 3: Implement one renderer helper backed by the shared missing-field set**

Import the Task 1 exports, then add above `pointCard`:

```typescript
function showRequiredFieldError(
  field: HTMLInputElement | HTMLTextAreaElement,
  error: HTMLElement,
  label: string,
  isMissing: boolean
): void {
  if (!isMissing) return;
  field.setAttribute('aria-invalid', 'true');
  error.textContent = `请填写${label}`;
}
```

At the start of `pointCard`, create:

```typescript
const missing = new Set(missingKnowledgePointFields(point));
```

After assigning each field value, call `showRequiredFieldError` with the matching field key and shared Chinese label. After creating `advanced`, set:

```typescript
showRequiredFieldError(
  name.input,
  name.error,
  KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS.name,
  missing.has('name')
);
showRequiredFieldError(
  evidenceQuestion.textarea,
  evidenceQuestion.error,
  KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS.evidenceQuestion,
  missing.has('evidenceQuestion')
);
showRequiredFieldError(
  support.textarea,
  support.error,
  KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS.supportStatement,
  missing.has('supportStatement')
);
showRequiredFieldError(
  exclusion.textarea,
  exclusion.error,
  KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS.exclusionStatement,
  missing.has('exclusionStatement')
);

advanced.open =
  missing.has('evidenceQuestion') ||
  missing.has('supportStatement') ||
  missing.has('exclusionStatement');
```

Do not add a second validation table or duplicate the required-field labels in this file.

- [ ] **Step 4: Run both UI and state regression suites and confirm GREEN**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/assessmentPlanSteps.spec.ts src/__tests__/assessmentPlanForm.spec.ts --runInBand
```

Expected: existing complete cards keep advanced settings closed, invalid cards open automatically, and all field/global messages agree.

- [ ] **Step 5: Commit the accessible error presentation**

```bash
git add src/ui/knowledgePointStep.ts src/__tests__/assessmentPlanSteps.spec.ts
git commit -m "fix: reveal incomplete knowledge observation fields"
```

---

### Task 3: Render the plugin activity-bar label upright

**Files:**
- Modify: `src/ui/behaviorAnalysisSidebar.ts:412-419`
- Modify: `src/__tests__/behaviorAnalysisSidebar.spec.ts:1-20,892-903`
- Modify: `style/base.css:739`

**Interfaces:**
- Produces: Lumino title class `jp-BehaviorAudit-sidebarTab` and one scoped CSS selector.
- Consumes: JupyterLab 4 DOM classes `.jp-SideBar.lm-TabBar.jp-mod-left`, `[data-orientation='vertical']`, `.lm-TabBar-tab`, and `.lm-TabBar-tabLabel`.
- Preserves: `inspectorIcon`, visible label `行为分析`, caption `编程行为分析`, all other tabs, and the right sidebar.

- [ ] **Step 1: Write failing title-class and computed-style behavior tests**

Import `readFileSync` from `node:fs`. Rename the existing icon test to describe upright label metadata and add these assertions:

```typescript
expect(sidebar.title.label).toBe('行为分析');
expect(sidebar.title.caption).toBe('编程行为分析');
expect(sidebar.title.className.split(/\s+/)).toContain(
  'jp-BehaviorAudit-sidebarTab'
);
```

Add a computed-style test. It first injects the two relevant JupyterLab defaults, then the plugin stylesheet, so it proves both the override and its isolation from an ordinary tab:

```typescript
it('keeps this left Chinese tab upright without changing other tabs', () => {
  const style = document.createElement('style');
  style.textContent = `
    .jp-SideBar.lm-TabBar[data-orientation='vertical'] .lm-TabBar-tabLabel {
      writing-mode: vertical-rl;
    }
    .jp-SideBar.lm-TabBar.jp-mod-left .lm-TabBar-tabLabel {
      transform: rotate(180deg);
    }
    ${readFileSync('style/base.css', 'utf8')}
  `;
  const sideBar = document.createElement('div');
  sideBar.className = 'jp-SideBar lm-TabBar jp-mod-left';
  sideBar.dataset.orientation = 'vertical';
  const pluginTab = document.createElement('div');
  pluginTab.className = 'lm-TabBar-tab jp-BehaviorAudit-sidebarTab';
  const pluginLabel = document.createElement('div');
  pluginLabel.className = 'lm-TabBar-tabLabel';
  const otherTab = document.createElement('div');
  otherTab.className = 'lm-TabBar-tab';
  const otherLabel = document.createElement('div');
  otherLabel.className = 'lm-TabBar-tabLabel';
  pluginTab.appendChild(pluginLabel);
  otherTab.appendChild(otherLabel);
  sideBar.append(pluginTab, otherTab);
  document.head.appendChild(style);
  document.body.appendChild(sideBar);

  expect(getComputedStyle(pluginLabel).textOrientation).toBe('upright');
  expect(getComputedStyle(pluginLabel).transform).toBe('none');
  expect(getComputedStyle(otherLabel).transform).toBe('rotate(180deg)');

  sideBar.remove();
  style.remove();
});
```

- [ ] **Step 2: Run the sidebar test and confirm RED**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand
```

Expected: the title lacks the plugin class and `style/base.css` lacks the scoped override.

- [ ] **Step 3: Add the title hook and scoped CSS**

After setting the title icon, label, and caption, set:

```typescript
this.title.className = 'jp-BehaviorAudit-sidebarTab';
```

Add before the media queries in `style/base.css`:

```css
.jp-SideBar.lm-TabBar.jp-mod-left[data-orientation='vertical']
  .lm-TabBar-tab.jp-BehaviorAudit-sidebarTab
  .lm-TabBar-tabLabel {
  writing-mode: vertical-rl;
  text-orientation: upright;
  transform: none;
}
```

- [ ] **Step 4: Run focused tests and style lint and confirm GREEN**

Run:

```bash
.venv/bin/jlpm jest src/__tests__/behaviorAnalysisSidebar.spec.ts --runInBand
.venv/bin/jlpm stylelint:check
```

Expected: the suite and stylelint pass; the selector contains both the left/orientation scope and plugin class.

- [ ] **Step 5: Commit the sidebar-label hotfix**

```bash
git add src/ui/behaviorAnalysisSidebar.ts src/__tests__/behaviorAnalysisSidebar.spec.ts style/base.css
git commit -m "fix: render behavior analysis tab label upright"
```

---

### Task 4: Rebuild and synchronize the `0.2.1` delivery

**Files:**
- Modify: `myextension/tests/test_labextension_artifact.py:28-41`
- Modify: `deploy/bluedot/release-0.2.1/{README.md,SHA256SUMS,artifacts/myextension-0.2.1-py3-none-any.whl}`
- Modify: `myextension-0.2.1-BLUEDOT-完整交付包/{00_从这里开始.md,MANIFEST.json,README.md,SHA256SUMS,artifacts/myextension-0.2.1-py3-none-any.whl}`
- Create: `docs/2026-08-06-ai-suggestion-sidebar-label-hotfix-verification.md`

**Interfaces:**
- Consumes: the three green source commits from Tasks 1–3 and package version `0.2.1`.
- Produces: one wheel copied byte-for-byte to both tracked delivery locations, matching README/SHA/manifest metadata, and a verification record ready for final preview evidence.
- Preserves: Dockerfile, scripts, runtime configuration logic, the old untracked ZIP, and the rollback tag `self-contained-delivery-0.2.1`.

- [ ] **Step 1: Add a failing compiled-artifact marker gate**

Append `"jp-BehaviorAudit-sidebarTab"` to `REQUIRED_TASK_12_MARKERS` in `myextension/tests/test_labextension_artifact.py`, then run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_labextension_artifact.py
```

Expected: RED because the repository labextension and delivery wheel still contain the pre-hotfix frontend.

- [ ] **Step 2: Run all source-level quality gates before packaging**

Run in this order and record the exact suite/test counts:

```bash
.venv/bin/jlpm test --runInBand
.venv/bin/python -m pytest -q myextension/tests --ignore=myextension/tests/test_labextension_artifact.py
.venv/bin/jlpm lint:check
.venv/bin/jlpm build:prod
```

Expected: frontend full suite, backend suite excluding the intentionally-red artifact gate, lint, and production build all exit `0`; `myextension/labextension` is recreated from current source.

- [ ] **Step 3: Build the wheel offline and copy the exact bytes to both delivery roots**

Run:

```bash
uv build --wheel --offline
cp -p dist/myextension-0.2.1-py3-none-any.whl deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
cp -p dist/myextension-0.2.1-py3-none-any.whl myextension-0.2.1-BLUEDOT-完整交付包/artifacts/myextension-0.2.1-py3-none-any.whl
shasum -a 256 dist/myextension-0.2.1-py3-none-any.whl
```

Expected: build succeeds and prints a new 64-character digest different from `8436b8e69f9e25c58df68c0024723c660e9fe8751c52a60b320c1e97f28ea16e`.

- [ ] **Step 4: Update all artifact identity and operator documentation with actual values**

Use `apply_patch` to replace the old wheel digest in both `SHA256SUMS` files and both README files with the digest printed in Step 3. In both READMEs, change the opening description to state that this wheel contains both the analysis reliability fixes and the 2026-08-06 UI hotfix for adopted AI evidence and the upright “行为分析” label.

Update `00_从这里开始.md` to call the folder the latest `0.2.1` UI-hotfix delivery. Update `MANIFEST.json` with:

- the actual wheel digest;
- design commit `5b05674`;
- the Task 3 source commit from `git rev-parse HEAD` before this delivery commit;
- the exact frontend/backend pass counts from Step 2;
- current lint, production build, wheel, release script, and isolated-install status;
- unchanged not-validated entries for Docker/BLUEDOT/real AI.

Create `docs/2026-08-06-ai-suggestion-sidebar-label-hotfix-verification.md` with the exact RED/GREEN commands and outputs, artifact paths/digests, preview boundary, rollback point, and every external action not executed. Do not write a pass count or validation claim before its command has actually passed.

- [ ] **Step 5: Run artifact, release, checksum, JSON, ZIP, and byte-identity gates**

Run:

```bash
.venv/bin/python -m pytest -q myextension/tests/test_labextension_artifact.py myextension/tests/test_bluedot_release.py
.venv/bin/check-wheel-contents deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
.venv/bin/python -m zipfile -t deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
.venv/bin/python -m json.tool myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json
shasum -a 256 -c deploy/bluedot/release-0.2.1/SHA256SUMS
shasum -a 256 -c myextension-0.2.1-BLUEDOT-完整交付包/SHA256SUMS
cmp deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl myextension-0.2.1-BLUEDOT-完整交付包/artifacts/myextension-0.2.1-py3-none-any.whl
cmp deploy/bluedot/release-0.2.1/README.md myextension-0.2.1-BLUEDOT-完整交付包/README.md
sh -n deploy/bluedot/release-0.2.1/build_image.sh
sh -n deploy/bluedot/release-0.2.1/verify_image.sh
```

Expected: artifact/release tests pass, wheel structure prints `OK`, ZIP integrity prints `Done testing`, JSON parses, both checksum checks print `OK`, both `cmp` calls and script syntax checks exit `0`.

- [ ] **Step 6: Verify an isolated install without changing the project environment**

Create a private temporary root and install only into it:

```bash
PREVIEW_ROOT="$(mktemp -d /private/tmp/myextension-ui-hotfix-preview.XXXXXX)"
.venv/bin/python -m pip install --no-deps --no-cache-dir --target "$PREVIEW_ROOT/site" deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl
PYTHONPATH="$PREVIEW_ROOT/site" .venv/bin/python -c "import myextension; assert myextension.__version__ == '0.2.1'; print(myextension.__version__)"
JUPYTER_PATH="$PREVIEW_ROOT/site/share/jupyter" .venv/bin/python -m jupyter labextension list
```

Expected: import prints `0.2.1`; the labextension list reports enabled `myextension v0.2.1`. Keep the printed `PREVIEW_ROOT` for Task 5; do not use `/private/tmp/myextension-preview.JJox8o`.

- [ ] **Step 7: Confirm the final folder file set and preserve the prior ZIP**

Run:

```bash
test ! -e myextension-0.2.1-BLUEDOT-完整交付包-20260806-ui-hotfix.zip
test -f myextension-0.2.1-BLUEDOT-完整交付包.zip
find myextension-0.2.1-BLUEDOT-完整交付包 -type f ! -name '.DS_Store' -print | sort
```

Expected: the new archive name is unused, the old archive still exists unchanged, and the folder contains exactly the documented operator files plus the wheel. The final archive is intentionally deferred until Task 5 adds preview truth to `MANIFEST.json`.

- [ ] **Step 8: Commit the synchronized delivery**

```bash
git add myextension/tests/test_labextension_artifact.py deploy/bluedot/release-0.2.1 myextension-0.2.1-BLUEDOT-完整交付包 docs/2026-08-06-ai-suggestion-sidebar-label-hotfix-verification.md
git commit -m "build: deliver 0.2.1 UI hotfix"
```

Verify `git status --short` shows only the pre-existing old ZIP and no modified tracked files.

---

### Task 5: Start and verify the fresh wheel-based local preview

**Files:**
- Reuse: the Task 4 `PREVIEW_ROOT`, wheel, and demo notebook.
- Runtime only: `$PREVIEW_ROOT/workspace/demo_notebook.ipynb` and a JupyterLab process bound to `127.0.0.1:18996`.
- Modify after successful preview: `docs/2026-08-06-ai-suggestion-sidebar-label-hotfix-verification.md` with the actual URL shape, frontend filename, and visual/smoke result; never record the token.
- Modify after successful preview: `myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json` with a non-secret fresh-wheel preview result.

**Interfaces:**
- Consumes: the isolated `site` installed from the final delivery wheel.
- Produces: a loopback-only preview URL and evidence that Jupyter loaded the same `remoteEntry` contained in the wheel.
- Preserves: the existing mixed preview on port `18995`; no real AI or external network request.

- [ ] **Step 1: Confirm the new port and wheel frontend identity**

Run:

```bash
lsof -nP -iTCP:18996 -sTCP:LISTEN
unzip -Z1 deploy/bluedot/release-0.2.1/artifacts/myextension-0.2.1-py3-none-any.whl | rg 'share/jupyter/labextensions/myextension/static/remoteEntry\.[0-9a-f]+\.js$'
```

Expected: `lsof` prints no listener; `unzip` prints exactly one hashed `remoteEntry` path. Stop if either expectation fails rather than selecting or killing an unknown process.

- [ ] **Step 2: Prepare the preview workspace**

Run:

```bash
mkdir -p "$PREVIEW_ROOT/workspace"
cp -p demo/macos_real_ai/demo_notebook.ipynb "$PREVIEW_ROOT/workspace/demo_notebook.ipynb"
```

JupyterLab will generate its own random runtime token. Read the tokenized loopback URL from the managed terminal output for the browser step and final handoff; do not put the token in Git, documentation, screenshots, or shell history files.

- [ ] **Step 3: Start JupyterLab from the isolated wheel in a managed terminal session**

Start one foreground process through the execution tool with these environment bindings and arguments:

```bash
PYTHONPATH="$PREVIEW_ROOT/site" \
JUPYTER_PATH="$PREVIEW_ROOT/site/share/jupyter" \
JUPYTER_CONFIG_PATH="$PREVIEW_ROOT/site/etc/jupyter" \
.venv/bin/python -m jupyter lab \
  --no-browser \
  --ServerApp.ip=127.0.0.1 \
  --ServerApp.port=18996 \
  --ServerApp.port_retries=0 \
  --ServerApp.root_dir="$PREVIEW_ROOT/workspace" \
  --ServerApp.password=''
```

Expected terminal evidence: server extension `myextension` loads successfully and JupyterLab prints a generated `http://127.0.0.1:18996/lab?token=...` URL.

- [ ] **Step 4: Perform HTTP and browser smoke acceptance**

First request the loopback page with the runtime token and require HTTP `200`. Then load the URL in a real browser using the project’s browser-automation skill and check:

1. the plugin appears in the left activity bar;
2. the visible label reads “行为分析” top-to-bottom with upright glyphs;
3. computed style of its `.lm-TabBar-tabLabel` reports `text-orientation: upright` and a non-rotated transform;
4. the loaded extension network/resource URL uses the exact wheel `remoteEntry` filename from Step 1;
5. no real AI action is triggered.

Capture one local screenshot without including the token in its filename or written verification record. If browser automation is unavailable, report visual acceptance as pending and give the user the URL; do not claim it passed from unit tests alone.

- [ ] **Step 5: Record preview evidence and run the final verification gate**

Use `apply_patch` to add only the non-secret preview base (`http://127.0.0.1:18996/lab`), matching `remoteEntry` filename, HTTP result, visual result, process lifecycle, and unexecuted real-AI/BLUEDOT boundaries to the verification document. Add the same non-secret result to `MANIFEST.json`; if browser automation was unavailable, record that limitation under `not_validated` instead of `validated`.

Now create the final archive for the first time, record its digest with `apply_patch`, and verify it:

```bash
test ! -e myextension-0.2.1-BLUEDOT-完整交付包-20260806-ui-hotfix.zip
/usr/bin/zip -qr myextension-0.2.1-BLUEDOT-完整交付包-20260806-ui-hotfix.zip myextension-0.2.1-BLUEDOT-完整交付包 -x '*/.DS_Store'
.venv/bin/python -m zipfile -t myextension-0.2.1-BLUEDOT-完整交付包-20260806-ui-hotfix.zip
shasum -a 256 myextension-0.2.1-BLUEDOT-完整交付包-20260806-ui-hotfix.zip
```

Write the printed digest and exact filename to `myextension-0.2.1-BLUEDOT-完整交付包-20260806-ui-hotfix.zip.sha256` with `apply_patch`, then run `shasum -a 256 -c myextension-0.2.1-BLUEDOT-完整交付包-20260806-ui-hotfix.zip.sha256`.

Run the final gates:

```bash
git diff --check
git status --short
.venv/bin/jlpm test --runInBand
.venv/bin/python -m pytest -q myextension/tests
.venv/bin/jlpm lint:check
```

Expected: all gates exit `0`; tracked changes are limited to the preview evidence document before its final commit, while the pre-existing old ZIP remains untracked.

- [ ] **Step 6: Commit evidence and create the local rollback tag**

```bash
git add docs/2026-08-06-ai-suggestion-sidebar-label-hotfix-verification.md myextension-0.2.1-BLUEDOT-完整交付包/MANIFEST.json myextension-0.2.1-BLUEDOT-完整交付包-20260806-ui-hotfix.zip myextension-0.2.1-BLUEDOT-完整交付包-20260806-ui-hotfix.zip.sha256
git commit -m "docs: record 0.2.1 UI hotfix verification"
git tag ui-hotfix-delivery-0.2.1
git status --short
```

Expected: the tag points to the verification commit; status shows only the preserved pre-existing `myextension-0.2.1-BLUEDOT-完整交付包.zip`. The final handoff gives the tokenized clickable URL, the new folder/archive/wheel paths and SHA-256 values, tests actually run, rollback tag, installation steps, and explicit unvalidated external actions.
