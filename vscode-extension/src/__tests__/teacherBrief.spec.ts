import { describe, expect, it } from 'vitest';

import type { ClassroomBriefV2 } from '../domain/types';
import { renderTeacherBrief } from '../reports/teacherBrief';

function brief(): ClassroomBriefV2 {
  return {
    schema_version: 2,
    session_id: 'session-teacher-brief-001',
    generated_at: '2026-08-17T00:03:00.000Z',
    session_result: { status: 'completed' },
    effective_observation: { milliseconds: 42_000, method: 'focused_event_gaps_capped_at_30_seconds' },
    run_statistics: { total: 3, success: 2, failure: 1, unknown: 0 },
    evidence_summary: [],
    attention_point: '记录到 1 次失败运行；建议查看对应时间点的运行记录。',
    teacher_evaluation: {
      label: '课题实践表现',
      overall_grade: 'A',
      evidence_confidence: 'high',
      summary: '基于 3 次可判定运行与 1 个完整工作闭环，课题实践表现为 A。',
      dimensions: [
        { name: '运行验证', score: 40, maximum_score: 55, evidence_event_ids: ['event-1'] },
        { name: '调试与修正', score: 25, maximum_score: 25, evidence_event_ids: ['event-2'] },
        { name: '任务推进', score: 20, maximum_score: 20, evidence_event_ids: ['event-3'] },
      ],
      classroom_focus: {
        reference: 'stable',
        focus_loss_count: 1,
        focus_loss_milliseconds: 10_000,
        longest_focus_loss_milliseconds: 10_000,
        unclosed_focus_loss: false,
        note: '课堂专注记录较稳定，仅供教师参考，不参与课题实践表现评级。',
      },
      metrics: {
        edit_count: 3,
        save_count: 2,
        run_count: 3,
        determinate_run_count: 3,
        successful_run_count: 2,
        failed_run_count: 1,
        unknown_run_count: 0,
        execution_success_rate: 66.67,
        recovery_success_count: 1,
        complete_work_cycle_count: 1,
      },
      teaching_suggestion: '可在课后复盘一次“发现问题—修改—再次验证”的过程，巩固调试策略。',
      limitations: '该结论仅基于本扩展记录的编辑、保存和运行事件；运行成功不等同于题目答案正确，课堂专注参考不参与评级。',
    },
  };
}

describe('renderTeacherBrief', () => {
  it('renders a teacher-first Markdown brief without raw path or terminal content', () => {
    const markdown = renderTeacherBrief(brief());

    expect(markdown).toContain('# 课堂简报');
    expect(markdown).toContain('## 课题实践表现：A');
    expect(markdown).toContain('## 三项证据维度');
    expect(markdown).toContain('## 课堂专注参考（不参与评级）');
    expect(markdown).toContain('## 教学建议');
    expect(markdown).toContain('运行成功不等同于题目答案正确');
    expect(markdown).not.toMatch(/\/Users\/|terminal|stderr|stdout/i);
  });
});
