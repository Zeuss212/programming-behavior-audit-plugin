import { describe, expect, it } from 'vitest';

import {
  applySuggestion,
  emptyPlanDraft,
  parsePlanDraft,
  toPublishPlanInput,
  validateDraftForStep,
} from '../plans/planDraft';

const suggestion = {
  schema_version: 1 as const,
  knowledge_points: [
    {
      name: '边界处理',
      description: '处理空列表输入。',
      observation_basis: '运行空列表用例。',
    },
  ],
  tests: [
    {
      title: '空列表',
      description: '调用空列表。',
      expected_behavior: '返回约定的空结果。',
    },
  ],
};

describe('plan draft', () => {
  it('applies an AI suggestion as editable draft fields without publication metadata', () => {
    const draft = applySuggestion(
      { ...emptyPlanDraft('2026-08-12T10:00:00.000Z'), problemText: '实现列表分析。' },
      suggestion,
      '2026-08-12T10:01:00.000Z',
    );

    expect(draft.currentStep).toBe(2);
    expect(draft.knowledgePoints[0]).toEqual({
      localId: 'kp-1',
      name: '边界处理',
      description: '处理空列表输入。',
      observationBasis: '运行空列表用例。',
      needsReview: false,
    });
    expect(draft).not.toHaveProperty('plan_id');
  });

  it('blocks progression when a required observation basis is blank', () => {
    const draft = {
      ...emptyPlanDraft('2026-08-12T10:00:00.000Z'),
      currentStep: 2 as const,
      problemText: '题目',
      knowledgePoints: [
        {
          localId: 'kp-1',
          name: '边界',
          description: '说明',
          observationBasis: '',
          needsReview: false,
        },
      ],
    };

    expect(validateDraftForStep(draft, 3)).toEqual({
      ok: false,
      field: 'knowledgePoints.0.observationBasis',
    });
  });

  it('converts a complete draft to the unchanged published-plan input contract', () => {
    const draft = applySuggestion(
      { ...emptyPlanDraft('2026-08-12T10:00:00.000Z'), problemText: '  题目  ' },
      suggestion,
      '2026-08-12T10:01:00.000Z',
    );

    expect(toPublishPlanInput(draft)).toEqual({
      problem_text: '题目',
      knowledge_points: [
        {
          knowledge_point_id: 'kp-1',
          name: '边界处理',
          description: '处理空列表输入。',
          observation_basis: '运行空列表用例。',
        },
      ],
      tests: [
        {
          test_id: 'test-1',
          title: '空列表',
          description: '调用空列表。',
          expected_behavior: '返回约定的空结果。',
        },
      ],
    });
  });

  it('rejects unknown or malformed persisted draft versions', () => {
    expect(parsePlanDraft({ schemaVersion: 99, problemText: '损坏' })).toBeUndefined();
    expect(parsePlanDraft({ schemaVersion: 1, problemText: '题目' })).toBeUndefined();
  });
});
