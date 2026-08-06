export type BehaviorEventType =
  | 'code_input_completed'
  | 'typing_start'
  | 'typing_end'
  | 'deleting_start'
  | 'deleting_end'
  | 'paste'
  | 'idle'
  | 'cell_changed'
  | 'notebook_changed'
  | 'cell_execution_scheduled'
  | 'cell_execution_started'
  | 'cell_execution_success'
  | 'cell_execution_error'
  | 'kernel_busy'
  | 'kernel_idle'
  | 'kernel_restarting'
  | 'kernel_dead'
  | 'page_blur'
  | 'page_focus'
  | 'page_hidden'
  | 'page_visible';

export interface IBehaviorContext {
  document_type?: 'notebook_cell' | 'python_file';
  file_path?: string;
  file_name?: string;
  notebook_path?: string;
  notebook_id?: string;
  cell_id?: string;
  cell_index?: number;
  cell_type?: string;
}

export interface IBehaviorEvent extends IBehaviorContext {
  event_type: BehaviorEventType;
  occurred_at: string;
  duration_ms?: number;
  inserted_char_count?: number;
  deleted_char_count?: number;
  paste_char_count?: number;
  input_started_at?: string;
  input_ended_at?: string;
  cell_source?: string;
  deleted_content?: string;
  error_type?: string;
  error_message?: string;
  kernel_status?: string;
  previous_notebook_path?: string;
  previous_notebook_id?: string;
  previous_cell_id?: string;
  previous_cell_index?: number;
  previous_cell_type?: string;
  next_notebook_path?: string;
  next_notebook_id?: string;
  next_cell_id?: string;
  next_cell_index?: number;
  next_cell_type?: string;
  deleted_is_full_line?: boolean;
  had_paste?: boolean;
}

export interface IBehaviorEventSink {
  enqueue(event: IBehaviorEvent): void;
}

export type IBehaviorEventDetails = Omit<
  Partial<IBehaviorEvent>,
  'event_type' | 'occurred_at'
>;

export class BehaviorEventLogger {
  private enabled = true;

  constructor(private readonly eventSink?: IBehaviorEventSink) {}

  isEnabled(): boolean {
    return this.enabled;
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
  }

  emit(
    eventType: BehaviorEventType,
    context: IBehaviorContext = {},
    details: IBehaviorEventDetails = {}
  ): IBehaviorEvent {
    const event: IBehaviorEvent = stripUndefined({
      event_type: eventType,
      occurred_at: new Date().toISOString(),
      ...context,
      ...details
    });

    if (!this.enabled) {
      return event;
    }

    this.eventSink?.enqueue(event);
    return event;
  }
}

export function stripUndefined<T extends Record<string, unknown>>(value: T): T {
  for (const key of Object.keys(value)) {
    if (value[key] === undefined) {
      delete value[key];
    }
  }
  return value;
}

export function errorTypeFromUnknown(error: unknown): string | undefined {
  if (!error || typeof error !== 'object') {
    return undefined;
  }

  const record = error as Record<string, unknown>;
  const candidates = [record.ename, record.name, record.errorName, record.type];
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.length > 0) {
      return candidate;
    }
  }

  const content = record.content;
  if (content && typeof content === 'object') {
    return errorTypeFromUnknown(content);
  }

  return undefined;
}

export function errorMessageFromUnknown(error: unknown): string | undefined {
  if (!error || typeof error !== 'object') {
    return undefined;
  }

  const record = error as Record<string, unknown>;
  const candidates = [record.evalue, record.message, record.errorValue];
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.length > 0) {
      return candidate;
    }
  }

  // Fallback: traceback array — last line often contains the actual error
  const traceback = record.traceback;
  if (Array.isArray(traceback) && traceback.length > 0) {
    const lastLine = traceback[traceback.length - 1];
    if (typeof lastLine === 'string' && lastLine.length > 0) {
      return lastLine;
    }
  }

  const content = record.content;
  if (content && typeof content === 'object') {
    return errorMessageFromUnknown(content);
  }

  return undefined;
}
