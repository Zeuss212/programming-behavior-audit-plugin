import { describe, expect, it } from 'vitest';

import type { AuditEvent } from '../domain/types';
import { evaluateTeacherEvidence } from '../reports/teacherEvaluation';

function event(
  sessionSeq: number,
  monotonicMs: number,
  kind: AuditEvent['kind'],
  payload: AuditEvent['payload'] = {},
): AuditEvent {
  return {
    schema_version: 1,
    event_id: `session-teacher-evaluation:${String(sessionSeq)}`,
    session_id: 'session-teacher-evaluation',
    session_seq: sessionSeq,
    occurred_at: new Date(Date.UTC(2026, 7, 17, 0, 0, sessionSeq)).toISOString(),
    monotonic_ms: monotonicMs,
    kind,
    payload,
  };
}

describe('evaluateTeacherEvidence', () => {
  it('awards S only when repeated deterministic verification and a complete work cycle exist', () => {
    const evaluation = evaluateTeacherEvidence([
      event(1, 0, 'edit'),
      event(2, 1000, 'save'),
      event(3, 2000, 'python_run', { exit_code: 1, timed_out: false, launch_failed: false }),
      event(4, 3000, 'edit'),
      event(5, 4000, 'save'),
      event(6, 5000, 'python_run', { exit_code: 0, timed_out: false, launch_failed: false }),
      event(7, 6000, 'python_run', { exit_code: 0, timed_out: false, launch_failed: false }),
      event(8, 7000, 'python_run', { exit_code: 0, timed_out: false, launch_failed: false }),
      event(9, 8000, 'python_run', { exit_code: 0, timed_out: false, launch_failed: false }),
      event(10, 9000, 'window_focus', { focused: false }),
      event(11, 10_000, 'window_focus', { focused: true }),
    ]);

    expect(evaluation.overall_grade).toBe('S');
    expect(evaluation.evidence_confidence).toBe('high');
    expect(evaluation.metrics).toMatchObject({
      determinate_run_count: 5,
      successful_run_count: 4,
      failed_run_count: 1,
      recovery_success_count: 1,
      complete_work_cycle_count: 2,
    });
    expect(evaluation.classroom_focus).toMatchObject({
      reference: 'stable',
      focus_loss_count: 1,
      focus_loss_milliseconds: 1000,
    });
  });

  it('does not infer achievement from an indeterminate run', () => {
    const evaluation = evaluateTeacherEvidence([
      event(1, 0, 'edit'),
      event(2, 1000, 'python_run', { exit_code: null, timed_out: false, launch_failed: false }),
    ]);

    expect(evaluation.overall_grade).toBe('D');
    expect(evaluation.evidence_confidence).toBe('low');
    expect(evaluation.summary).toContain('尚未形成可验证成果');
    expect(evaluation.metrics).toMatchObject({
      determinate_run_count: 0,
      unknown_run_count: 1,
    });
  });

  it('caps sparse but positive evidence at B and keeps focus separate from the grade', () => {
    const evaluation = evaluateTeacherEvidence([
      event(1, 0, 'edit'),
      event(2, 1000, 'save'),
      event(3, 2000, 'python_run', { exit_code: 0, timed_out: false, launch_failed: false }),
      event(4, 3000, 'window_focus', { focused: false }),
      event(5, 1_003_000, 'window_focus', { focused: true }),
    ]);

    expect(evaluation.overall_grade).toBe('B');
    expect(evaluation.evidence_confidence).toBe('medium');
    expect(evaluation.classroom_focus.reference).toBe('frequent_switching');
  });
});
