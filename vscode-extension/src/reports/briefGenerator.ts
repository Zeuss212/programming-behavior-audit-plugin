import { canonicalJson } from '../domain/canonicalJson';
import { AuditError } from '../domain/errors';
import {
  CLASSROOM_BRIEF_SCHEMA_VERSION,
  type AuditEvent,
  type ClassroomBriefV2,
  type EvidenceSummaryItem,
  type JsonValue,
  type PublishedPlan,
  type SessionState,
} from '../domain/types';
import { evaluateTeacherEvidence, runOutcome } from './teacherEvaluation';

const TERMINAL_STATUSES = ['completed', 'partial', 'abandoned'] as const;
const QUALIFYING_KINDS = new Set<AuditEvent['kind']>([
  'edit',
  'paste_shortcut',
  'save',
  'python_run',
  'notebook_edit',
  'notebook_run',
]);
const MAX_EVIDENCE_ITEMS = 20;
const MAX_EVIDENCE_BYTES = 8 * 1024;
const MAX_FOCUSED_GAP_MS = 30_000;

export interface BriefInput {
  readonly session: SessionState;
  readonly plan: PublishedPlan;
  readonly events: readonly AuditEvent[];
  readonly generatedAt: string;
}

export type ReportInput = Omit<BriefInput, 'generatedAt'>;

function isTerminal(
  status: SessionState['status'],
): status is (typeof TERMINAL_STATUSES)[number] {
  return TERMINAL_STATUSES.includes(status as (typeof TERMINAL_STATUSES)[number]);
}

export function orderReportEvents(input: ReportInput): readonly AuditEvent[] {
  if (!isTerminal(input.session.status)) {
    throw new AuditError(
      'session_conflict',
      '只有已结束的会话可以生成课堂简报。',
      '请先正常结束、部分结束或放弃当前会话。',
    );
  }
  if (
    input.plan.plan_id !== input.session.plan_id ||
    input.plan.version !== input.session.plan_version ||
    input.plan.content_sha256 !== input.session.plan_content_sha256
  ) {
    throw new AuditError(
      'storage_corrupt',
      '会话与方案快照不一致。',
      '请保留本地文件并导出诊断信息。',
    );
  }

  const ordered = [...input.events].sort((left, right) => left.session_seq - right.session_seq);
  for (const [index, event] of ordered.entries()) {
    const expected = index + 1;
    if (
      event.session_id !== input.session.session_id ||
      event.session_seq !== expected ||
      event.event_id !== `${input.session.session_id}:${String(expected)}`
    ) {
      throw new AuditError(
        'session_sequence_invalid',
        `课堂简报要求从 1 开始连续的事件序号，当前缺少序号 ${String(expected)}。`,
        '请保留原始事件文件并导出诊断信息。',
      );
    }
  }
  if (ordered.length !== input.session.last_persisted_seq) {
    throw new AuditError(
      'session_sequence_invalid',
      '事件数量与会话最后保存序号不一致。',
      '请保留原始事件文件并导出诊断信息。',
    );
  }
  return ordered;
}

function booleanPayload(event: AuditEvent, key: string): boolean | undefined {
  const value = event.payload[key];
  return typeof value === 'boolean' ? value : undefined;
}

function effectiveObservation(events: readonly AuditEvent[]): number {
  let focused = true;
  let previousQualifying: number | undefined;
  let total = 0;
  for (const event of events) {
    if (event.kind === 'window_focus') {
      const nextFocused = booleanPayload(event, 'focused');
      if (nextFocused !== undefined && nextFocused !== focused) {
        focused = nextFocused;
        previousQualifying = undefined;
      }
      continue;
    }
    if (!focused || !QUALIFYING_KINDS.has(event.kind)) {
      continue;
    }
    if (previousQualifying !== undefined) {
      const gap = event.monotonic_ms - previousQualifying;
      if (gap > 0) {
        total += Math.min(gap, MAX_FOCUSED_GAP_MS);
      }
    }
    previousQualifying = event.monotonic_ms;
  }
  return total;
}

function objectiveSummary(event: AuditEvent): string {
  const document = event.document?.relative_uri;
  const suffix = document === undefined ? '' : `（${document}）`;
  switch (event.kind) {
    case 'edit':
      return `记录到一次文本编辑${suffix}。`;
    case 'paste_shortcut':
      return `记录到一次由扩展快捷键触发的粘贴${suffix}。`;
    case 'save':
      return `记录到一次文件保存${suffix}。`;
    case 'python_run': {
      const outcome = runOutcome(event);
      return outcome === 'success'
        ? '记录到一次成功的 Python 运行。'
        : outcome === 'failure'
          ? '记录到一次失败的 Python 运行。'
          : '记录到一次结果未知的 Python 运行。';
    }
    case 'notebook_edit':
      return `记录到一次 Notebook 结构修改${suffix}。`;
    case 'notebook_run': {
      const outcome = runOutcome(event);
      return outcome === 'success'
        ? '记录到一次成功的 Notebook 单元格运行。'
        : outcome === 'failure'
          ? '记录到一次失败的 Notebook 单元格运行。'
          : '记录到一次结果未知的 Notebook 单元格运行。';
    }
    case 'document_focus':
      return `记录到编辑文档切换${suffix}。`;
    case 'window_focus':
      return booleanPayload(event, 'focused') === false
        ? '记录到 VS Code 窗口失去焦点。'
        : '记录到 VS Code 窗口恢复焦点。';
    case 'external_terminal_activity':
      return '记录到外部终端活动提示；未采集命令或输出内容。';
  }
}

function evidenceSummary(events: readonly AuditEvent[]): readonly EvidenceSummaryItem[] {
  const items: EvidenceSummaryItem[] = events.slice(0, MAX_EVIDENCE_ITEMS).map((event) => ({
    occurred_at: event.occurred_at,
    kind: event.kind,
    summary: objectiveSummary(event),
  }));
  while (
    items.length > 0 &&
    Buffer.byteLength(canonicalJson(items as unknown as JsonValue), 'utf8') > MAX_EVIDENCE_BYTES
  ) {
    items.pop();
  }
  return items;
}

export function generateClassroomBrief(input: BriefInput): ClassroomBriefV2 {
  const ordered = orderReportEvents(input);
  if (!isTerminal(input.session.status)) {
    throw new AuditError(
      'session_conflict',
      '只有已结束的会话可以生成课堂简报。',
      '请先结束当前会话。',
    );
  }
  const outcomes = ordered.map(runOutcome).filter((value) => value !== undefined);
  const success = outcomes.filter((value) => value === 'success').length;
  const failure = outcomes.filter((value) => value === 'failure').length;
  const unknown = outcomes.filter((value) => value === 'unknown').length;

  return {
    schema_version: CLASSROOM_BRIEF_SCHEMA_VERSION,
    session_id: input.session.session_id,
    generated_at: input.generatedAt,
    session_result: {
      status: input.session.status,
      ...(input.session.status_reason === undefined ? {} : { reason: input.session.status_reason }),
    },
    effective_observation: {
      milliseconds: effectiveObservation(ordered),
      method: 'focused_event_gaps_capped_at_30_seconds',
    },
    run_statistics: {
      total: outcomes.length,
      success,
      failure,
      unknown,
    },
    evidence_summary: evidenceSummary(ordered),
    attention_point:
      failure === 0
        ? null
        : `记录到 ${String(failure)} 次失败运行；建议查看对应时间点的运行记录。`,
    teacher_evaluation: evaluateTeacherEvidence(ordered),
  };
}
