# AI Plan Rule-Based Mastery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let AI classroom-plan suggestions define safe, broad Python evidence rules that the local Jupyter plugin uses to auto-mark supported knowledge points.

**Architecture:** The API validates a finite rule DSL, the frontend persists that DSL in the published profile, and a local AST evaluator converts protected code history and execution events into mastery statuses. The classroom service stores only the status and teaching-safe evidence text.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, JSON Schema, Vue 3/TypeScript, Vitest, pytest.

## Global Constraints

- AI rules must use only the seven named DSL values in the approved design.
- No student source, stdout, ticket, or key may cross the classroom plugin boundary.
- AST structure, not identifier or literal equality, determines equivalent code.
- Teacher review remains the final override.

---

### Task 1: Define and validate the safe rule DSL in plan suggestions

**Files:**
- Modify: `services/classroom-sync/src/classroom_sync/services/plan_suggestions.py`
- Modify: `services/classroom-sync/tests/unit/test_plan_suggestions.py`
- Modify: `services/classroom-sync/tests/integration/test_plan_suggestions_route.py`

**Interfaces:**
- Produces `SuggestedKnowledgePoint.automatic_evaluation: AutomaticEvaluation | None`.
- Produces `AutomaticEvaluation(mode: Literal["all"], summary: str, requirements: tuple[AutomaticEvaluationRequirement, ...])`.

- [ ] **Step 1: Write failing API-model tests**

```python
def test_generate_accepts_a_white_listed_automatic_evaluation_rule():
    result = service.generate(PlanSuggestionInput(statement="使用字典安全查询"))
    assert result.knowledge_points[0].automatic_evaluation.requirements[0].kind == "dict_get_with_default"

def test_generate_rejects_an_unknown_automatic_evaluation_rule():
    client = completion_client_with_rule("arbitrary_python")
    with pytest.raises(UpstreamUnavailableError):
        service_using(client).generate(PlanSuggestionInput(statement="x"))
```

- [ ] **Step 2: Run the focused test before production changes**

Run: `.venv/bin/pytest tests/unit/test_plan_suggestions.py -k automatic_evaluation -v`

Expected: FAIL because `automatic_evaluation` is not part of the current provider model.

- [ ] **Step 3: Add the Pydantic rule models and prompt contract**

```python
class AutomaticEvaluationRequirement(BaseModel):
    kind: Literal[
        "successful_execution", "dict_literal_assignment", "dict_key_value_pairs",
        "dict_subscript_access", "dict_get_with_default", "print_call", "input_call",
    ]

class AutomaticEvaluation(BaseModel):
    mode: Literal["all"]
    summary: str = Field(min_length=1, max_length=500)
    requirements: list[AutomaticEvaluationRequirement] = Field(min_length=1, max_length=7)
```

Extend `SuggestedKnowledgePoint`, `_ProviderSuggestion`, `PlanSuggestion`, and the system prompt so the field is optional for unsupported knowledge points.

- [ ] **Step 4: Run focused backend tests**

Run: `.venv/bin/pytest tests/unit/test_plan_suggestions.py tests/integration/test_plan_suggestions_route.py -k automatic_evaluation -v`

Expected: PASS with both accepted and rejected rules covered.

### Task 2: Preserve AI rules in the editable classroom-plan profile

**Files:**
- Modify: `myextension/api_schemas/profile-draft-v2.json`
- Modify: `myextension/api_schemas/profile-version-v2.json`
- Modify: `myextension/profile_validator.py`
- Modify: `src/modules/classroom-monitoring/types.ts`
- Modify: `src/modules/classroom-monitoring/api.ts`
- Modify: `src/modules/classroom-monitoring/plan-draft.ts`
- Modify: `src/modules/classroom-monitoring/components/PlanWizard.vue`
- Test: `myextension/tests/test_assessment_profile.py`
- Test: `src/modules/classroom-monitoring/__tests__/plan-draft.test.ts`

**Interfaces:**
- Consumes optional knowledge-point `automatic_evaluation` validated in Task 1.
- Produces immutable profile knowledge points containing `automatic_evaluation` or no rule.

- [ ] **Step 1: Write failing profile and frontend payload tests**

```python
def test_profile_keeps_a_valid_automatic_evaluation_rule():
    payload = make_assessment_profile()
    payload["knowledge_points"][0]["automatic_evaluation"] = valid_rule()
    assert DimensionProfileStore(tmp_path).create_draft(payload)["knowledge_points"][0]["automatic_evaluation"] == valid_rule()
```

```ts
it('keeps applied AI automatic-evaluation rules in the plan profile', () => {
  const payload = toPlanDraftPayload(formWithAiRule(), context)
  expect(payload.profile.knowledge_points[0].automatic_evaluation.requirements)
    .toContainEqual({ kind: 'dict_literal_assignment' })
})
```

- [ ] **Step 2: Run both tests before schema and mapping changes**

Run: `pytest myextension/tests/test_assessment_profile.py -k automatic_evaluation -v` and `npm test -- --run src/modules/classroom-monitoring/__tests__/plan-draft.test.ts`

Expected: Python schema validation fails and TypeScript mapping lacks the rule field.

