import { describe, expect, it } from 'vitest';

import { emptyPlanDraft } from '../plans/planDraft';
import { parsePlanWizardMessage } from '../ui/planWizardProtocol';

const draft = {
  ...emptyPlanDraft('2026-08-12T10:00:00.000Z'),
  problemText: '实现列表分析。',
};

describe('parsePlanWizardMessage', () => {
  it('accepts exact valid wizard messages', () => {
    expect(parsePlanWizardMessage({ type: 'ready' })).toEqual({ type: 'ready' });
    expect(parsePlanWizardMessage({ type: 'saveDraft', draft })).toEqual({
      type: 'saveDraft',
      draft,
    });
    expect(
      parsePlanWizardMessage({ type: 'requestSuggestion', problemText: '实现列表分析。' }),
    ).toEqual({ type: 'requestSuggestion', problemText: '实现列表分析。' });
  });

  it('rejects extra keys, invalid drafts, and oversized problem text', () => {
    expect(() =>
      parsePlanWizardMessage({ type: 'publishDraft', draft, injected: true }),
    ).toThrowError(/消息格式无效/u);
    expect(() => parsePlanWizardMessage({ type: 'saveDraft', draft: {} })).toThrowError(
      /消息格式无效/u,
    );
    expect(() =>
      parsePlanWizardMessage({ type: 'requestSuggestion', problemText: 'x'.repeat(20_001) }),
    ).toThrowError(/消息格式无效/u);
  });
});
