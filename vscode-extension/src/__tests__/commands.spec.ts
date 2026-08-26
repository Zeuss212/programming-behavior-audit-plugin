import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it, vi } from 'vitest';

import {
  AUDIT_COMMAND_IDS,
  CONFIRMATION_COMMAND_IDS,
  registerAuditCommands,
  type AuditCommandHost,
  type AuditCommandServices,
} from '../commands/registerCommands';
import { PLAN_SCHEMA_VERSION, type PublishedPlan } from '../domain/types';

function plan(): PublishedPlan {
  return {
    schema_version: PLAN_SCHEMA_VERSION,
    plan_id: 'plan-command-001',
    version: 1,
    problem_text: '实现空列表处理。',
    knowledge_points: [
      {
        knowledge_point_id: 'kp-empty',
        name: '空列表处理',
        description: '处理空列表。',
        observation_basis: '运行空列表用例。',
      },
    ],
    tests: [],
    published_at: '2026-08-10T00:00:00.000Z',
    content_sha256: 'd'.repeat(64),
  };
}

function setup(overrides: Partial<AuditCommandServices> = {}) {
  const handlers = new Map<string, () => Promise<void>>();
  const confirm = vi.fn(() => Promise.resolve(true));
  const showError = vi.fn(() => Promise.resolve<string | undefined>(undefined));
  const host: AuditCommandHost = {
    registerCommand: (id, handler) => {
      handlers.set(id, handler);
      return { dispose: () => undefined };
    },
    confirm,
    showError,
    isWorkspaceTrusted: () => true,
  };
  const actions = Object.fromEntries(
    AUDIT_COMMAND_IDS.map((id) => [id, vi.fn(() => Promise.resolve())]),
  ) as unknown as AuditCommandServices['actions'];
  const captureStart = vi.fn(() => Promise.resolve({ session_id: 'session-command-001' } as never));
  const captureFinish = vi.fn(() =>
    Promise.resolve({ session_id: 'session-command-001', status: 'completed' } as never),
  );
  const captureCurrent = vi.fn(() => undefined);
  const reportMaterialize = vi.fn(() => Promise.resolve({} as never));
  const onBriefReady = vi.fn(() => Promise.resolve());
  const finishAnalyzeExport = vi.fn(() => Promise.resolve());
  const services: AuditCommandServices = {
    capture: {
      start: captureStart,
      resume: vi.fn(() => Promise.resolve({ session_id: 'session-command-001' } as never)),
      finish: captureFinish,
      flush: vi.fn(() => Promise.resolve()),
      current: captureCurrent,
      record: vi.fn(),
    },
    reportService: { materialize: reportMaterialize },
    selectedPlan: () => plan(),
    hasConsent: () => true,
    interruptedSessionId: () => 'session-command-001',
    onBriefReady,
    finishAnalyzeExport,
    actions,
    ...overrides,
  };
  const disposables = registerAuditCommands(host, services);
  return {
    handlers,
    host,
    services,
    actions,
    disposables,
    spies: {
      confirm,
      showError,
      captureStart,
      captureFinish,
      captureCurrent,
      reportMaterialize,
      onBriefReady,
      finishAnalyzeExport,
    },
  };
}