- [ ] **Step 3: Add optional schema fields and mapping**

Add optional `automatic_evaluation` under each profile knowledge point. Use the same closed enum and `all` mode in draft/version schemas. Extend wire and draft types; copy an applied AI rule into the matching editable point; render its summary as the plan’s automatic-check criterion. A manually added knowledge point keeps no rule.

- [ ] **Step 4: Run profile and frontend tests**

Run: `pytest myextension/tests/test_assessment_profile.py -k automatic_evaluation -v` and `npm test -- --run src/modules/classroom-monitoring/__tests__/plan-draft.test.ts`

Expected: PASS; malformed rules are rejected and valid rules survive profile construction.

### Task 3: Evaluate local code history with AST rather than literal matching

**Files:**
- Create: `myextension/classroom_mastery.py`
- Modify: `myextension/submission_coordinator.py`
- Modify: `myextension/tests/test_submission_coordinator.py`
- Create: `myextension/tests/test_classroom_mastery.py`

**Interfaces:**
- Produces `evaluate_knowledge_points(profile: Mapping[str, object], detail: Mapping[str, object], evidence_refs: Sequence[str]) -> list[dict[str, object]]`.
- Consumes profile rule DSL and local `behavior_events`; never returns source text.

- [ ] **Step 1: Write failing evaluator tests for semantic equivalents**

```python
def test_equivalent_dictionary_code_with_different_names_is_mastered():
    rows = evaluate_knowledge_points(
        profile_with_rule("dict_get_with_default"),
        detail_with_successful_code('records = {"甲": 91}; records.get("乙", "缺失")'),
        ["chunk-1#event-1"],
    )
    assert rows[0]["status"] == "mastered"

def test_missing_default_value_is_partial_after_successful_execution():
    rows = evaluate_knowledge_points(
        profile_with_rule("dict_get_with_default"),
        detail_with_successful_code('records = {"甲": 91}; records.get("乙")'),
        ["chunk-1#event-1"],
    )
    assert rows[0]["status"] == "partial"
```

- [ ] **Step 2: Run evaluator tests before implementation**

Run: `pytest myextension/tests/test_classroom_mastery.py -v`

Expected: FAIL because `classroom_mastery` does not exist.

- [ ] **Step 3: Implement feature extraction and status derivation**

Parse every local `cell_source` with `ast.parse`, ignoring individual syntax failures. Track names assigned an `ast.Dict`, dictionary literals with two entries, `ast.Subscript` on known dictionary names, `.get()` calls with two or more arguments on known dictionary names, `print()`, and `input()`. Count a successful `code_execution` event. Produce only `mastered`, `partial`, `not_demonstrated`, or `review_required` with Chinese evidence/gap/teacher suggestion text.

- [ ] **Step 4: Wire evaluator into submission**

Obtain local detail inside `SubmissionCoordinator._prepare_payload`, call the evaluator, and use its rows in `_remote_payload`; keep only references and explanations in the remote payload. Maintain current conservative rows for legacy profiles without rules.

- [ ] **Step 5: Run focused plugin tests**

Run: `pytest myextension/tests/test_classroom_mastery.py myextension/tests/test_submission_coordinator.py -v`

Expected: PASS; broad equivalent code passes, incomplete code is not mastered, and payload contains no source.

### Task 4: Show the automatic basis on the teacher page and run regression verification

**Files:**
- Modify: `src/modules/classroom-monitoring/components/StudentBriefPanel.vue`
- Modify: `src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts`
- Modify: `services/classroom-sync/tests/contract/test_schemas.py` only if contract fixtures require the new profile field.

**Interfaces:**
- Consumes the existing `status`, `demonstrated`, `gap`, and `teacherSuggestion` output from Task 3.
- Produces `自动判定：已掌握` when no teacher override exists; `教师复核` remains the visible override source.

- [ ] **Step 1: Write a failing panel test**

```ts
it('labels a successful rule-based knowledge point as automatic mastery', () => {
  const wrapper = mount(StudentBriefPanel, { props: { brief: masteredBrief() } })
  expect(wrapper.get('[data-testid="mastery-source-KP_00000001"]').text())
    .toContain('自动判定：已掌握')
})
```

- [ ] **Step 2: Run the focused frontend test before copy changes**

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts`

Expected: FAIL because the panel currently says “自动简报”.

- [ ] **Step 3: Update only the automatic-source label**

Change the automatic label from `自动简报` to `自动判定`; do not alter teacher-review precedence or controls.

- [ ] **Step 4: Run complete affected verification**

Run: `pytest myextension/tests/test_classroom_mastery.py myextension/tests/test_submission_coordinator.py services/classroom-sync/tests/unit/test_plan_suggestions.py services/classroom-sync/tests/integration/test_plan_suggestions_route.py services/classroom-sync/tests/contract/test_schemas.py`.

Run: `npm test -- --run src/modules/classroom-monitoring/__tests__/plan-draft.test.ts src/modules/classroom-monitoring/__tests__/StudentBriefPanel.test.ts` and `npm run build`.

Expected: all selected tests and the production frontend build exit 0.
