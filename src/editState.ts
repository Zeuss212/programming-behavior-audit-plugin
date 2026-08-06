import {
  IBehaviorContext,
  BehaviorEventLogger,
  BehaviorEventType
} from './events';
import { ACTIVE_IDLE_THRESHOLD_MS } from './signalConfig';

type EditMode = 'typing' | 'deleting';

interface IActiveEditInterval {
  mode: EditMode;
  startedAt: number;
  lastChangedAt: number;
  context: IBehaviorContext;
  insertedCharCount: number;
  deletedCharCount: number;
  deletedContent: string;
  lastTypingCellSource?: string;
  deletedIsFullLine: boolean;
  hadPaste: boolean;
  pasteCharCount: number;
}

interface IPendingPaste {
  context: IBehaviorContext;
  timer: number;
}

export interface ITextChangeCount {
  inserted: number;
  deleted: number;
  deletedContent?: string;
  deletedIsFullLine?: boolean;
}

export interface ITypingCompletedArgs {
  context: IBehaviorContext;
  inputStartedAt: number;
  inputEndedAt: number;
  durationMs: number;
  cellSource?: string;
}

const PASTE_MATCH_WINDOW_MS = 1_000;

export class EditStateMachine {
  private activeInterval: IActiveEditInterval | null = null;
  private idleTimer: number | undefined;
  private pendingPaste: IPendingPaste | null = null;
  private pasteMarkedAt = 0;

  constructor(
    private readonly logger: BehaviorEventLogger,
    private readonly getContext: () => IBehaviorContext,
    private readonly onTypingCompleted: (args: ITypingCompletedArgs) => void
  ) {}

  handleTextChange(
    change: ITextChangeCount,
    context = this.getContext(),
    cellSourceAfterChange?: string
  ): void {
    if (!this.logger.isEnabled()) {
      this.reset();
      return;
    }

    if (change.inserted <= 0 && change.deleted <= 0) {
      return;
    }

    // If paste matched, text change is already accounted for in the typing interval
    if (this.emitPendingPasteIfMatched(change, context)) {
      if (
        this.activeInterval &&
        this.activeInterval.mode === 'typing' &&
        cellSourceAfterChange !== undefined
      ) {
        this.activeInterval.lastTypingCellSource = cellSourceAfterChange;
      }
      this.scheduleIdleTimer();
      return;
    }

    const mode = this.classifyEdit(change);
    const now = Date.now();

    if (this.activeInterval && this.activeInterval.mode !== mode) {
      this.endActiveInterval(now);
    }

    if (!this.activeInterval) {
      this.activeInterval = {
        mode,
        startedAt: now,
        lastChangedAt: now,
        context,
        insertedCharCount: 0,
        deletedCharCount: 0,
        deletedContent: '',
        lastTypingCellSource:
          mode === 'typing' ? cellSourceAfterChange : undefined,
        deletedIsFullLine: false,
        hadPaste: false,
        pasteCharCount: 0
      };
      this.logger.emit(this.startEventType(mode), context);
    }

    this.activeInterval.lastChangedAt = now;
    this.activeInterval.context = context;
    this.activeInterval.insertedCharCount += change.inserted;
    this.activeInterval.deletedCharCount += change.deleted;

    if (mode === 'deleting' && change.deletedContent !== undefined) {
      this.activeInterval.deletedContent =
        change.deletedContent + this.activeInterval.deletedContent;
    }
    if (mode === 'deleting' && change.deletedIsFullLine === true) {
      this.activeInterval.deletedIsFullLine = true;
    }

    if (mode === 'typing' && cellSourceAfterChange !== undefined) {
      this.activeInterval.lastTypingCellSource = cellSourceAfterChange;
    }

    this.scheduleIdleTimer();
  }

  markPaste(context = this.getContext()): void {
    if (!this.logger.isEnabled()) {
      this.reset();
      return;
    }

    this.clearPendingPaste();
    this.pasteMarkedAt = Date.now();

    const timer = window.setTimeout(() => {
      this.pendingPaste = null;
    }, PASTE_MATCH_WINDOW_MS);

    this.pendingPaste = { context, timer };
  }

  close(
    reason:
      | BehaviorEventType
      | 'context_change'
      | 'execution' = 'context_change'
  ): void {
    const now = Date.now();
    this.clearPendingPaste();
    this.endActiveInterval(now);

    if (reason === 'idle') {
      this.logger.emit('idle', this.getContext());
    }
  }

