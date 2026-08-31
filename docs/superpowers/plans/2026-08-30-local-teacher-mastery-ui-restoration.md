# Local Teacher Mastery UI Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the approved teacher mastery overview and evidence-first student brief in the current local classroom flow without changing creation, assignment, workbench, or plugin behavior.

**Architecture:** The classroom API continues to own the bounded mastery projection. The monitoring read model derives `mastery_overview` from the latest stored brief plus the latest teacher review; the frontend maps that allowlisted projection and renders it in the existing teacher shell. The student brief keeps the current AI policy and error recovery while adding result-first mastery, evidence, and review presentation.

**Tech Stack:** FastAPI, SQLAlchemy, pytest; Vue 3, TypeScript, Vitest, Vue Test Utils, Vite.

## Global Constraints

- Modify only the local `codex/classroom-version-chain-fix-20260829` and `codex/course-classroom-0824-adaptation` worktrees.
- Do not push, deploy, merge, or modify another worktree.
- Preserve the current experiment creation, assignment provisioning, student workbench, Jupyter plugin, AI policy, draft recovery, and teacher desktop shell.
- Monitoring returns only bounded counts, a maximum of three attention items, and evidence counts; it must not expose raw logs, tickets, storage paths, or complete teacher-review payloads.
- Missing evidence is displayed as evidence-insufficient, never as proof that the student failed to learn.

---

### Task 1: Restore the backend mastery read model

**Files:**

- Modify: `services/classroom-sync/src/classroom_sync/repositories.py`
- Modify: `services/classroom-sync/src/classroom_sync/services/read_models.py`
- Test: `services/classroom-sync/tests/integration/test_classroom_read_models.py`

**Interfaces:**

- Produces: `ClassroomRepository.list_teacher_reviews_for_sessions(session_ids: list[str]) -> list[TeacherReview]`.
- Produces: monitoring `brief.mastery_overview` with `counts`, `attention_items`, `data_completeness`, and `source`.

- [ ] **Step 1: Write the failing monitoring contract test**

Add a brief containing mastered, partial, and missing-evidence knowledge points plus a teacher review. Assert that monitoring returns one mastered, one partial, one evidence-insufficient result and orders the two attention items by instructional priority.

```python
assert monitoring.json()["students"][0]["brief"]["mastery_overview"] == {
    "counts": {
        "mastered": 1,
        "partial": 1,
        "not_mastered": 0,
        "evidence_insufficient": 1,
        "review_required": 0,
    },
    "attention_items": [
        {
            "knowledge_point_id": "KP_QUERY",
            "name": "安全查询",
            "status": "partial",
            "reason": "尚未覆盖默认值场景。",
            "evidence_count": 1,
        },
        {
            "knowledge_point_id": "KP_BOUNDARY",
            "name": "边界处理",
            "status": "evidence_insufficient",
            "reason": "缺少相关运行证据。",
            "evidence_count": 0,
        },
    ],
    "data_completeness": "complete",
    "source": "teacher",
}
```

- [ ] **Step 2: Run the test and verify RED**

Run: `uv run --group dev pytest tests/integration/test_classroom_read_models.py -q`

Expected: FAIL because `mastery_overview` is absent.

- [ ] **Step 3: Implement the bounded projection**

Add the repository batch read, select the latest review per session, normalize legacy `not_demonstrated` to `evidence_insufficient`, apply valid teacher overrides, count statuses, and return at most three attention rows. Keep the current allowlisted monitoring envelope unchanged except for `mastery_overview`.

- [ ] **Step 4: Run the focused backend test and verify GREEN**

Run: `uv run --group dev pytest tests/integration/test_classroom_read_models.py -q`

Expected: PASS.

### Task 2: Restore the frontend monitoring overview

**Files:**

- Modify: `src/modules/classroom-monitoring/types.ts`
- Modify: `src/modules/classroom-monitoring/api.ts`
- Modify: `src/modules/classroom-monitoring/components/ClassroomStudentTable.vue`
- Test: `src/modules/classroom-monitoring/__tests__/api.test.ts`
- Test: `src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts`

**Interfaces:**

