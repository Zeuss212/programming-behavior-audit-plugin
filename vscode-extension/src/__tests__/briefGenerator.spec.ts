import { describe, expect, it } from 'vitest';

import { canonicalJson } from '../domain/canonicalJson';
import {
  AUDIT_EVENT_SCHEMA_VERSION,
  PLAN_SCHEMA_VERSION,
  SESSION_SCHEMA_VERSION,
  type AuditEvent,
  type JsonValue,
  type PublishedPlan,
  type SessionState,
} from '../domain/types';
import { generateClassroomBrief } from '../reports/briefGenerator';

const terminalStatuses = ['completed', 'partial', 'abandoned'] as const;

function plan(): PublishedPlan {
  return {
    schema_version: PLAN_SCHEMA_VERSION,
    plan_id: 'plan-report-001',
    version: 1,
    problem_text: '实现一个能够处理空列表的成绩分析函数。',
    knowledge_points: [
      {
        knowledge_point_id: 'kp-boundary',
        name: '边界处理',
        description: '处理空列表输入。',
        observation_basis: '运行空列表用例并得到约定结果。',
      },
    ],
    tests: [],
    published_at: '2026-08-10T00:00:00.000Z',
    content_sha256: 'a'.repeat(64),
  };
}

function state(status: (typeof terminalStatuses)[number]): SessionState {
  return {
    schema_version: SESSION_SCHEMA_VERSION,
    session_id: 'session-report-001',
    workspace_id: 'workspace-report-001',
    status,
    plan_id: 'plan-report-001',
    plan_version: 1,
    plan_content_sha256: 'a'.repeat(64),
    started_at: '2026-08-10T00:00:00.000Z',
    updated_at: '2026-08-10T00:02:00.000Z',
    last_event_seq: 7,
    last_persisted_seq: 7,
    ended_at: '2026-08-10T00:02:00.000Z',
    ...(status === 'completed' ? {} : { status_reason: `${status} reason` }),
  };
}

function event(
  sequence: number,
  monotonicMs: number,
  kind: AuditEvent['kind'],
  payload: AuditEvent['payload'] = {},
): AuditEvent {
  return {
    schema_version: AUDIT_EVENT_SCHEMA_VERSION,
    event_id: `session-report-001:${String(sequence)}`,
    session_id: 'session-report-001',
    session_seq: sequence,
    occurred_at: new Date(Date.UTC(2026, 7, 10, 0, 0, sequence)).toISOString(),
    monotonic_ms: monotonicMs,
    kind,
    payload,
  };
}

function events(): readonly AuditEvent[] {
  return [
    event(1, 0, 'edit', { inserted_chars: 4 }),
    event(2, 10_000, 'save'),
    event(3, 15_000, 'window_focus', { focused: false }),
    event(4, 20_000, 'python_run', { exit_code: 1, timed_out: false, launch_failed: false }),
    event(5, 30_000, 'window_focus', { focused: true }),
    event(6, 40_000, 'notebook_run', { outcome: 'unknown' }),
    event(7, 80_000, 'python_run', { exit_code: 0, timed_out: false, launch_failed: false }),
  ];
}

describe('generateClassroomBrief', () => {
  it.each(terminalStatuses)('creates the five objective categories for %s sessions', (status) => {
    const brief = generateClassroomBrief({
      session: state(status),
      plan: plan(),
      events: events(),
      generatedAt: '2026-08-10T00:03:00.000Z',
    });

    const semanticKeys = Object.keys(brief).filter(
      (key) => !['schema_version', 'session_id', 'generated_at'].includes(key),
    );
    expect(semanticKeys).toEqual([
      'session_result',
      'effective_observation',
      'run_statistics',
      'evidence_summary',
      'attention_point',
    ]);
    expect(brief.session_result.status).toBe(status);
    expect(brief.effective_observation.milliseconds).toBe(40_000);
    expect(brief.run_statistics).toEqual({ total: 3, success: 1, failure: 1, unknown: 1 });
    expect(JSON.stringify(brief).toLowerCase()).not.toMatch(
      /score|rank|mastery|ability|personality/,
    );
  });

  it('is deterministic and limits objective evidence to 20 items and 8 KiB', () => {
    const manyEvents = Array.from({ length: 25 }, (_, index) =>
      event(index + 1, index * 1000, 'edit', { inserted_chars: 1 }),
    );
    const input = {
      session: { ...state('completed'), last_event_seq: 25, last_persisted_seq: 25 },
      plan: plan(),
      events: manyEvents,
      generatedAt: '2026-08-10T00:03:00.000Z',
    } as const;

    const first = generateClassroomBrief(input);
    const second = generateClassroomBrief(input);

    expect(canonicalJson(first as unknown as JsonValue)).toBe(
      canonicalJson(second as unknown as JsonValue),
    );
    expect(first.evidence_summary.length).toBe(20);
    expect(Buffer.byteLength(canonicalJson(first.evidence_summary as unknown as JsonValue))).toBeLessThanOrEqual(
      8 * 1024,
    );
  });

  it('sorts valid input but rejects a missing event sequence', () => {
    const outOfOrder = [events()[1], events()[0]] as readonly AuditEvent[];
    expect(() =>
      generateClassroomBrief({
        session: { ...state('completed'), last_event_seq: 2, last_persisted_seq: 2 },
        plan: plan(),
        events: outOfOrder,
        generatedAt: '2026-08-10T00:03:00.000Z',
      }),
    ).not.toThrow();

    expect(() =>
      generateClassroomBrief({
        session: { ...state('completed'), last_event_seq: 2, last_persisted_seq: 2 },
        plan: plan(),
        events: [events()[1]] as readonly AuditEvent[],
        generatedAt: '2026-08-10T00:03:00.000Z',
      }),
    ).toThrowError(expect.objectContaining({ code: 'session_sequence_invalid' }));
  });
});
