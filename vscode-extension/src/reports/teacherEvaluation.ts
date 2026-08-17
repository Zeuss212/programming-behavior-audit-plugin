import type {
  AuditEvent,
  ClassroomFocusReference,
  TeacherEvaluation,
  TeacherEvaluationDimension,
  TeacherPerformanceGrade,
} from '../domain/types';

type RunOutcome = 'success' | 'failure' | 'unknown';

function booleanPayload(event: AuditEvent, key: string): boolean | undefined {
  const value = event.payload[key];
  return typeof value === 'boolean' ? value : undefined;
}

function numberPayload(event: AuditEvent, key: string): number | null | undefined {
  const value = event.payload[key];
  return typeof value === 'number' || value === null ? value : undefined;
}

function stringPayload(event: AuditEvent, key: string): string | undefined {
  const value = event.payload[key];
  return typeof value === 'string' ? value : undefined;
}

export function runOutcome(event: AuditEvent): RunOutcome | undefined {
  if (event.kind === 'python_run') {
    if (booleanPayload(event, 'timed_out') === true || booleanPayload(event, 'launch_failed') === true) {
      return 'failure';
    }
    const exitCode = numberPayload(event, 'exit_code');
    if (exitCode === 0) {
      return 'success';
    }
    return typeof exitCode === 'number' ? 'failure' : 'unknown';
  }
  if (event.kind === 'notebook_run') {
    const outcome = stringPayload(event, 'outcome');
    return outcome === 'success' || outcome === 'failure' ? outcome : 'unknown';
  }
  return undefined;
}

function scoreToGrade(score: number): TeacherPerformanceGrade {
  if (score >= 90) return 'S';
  if (score >= 75) return 'A';
  if (score >= 60) return 'B';
  if (score >= 40) return 'C';
  return 'D';
}

function capAtB(grade: TeacherPerformanceGrade): TeacherPerformanceGrade {
  return grade === 'S' || grade === 'A' ? 'B' : grade;
}

function focusReference(
  count: number,
  milliseconds: number,
  hasFocusEvents: boolean,
): ClassroomFocusReference {
  if (!hasFocusEvents) return 'insufficient';
  if (count <= 2 && milliseconds <= 5 * 60_000) return 'stable';
  if (count <= 5 && milliseconds <= 15 * 60_000) return 'fluctuating';
  return 'frequent_switching';
}

function focusNote(reference: ClassroomFocusReference): string {
  switch (reference) {
    case 'stable':
      return '课堂专注记录较稳定，仅供教师参考，不参与课题实践表现评级。';
    case 'fluctuating':
      return '课堂专注记录存在波动，仅供教师参考，不参与课题实践表现评级。';
    case 'frequent_switching':
      return '课堂专注记录显示较多切换或较长离开时段，仅供教师参考，不参与课题实践表现评级。';
    case 'insufficient':
      return '未形成完整的窗口焦点记录，无法据此判断课堂专注情况。';
  }
}

function eventIds(events: readonly AuditEvent[]): readonly string[] {
  return events.map((event) => event.event_id);
}

function hasLaterEditOrSave(events: readonly AuditEvent[], index: number): boolean {
  return events.slice(index + 1).some((event) =>
    event.kind === 'edit' || event.kind === 'notebook_edit' || event.kind === 'save',
  );
}

function hasLaterSuccessAfterWork(events: readonly AuditEvent[], index: number): boolean {
  const workIndex = events.findIndex(
    (event, offset) =>
      offset > index &&
      (event.kind === 'edit' || event.kind === 'notebook_edit' || event.kind === 'save'),
  );
  return workIndex >= 0 && events.slice(workIndex + 1).some((event) => runOutcome(event) === 'success');
}

function countCompleteWorkCycles(events: readonly AuditEvent[]): number {
  let textEdited = false;
  let textSaved = false;
  let notebookEdited = false;
  let complete = 0;

  for (const event of events) {
    if (event.kind === 'edit') {
      textEdited = true;
      textSaved = false;
      continue;
    }
    if (event.kind === 'save' && textEdited) {
      textSaved = true;
      continue;
    }
    if (event.kind === 'notebook_edit') {
      notebookEdited = true;
      continue;
    }
    if (runOutcome(event) !== undefined) {
      if (textSaved || (notebookEdited && event.kind === 'notebook_run')) {
        complete += 1;
      }
      textEdited = false;
      textSaved = false;
      notebookEdited = false;
    }
  }
  return complete;
}

