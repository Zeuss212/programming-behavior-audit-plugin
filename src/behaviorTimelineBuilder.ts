import { IBehaviorSegment, IBehaviorSegmentSink } from './behaviorSegments';
import { IBehaviorContext, IBehaviorEvent, IBehaviorEventSink } from './events';
import { ACTIVE_IDLE_THRESHOLD_MS } from './signalConfig';

interface IOpenInterval {
  startedAt: string;
  context: IBehaviorContext;
}

interface IOpenTypingInterval extends IOpenInterval {
  insertedCharCount?: number;
  endedAt?: string;
  hadPaste?: boolean;
  pasteCharCount?: number;
}

type IOpenDeletingInterval = IOpenInterval;

type IOpenExecutionInterval = IOpenInterval;

export class BehaviorTimelineBuilder implements IBehaviorEventSink {
  private readonly typingIntervals = new Map<string, IOpenTypingInterval>();
  private readonly deletingIntervals = new Map<string, IOpenDeletingInterval>();
  private readonly executionIntervals = new Map<
    string,
    IOpenExecutionInterval
  >();
  private pageAwayInterval: IOpenInterval | null = null;
  private lastEffectiveBehaviorEndedAt: string | null = null;
  private lastEffectiveBehaviorContext: IBehaviorContext = {};

  constructor(private readonly segmentSink: IBehaviorSegmentSink) {}

  reset(): void {
    this.typingIntervals.clear();
    this.deletingIntervals.clear();
    this.executionIntervals.clear();
    this.pageAwayInterval = null;
    this.lastEffectiveBehaviorEndedAt = null;
    this.lastEffectiveBehaviorContext = {};
  }

  closeObservation(occurredAt: string, context: IBehaviorContext = {}): void {
    this.emitIdleBefore(occurredAt, context);
  }

  enqueue(event: IBehaviorEvent): void {
    switch (event.event_type) {
      case 'typing_start':
        this.startTyping(event);
        return;
      case 'typing_end':
        this.endTyping(event);
        return;
      case 'code_input_completed':
        this.completeTyping(event);
        return;
      case 'deleting_start':
        this.startDeleting(event);
        return;
      case 'deleting_end':
        this.endDeleting(event);
        return;
      case 'paste':
        this.emitPaste(event);
        return;
      case 'cell_execution_scheduled':
        this.startExecution(event);
        return;
      case 'cell_execution_success':
      case 'cell_execution_error':
        this.endExecution(event);
        return;
      case 'page_blur':
      case 'page_hidden':
        this.startPageAway(event);
        return;
      case 'page_focus':
      case 'page_visible':
        this.endPageAway(event);
        return;
      case 'cell_changed':
        this.emitIdleBefore(event.occurred_at, event);
        this.emitCellSwitch(event);
        return;
      case 'notebook_changed':
        this.emitIdleBefore(event.occurred_at, event);
        this.emitNotebookSwitch(event);
        return;
      case 'kernel_restarting':
        this.emitIdleBefore(event.occurred_at, event);
        this.emitKernelRestart(event);
        return;
      default:
        return;
    }
  }

  private startTyping(event: IBehaviorEvent): void {
    this.emitIdleBefore(event.occurred_at, event);
    this.typingIntervals.set(contextKey(event), {
      startedAt: event.occurred_at,
      context: copyContext(event)
    });
  }

  private endTyping(event: IBehaviorEvent): void {
    const interval = this.typingIntervals.get(contextKey(event));
    if (!interval) {
      return;
    }

    interval.endedAt = event.occurred_at;
    interval.insertedCharCount = event.inserted_char_count;
    interval.context = { ...interval.context, ...copyContext(event) };
    if (event.had_paste) {
      interval.hadPaste = true;
      interval.pasteCharCount = event.paste_char_count;
    }
  }

