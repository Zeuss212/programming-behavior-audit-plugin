import { AuditError } from '../domain/errors';
import type {
  AuditEvent,
  AuditEventKind,
  DocumentRef,
  JsonObject,
  PublishedPlan,
  SessionState,
} from '../domain/types';
import type { SessionRepository } from '../storage/sessionRepository';
import { EVENT_BATCH_SIZE, EVENT_FLUSH_INTERVAL_MS } from '../storage/eventWriter';
import { SessionEventFactory } from './eventFactory';

export interface AuditEventInput {
  readonly kind: AuditEventKind;
  readonly document?: DocumentRef;
  readonly payload: JsonObject;
}

export interface CaptureController {
  start(plan: PublishedPlan, consent: true): Promise<SessionState>;
  resume(sessionId: string): Promise<SessionState>;
  record(input: AuditEventInput): Promise<AuditEvent>;
  finish(
    outcome: 'completed' | 'partial' | 'abandoned',
    reason?: string,
  ): Promise<SessionState>;
  flush(): Promise<void>;
  current(): SessionState | undefined;
}

export interface CaptureControllerOptions {
  readonly repository: SessionRepository;
  readonly workspaceId: string;
  readonly isTrusted: () => boolean;
  readonly now: () => Date;
  readonly monotonicNow: () => number;
  readonly setCollectingContext?: (collecting: boolean) => Promise<void>;
}

export class DurableCaptureController implements CaptureController {
  private readonly pending: AuditEvent[] = [];
  private activeState: SessionState | undefined;
  private eventFactory: SessionEventFactory | undefined;
  private flushTimer: NodeJS.Timeout | undefined;
  private flushChain: Promise<void> = Promise.resolve();
  private failure: AuditError | undefined;

  public constructor(private readonly options: CaptureControllerOptions) {}

  public async start(plan: PublishedPlan, consent: true): Promise<SessionState> {
    if (!this.options.isTrusted()) {
      throw new AuditError(
        'workspace_untrusted',
        '未受信工作区不能开始行为采集。',
        '请确认工作区来源并在 VS Code 中设为受信。',
      );
    }
    if (consent !== true) {
      throw new AuditError(
        'session_conflict',
        '开始采集前必须明确确认采集范围。',
        '请勾选确认后重新开始。',
      );
    }
    if (this.activeState !== undefined) {
      throw new AuditError(
        'session_conflict',
        '当前已有正在处理的采集会话。',
        '请先结束当前会话。',
      );
    }

    const state = await this.options.repository.create(plan, this.options.workspaceId);
    this.activate(state);
    await this.setCollectingContext(true);
    return state;
  }

  public async resume(sessionId: string): Promise<SessionState> {
    if (!this.options.isTrusted()) {
      throw new AuditError(
        'workspace_untrusted',
        '未受信工作区不能恢复行为采集。',
        '请确认工作区来源并在 VS Code 中设为受信。',
      );
    }
    const stored = await this.options.repository.get(sessionId);
    if (stored?.status !== 'interrupted') {
      throw new AuditError(
        'session_recovery_required',
        '找不到可恢复的中断会话。',
        '请刷新状态或开始新会话。',
      );
    }
    const state = await this.options.repository.transition(
      sessionId,
      'interrupted',
      'collecting',
    );
    this.activate(state);
    await this.setCollectingContext(true);
    return state;
  }

  public async record(input: AuditEventInput): Promise<AuditEvent> {
    if (this.failure !== undefined) {
      throw this.failure;
    }
    if (this.activeState === undefined || this.eventFactory === undefined) {
      throw new AuditError(
        'session_conflict',
        '当前没有正在采集的会话。',
        '请先开始或恢复会话。',
      );
    }

    const event = this.eventFactory.create(input);
    this.pending.push(event);
    this.activeState = { ...this.activeState, last_event_seq: event.session_seq };
    this.scheduleFlush();
    if (this.pending.length >= EVENT_BATCH_SIZE) {
      await this.flush();
    }
    return event;
  }

  public async finish(
    outcome: 'completed' | 'partial' | 'abandoned',
    reason?: string,
  ): Promise<SessionState> {
    if (this.activeState === undefined) {
      throw new AuditError(
        'session_conflict',
        '当前没有可结束的采集会话。',
        '请刷新会话状态。',
      );
    }
    await this.flush();
    const sessionId = this.activeState.session_id;
    await this.options.repository.transition(sessionId, 'collecting', 'finalizing');
    const terminal = await this.options.repository.transition(
      sessionId,
      'finalizing',
      outcome,
      reason,
    );
    this.clearTimer();
    this.pending.length = 0;
    this.activeState = undefined;
    this.eventFactory = undefined;
    await this.setCollectingContext(false);
    return terminal;
  }

  public async flush(): Promise<void> {
    if (this.failure !== undefined) {
      throw this.failure;
    }
    this.clearTimer();
    if (this.pending.length === 0) {
      await this.flushChain;
      return;
    }
    const state = this.activeState;
    if (state === undefined) {
      return;
    }
    const batch = this.pending.splice(0, this.pending.length);
    const operation = this.flushChain.then(() =>
      this.options.repository.append(state.session_id, batch),
    );
    this.flushChain = operation;
    try {
      await operation;
      const persisted = await this.options.repository.get(state.session_id);
      if (persisted !== undefined && this.activeState !== undefined) {
        this.activeState = {
          ...persisted,
          last_event_seq: Math.max(
            persisted.last_event_seq,
            this.activeState.last_event_seq,
          ),
        };
      }
    } catch (error) {
      throw await this.stopAfterFailure(error);
    }
  }

  public current(): SessionState | undefined {
    return this.activeState;
  }

  private activate(state: SessionState): void {
    this.clearTimer();
    this.pending.length = 0;
    this.flushChain = Promise.resolve();
    this.failure = undefined;
    this.activeState = state;
    this.eventFactory = new SessionEventFactory(
      state.session_id,
      state.last_persisted_seq,
      this.options.now,
      this.options.monotonicNow,
    );
  }

  private scheduleFlush(): void {
    if (this.flushTimer !== undefined) {
      return;
    }
    this.flushTimer = setTimeout(() => {
      this.flushTimer = undefined;
      void this.flush().catch(() => undefined);
    }, EVENT_FLUSH_INTERVAL_MS);
  }

  private clearTimer(): void {
    if (this.flushTimer !== undefined) {
      clearTimeout(this.flushTimer);
      this.flushTimer = undefined;
    }
  }

  private async stopAfterFailure(error: unknown): Promise<AuditError> {
    if (this.failure === undefined) {
      this.failure =
        error instanceof AuditError
          ? error
          : new AuditError(
              'storage_write_failed',
              '无法保存行为事件。',
              '请保留本地数据并结束当前会话。',
              error,
            );
      this.clearTimer();
      this.pending.length = 0;
      this.activeState = undefined;
      this.eventFactory = undefined;
      await this.setCollectingContext(false);
    }
    return this.failure;
  }

  private async setCollectingContext(value: boolean): Promise<void> {
    await (this.options.setCollectingContext?.(value) ?? Promise.resolve());
  }
}
