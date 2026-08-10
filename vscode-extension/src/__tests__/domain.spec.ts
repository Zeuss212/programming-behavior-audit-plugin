import { describe, expect, it } from 'vitest';

import { canonicalJson, sha256Hex } from '../domain/canonicalJson';
import {
  AUDIT_EVENT_KINDS,
  PLAN_SCHEMA_VERSION,
  SESSION_STATUSES,
  type JsonValue,
  type PublishedPlan,
} from '../domain/types';
import { validatePlan } from '../domain/validation';

type PlanWithoutHash = Omit<PublishedPlan, 'content_sha256'>;

function createPlan(overrides: Partial<PlanWithoutHash> = {}): PublishedPlan {
  const plan: PlanWithoutHash = {
    schema_version: PLAN_SCHEMA_VERSION,
    plan_id: 'plan-demo-001',
    version: 1,
    problem_text: '编写函数 analyze_scores，正确处理空列表并返回统计结果。',
    knowledge_points: [
      {
        knowledge_point_id: 'kp-empty-input',
        name: '空输入边界处理',
        description: '识别并处理空列表，避免除零和聚合函数异常。',
        observation_basis: '运行空列表示例时能够得到约定的空统计结果。',
      },
    ],
    tests: [
      {
        test_id: 'test-empty-input',
        title: '空列表',
        description: '使用空列表调用函数。',
        expected_behavior: '返回 count 为 0 的约定结果且不抛出异常。',
      },
    ],
    published_at: '2026-08-10T00:00:00.000Z',
    ...overrides,
  };

  return {
    ...plan,
    content_sha256: sha256Hex(canonicalJson(plan as unknown as JsonValue)),
  };
}

describe('canonical domain serialization', () => {
  it('sorts object keys recursively and preserves array order', () => {
    expect(canonicalJson({ b: 1, a: { d: 3, c: [2, 1] } })).toBe(
      '{"a":{"c":[2,1],"d":3},"b":1}',
    );
  });

  it('uses locale-independent Unicode code-point key order', () => {
    expect(canonicalJson({ 'ä': 1, z: 2 })).toBe('{"z":2,"ä":1}');
  });

  it('produces a stable SHA-256 digest', () => {
    expect(sha256Hex('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
  });
});

describe('stable domain constants', () => {
  it('locks session statuses and event kinds', () => {
    expect(SESSION_STATUSES).toEqual([
      'collecting',
      'interrupted',
      'finalizing',
      'completed',
      'partial',
      'abandoned',
    ]);
    expect(AUDIT_EVENT_KINDS).toEqual([
      'edit',
      'paste_shortcut',
      'save',
      'document_focus',
      'window_focus',
      'python_run',
      'notebook_edit',
      'notebook_run',
      'external_terminal_activity',
    ]);
  });
});

describe('published plan validation', () => {
  it('accepts a valid plan with a matching canonical digest', () => {
    expect(validatePlan(createPlan())).toEqual(createPlan());
  });

  it('rejects incomplete and empty problem plans', () => {
    expect(() => validatePlan({ schema_version: 1 })).toThrowError(
      expect.objectContaining({ code: 'import_invalid' }),
    );
    expect(() => validatePlan(createPlan({ problem_text: '' }))).toThrowError(
      expect.objectContaining({ code: 'import_invalid' }),
    );
  });

  it('rejects duplicate knowledge-point IDs', () => {
    const knowledgePoint = createPlan().knowledge_points[0];
    expect(knowledgePoint).toBeDefined();

    expect(() =>
      validatePlan(
        createPlan({ knowledge_points: [knowledgePoint!, { ...knowledgePoint! }] }),
      ),
    ).toThrowError(expect.objectContaining({ code: 'import_invalid' }));
  });

  it('rejects unknown schema versions before structural validation', () => {
    expect(() =>
      validatePlan({ ...createPlan(), schema_version: 2 }),
    ).toThrowError(expect.objectContaining({ code: 'unsupported_schema_version' }));
  });

  it('rejects mismatched content hashes', () => {
    expect(() =>
      validatePlan({ ...createPlan(), content_sha256: '0'.repeat(64) }),
    ).toThrowError(expect.objectContaining({ code: 'import_invalid' }));
  });
});
