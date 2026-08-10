import { describe, expect, it } from 'vitest';

import { sanitizePlanSuggestionInput, sanitizeSessionAnalysisInput } from '../ai/sanitize';

describe('AI request sanitization', () => {
  it('uses relative URIs, bounds code, and labels student content as untrusted quoted data', () => {
    const workspaceRoot = '/private/tmp/student-workspace';
    const sanitized = sanitizePlanSuggestionInput({
      problemText: '实现列表分析函数。',
      workspaceRoot,
      codeFragments: [
        {
          absolutePath: '/private/tmp/student-workspace/src/analyze.py',
          languageId: 'python',
          content: `# student comment\n${'x'.repeat(40 * 1024)}`,
        },
      ],
    });
    const text = JSON.stringify(sanitized);
    const fragment = sanitized.code_fragments[0];

    expect(fragment?.relative_uri).toBe('src/analyze.py');
    expect(fragment?.untrusted).toBe(true);
    expect(Buffer.byteLength(fragment?.content ?? '', 'utf8')).toBeLessThanOrEqual(32 * 1024);
    expect(text).not.toContain(workspaceRoot);
    expect(text).toContain('student comment');
  });

  it('bounds evidence to 20 items and drops environment and unknown input fields', () => {
    const input = {
      sessionId: 'session-ai-001',
      brief: {
        session_result: { status: 'completed' },
        attention_point: null,
      },
      evidence: Array.from({ length: 25 }, (_, index) => ({
        eventId: `session-ai-001:${String(index + 1)}`,
        kind: 'edit',
        summary: `证据 ${String(index + 1)}`,
      })),
      codeFragments: [],
      workspaceRoot: '/private/tmp/student-workspace',
      environment: { ARK_API_KEY: 'raw-environment-secret' },
      unexpected: 'drop-me',
    };

    const sanitized = sanitizeSessionAnalysisInput(input);
    const text = JSON.stringify(sanitized);

    expect(sanitized.evidence).toHaveLength(20);
    expect(sanitized.evidence.every((item) => item.untrusted)).toBe(true);
    expect(text).not.toContain('raw-environment-secret');
    expect(text).not.toContain('drop-me');
    expect(text).not.toContain('/private/tmp/student-workspace');
  });
});