describe('registerAuditCommands', () => {
  it('registers exactly the locked command IDs', () => {
    const { handlers } = setup();
    expect([...handlers.keys()].sort()).toEqual([...AUDIT_COMMAND_IDS].sort());
    expect(handlers.has('behaviorAudit.openPlanWizard')).toBe(true);
  });

  it('keeps manifest commands and the single sidebar view synchronized with code', () => {
    const manifest = JSON.parse(
      readFileSync(resolve(process.cwd(), 'package.json'), 'utf8'),
    ) as {
      readonly contributes: {
        readonly commands: readonly { readonly command: string }[];
        readonly views: Readonly<Record<string, readonly { readonly id: string }[]>>;
      };
    };

    expect(manifest.contributes.commands.map((item) => item.command).sort()).toEqual(
      [...AUDIT_COMMAND_IDS].sort(),
    );
    expect(manifest.contributes.views.behaviorAudit).toEqual([
      { id: 'behaviorAudit.sidebar', name: '教师与学生工作台', type: 'webview' },
    ]);
  });

  it.each(CONFIRMATION_COMMAND_IDS)('%s confirms and stops completely on cancel', async (id) => {
    const { handlers, actions, spies } = setup();
    spies.confirm.mockResolvedValue(false);

    await handlers.get(id)?.();

    expect(spies.confirm).toHaveBeenCalledOnce();
    expect(actions[id]).not.toHaveBeenCalled();
    expect(spies.captureFinish).not.toHaveBeenCalled();
    expect(spies.reportMaterialize).not.toHaveBeenCalled();
  });

  it('starts only with a selected plan, explicit consent, trusted workspace, and no active session', async () => {
    const missingPlan = setup({ selectedPlan: () => undefined });
    await missingPlan.handlers.get('behaviorAudit.startCapture')?.();
    expect(missingPlan.spies.captureStart).not.toHaveBeenCalled();

    const noConsent = setup({ hasConsent: () => false });
    await noConsent.handlers.get('behaviorAudit.startCapture')?.();
    expect(noConsent.spies.captureStart).not.toHaveBeenCalled();

    const untrusted = setup();
    vi.spyOn(untrusted.host, 'isWorkspaceTrusted').mockReturnValue(false);
    await untrusted.handlers.get('behaviorAudit.startCapture')?.();
    expect(untrusted.spies.captureStart).not.toHaveBeenCalled();

    const active = setup();
    active.spies.captureCurrent.mockReturnValue({ status: 'collecting' } as never);
    await active.handlers.get('behaviorAudit.startCapture')?.();
    expect(active.spies.captureStart).not.toHaveBeenCalled();

    const valid = setup();
    await valid.handlers.get('behaviorAudit.startCapture')?.();
    expect(valid.spies.captureStart).toHaveBeenCalledWith(plan(), true);
  });

  it('finishes terminal state before materializing and offers report-only retry on failure', async () => {
    const { handlers, spies } = setup();
    spies.reportMaterialize
      .mockRejectedValueOnce(new Error('disk full'))
      .mockResolvedValueOnce({} as never);
    spies.showError.mockResolvedValue('重试生成简报');

    await handlers.get('behaviorAudit.finishCapture')?.();

    expect(spies.captureFinish).toHaveBeenCalledWith('completed');
    expect(spies.reportMaterialize).toHaveBeenCalledTimes(2);
    expect(spies.captureFinish).toHaveBeenCalledTimes(1);
  });

  it('confirms the one-click workflow and stops completely when confirmation is cancelled', async () => {
    const { handlers, spies } = setup();
    spies.confirm.mockResolvedValue(false);

    await handlers.get('behaviorAudit.finishAnalyzeExport')?.();

    expect(handlers.has('behaviorAudit.finishAnalyzeExport')).toBe(true);
    expect(spies.confirm).toHaveBeenCalledOnce();
    expect(spies.captureFinish).not.toHaveBeenCalled();
    expect(spies.reportMaterialize).not.toHaveBeenCalled();
    expect(spies.finishAnalyzeExport).not.toHaveBeenCalled();
  });

  it('runs finish, local brief, and the post-brief export workflow in order', async () => {
    const { handlers, spies } = setup();

    await handlers.get('behaviorAudit.finishAnalyzeExport')?.();

    expect(handlers.has('behaviorAudit.finishAnalyzeExport')).toBe(true);
    expect(spies.captureFinish).toHaveBeenCalledWith('completed');
    expect(spies.reportMaterialize).toHaveBeenCalledWith('session-command-001');
    expect(spies.onBriefReady).toHaveBeenCalledWith('session-command-001');
    expect(spies.finishAnalyzeExport).toHaveBeenCalledWith('session-command-001');
    expect(spies.captureFinish.mock.invocationCallOrder[0]).toBeLessThan(
      spies.reportMaterialize.mock.invocationCallOrder[0]!,
    );
    expect(spies.reportMaterialize.mock.invocationCallOrder[0]).toBeLessThan(
      spies.onBriefReady.mock.invocationCallOrder[0]!,
    );
    expect(spies.onBriefReady.mock.invocationCallOrder[0]).toBeLessThan(
      spies.finishAnalyzeExport.mock.invocationCallOrder[0]!,
    );
  });

  it('does not invoke export after a brief failure when the retry is declined', async () => {
    const { handlers, spies } = setup();
    spies.reportMaterialize.mockRejectedValueOnce(new Error('disk full'));
    spies.showError.mockResolvedValue(undefined);

    await handlers.get('behaviorAudit.finishAnalyzeExport')?.();

    expect(spies.captureFinish).toHaveBeenCalledWith('completed');
    expect(spies.reportMaterialize).toHaveBeenCalledOnce();
    expect(spies.finishAnalyzeExport).not.toHaveBeenCalled();
  });
});
