import { describe, expect, it, vi } from 'vitest';

import { SESSION_SCHEMA_VERSION, type SessionState } from '../domain/types';
import { AuditStatusBar, type StatusBarItemLike } from '../ui/statusBar';

function state(status: SessionState['status']): SessionState {
  return {
    schema_version: SESSION_SCHEMA_VERSION,
    session_id: 'session-status-001',
    workspace_id: 'workspace-status-001',
    status,
    plan_id: 'plan-status-001',
    plan_version: 1,
    plan_content_sha256: 'c'.repeat(64),
    started_at: '2026-08-10T00:00:00.000Z',
    updated_at: '2026-08-10T00:12:34.000Z',
    last_event_seq: 42,
    last_persisted_seq: 42,
    last_flushed_at: '2026-08-10T00:12:30.000Z',
  };
}

describe('AuditStatusBar', () => {
  it('shows elapsed time, event count, and most recent save while collecting', () => {
    const show = vi.fn();
    const item: StatusBarItemLike = {
      text: '',
      tooltip: '',
      show,
      hide: vi.fn(),
      dispose: vi.fn(),
    };
    const statusBar = new AuditStatusBar(item, () => new Date('2026-08-10T00:12:34.000Z'));

    statusBar.update(state('collecting'));

    expect(item.text).toBe('$(record) 正在监控 · 00:12:34 · 42 个事件');
    expect(item.tooltip).toBe('最近保存：2026-08-10T00:12:30.000Z');
    expect(show).toHaveBeenCalledOnce();
  });

  it('shows recovery state when interrupted and hides when idle', () => {
    const hide = vi.fn();
    const item: StatusBarItemLike = {
      text: '',
      tooltip: '',
      show: vi.fn(),
      hide,
      dispose: vi.fn(),
    };
    const statusBar = new AuditStatusBar(item, () => new Date());

    statusBar.update(state('interrupted'));
    expect(item.text).toBe('$(debug-pause) 监控已中断 · 可恢复 · 42 个事件');
    expect(item.tooltip).toContain('恢复');
    statusBar.update(undefined);
    expect(hide).toHaveBeenCalledOnce();
  });

  it('keeps a visible spinner while a completed session is generating AI advice or exporting', () => {
    const show = vi.fn();
    const item: StatusBarItemLike = {
      text: '',
      tooltip: '',
      show,
      hide: vi.fn(),
      dispose: vi.fn(),
    };
    const statusBar = new AuditStatusBar(item, () => new Date());

    statusBar.update(undefined, { message: '正在生成 AI 建议…' });

    expect(item.text).toBe('$(sync~spin) 正在生成 AI 建议…');
    expect(item.tooltip).toBe('请勿重复操作，完成后会自动提示。');
    expect(show).toHaveBeenCalledOnce();
  });
});
