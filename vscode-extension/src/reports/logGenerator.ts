import { canonicalJson } from '../domain/canonicalJson';
import type { AuditEvent, JsonValue } from '../domain/types';
import { orderReportEvents, type ReportInput } from './briefGenerator';

const encoder = new TextEncoder();

function operationEvent(event: AuditEvent): JsonValue {
  return {
    event_id: event.event_id,
    session_seq: event.session_seq,
    occurred_at: event.occurred_at,
    monotonic_ms: event.monotonic_ms,
    kind: event.kind,
    ...(event.document === undefined
      ? {}
      : {
          document: {
            relative_uri: event.document.relative_uri,
            language_id: event.document.language_id,
            ...(event.document.notebook_cell_id === undefined
              ? {}
              : { notebook_cell_id: event.document.notebook_cell_id }),
          },
        }),
    payload: event.payload,
  };
}

export function generateOperationLog(input: ReportInput): Uint8Array {
  const events = orderReportEvents(input);
  const value: JsonValue = {
    session: input.session as unknown as JsonValue,
    plan: input.plan as unknown as JsonValue,
    events: events.map(operationEvent),
  };
  return encoder.encode(`${canonicalJson(value)}\n`);
}

function payloadNumber(event: AuditEvent, key: string): number | null | undefined {
  const value = event.payload[key];
  return typeof value === 'number' || value === null ? value : undefined;
}

function payloadString(event: AuditEvent, key: string): string | undefined {
  const value = event.payload[key];
  return typeof value === 'string' ? value : undefined;
}

function describeEvent(event: AuditEvent): string {
  const document = event.document?.relative_uri;
  switch (event.kind) {
    case 'edit':
      return document === undefined ? '编辑了受支持的文本文件。' : `编辑了 \`${document}\`。`;
    case 'paste_shortcut':
      return document === undefined ? '通过扩展快捷键完成粘贴。' : `在 \`${document}\` 中通过扩展快捷键完成粘贴。`;
    case 'save':
      return document === undefined ? '保存了文件。' : `保存了 \`${document}\`。`;
    case 'python_run': {
      const exitCode = payloadNumber(event, 'exit_code');
      return exitCode === 0
        ? 'Python 运行成功。'
        : typeof exitCode === 'number'
          ? `Python 运行失败，退出码为 ${String(exitCode)}。`
          : 'Python 运行结果未知。';
    }
    case 'notebook_edit':
      return '修改了 Notebook 的单元格结构。';
    case 'notebook_run': {
      const outcome = payloadString(event, 'outcome');
      return outcome === 'success'
        ? 'Notebook 单元格运行成功。'
        : outcome === 'failure'
          ? 'Notebook 单元格运行失败。'
          : 'Notebook 单元格运行结果未知。';
    }
    case 'document_focus':
      return document === undefined ? '切换了编辑文档。' : `切换到 \`${document}\`。`;
    case 'window_focus':
      return event.payload.focused === false ? 'VS Code 窗口失去焦点。' : 'VS Code 窗口恢复焦点。';
    case 'external_terminal_activity':
      return '出现外部终端活动提示；扩展未采集命令或输出内容。';
  }
}

function statusLabel(status: ReportInput['session']['status']): string {
  if (status === 'completed') {
    return '正常完成';
  }
  if (status === 'partial') {
    return '部分完成';
  }
  return '已放弃';
}

export function generateProcessLog(input: ReportInput): Uint8Array {
  const events = orderReportEvents(input);
  const lines = [
    '# 编程行为过程日志',
    '',
    `- 会话：${input.session.session_id}`,
    `- 方案：${input.plan.plan_id}（版本 ${String(input.plan.version)}）`,
    `- 会话结果：${statusLabel(input.session.status)}`,
    '',
    '## 时间线',
    '',
    ...events.map(
      (event) => `${String(event.session_seq)}. ${describeEvent(event)}（${event.occurred_at}）`,
    ),
    '',
  ];
  return encoder.encode(lines.join('\n'));
}