- Consumes: backend `brief.mastery_overview`.
- Produces: `ClassroomMasteryOverview` and `ClassroomBriefStatus.masteryOverview`.
- Produces: a horizontally scrollable desktop table showing mastery counts and the first attention item, with the existing narrow-screen card fallback.

- [ ] **Step 1: Write failing mapping and component tests**

Assert that the API maps the bounded wire object and that the table displays `已掌握 1 · 部分掌握 1`, the first attention item, and a student-specific accessible brief button.

```typescript
expect(wrapper.get('[data-testid="mastery-overview-student001"]').text())
  .toContain('已掌握 1 · 部分掌握 1')
expect(wrapper.text()).toContain('边界处理：缺少相关运行证据。')
expect(wrapper.get('button').attributes('aria-label')).toContain('student001')
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `npm test -- src/modules/classroom-monitoring/__tests__/api.test.ts src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts`

Expected: FAIL because the mapping and mastery presentation are absent.

- [ ] **Step 3: Implement the smallest compatible mapping and UI**

Strictly validate status counts and attention items, redact their teacher-facing text, then render the mastery column without changing navigation or polling. Keep the wide table inside its own horizontal scroll container.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `npm test -- src/modules/classroom-monitoring/__tests__/api.test.ts src/modules/classroom-monitoring/__tests__/classroom-monitor.test.ts`

Expected: PASS.

### Task 3: Restore the evidence-first student brief

**Files:**

- Modify: `src/modules/classroom-monitoring/types.ts`
- Modify: `src/modules/classroom-monitoring/api.ts`
- Modify: `src/modules/classroom-monitoring/components/StudentBriefPanel.vue`
- Test: `src/modules/classroom-monitoring/__tests__/api.test.ts`
- Test: `src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts`

**Interfaces:**

- Consumes: optional `evidence_refs`, `assessment_dimension`, and `evidence_criteria` fields while remaining compatible with existing briefs.
- Produces: result-first counts, priority gaps, evidence-aware knowledge-point cards, and the existing teacher-review form.

- [ ] **Step 1: Write the failing brief presentation test**

Assert that the brief shows result counts, the highest-priority gap, an assessment dimension, support/exclusion criteria, and safe evidence IDs while preserving the current AI-derived display state and teacher override behavior.

```typescript
expect(wrapper.get('[data-testid="brief-mastery-overview"]').text()).toContain('证据不足 1')
expect(wrapper.get('[data-testid="priority-gaps"]').text()).toContain('没有验证不存在的键。')
expect(wrapper.text()).toContain('测评维度：能够安全读取缺失键并返回默认值。')
expect(wrapper.text()).toContain('达标条件：使用 get 并明确默认值。')
expect(wrapper.text()).toContain('证据事件 chunk-1#event-1')
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `npm test -- src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts src/modules/classroom-monitoring/__tests__/api.test.ts`

Expected: FAIL because the result-first sections and optional field mappings are absent.

- [ ] **Step 3: Implement the evidence-first layout without changing AI policy**

Add optional field mapping and the approved sections. Continue using the current `displayedStatus` precedence so safe ready AI results and saved teacher overrides behave exactly as before; retain all existing redaction and error messages.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `npm test -- src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts src/modules/classroom-monitoring/__tests__/api.test.ts`

Expected: PASS.

### Task 4: Local closed-loop verification

**Files:** No production files beyond Tasks 1–3.

- [ ] **Step 1: Run backend regression checks**

Run: `uv run --group dev pytest -q`

Expected: all classroom-sync tests pass.

- [ ] **Step 2: Run frontend regression checks**

Run:

```bash
npm test -- --run
npm run type-check
npm run build
```

Expected: tests, type checking, and build pass.

- [ ] **Step 3: Restart only the local classroom services if required and validate in the browser**

Verify the existing teacher classroom URL shows the mastery overview and opens the evidence-first brief. Confirm the student home, assignment launch, `127.0.0.1:8888/lab`, and `课堂监控` plugin still work. Check the teacher shell at approximately 640, 900, 1280, and 1440 CSS pixels; wide tables must scroll inside content instead of changing the shell to top navigation.

- [ ] **Step 4: Stop at the local verified state**

Do not push, deploy, or merge. Report exact worktree paths, verification commands, and any remaining limitation.
