import { IBehaviorContext } from './events';

export type BehaviorSegmentType =
  | 'code_writing'
  | 'code_deletion'
  | 'code_paste'
  | 'code_execution'
  | 'idle'
  | 'page_away'
  | 'cell_switch'
  | 'notebook_switch'
  | 'kernel_restart';

export interface IBehaviorSegment extends IBehaviorContext {
  event_id?: string;
  session_seq?: number;
  segment_type: BehaviorSegmentType;
  started_at: string;
  ended_at: string;
  duration_ms: number;
  inserted_char_count?: number;
  deleted_char_count?: number;
  paste_char_count?: number;
  cell_source?: string;
  execution_result?: 'success' | 'failure';
  error_type?: string;
  error_message?: string;
  deleted_content?: string;
  thinking_of?: string;
  previous_cell_index?: number;
  next_cell_index?: number;
  previous_notebook_path?: string;
  next_notebook_path?: string;
  kernel_status?: string;
  deleted_is_full_line?: boolean;
  had_paste?: boolean;
}

export interface IBehaviorSegmentSink {
  enqueue(segment: IBehaviorSegment): void;
  flush(): Promise<void>;
}