  private completeTyping(event: IBehaviorEvent): void {
    const key = contextKey(event);
    const interval = this.typingIntervals.get(key);
    if (!interval) {
      return;
    }

    const endedAt =
      interval.endedAt ?? event.input_ended_at ?? event.occurred_at;
    const segment: IBehaviorSegment = {
      segment_type: 'code_writing',
      started_at: interval.startedAt,
      ended_at: endedAt,
      duration_ms: durationMs(interval.startedAt, endedAt),
      ...interval.context,
      ...copyContext(event),
      inserted_char_count:
        interval.insertedCharCount ?? event.inserted_char_count,
      cell_source: event.cell_source,
      had_paste: interval.hadPaste ?? event.had_paste,
      paste_char_count: interval.pasteCharCount ?? event.paste_char_count
    };

    this.typingIntervals.delete(key);
    this.emitSegment(cleanSegment(segment));
  }

  private startDeleting(event: IBehaviorEvent): void {
    this.emitIdleBefore(event.occurred_at, event);
    this.deletingIntervals.set(contextKey(event), {
      startedAt: event.occurred_at,
      context: copyContext(event)
    });
  }

  private endDeleting(event: IBehaviorEvent): void {
    const key = contextKey(event);
    const interval = this.deletingIntervals.get(key);
    if (!interval) {
      return;
    }

    this.deletingIntervals.delete(key);
    this.emitSegment(
      cleanSegment({
        segment_type: 'code_deletion',
        started_at: interval.startedAt,
        ended_at: event.occurred_at,
        duration_ms: durationMs(interval.startedAt, event.occurred_at),
        ...interval.context,
        ...copyContext(event),
        deleted_char_count: event.deleted_char_count,
        deleted_content: event.deleted_content,
        deleted_is_full_line: event.deleted_is_full_line
      })
    );
  }

  private emitPaste(event: IBehaviorEvent): void {
    this.emitIdleBefore(event.occurred_at, event);
    this.emitSegment(
      cleanSegment({
        segment_type: 'code_paste',
        started_at: event.occurred_at,
        ended_at: event.occurred_at,
        duration_ms: 0,
        ...copyContext(event),
        paste_char_count: event.paste_char_count
      })
    );
  }

  private startExecution(event: IBehaviorEvent): void {
    this.emitIdleBefore(event.occurred_at, event);
    this.executionIntervals.set(contextKey(event), {
      startedAt: event.occurred_at,
      context: copyContext(event)
    });
  }

  private endExecution(event: IBehaviorEvent): void {
    const key = contextKey(event);
    const interval =
      this.executionIntervals.get(key) ?? this.firstExecutionInterval();
    if (!interval) {
      return;
    }

    this.executionIntervals.delete(contextKey(interval.context));
    this.emitSegment(
      cleanSegment({
        segment_type: 'code_execution',
        started_at: interval.startedAt,
        ended_at: event.occurred_at,
        duration_ms: durationMs(interval.startedAt, event.occurred_at),
        ...interval.context,
        ...copyContext(event),
        execution_result:
          event.event_type === 'cell_execution_success' ? 'success' : 'failure',
        error_type:
          event.event_type === 'cell_execution_error'
            ? event.error_type
            : undefined,
        error_message:
          event.event_type === 'cell_execution_error'
            ? event.error_message
            : undefined,
        cell_source: event.cell_source
      })
    );
  }

  private startPageAway(event: IBehaviorEvent): void {
    if (this.pageAwayInterval) {
      return;
    }

    this.emitIdleBefore(event.occurred_at, event);
    this.pageAwayInterval = {
      startedAt: event.occurred_at,
      context: copyContext(event)
    };
  }

  private endPageAway(event: IBehaviorEvent): void {
    if (!this.pageAwayInterval) {
      return;
    }

    const interval = this.pageAwayInterval;
    this.pageAwayInterval = null;
    this.emitSegment(
      cleanSegment({
        segment_type: 'page_away',
        started_at: interval.startedAt,
        ended_at: event.occurred_at,
        duration_ms: durationMs(interval.startedAt, event.occurred_at),
        ...interval.context,
        ...copyContext(event)
      })
    );
  }

