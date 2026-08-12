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

- [x] Add a failing test that passes a legacy AI point with absent, whitespace, and non-string evidence fields directly to `confirmKnowledgePoints` and expects deterministic defaults.
- [x] Run the focused test with coverage disabled and confirm it fails because the legacy point is not normalized.
- [x] Implement `normalizeKnowledgePointEvidence` and make `missingKnowledgePointFields` safe for unknown runtime values.
- [x] Invoke normalization before knowledge rendering, confirmation, and draft construction.
- [x] Run focused form/editor tests and confirm they pass.

### Task 2: Unique release artifact and regression verification

**Files:**

- Modify: `package.json`
- Modify: `README.md`
- Modify: `启动说明.md`
- Modify: `deploy/bluedot/Dockerfile`
- Create: `dist/myextension-0.2.2-py3-none-any.whl`
- Create: `deploy/bluedot/release-0.2.2/`
- Create: `myextension-0.2.2-BLUEDOT-演示热修复交付包.zip`

**Interfaces:**

- Produces: installable `myextension 0.2.2` with a new `remoteEntry.*.js`

- [x] Change the package version from 0.2.1 to 0.2.2. This Yarn workspace uses a local workspace reference, so no lockfile version entry changes.
- [x] Run the complete frontend test suite and lint checks.
- [x] Build the production labextension and wheel.
- [x] Install the wheel into an isolated target and verify Python version, server extension, labextension, autofill strings, and unique remote entry.
- [x] Copy only the verified wheel into a new delivery folder, generate `SHA256SUMS`, and document force-reinstall, full server restart, new image tag/digest, and browser hard refresh.
- [x] Commit source, tests, documentation, and the verified delivery folder without adding the unrelated 0.2.1 ZIP.
