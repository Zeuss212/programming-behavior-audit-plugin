import { describe, expect, it } from 'vitest';

import {
  AUDIT_EVENT_SCHEMA_VERSION,
  PLAN_SCHEMA_VERSION,
  SESSION_SCHEMA_VERSION,
  type AuditEvent,
  type PublishedPlan,
  type SessionState,
} from '../domain/types';
import { generateOperationLog, generateProcessLog } from '../reports/logGenerator';

const session: SessionState = {
  schema_version: SESSION_SCHEMA_VERSION,
  session_id: 'session-log-001',
  workspace_id: 'workspace-log-001',
  status: 'partial',
  plan_id: 'plan-log-001',
  plan_version: 1,
  plan_content_sha256: 'b'.repeat(64),
  started_at: '2026-08-10T00:00:00.000Z',
  updated_at: '2026-08-10T00:01:00.000Z',
  last_event_seq: 2,
  last_persisted_seq: 2,
  ended_at: '2026-08-10T00:01:00.000Z',
  status_reason: '教师结束了未完成的采集。',
};

const plan: PublishedPlan = {
  schema_version: PLAN_SCHEMA_VERSION,
  plan_id: 'plan-log-001',
  version: 1,
  problem_text: '实现空列表处理。',
  knowledge_points: [],
  tests: [],
  published_at: '2026-08-10T00:00:00.000Z',
  content_sha256: 'b'.repeat(64),
};

const events: readonly AuditEvent[] = [
  {
    schema_version: AUDIT_EVENT_SCHEMA_VERSION,
    event_id: 'session-log-001:1',
    session_id: 'session-log-001',
    session_seq: 1,
    occurred_at: '2026-08-10T00:00:10.000Z',
    monotonic_ms: 10_000,
    kind: 'edit',
    document: { relative_uri: 'src/analyze.py', language_id: 'python' },
    payload: { inserted_chars: 8, deleted_chars: 0 },
  },
  {
    schema_version: AUDIT_EVENT_SCHEMA_VERSION,
    event_id: 'session-log-001:2',
    session_id: 'session-log-001',
    session_seq: 2,
    occurred_at: '2026-08-10T00:00:20.000Z',
    monotonic_ms: 20_000,
    kind: 'python_run',
    payload: { exit_code: 0, duration_ms: 25 },
  },
];

describe('report logs', () => {
  it('generates deterministic canonical operation JSON without absolute paths', () => {
    const input = { session, plan, events };
    const first = generateOperationLog(input);
    const second = generateOperationLog(input);
    const text = new TextDecoder().decode(first);

    expect(first).toEqual(second);
    expect(JSON.parse(text)).toMatchObject({
      session: { session_id: 'session-log-001', status: 'partial' },
      plan: { plan_id: 'plan-log-001', version: 1 },
      events: [{ session_seq: 1 }, { session_seq: 2 }],
    });
    expect(text.endsWith('\n')).toBe(true);
    expect(text).not.toContain('/Users/');
    expect(text).not.toContain('C:\\Users\\');
  });

  it('generates a readable Chinese process timeline', () => {
    const text = new TextDecoder().decode(generateProcessLog({ session, plan, events }));

    expect(text).toContain('# 编程行为过程日志');
    expect(text).toContain('1. 编辑了 `src/analyze.py`');
    expect(text).toContain('2. Python 运行成功');
    expect(text).toContain('会话结果：部分完成');
    expect(text).not.toMatch(/score|rank|mastery|ability|personality/i);
  });
});
