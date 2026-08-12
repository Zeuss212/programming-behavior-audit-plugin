import { AuditError } from '../domain/errors';

export const AUDIT_COMMAND_IDS = [
  'behaviorAudit.openTeacher',
  'behaviorAudit.openStudent',
  'behaviorAudit.openPlanWizard',
  'behaviorAudit.publishPlan',
  'behaviorAudit.suggestPlan',
  'behaviorAudit.importPlan',
  'behaviorAudit.exportPlan',
  'behaviorAudit.startCapture',
  'behaviorAudit.resumeCapture',
  'behaviorAudit.finishCapture',
  'behaviorAudit.abandonCapture',
  'behaviorAudit.runPython',
  'behaviorAudit.analyzeSession',
  'behaviorAudit.exportSession',
  'behaviorAudit.openDataLocation',
  'behaviorAudit.configureAiKey',
  'behaviorAudit.clearAiKey',
  'behaviorAudit.pasteAndRecord',
] as const;

export type AuditCommandId = (typeof AUDIT_COMMAND_IDS)[number];
export type SidebarRoute = 'teacher' | 'student';

export type WebviewMessage =
  | { readonly type: 'navigate'; readonly route: SidebarRoute }
  | { readonly type: 'setConsent'; readonly value: boolean }
  | { readonly type: 'command'; readonly command: AuditCommandId }
  | { readonly type: 'refresh' };

export interface SidebarPlanSummary {
  readonly planId: string;
  readonly version: number;
  readonly problemText: string;
}

export interface SidebarViewModel {
  readonly route: SidebarRoute;
  readonly trusted: boolean;
  readonly plans: readonly SidebarPlanSummary[];
  readonly selectedPlan?: Readonly<{ planId: string; version: number }>;
  readonly consent: boolean;
  readonly session?: Readonly<{
    sessionId: string;
    status: 'collecting' | 'interrupted' | 'finalizing' | 'completed' | 'partial' | 'abandoned';
    eventCount: number;
  }>;
  readonly ai: Readonly<{ configured: boolean }>;
  readonly notice?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === keys.length && actual.every((key, index) => key === [...keys].sort()[index]);
}

function invalidMessage(): AuditError {
  return new AuditError(
    'import_invalid',
    '侧边栏消息格式无效。',
    '请刷新侧边栏后重试。',
  );
}

export function parseWebviewMessage(value: unknown): WebviewMessage {
  if (!isRecord(value) || typeof value.type !== 'string') {
    throw invalidMessage();
  }
  if (
    value.type === 'navigate' &&
    hasExactKeys(value, ['type', 'route']) &&
    (value.route === 'teacher' || value.route === 'student')
  ) {
    return { type: 'navigate', route: value.route };
  }
  if (
    value.type === 'setConsent' &&
    hasExactKeys(value, ['type', 'value']) &&
    typeof value.value === 'boolean'
  ) {
    return { type: 'setConsent', value: value.value };
  }
  if (
    value.type === 'command' &&
    hasExactKeys(value, ['type', 'command']) &&
    typeof value.command === 'string' &&
    AUDIT_COMMAND_IDS.includes(value.command as AuditCommandId)
  ) {
    return { type: 'command', command: value.command as AuditCommandId };
  }
  if (value.type === 'refresh' && hasExactKeys(value, ['type'])) {
    return { type: 'refresh' };
  }
  throw invalidMessage();
}
