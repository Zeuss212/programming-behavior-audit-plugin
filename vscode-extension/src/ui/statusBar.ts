import type { SessionState } from '../domain/types';

export interface StatusBarItemLike {
  text: string;
  tooltip: string;
  show(): void;
  hide(): void;
  dispose(): void;
}

export interface AuditProgress {
  readonly message: string;
}

function elapsed(startedAt: string, now: Date): string {
  const milliseconds = Math.max(0, now.getTime() - new Date(startedAt).getTime());
  const totalSeconds = Math.floor(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(':');
}

export class AuditStatusBar {
  public constructor(
    private readonly item: StatusBarItemLike,
    private readonly now: () => Date,
  ) {}

  public update(session: SessionState | undefined, progress?: AuditProgress): void {
    if (progress !== undefined) {
      this.item.text = `$(sync~spin) ${progress.message}`;
      this.item.tooltip = '请勿重复操作，完成后会自动提示。';
      this.item.show();
      return;
    }
    if (session === undefined) {
      this.item.hide();
      return;
    }
    if (session.status === 'collecting') {
      this.item.text = `$(record) 正在监控 · ${elapsed(session.started_at, this.now())} · ${String(session.last_event_seq)} 个事件`;
      this.item.tooltip = `最近保存：${session.last_flushed_at ?? '尚未保存'}`;
      this.item.show();
      return;
    }
    if (session.status === 'interrupted') {
      this.item.text = `$(debug-pause) 监控已中断 · 可恢复 · ${String(session.last_event_seq)} 个事件`;
      this.item.tooltip = '可恢复中断会话，或结束并生成部分简报。';
      this.item.show();
      return;
    }
    if (session.status === 'finalizing') {
      this.item.text = '$(sync~spin) 正在生成课堂简报';
      this.item.tooltip = '会话已经停止采集，正在写入本地报告。';
      this.item.show();
      return;
    }
    this.item.hide();
  }

  public dispose(): void {
    this.item.dispose();
  }
}