  private emitIdleBefore(startedAt: string, context: IBehaviorContext): void {
    if (this.pageAwayInterval || !this.lastEffectiveBehaviorEndedAt) {
      return;
    }

    const idleDurationMs = durationMs(
      this.lastEffectiveBehaviorEndedAt,
      startedAt
    );
    if (idleDurationMs < ACTIVE_IDLE_THRESHOLD_MS) {
      return;
    }

    this.emitSegment(
      cleanSegment({
        segment_type: 'idle',
        started_at: this.lastEffectiveBehaviorEndedAt,
        ended_at: startedAt,
        duration_ms: idleDurationMs,
        ...this.lastEffectiveBehaviorContext,
        ...copyContext(context)
      })
    );
  }

  private emitCellSwitch(event: IBehaviorEvent): void {
    this.emitSegment(
      cleanSegment({
        segment_type: 'cell_switch',
        started_at: event.occurred_at,
        ended_at: event.occurred_at,
        duration_ms: 0,
        ...copyContext(event),
        previous_cell_index: event.previous_cell_index,
        next_cell_index: event.next_cell_index
      })
    );
  }

  private emitNotebookSwitch(event: IBehaviorEvent): void {
    this.emitSegment(
      cleanSegment({
        segment_type: 'notebook_switch',
        started_at: event.occurred_at,
        ended_at: event.occurred_at,
        duration_ms: 0,
        ...copyContext(event),
        previous_notebook_path: event.previous_notebook_path,
        next_notebook_path: event.next_notebook_path
      })
    );
  }

  private emitKernelRestart(event: IBehaviorEvent): void {
    this.emitSegment(
      cleanSegment({
        segment_type: 'kernel_restart',
        started_at: event.occurred_at,
        ended_at: event.occurred_at,
        duration_ms: 0,
        ...copyContext(event)
      })
    );
  }

  private emitSegment(segment: IBehaviorSegment): void {
    this.segmentSink.enqueue(segment);
    this.lastEffectiveBehaviorEndedAt = segment.ended_at;
    this.lastEffectiveBehaviorContext = copyContext(segment);

    if (segment.segment_type === 'page_away') {
      void this.segmentSink.flush();
    }
  }

  private firstExecutionInterval(): IOpenExecutionInterval | undefined {
    return this.executionIntervals.values().next().value;
  }
}

function contextKey(context: IBehaviorContext): string {
  return [
    context.document_type ?? '',
    context.file_path ?? '',
    context.notebook_id ?? context.notebook_path ?? '',
    context.cell_id ?? '',
    context.cell_index ?? ''
  ].join('::');
}

function copyContext(context: IBehaviorContext): IBehaviorContext {
  return cleanContext({
    document_type: context.document_type,
    file_path: context.file_path,
    file_name: context.file_name,
    notebook_path: context.notebook_path,
    notebook_id: context.notebook_id,
    cell_id: context.cell_id,
    cell_index: context.cell_index,
    cell_type: context.cell_type
  });
}

function durationMs(startedAt: string, endedAt: string): number {
  const duration = Date.parse(endedAt) - Date.parse(startedAt);
  return Number.isFinite(duration) ? Math.max(0, duration) : 0;
}

function cleanContext(context: IBehaviorContext): IBehaviorContext {
  for (const key of Object.keys(context) as Array<keyof IBehaviorContext>) {
    if (context[key] === undefined) {
      delete context[key];
    }
  }
  return context;
}

function cleanSegment(segment: IBehaviorSegment): IBehaviorSegment {
  for (const key of Object.keys(segment) as Array<keyof IBehaviorSegment>) {
    if (segment[key] === undefined) {
      delete segment[key];
    }
  }
  return segment;
}
