import { AuditError } from '../domain/errors';
import type { PublishedPlan } from '../domain/types';
import type { CaptureController } from '../capture/captureController';
import type { ReportService } from '../reports/exporter';
import {
  AUDIT_COMMAND_IDS,
  type AuditCommandId,
} from '../ui/protocol';

export { AUDIT_COMMAND_IDS } from '../ui/protocol';

export const CONFIRMATION_COMMAND_IDS = [
  'behaviorAudit.publishPlan',
  'behaviorAudit.suggestPlan',
  'behaviorAudit.importPlan',
  'behaviorAudit.finishCapture',
  'behaviorAudit.abandonCapture',
  'behaviorAudit.analyzeSession',
  'behaviorAudit.clearAiKey',
] as const satisfies readonly AuditCommandId[];

export interface CommandDisposable {
  dispose(): void;
}

export interface AuditCommandHost {
  registerCommand(id: AuditCommandId, handler: () => Promise<void>): CommandDisposable;
  confirm(id: AuditCommandId): Promise<boolean>;
  showError(message: string, ...actions: readonly string[]): Promise<string | undefined>;
  isWorkspaceTrusted(): boolean;
}

export type CommandActions = Readonly<Record<AuditCommandId, () => Promise<void>>>;

export interface AuditCommandServices {
  readonly capture: CaptureController;
  readonly reportService: ReportService;
  readonly selectedPlan: () => PublishedPlan | undefined;
  readonly hasConsent: () => boolean;
  readonly interruptedSessionId: () => string | undefined;
  readonly actions: CommandActions;
}

async function reportError(host: AuditCommandHost, error: unknown): Promise<void> {
  const message = error instanceof Error ? error.message : '操作失败，请重试。';
  await host.showError(message);
}

async function materializeWithRetry(
  host: AuditCommandHost,
  services: AuditCommandServices,
  sessionId: string,
): Promise<void> {
  try {
    await services.reportService.materialize(sessionId);
  } catch (error) {
    const choice = await host.showError(
      error instanceof Error ? error.message : '课堂简报生成失败。',
      '重试生成简报',
    );
    if (choice === '重试生成简报') {
      await services.reportService.materialize(sessionId);
    }
  }
}

async function startCapture(
  host: AuditCommandHost,
  services: AuditCommandServices,
): Promise<void> {
  const selectedPlan = services.selectedPlan();
  if (selectedPlan === undefined) {
    throw new AuditError('session_conflict', '尚未选择已发布方案。', '请先选择或导入方案。');
  }
  if (!services.hasConsent()) {
    throw new AuditError(
      'session_conflict',
      '开始监控前必须明确确认采集范围。',
      '请勾选知情确认后重试。',
    );
  }
  if (!host.isWorkspaceTrusted()) {
    throw new AuditError(
      'workspace_untrusted',
      '未受信工作区不能开始监控。',
      '请确认工作区来源并设为受信。',
    );
  }
  if (services.capture.current() !== undefined) {
    throw new AuditError('session_conflict', '当前已有活动会话。', '请先结束当前会话。');
  }
  await services.capture.start(selectedPlan, true);
}

async function execute(
  id: AuditCommandId,
  host: AuditCommandHost,
  services: AuditCommandServices,
): Promise<void> {
  switch (id) {
    case 'behaviorAudit.startCapture':
      await startCapture(host, services);
      return;
    case 'behaviorAudit.resumeCapture': {
      const sessionId = services.interruptedSessionId();
      if (sessionId === undefined) {
        throw new AuditError(
          'session_recovery_required',
          '没有可恢复的中断会话。',
          '请刷新状态或开始新会话。',
        );
      }
      await services.capture.resume(sessionId);
      return;
    }
    case 'behaviorAudit.finishCapture': {
      const terminal = await services.capture.finish('completed');
      await materializeWithRetry(host, services, terminal.session_id);
      return;
    }
    case 'behaviorAudit.abandonCapture': {
      const terminal = await services.capture.finish('abandoned', '用户明确放弃本次会话。');
      await materializeWithRetry(host, services, terminal.session_id);
      return;
    }
    default:
      await services.actions[id]();
  }
}

export function registerAuditCommands(
  host: AuditCommandHost,
  services: AuditCommandServices,
): readonly CommandDisposable[] {
  return AUDIT_COMMAND_IDS.map((id) =>
    host.registerCommand(id, async () => {
      if (CONFIRMATION_COMMAND_IDS.includes(id as (typeof CONFIRMATION_COMMAND_IDS)[number])) {
        if (!(await host.confirm(id))) {
          return;
        }
      }
      try {
        await execute(id, host, services);
      } catch (error) {
        await reportError(host, error);
      }
    }),
  );
}
