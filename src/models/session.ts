import { IBehaviorSegment } from '../behaviorSegments';

export interface ISessionStartResponse {
  schema_version: 1;
  request_id: string;
  session_id: string;
  problem_id: string;
  profile_id: string;
  profile_version: number;
  profile_content_hash: string;
  signal_dictionary_version: 'pilot-v1';
  signal_dictionary_hash: string;
  status: 'collecting';
  last_contiguous_sequence: 0;
}

export interface ISessionFinalizeResponse {
  schema_version: 1;
  request_id: string;
  session_id: string;
  status: 'finalized';
  last_contiguous_sequence: number;
  analysis_job_id: string;
}

export interface ISessionState {
  schema_version: 1;
  request_id: string;
  session_id: string;
  problem_id: string;
  profile_id: string;
  profile_version: number;
  profile_content_hash: string;
  status: 'collecting' | 'finalizing' | 'finalized' | 'abandoned';
  last_contiguous_sequence: number;
  received_event_count: number;
  analysis_job_id: string | null;
}

export interface IQueuedBehaviorSegment extends IBehaviorSegment {
  event_id: string;
  session_seq: number;
}

export interface ISegmentBatchRequest {
  schema_version: 1;
  segment_id: string;
  first_sequence: number;
  last_sequence: number;
  content_hash: string;
  segments: IQueuedBehaviorSegment[];
}

export interface ISegmentBatchReceipt {
  schema_version: 1;
  request_id: string;
  session_id: string;
  segment_id: string;
  accepted_count: number;
  last_contiguous_sequence: number;
}

export interface IAnalysisJob {
  schema_version: 1;
  request_id: string;
  job_id: string;
  session_id: string;
  status: 'queued' | 'running' | 'ready' | 'partial' | 'error';
  active_attempt_id: string | null;
  attempt_ids: string[];
  analysis_id: string | null;
  error_code: string | null;
}

export type UploadState =
  | 'idle'
  | 'starting'
  | 'collecting'
  | 'uploading'
  | 'draining'
  | 'finalizing'
  | 'finalized'
  | 'error';

export interface IUploadSnapshot {
  sessionId: string | null;
  uploadState: UploadState;
  eventCount: number;
  queuedCount: number;
  lastSequence: number;
  lastServerSequence: number;
  validObservationDurationMs: number;
  pageAwayDurationMs: number;
  observationAnchorAt: string | null;
  errorCode?: string;
}

export const ACTIVE_SESSION_STORAGE_KEY = 'myextension:active-session';
