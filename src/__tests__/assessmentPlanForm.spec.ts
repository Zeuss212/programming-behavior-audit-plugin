import { webcrypto } from 'node:crypto';

import {
  addTeacherKnowledgePoint,
  buildAssessmentProfileDraft,
  canPublishAssessmentPlan,
  confirmAssessmentTests,
  confirmKnowledgePoints,
  createAssessmentPlanState,
  invalidateAssessmentTestConfirmation,
  mergeAssessmentTestSuggestions,
  mergeKnowledgeSuggestions,
  moveKnowledgePoint,
  removeKnowledgePoint,
  replaceAssessmentTests,
  updateAssessmentPlanContext,
  updateAssessmentTest,
  updateKnowledgePoint,
  validateAssessmentPlanState
} from '../ui/assessmentPlanForm';
import { sha256Json } from '../utils/canonicalJson';

const subtle = webcrypto.subtle as SubtleCrypto;
const pointId = () => 'KP_A1B2C3D4';
const secondPointId = () => 'KP_B1C2D3E4';

function baseState() {
  return {
    ...createAssessmentPlanState(),
    title: '平均分知识点分析',
    problemId: 'average-debug',
    problemStatement: '编写 calculate_average(numbers)，返回数字列表的平均值。',
    submissionContract: {
      kind: 'function' as const,
      entrypoint: 'calculate_average'
    }
  };
}

function withPoint() {
  return addTeacherKnowledgePoint(
    baseState(),
    {
      name: '循环边界',
      description: '正确遍历全部输入元素并处理边界。'
    },
    pointId
  );
}

function generatedTest() {
  return {
    id: 'TEST_A1B2C3D4',
    name: '普通整数列表',
    knowledge_point_ids: ['KP_A1B2C3D4'],
    kind: 'function_call' as const,
    input: '[[78, 85, 92, 66, 88]]',
    expected: '81.8',
    enabled: true,
    source: 'ai_suggestion' as const,
    order: 0
  };
}

