# Knowledge Evidence Autofill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make missing knowledge-point observation fields self-healing at every authoring boundary and ship the fix as a uniquely identifiable 0.2.2 wheel.

**Architecture:** Add one pure normalizer in `assessmentPlanForm.ts` and reuse it before rendering, confirmation, and draft construction. Keep existing teacher text immutable and generate deterministic defaults only for invalid fields.

**Tech Stack:** TypeScript 5.5, Jest, JupyterLab 4 prebuilt extension, Python wheel via Hatch/build.

## Global Constraints

- Do not overwrite valid teacher-authored observation text.
- Do not invent a missing knowledge-point name.
- Preserve the existing 0.2.1 delivery artifacts.
- The new package version is 0.2.2.

---

### Task 1: Central observation-field normalization

**Files:**

- Modify: `src/ui/assessmentPlanForm.ts`
- Modify: `src/ui/guidedProfileEditor.ts`
- Test: `src/__tests__/assessmentPlanForm.spec.ts`
- Test: `src/__tests__/assessmentPlanEditor.spec.ts`

**Interfaces:**

- Produces: `normalizeKnowledgePointEvidence(state: IAssessmentPlanState): IAssessmentPlanState`
- Consumes: existing `defaultEvidence`, `confirmKnowledgePoints`, and `buildAssessmentProfileDraft`

- [ ] Add a failing test that passes a legacy AI point with absent, whitespace, and non-string evidence fields directly to `confirmKnowledgePoints` and expects deterministic defaults.
- [ ] Run the focused test with coverage disabled and confirm it fails because the legacy point is not normalized.
- [ ] Implement `normalizeKnowledgePointEvidence` and make `missingKnowledgePointFields` safe for unknown runtime values.
- [ ] Invoke normalization before knowledge rendering, confirmation, and draft construction.
- [ ] Run focused form/editor tests and confirm they pass.

### Task 2: Unique release artifact and regression verification

**Files:**

- Modify: `package.json`
- Modify: `yarn.lock`
- Create: `dist/myextension-0.2.2-py3-none-any.whl`
- Create: `myextension-0.2.2-演示热修复包/README.md`
- Create: `myextension-0.2.2-演示热修复包/SHA256SUMS`
- Create: `myextension-0.2.2-演示热修复包/artifacts/myextension-0.2.2-py3-none-any.whl`

**Interfaces:**

- Produces: installable `myextension 0.2.2` with a new `remoteEntry.*.js`

- [ ] Change the package version from 0.2.1 to 0.2.2 using the project package manager so the lockfile stays consistent.
- [ ] Run the complete frontend test suite and lint checks.
- [ ] Build the production labextension and wheel.
- [ ] Install the wheel into an isolated target and verify Python version, server extension, labextension, autofill strings, and unique remote entry.
- [ ] Copy only the verified wheel into a new delivery folder, generate `SHA256SUMS`, and document force-reinstall, full server restart, new image tag/digest, and browser hard refresh.
- [ ] Commit source, tests, documentation, and the verified delivery folder without adding unrelated ZIP files.