  reset(): void {
    this.activeInterval = null;
    this.clearPendingPaste();
    if (this.idleTimer !== undefined) {
      window.clearTimeout(this.idleTimer);
      this.idleTimer = undefined;
    }
  }

  private emitPendingPasteIfMatched(
    change: ITextChangeCount,
    context: IBehaviorContext
  ): boolean {
    if (!this.pendingPaste) {
      return false;
    }

    const elapsed = Date.now() - this.pasteMarkedAt;
    if (elapsed > PASTE_MATCH_WINDOW_MS || change.inserted <= 0) {
      this.clearPendingPaste();
      return false;
    }

    const pendingContext = this.pendingPaste.context;
    this.clearPendingPaste();

    const effectiveContext = context.cell_id ? context : pendingContext;

    // Ensure we have an active typing interval
    if (!this.activeInterval || this.activeInterval.mode !== 'typing') {
      if (this.activeInterval) {
        this.endActiveInterval(Date.now());
      }
      const now = Date.now();
      this.activeInterval = {
        mode: 'typing',
        startedAt: now,
        lastChangedAt: now,
        context: effectiveContext,
        insertedCharCount: 0,
        deletedCharCount: 0,
        deletedContent: '',
        deletedIsFullLine: false,
        hadPaste: false,
        pasteCharCount: 0
      };
      this.logger.emit('typing_start', effectiveContext);
    }

    // Mark paste on the typing interval
    this.activeInterval.hadPaste = true;
    this.activeInterval.pasteCharCount =
      (this.activeInterval.pasteCharCount || 0) + change.inserted;
    this.activeInterval.insertedCharCount += change.inserted;
    this.activeInterval.context = effectiveContext;

    return true;
  }

  private classifyEdit(change: ITextChangeCount): EditMode {
    if (change.deleted > change.inserted) {
      return 'deleting';
    }
    return 'typing';
  }

  private scheduleIdleTimer(): void {
    if (this.idleTimer !== undefined) {
      window.clearTimeout(this.idleTimer);
    }

    this.idleTimer = window.setTimeout(() => {
      const context = this.activeInterval?.context ?? this.getContext();
      this.endActiveInterval(Date.now());
      this.logger.emit('idle', context);
    }, ACTIVE_IDLE_THRESHOLD_MS);
  }

  private endActiveInterval(endedAt: number): void {
    if (!this.activeInterval) {
      return;
    }

    const interval = this.activeInterval;
    this.activeInterval = null;

    if (this.idleTimer !== undefined) {
      window.clearTimeout(this.idleTimer);
      this.idleTimer = undefined;
    }

    const durationMs = Math.max(0, endedAt - interval.startedAt);
    const details: Record<string, unknown> = {
      duration_ms: durationMs,
      inserted_char_count:
        interval.insertedCharCount > 0 ? interval.insertedCharCount : undefined,
      deleted_char_count:
        interval.deletedCharCount > 0 ? interval.deletedCharCount : undefined
    };

    if (interval.mode === 'deleting' && interval.deletedContent.length > 0) {
      details.deleted_content = interval.deletedContent;
    }
    if (interval.mode === 'deleting' && interval.deletedIsFullLine) {
      details.deleted_is_full_line = true;
    }
    if (interval.mode === 'typing' && interval.hadPaste) {
      details.had_paste = true;
      details.paste_char_count = interval.pasteCharCount;
    }

    this.logger.emit(
      this.endEventType(interval.mode),
      interval.context,
      details
    );

    if (interval.mode === 'typing' && interval.insertedCharCount > 0) {
      this.onTypingCompleted({
        context: interval.context,
        inputStartedAt: interval.startedAt,
        inputEndedAt: endedAt,
        durationMs,
        cellSource: interval.lastTypingCellSource
      });
    }
  }

  private clearPendingPaste(): void {
    if (!this.pendingPaste) {
      return;
    }
    window.clearTimeout(this.pendingPaste.timer);
    this.pendingPaste = null;
  }

  private startEventType(mode: EditMode): BehaviorEventType {
    return mode === 'typing' ? 'typing_start' : 'deleting_start';
  }

  private endEventType(mode: EditMode): BehaviorEventType {
    return mode === 'typing' ? 'typing_end' : 'deleting_end';
  }
}
