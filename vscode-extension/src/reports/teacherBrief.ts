import type { ClassroomBriefV2 } from '../domain/types';

function formatDuration(milliseconds: number): string {
  const totalSeconds = Math.round(milliseconds / 1000);
  if (totalSeconds < 60) return `${String(totalSeconds)} 秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds === 0 ? `${String(minutes)} 分钟` : `${String(minutes)} 分 ${String(seconds)} 秒`;
}

function confidenceLabel(confidence: ClassroomBriefV2['teacher_evaluation']['evidence_confidence']): string {
  switch (confidence) {
    case 'high':
      return '高：记录中有至少 3 次可判定运行和完整工作闭环。';
    case 'medium':
      return '中：已有可判定运行，但样本数量有限。';
    case 'low':
      return '低：尚未形成可判定运行结果。';
  }
}

export function renderTeacherBrief(brief: ClassroomBriefV2): string {
  const evaluation = brief.teacher_evaluation;
  const dimensionRows = evaluation.dimensions
    .map((dimension) => `| ${dimension.name} | ${String(dimension.score)} / ${String(dimension.maximum_score)} |`)
    .join('\n');
  const successRate =
    evaluation.metrics.execution_success_rate === null
      ? '暂无可判定运行'
      : `${String(evaluation.metrics.execution_success_rate)}%`;

  return [
    '# 课堂简报',
    '',
    `生成时间：${brief.generated_at}`,
    '',
    `## 课题实践表现：${evaluation.overall_grade}`,
    '',
    `证据置信度：${confidenceLabel(evaluation.evidence_confidence)}`,
    '',
    evaluation.summary,
    '',
    '## 三项证据维度',
    '',
    '| 维度 | 记录得分 |',
    '| --- | --- |',
    dimensionRows,
    '',
    '## 关键过程记录',
    '',
    `- 编辑：${String(evaluation.metrics.edit_count)} 次；保存：${String(evaluation.metrics.save_count)} 次。`,
    `- 运行：${String(evaluation.metrics.run_count)} 次（可判定 ${String(evaluation.metrics.determinate_run_count)} 次，运行层面成功率 ${successRate}）。`,
    `- 失败后完成“修改并再次运行”的记录：${String(evaluation.metrics.recovery_success_count)} 次；完整工作闭环：${String(evaluation.metrics.complete_work_cycle_count)} 个。`,
    '',
    '## 课堂专注参考（不参与评级）',
    '',
    `- 焦点离开：${String(evaluation.classroom_focus.focus_loss_count)} 次；累计 ${formatDuration(evaluation.classroom_focus.focus_loss_milliseconds)}；最长 ${formatDuration(evaluation.classroom_focus.longest_focus_loss_milliseconds)}。`,
    `- 参考状态：${evaluation.classroom_focus.reference}${evaluation.classroom_focus.unclosed_focus_loss ? '（会话结束时仍未恢复焦点）' : ''}。`,
    `- ${evaluation.classroom_focus.note}`,
    '',
    '## 教学建议',
    '',
    evaluation.teaching_suggestion,
    '',
    '## 使用边界',
    '',
    evaluation.limitations,
    '',
  ].join('\n');
}
