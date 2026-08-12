import { AuditError } from '../domain/errors';
import { parsePlanDraft, type PlanDraft } from '../plans/planDraft';

export type PlanWizardMessage =
  | { readonly type: 'ready' }
  | { readonly type: 'saveDraft'; readonly draft: PlanDraft }
  | { readonly type: 'requestSuggestion'; readonly problemText: string }
  | { readonly type: 'publishDraft'; readonly draft: PlanDraft }
  | { readonly type: 'exportPublishedPlan' }
  | { readonly type: 'closeWizard' };

export interface PlanWizardViewState {
  readonly draft?: PlanDraft;
  readonly aiConfigured?: boolean;
  readonly busy: boolean;
  readonly published?: { readonly planId: string; readonly version: number };
  readonly notice?: {
    readonly kind: 'info' | 'warning' | 'error';
    readonly message: string;
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const expected = [...keys].sort();
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function invalidMessage(): AuditError {
  return new AuditError('import_invalid', '方案向导消息格式无效。', '请关闭后重新打开方案向导。');
}

export function parsePlanWizardMessage(value: unknown): PlanWizardMessage {
  if (!isRecord(value) || typeof value.type !== 'string') {
    throw invalidMessage();
  }
  if (
    (value.type === 'ready' || value.type === 'exportPublishedPlan' || value.type === 'closeWizard') &&
    exactKeys(value, ['type'])
  ) {
    return { type: value.type };
  }
  if ((value.type === 'saveDraft' || value.type === 'publishDraft') && exactKeys(value, ['type', 'draft'])) {
    const draft = parsePlanDraft(value.draft);
    if (draft !== undefined) {
      return { type: value.type, draft };
    }
  }
  if (
    value.type === 'requestSuggestion' &&
    exactKeys(value, ['type', 'problemText']) &&
    typeof value.problemText === 'string' &&
    value.problemText.trim().length > 0 &&
    value.problemText.length <= 20_000
  ) {
    return { type: 'requestSuggestion', problemText: value.problemText };
  }
  throw invalidMessage();
}
