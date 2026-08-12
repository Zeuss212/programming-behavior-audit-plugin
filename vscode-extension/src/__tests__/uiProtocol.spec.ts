import { describe, expect, it } from 'vitest';

import { parseWebviewMessage } from '../ui/protocol';

describe('parseWebviewMessage', () => {
  it('accepts the small discriminated message union', () => {
    expect(parseWebviewMessage({ type: 'navigate', route: 'teacher' })).toEqual({
      type: 'navigate',
      route: 'teacher',
    });
    expect(parseWebviewMessage({ type: 'setConsent', value: true })).toEqual({
      type: 'setConsent',
      value: true,
    });
    expect(parseWebviewMessage({ type: 'command', command: 'behaviorAudit.startCapture' })).toEqual({
      type: 'command',
      command: 'behaviorAudit.startCapture',
    });
    expect(parseWebviewMessage({ type: 'command', command: 'behaviorAudit.openPlanWizard' })).toEqual({
      type: 'command',
      command: 'behaviorAudit.openPlanWizard',
    });
    expect(parseWebviewMessage({ type: 'refresh' })).toEqual({ type: 'refresh' });
  });

  it.each([
    { type: 'unknown' },
    { type: 'navigate', route: 'admin' },
    { type: 'refresh', extra: true },
    { type: 'setConsent', value: 'yes' },
    { type: 'command', command: 'workbench.action.delete' },
    null,
  ])('rejects unknown or extra data: %j', (value) => {
    expect(() => parseWebviewMessage(value)).toThrowError(
      expect.objectContaining({ code: 'import_invalid' }),
    );
  });
});