export function evaluateTeacherEvidence(events: readonly AuditEvent[]): TeacherEvaluation {
  const ordered = [...events].sort((left, right) => left.session_seq - right.session_seq);
  const runEvents = ordered.filter((event) => runOutcome(event) !== undefined);
  const successEvents = runEvents.filter((event) => runOutcome(event) === 'success');
  const failureEvents = runEvents.filter((event) => runOutcome(event) === 'failure');
  const unknownEvents = runEvents.filter((event) => runOutcome(event) === 'unknown');
  const determinateRunCount = successEvents.length + failureEvents.length;
  const executionSuccessRate =
    determinateRunCount === 0 ? null : Math.round((successEvents.length / determinateRunCount) * 10000) / 100;
  const editEvents = ordered.filter((event) => event.kind === 'edit' || event.kind === 'notebook_edit');
  const saveEvents = ordered.filter((event) => event.kind === 'save');
  const completeWorkCycleCount = countCompleteWorkCycles(ordered);
  const recoverySuccessCount = ordered.filter(
    (event, index) => runOutcome(event) === 'failure' && hasLaterSuccessAfterWork(ordered, index),
  ).length;

  const executionScore =
    determinateRunCount === 0
      ? 0
      : successEvents.length === 0
        ? 15
        : determinateRunCount === 1
          ? 25
          : executionSuccessRate !== null && executionSuccessRate < 50
            ? 25
            : executionSuccessRate !== null && executionSuccessRate < 80
              ? 40
              : determinateRunCount >= 3
                ? 55
                : 40;
  const debuggingScore =
    runEvents.length === 0
      ? 0
      : failureEvents.length === 0
        ? successEvents.length > 0
          ? 25
          : 0
        : recoverySuccessCount > 0
          ? 25
          : failureEvents.some((event) => hasLaterEditOrSave(ordered, ordered.indexOf(event)))
            ? 15
            : 5;
  const progressScore =
    completeWorkCycleCount > 0
      ? 20
      : (editEvents.length > 0 || saveEvents.length > 0) && runEvents.length > 0
        ? 12
        : editEvents.length > 0 || saveEvents.length > 0
          ? 5
          : 0;

  const dimensions: readonly TeacherEvaluationDimension[] = [
    {
      name: '运行验证',
      score: executionScore,
      maximum_score: 55,
      evidence_event_ids: eventIds(runEvents),
    },
    {
      name: '调试与修正',
      score: debuggingScore,
      maximum_score: 25,
      evidence_event_ids: eventIds([...failureEvents, ...successEvents]),
    },
    {
      name: '任务推进',
      score: progressScore,
      maximum_score: 20,
      evidence_event_ids: eventIds([...editEvents, ...saveEvents, ...runEvents]),
    },
  ];
  const rawScore = executionScore + debuggingScore + progressScore;
  const overallGrade =
    determinateRunCount === 0 ? 'D' : determinateRunCount < 3 ? capAtB(scoreToGrade(rawScore)) : scoreToGrade(rawScore);
  const evidenceConfidence =
    determinateRunCount >= 3 && completeWorkCycleCount > 0
      ? 'high'
      : determinateRunCount >= 1
        ? 'medium'
        : 'low';

  let focusLossCount = 0;
  let focusLossMilliseconds = 0;
  let longestFocusLossMilliseconds = 0;
  let focusLostAt: number | undefined;
  let hasFocusEvents = false;
  for (const event of ordered) {
    if (event.kind !== 'window_focus') continue;
    const focused = booleanPayload(event, 'focused');
    if (focused === undefined) continue;
    hasFocusEvents = true;
    if (!focused && focusLostAt === undefined) {
      focusLostAt = event.monotonic_ms;
      continue;
    }
    if (focused && focusLostAt !== undefined) {
      const duration = Math.max(0, event.monotonic_ms - focusLostAt);
      focusLossCount += 1;
      focusLossMilliseconds += duration;
      longestFocusLossMilliseconds = Math.max(longestFocusLossMilliseconds, duration);
      focusLostAt = undefined;
    }
  }
  const classroomFocusReference = focusReference(focusLossCount, focusLossMilliseconds, hasFocusEvents);

  const summary =
    determinateRunCount === 0
      ? '尚未形成可验证成果：本次记录没有可判定结果的运行。'
      : `基于 ${String(determinateRunCount)} 次可判定运行与 ${String(completeWorkCycleCount)} 个完整工作闭环，课题实践表现为 ${overallGrade}。`;
  const teachingSuggestion =
    determinateRunCount === 0
      ? '建议课后补充一次可判定运行，并保留编辑、保存和运行过程。'
      : recoverySuccessCount > 0
        ? '可在课后复盘一次“发现问题—修改—再次验证”的过程，巩固调试策略。'
        : '建议结合课堂任务要求，进一步查看运行记录与提交成果。';
  const limitations =
    '该结论仅基于本扩展记录的编辑、保存和运行事件；运行成功不等同于题目答案正确，课堂专注参考不参与评级。';

  return {
    label: '课题实践表现',
    overall_grade: overallGrade,
    evidence_confidence: evidenceConfidence,
    summary,
    dimensions,
    classroom_focus: {
      reference: classroomFocusReference,
      focus_loss_count: focusLossCount,
      focus_loss_milliseconds: focusLossMilliseconds,
      longest_focus_loss_milliseconds: longestFocusLossMilliseconds,
      unclosed_focus_loss: focusLostAt !== undefined,
      note: focusNote(classroomFocusReference),
    },
    metrics: {
      edit_count: editEvents.length,
      save_count: saveEvents.length,
      run_count: runEvents.length,
      determinate_run_count: determinateRunCount,
      successful_run_count: successEvents.length,
      failed_run_count: failureEvents.length,
      unknown_run_count: unknownEvents.length,
      execution_success_rate: executionSuccessRate,
      recovery_success_count: recoverySuccessCount,
      complete_work_cycle_count: completeWorkCycleCount,
    },
    teaching_suggestion: teachingSuggestion,
    limitations,
  };
}