describe('assessment plan pure state', () => {
  it('requires only teacher-facing question fields before knowledge work', () => {
    expect(validateAssessmentPlanState(createAssessmentPlanState())).toEqual({
      title: '请输入方案名称',
      problemId: '请输入题目标识',
      problemStatement: '请输入完整题目',
      entrypoint: '请输入学生需要实现的函数名',
      knowledgePoints: '请至少确认一个知识点'
    });
  });

  it('adds, trims and deduplicates teacher points without AI', () => {
    const once = addTeacherKnowledgePoint(
      baseState(),
      { name: '  循环边界  ', description: '  正确处理边界  ' },
      pointId
    );
    const duplicate = addTeacherKnowledgePoint(
      once,
      { name: '循环边界', description: '不会覆盖' },
      secondPointId
    );

    expect(duplicate.knowledgePoints).toHaveLength(1);
    expect(duplicate.knowledgePoints[0]).toMatchObject({
      id: 'KP_A1B2C3D4',
      name: '循环边界',
      description: '正确处理边界',
      source: 'teacher',
      order: 0
    });
  });

  it('merges AI suggestions without overwriting teacher points', () => {
    const state = withPoint();
    const merged = mergeKnowledgeSuggestions(state, [
      {
        id: 'KP_C1D2E3F4',
        name: '循环边界',
        description: 'AI 重复项',
        evidence_question: '重复问题',
        support_statement: '重复支持',
        exclusion_statement: '重复排除',
        source: 'ai_suggestion',
        order: 0
      },
      {
        id: 'KP_B1C2D3E4',
        name: '平均值计算',
        description: '使用总和除以元素数量。',
        evidence_question: '是否正确完成平均值计算？',
        support_statement: '使用多个样例验证平均值。',
        exclusion_statement: '固定输出单个结果不计入。',
        source: 'ai_suggestion',
        order: 1
      }
    ]);

    expect(merged.knowledgePoints).toHaveLength(2);
    expect(merged.knowledgePoints[0].source).toBe('teacher');
    expect(merged.knowledgePoints[0].description).toBe(
      '正确遍历全部输入元素并处理边界。'
    );
    expect(merged.knowledgePoints[1]).toMatchObject({
      id: 'KP_B1C2D3E4',
      name: '平均值计算',
      source: 'ai_suggestion',
      order: 1,
      evidenceQuestion: '是否正确完成平均值计算？',
      supportStatement: '使用多个样例验证平均值。',
      exclusionStatement: '固定输出单个结果不计入。'
    });
  });

  it('fills invalid AI evidence fields with deterministic defaults', async () => {
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
      evidenceQuestion:
        '学生是否通过代码、运行和修改过程正确应用“空序列处理”？',
      supportStatement: '代码与验证过程显示学生正确应用了“空序列处理”。',
      exclusionStatement:
        '只出现一次偶然正确输出，或缺少与“空序列处理”相关的验证，不计入。'
    });
    expect(validateAssessmentPlanState(merged)).not.toHaveProperty(
      'knowledgePoints'
    );
    await expect(confirmKnowledgePoints(merged, subtle)).resolves.toMatchObject(
      {
        confirmations: {
          knowledge_points_hash: expect.stringMatching(/^[0-9a-f]{64}$/)
        }
      }
    );
  });

  it('editing a point preserves draft tests but invalidates both confirmations', async () => {
    const withTests = replaceAssessmentTests(withPoint(), [generatedTest()]);
    const knowledgeConfirmed = await confirmKnowledgePoints(withTests, subtle);
    const fullyConfirmed = await confirmAssessmentTests(
      knowledgeConfirmed,
      subtle
    );

    const changed = updateKnowledgePoint(fullyConfirmed, 'KP_A1B2C3D4', {
      name: '列表遍历边界'
    });

    expect(changed.assessmentTests).toEqual([generatedTest()]);
    expect(changed.knowledgePoints[0].source).toBe('teacher');
    expect(changed.confirmations).toEqual({
      knowledge_points_hash: null,
      tests_hash: null
    });
  });

  it('invalidates confirmations when the question changes and drops incompatible tests when the answer kind changes', async () => {
    const withTests = replaceAssessmentTests(withPoint(), [generatedTest()]);
    const knowledgeConfirmed = await confirmKnowledgePoints(withTests, subtle);
    const fullyConfirmed = await confirmAssessmentTests(
      knowledgeConfirmed,
      subtle
    );

    const changedQuestion = updateAssessmentPlanContext(fullyConfirmed, {
      problemStatement: '修改后的平均值题目。'
    });
    expect(changedQuestion.assessmentTests).toEqual([generatedTest()]);
    expect(changedQuestion.confirmations).toEqual({
      knowledge_points_hash: null,
      tests_hash: null
    });

    const changedKind = updateAssessmentPlanContext(changedQuestion, {
      submissionContract: { kind: 'stdin_stdout' }
    });
    expect(changedKind.assessmentTests).toEqual([]);
    expect(changedKind.confirmations).toEqual({
      knowledge_points_hash: null,
      tests_hash: null
    });
  });

  it('preserves confirmations when only the display title or problem id changes', async () => {
    const withTests = replaceAssessmentTests(withPoint(), [generatedTest()]);
    const knowledgeConfirmed = await confirmKnowledgePoints(withTests, subtle);
    const fullyConfirmed = await confirmAssessmentTests(
      knowledgeConfirmed,
      subtle
    );

    const renamed = updateAssessmentPlanContext(fullyConfirmed, {
      title: '新的方案名称',
      problemId: 'average-debug-renamed'
    });

    expect(renamed.confirmations).toEqual(fullyConfirmed.confirmations);
    expect(renamed.assessmentTests).toEqual(fullyConfirmed.assessmentTests);
  });

  it('removing a point removes orphan tests and moving points restores order', () => {
    const twoPoints = addTeacherKnowledgePoint(
      withPoint(),
      { name: '平均值计算', description: '计算总和除以数量。' },
      secondPointId
    );
    const withTests = replaceAssessmentTests(twoPoints, [
      generatedTest(),
      {
        ...generatedTest(),
        id: 'TEST_B1C2D3E4',
        name: '第二个测试',
        knowledge_point_ids: ['KP_B1C2D3E4'],
        order: 1
      }
    ]);
    const moved = moveKnowledgePoint(withTests, 'KP_B1C2D3E4', -1);
    const removed = removeKnowledgePoint(moved, 'KP_A1B2C3D4');

    expect(moved.knowledgePoints.map(item => [item.id, item.order])).toEqual([
      ['KP_B1C2D3E4', 0],
      ['KP_A1B2C3D4', 1]
    ]);
    expect(removed.assessmentTests.map(item => item.id)).toEqual([
      'TEST_B1C2D3E4'
    ]);
    expect(removed.assessmentTests[0].order).toBe(0);
  });

  it('editing a generated test marks it as teacher-owned and clears test confirmation', async () => {
    const withTests = replaceAssessmentTests(withPoint(), [generatedTest()]);
    const knowledgeConfirmed = await confirmKnowledgePoints(withTests, subtle);
    const fullyConfirmed = await confirmAssessmentTests(
      knowledgeConfirmed,
      subtle
    );
    const changed = updateAssessmentTest(fullyConfirmed, 'TEST_A1B2C3D4', {
      expected: '81.80'
    });

    expect(changed.assessmentTests[0].source).toBe('teacher');
    expect(changed.confirmations.knowledge_points_hash).not.toBeNull();
    expect(changed.confirmations.tests_hash).toBeNull();
  });

  it('keeps teacher-edited tests when refreshing AI suggestions', () => {
    const teacherTest = {
      ...generatedTest(),
      source: 'teacher' as const,
      expected: '81.80'
    };
    const refreshed = mergeAssessmentTestSuggestions(
      replaceAssessmentTests(withPoint(), [teacherTest]),
      [
        {
          ...generatedTest(),
          id: 'TEST_B1C2D3E4',
          name: '新的 AI 边界测试'
        }
      ]
    );

    expect(refreshed.assessmentTests).toHaveLength(2);
    expect(refreshed.assessmentTests[0]).toMatchObject({
      source: 'teacher',
      expected: '81.80',
      order: 0
    });
    expect(refreshed.assessmentTests[1]).toMatchObject({
      id: 'TEST_B1C2D3E4',
      source: 'ai_suggestion',
      order: 1
    });
  });

  it('clears a test confirmation when the teacher unchecks it', async () => {
    const withTests = replaceAssessmentTests(withPoint(), [generatedTest()]);
    const knowledgeConfirmed = await confirmKnowledgePoints(withTests, subtle);
    const fullyConfirmed = await confirmAssessmentTests(
      knowledgeConfirmed,
      subtle
    );

    const unchecked = invalidateAssessmentTestConfirmation(fullyConfirmed);

    expect(unchecked.confirmations.knowledge_points_hash).not.toBeNull();
    expect(unchecked.confirmations.tests_hash).toBeNull();
    expect(canPublishAssessmentPlan(unchecked)).toBe(false);
  });

  it('rejects incomplete knowledge evidence and answer-kind mismatches before confirmation', async () => {
    const incompletePoint = updateKnowledgePoint(withPoint(), 'KP_A1B2C3D4', {
      evidenceQuestion: ''
    });
    expect(validateAssessmentPlanState(incompletePoint)).toMatchObject({
      knowledgePoints: '知识点 1 缺少：过程观察问题'
    });
    await expect(
      confirmKnowledgePoints(incompletePoint, subtle)
    ).rejects.toThrow('请先完成题目和知识点');

    const mismatched = replaceAssessmentTests(withPoint(), [
      {
        ...generatedTest(),
        kind: 'stdin_stdout'
      }
    ]);
    expect(validateAssessmentPlanState(mismatched)).toMatchObject({
      assessmentTests: '测试形式与题目答题形式不一致'
    });
    const confirmedKnowledge = await confirmKnowledgePoints(mismatched, subtle);
    await expect(
      confirmAssessmentTests(confirmedKnowledge, subtle)
    ).rejects.toThrow('请先补全并修正测试');
  });

  it('reports exact missing fields for the first incomplete point', () => {
    const incomplete = updateKnowledgePoint(withPoint(), 'KP_A1B2C3D4', {
      supportStatement: ' ',
      exclusionStatement: ''
    });

    expect(validateAssessmentPlanState(incomplete)).toMatchObject({
      knowledgePoints: '知识点 1 缺少：支持表现、排除情况'
    });
  });

  it('publishes only after current points and complete enabled tests are confirmed', async () => {
    const withTests = replaceAssessmentTests(withPoint(), [generatedTest()]);
    expect(canPublishAssessmentPlan(withTests)).toBe(false);
    const knowledgeConfirmed = await confirmKnowledgePoints(withTests, subtle);
    expect(canPublishAssessmentPlan(knowledgeConfirmed)).toBe(false);
    const fullyConfirmed = await confirmAssessmentTests(
      knowledgeConfirmed,
      subtle
    );

    expect(canPublishAssessmentPlan(fullyConfirmed)).toBe(true);
    const draft = buildAssessmentProfileDraft(fullyConfirmed);
    expect(draft.schema_version).toBe(2);
    expect(draft.knowledge_points).toHaveLength(1);
    expect(draft.assessment_tests).toHaveLength(1);
    expect(draft.dimensions[0]).toMatchObject({
      knowledge_point_id: 'KP_A1B2C3D4',
      name: '知识点：循环边界'
    });
    expect(draft.confirmations.knowledge_points_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(draft.confirmations.tests_hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it('will not confirm tests until every point has one enabled case', async () => {
    const twoPoints = addTeacherKnowledgePoint(
      withPoint(),
      { name: '平均值计算', description: '总和除以数量。' },
      secondPointId
    );
    const withOneTest = replaceAssessmentTests(twoPoints, [generatedTest()]);
    const knowledgeConfirmed = await confirmKnowledgePoints(
      withOneTest,
      subtle
    );

    await expect(
      confirmAssessmentTests(knowledgeConfirmed, subtle)
    ).rejects.toThrow('每个知识点至少需要一个启用的测试');
  });

  it('preserves exact test input and expected whitespace in the confirmation hash and draft', async () => {
    const input = '  [[78, 85, 92, 66, 88]]\n';
    const expected = '\n81.8  ';
    const withWhitespace = replaceAssessmentTests(withPoint(), [
      {
        ...generatedTest(),
        input,
        expected
      }
    ]);
    const knowledgeConfirmed = await confirmKnowledgePoints(
      withWhitespace,
      subtle
    );
    const fullyConfirmed = await confirmAssessmentTests(
      knowledgeConfirmed,
      subtle
    );
    const draft = buildAssessmentProfileDraft(fullyConfirmed);
    const expectedHash = await sha256Json(
      {
        problem_context: draft.problem_context,
        knowledge_points_hash: draft.confirmations
          .knowledge_points_hash as string,
        assessment_tests: draft.assessment_tests
      },
      subtle
    );

    expect(draft.assessment_tests[0].input).toBe(input);
    expect(draft.assessment_tests[0].expected).toBe(expected);
    expect(draft.confirmations.tests_hash).toBe(expectedHash);
  });

  it('rejects stale knowledge-point references even on disabled tests', async () => {
    const withStaleReference = replaceAssessmentTests(withPoint(), [
      {
        ...generatedTest(),
        enabled: false,
        knowledge_point_ids: ['KP_00000000']
      }
    ]);
    const knowledgeConfirmed = await confirmKnowledgePoints(
      withStaleReference,
      subtle
    );

    expect(validateAssessmentPlanState(knowledgeConfirmed)).toMatchObject({
      assessmentTests: '测试引用了不存在的知识点'
    });
    await expect(
      confirmAssessmentTests(knowledgeConfirmed, subtle)
    ).rejects.toThrow('请先补全并修正测试');
  });
});
