import { ServerConnection } from '@jupyterlab/services';

import { IBehaviorSegment, IBehaviorSegmentSink } from './behaviorSegments';
import {
  DurableStorageError,
  IDurableSegmentStore,
  IndexedDbDurableSegmentStore
} from './durableSegmentStore';
import { ApiError } from './models/apiError';
import {
  IQueuedBehaviorSegment,
  ISegmentBatchReceipt,
  ISegmentBatchRequest,
  ISessionFinalizeResponse,
  ISessionStartResponse,
  ISessionState,
  IUploadSnapshot,
  UploadState
} from './models/session';
import { calculateObservationProgress } from './observationProgress';
import { finalizeSession, uploadSegmentBatch } from './services/sessionApi';
import { sha256Json } from './utils/canonicalJson';

const DEFAULT_BATCH_SIZE = 100;
const DEFAULT_QUEUE_LIMIT = 500;
const DEFAULT_AUTOMATIC_FLUSH_THRESHOLD = 20;
const DEFAULT_FLUSH_INTERVAL_MS = 2_000;
const RETRY_DELAYS_MS = [1_000, 2_000] as const;

type UploadSegmentBatch = (
  settings: ServerConnection.ISettings,
  sessionId: string,
  batch: ISegmentBatchRequest
) => Promise<ISegmentBatchReceipt>;

type FinalizeSession = (
  settings: ServerConnection.ISettings,
  sessionId: string,
  lastSequence: number,
  requestAiAnalysis?: boolean
) => Promise<ISessionFinalizeResponse>;

export interface IBehaviorEventUploaderDependencies {
  uploadSegmentBatch: UploadSegmentBatch;
  finalizeSession: FinalizeSession;
  uuid: () => string;
  sleep: (delayMs: number) => Promise<void>;
  subtle: SubtleCrypto;
  maxBatchSize: number;
  queueLimit: number;
  automaticFlushThreshold: number;
  flushIntervalMs: number;
  durableStore: IDurableSegmentStore;
}

export class BehaviorEventUploader implements IBehaviorSegmentSink {
  private readonly queue: IQueuedBehaviorSegment[] = [];
  private readonly observationSegments: Array<
    Pick<IBehaviorSegment, 'segment_type' | 'started_at' | 'ended_at'>
  > = [];
  private readonly listeners = new Set<(snapshot: IUploadSnapshot) => void>();
  private readonly dependencies: IBehaviorEventUploaderDependencies;
  private session: ISessionStartResponse | ISessionState | null = null;
  private pendingBatch: ISegmentBatchRequest | null = null;
  private uploadPromise: Promise<void> | null = null;
  private drainPromise: Promise<IUploadSnapshot> | null = null;
  private finalizePromise: Promise<ISessionFinalizeResponse> | null = null;
  private durableWritePromise: Promise<void> = Promise.resolve();
  private flushTimer: number | undefined;
  private uploadState: UploadState = 'idle';
  private eventCount = 0;
  private lastSequence = 0;
  private lastServerSequence = 0;
  private errorCode: string | undefined;
  private accepting = false;
  private overflowStoppedCapture = false;
  private automaticRetryBlocked = false;
  private finalizing = false;

  constructor(
    private readonly serverSettings: ServerConnection.ISettings,
    dependencies: Partial<IBehaviorEventUploaderDependencies> = {}
  ) {
    this.dependencies = {
      uploadSegmentBatch,
      finalizeSession,
      uuid: () => globalThis.crypto.randomUUID(),
      sleep: delayMs =>
        new Promise(resolve => {
          globalThis.setTimeout(resolve, delayMs);
        }),
      subtle: globalThis.crypto.subtle,
      maxBatchSize: DEFAULT_BATCH_SIZE,
      queueLimit: DEFAULT_QUEUE_LIMIT,
      automaticFlushThreshold: DEFAULT_AUTOMATIC_FLUSH_THRESHOLD,
      flushIntervalMs: DEFAULT_FLUSH_INTERVAL_MS,
      durableStore: new IndexedDbDurableSegmentStore(),
      ...dependencies
    };

    if (typeof window !== 'undefined') {
      window.addEventListener('blur', () => {
        void this.flush().catch(() => undefined);
      });
      window.addEventListener('pagehide', () => {
        void this.flush().catch(() => undefined);
      });
    }
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') {
          void this.flush().catch(() => undefined);
        }
      });
    }
  }

  start(session: ISessionStartResponse): Promise<void> {
    if (!this.canActivateSession()) {
      throw new Error('Cannot start while an active upload session exists.');
    }

    this.clearFlushTimer();
    this.session = session;
    this.observationSegments.length = 0;
    this.eventCount = 0;
    this.lastSequence = session.last_contiguous_sequence;
    this.lastServerSequence = session.last_contiguous_sequence;
    this.errorCode = undefined;
    this.accepting = true;
    this.overflowStoppedCapture = false;
    this.automaticRetryBlocked = false;
    this.finalizing = false;
    this.durableWritePromise = Promise.resolve();
    this.uploadState = 'collecting';
    this.publish();
    return Promise.resolve();
  }

  async resume(session: ISessionState): Promise<void> {
    if (session.status !== 'collecting') {
      throw new Error('Only a collecting upload session can be resumed.');
    }
    if (!this.canActivateSession()) {
      throw new Error('Cannot resume while an active upload session exists.');
    }

    this.clearFlushTimer();
    this.session = session;
    this.observationSegments.length = 0;
    this.eventCount = session.received_event_count;
    this.lastSequence = session.last_contiguous_sequence;
    this.lastServerSequence = session.last_contiguous_sequence;
    this.errorCode = undefined;
    this.accepting = false;
    this.overflowStoppedCapture = false;
    this.automaticRetryBlocked = false;
    this.finalizing = false;
    this.durableWritePromise = Promise.resolve();
    this.uploadState = 'starting';
    this.publish();

    try {
      const stored = await this.dependencies.durableStore.load(
        session.session_id
      );
      const remaining = stored.filter(
        segment => segment.session_seq > session.last_contiguous_sequence
      );
      this.validateResumedSegments(session, remaining);

      try {
        await this.dependencies.durableStore.removeThrough(
          session.session_id,
          session.last_contiguous_sequence
        );
      } catch {
        // Confirmed records are filtered by the server receipt on every resume.
      }

      this.queue.push(...remaining);
      this.observationSegments.push(
        ...remaining.map(segment => ({
          segment_type: segment.segment_type,
          started_at: segment.started_at,
          ended_at: segment.ended_at
        }))
      );
      const latest =
        remaining.length === 0
          ? undefined
          : remaining[remaining.length - 1].session_seq;
      this.lastSequence = latest ?? session.last_contiguous_sequence;
      this.eventCount = Math.max(
        session.received_event_count,
        this.lastSequence
      );
      this.accepting = true;
      this.uploadState = 'collecting';
      this.publish();

      if (this.queue.length > 0) {
        this.scheduleFlush();
      }
    } catch (error) {
      this.accepting = false;
      this.automaticRetryBlocked = true;
      this.uploadState = 'error';
      this.errorCode = durableErrorCode(error);
      this.publish();
      throw error;
    }
  }

  enqueue(segment: IBehaviorSegment): void {
    if (!this.session || !this.accepting) {
      return;
    }
    if (this.queue.length >= this.dependencies.queueLimit) {
      this.accepting = false;
      this.overflowStoppedCapture = true;
      this.errorCode = 'queue_overflow';
      this.uploadState = 'error';
      this.publish();
      return;
    }

    const sessionSequence = this.lastSequence + 1;
    this.observationSegments.push({
      segment_type: segment.segment_type,
      started_at: segment.started_at,
      ended_at: segment.ended_at
    });
    const queued = Object.freeze({
      ...segment,
      event_id: `${this.session.session_id}:${sessionSequence}`,
      session_seq: sessionSequence
    }) as IQueuedBehaviorSegment;
    this.queue.push(queued);
    this.lastSequence = sessionSequence;
    this.eventCount += 1;
    this.serializeDurableAppend(this.session.session_id, queued);
    this.publish();

    if (
      this.queue.length >= this.dependencies.automaticFlushThreshold ||
      queued.segment_type === 'page_away'
    ) {
      if (!this.automaticRetryBlocked) {
        void this.flush().catch(() => undefined);
      }
      return;
    }
    if (!this.automaticRetryBlocked) {
      this.scheduleFlush();
    }
  }

  flush(): Promise<void> {
    if (this.uploadPromise) {
      return this.uploadPromise;
    }
    if (!this.session || (this.queue.length === 0 && !this.pendingBatch)) {
      return Promise.resolve();
    }

    this.clearFlushTimer();
    this.automaticRetryBlocked = false;
    const operation = this.uploadNextBatch();
    this.uploadPromise = operation;
    operation.then(
      () => {
        if (this.uploadPromise === operation) {
          this.uploadPromise = null;
        }
        if (
          this.queue.length > 0 &&
          !this.drainPromise &&
          !this.automaticRetryBlocked
        ) {
          this.scheduleFlush();
        }
      },
      () => {
        if (this.uploadPromise === operation) {
          this.uploadPromise = null;
        }
      }
    );
    return operation;
  }

  drain(): Promise<IUploadSnapshot> {
    if (this.drainPromise) {
      return this.drainPromise;
    }
    const operation = this.runDrain();
    this.drainPromise = operation;
    operation.then(
      () => {
        if (this.drainPromise === operation) {
          this.drainPromise = null;
        }
      },
      () => {
        if (this.drainPromise === operation) {
          this.drainPromise = null;
        }
      }
    );
    return operation;
  }

  finalize(requestAiAnalysis = false): Promise<ISessionFinalizeResponse> {
    if (this.finalizePromise) {
      return this.finalizePromise;
    }
    if (
      !this.session ||
      this.uploadState === 'idle' ||
      this.uploadState === 'finalized'
    ) {
      return Promise.reject(
        new Error('Finalize requires an active upload session.')
      );
    }

    const operation = this.runFinalize(requestAiAnalysis);
    this.finalizePromise = operation;
    operation.then(
      () => {
        if (this.finalizePromise === operation) {
          this.finalizePromise = null;
        }
      },
      () => {
        if (this.finalizePromise === operation) {
          this.finalizePromise = null;
        }
      }
    );
    return operation;
  }

  snapshot(): IUploadSnapshot {
    const observationProgress = calculateObservationProgress(
      this.observationSegments
    );
    const snapshot: IUploadSnapshot = {
      sessionId: this.session?.session_id ?? null,
      uploadState: this.uploadState,
      eventCount: this.eventCount,
      queuedCount: this.queue.length,
      lastSequence: this.lastSequence,
      lastServerSequence: this.lastServerSequence,
      ...observationProgress
    };
    if (this.errorCode !== undefined) {
      snapshot.errorCode = this.errorCode;
    }
    return snapshot;
  }

  subscribe(listener: (snapshot: IUploadSnapshot) => void): () => void {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => {
      this.listeners.delete(listener);
    };
  }

  private async uploadNextBatch(): Promise<void> {
    const session = this.session;
    if (!session) {
      return;
    }
    this.uploadState = 'uploading';
    this.errorCode = undefined;
    this.publish();

    try {
      await this.durableWritePromise;
      if (!this.pendingBatch) {
        this.pendingBatch = await this.createPendingBatch();
      }
      const batch = this.pendingBatch;
      if (!batch) {
        this.restoreActiveState();
        return;
      }

      const receipt = await this.sendWithBoundedRetry(
        session.session_id,
        batch
      );
      this.validateReceipt(session.session_id, batch, receipt);

      this.queue.splice(0, batch.segments.length);
      this.lastServerSequence = receipt.last_contiguous_sequence;
      this.pendingBatch = null;
      this.automaticRetryBlocked = false;
      if (this.errorCode === 'queue_overflow') {
        this.errorCode = undefined;
      }
      this.restoreActiveState();
      this.publish();
      try {
        await this.dependencies.durableStore.removeThrough(
          session.session_id,
          receipt.last_contiguous_sequence
        );
      } catch {
        // The server receipt is authoritative; stale durable rows replay safely.
      }
    } catch (error) {
      this.automaticRetryBlocked = true;
      this.uploadState = 'error';
      this.errorCode =
        error instanceof ReceiptMismatchError
          ? 'receipt_mismatch'
          : error instanceof DurableStorageError
            ? error.code
            : safeUploadErrorCode(error);
      this.publish();
      throw error;
    }
  }

  private async createPendingBatch(): Promise<ISegmentBatchRequest | null> {
    if (this.queue.length === 0) {
      return null;
    }
    const segments = this.queue.slice(0, this.dependencies.maxBatchSize);
    const firstSequence = segments[0].session_seq;
    const lastSequence = segments[segments.length - 1].session_seq;
    const contentHash = await sha256Json(
      {
        first_sequence: firstSequence,
        last_sequence: lastSequence,
        segments
      },
      this.dependencies.subtle
    );
    const frozenSegments = Object.freeze(
      segments.slice()
    ) as unknown as IQueuedBehaviorSegment[];
    return Object.freeze({
      schema_version: 1 as const,
      segment_id: this.dependencies.uuid(),
      first_sequence: firstSequence,
      last_sequence: lastSequence,
      content_hash: contentHash,
      segments: frozenSegments
    });
  }

  private async sendWithBoundedRetry(
    sessionId: string,
    batch: ISegmentBatchRequest
  ): Promise<ISegmentBatchReceipt> {
    for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt += 1) {
      try {
        return await this.dependencies.uploadSegmentBatch(
          this.serverSettings,
          sessionId,
          batch
        );
      } catch (error) {
        if (
          error instanceof ReceiptMismatchError ||
          !isRetryableUploadError(error) ||
          attempt === RETRY_DELAYS_MS.length
        ) {
          throw error;
        }
        await this.dependencies.sleep(RETRY_DELAYS_MS[attempt]);
      }
    }
    throw new Error('Unreachable upload retry state.');
  }

  private validateReceipt(
    sessionId: string,
    batch: ISegmentBatchRequest,
    receipt: ISegmentBatchReceipt
  ): void {
    if (
      receipt.schema_version !== 1 ||
      receipt.session_id !== sessionId ||
      receipt.segment_id !== batch.segment_id ||
      receipt.accepted_count !== batch.segments.length ||
      receipt.last_contiguous_sequence !== batch.last_sequence
    ) {
      throw new ReceiptMismatchError();
    }
  }

  private async runDrain(): Promise<IUploadSnapshot> {
    this.clearFlushTimer();
    while (
      this.queue.length > 0 ||
      this.pendingBatch !== null ||
      this.uploadPromise !== null
    ) {
      await this.flush();
    }
    return this.snapshot();
  }

  private async runFinalize(
    requestAiAnalysis: boolean
  ): Promise<ISessionFinalizeResponse> {
    const session = this.session;
    if (!session) {
      throw new Error('Finalize requires an active upload session.');
    }
    const wasAccepting = this.accepting;
    this.accepting = false;
    this.finalizing = true;
    this.uploadState = 'draining';
    this.errorCode = undefined;
    this.publish();

    try {
      await this.drain();
      if (
        this.queue.length !== 0 ||
        this.pendingBatch !== null ||
        this.uploadPromise !== null
      ) {
        throw new Error('Finalize requires an empty upload queue.');
      }
      this.uploadState = 'finalizing';
      this.publish();
      const response = await this.dependencies.finalizeSession(
        this.serverSettings,
        session.session_id,
        this.lastSequence,
        requestAiAnalysis
      );
      if (
        response.schema_version !== 1 ||
        response.session_id !== session.session_id ||
        response.last_contiguous_sequence !== this.lastSequence ||
        response.status !== 'finalized'
      ) {
        throw new FinalizeMismatchError();
      }
      this.lastServerSequence = response.last_contiguous_sequence;
      try {
        await this.dependencies.durableStore.clear(session.session_id);
      } catch {
        // Finalized server state makes retained local rows safe to ignore.
      }
      this.finalizing = false;
      this.accepting = false;
      this.uploadState = 'finalized';
      this.errorCode = undefined;
      this.publish();
      return response;
    } catch (error) {
      this.finalizing = false;
      if (
        !this.overflowStoppedCapture &&
        !(error instanceof DurableStorageError)
      ) {
        this.accepting = wasAccepting;
      }
      this.uploadState = 'error';
      this.errorCode =
        error instanceof FinalizeMismatchError
          ? 'finalize_mismatch'
          : (this.errorCode ?? safeFinalizeErrorCode(error));
      this.publish();
      throw error;
    }
  }

  private restoreActiveState(): void {
    this.uploadState = this.finalizing ? 'draining' : 'collecting';
  }

  private canActivateSession(): boolean {
    return (
      (this.uploadState === 'idle' || this.uploadState === 'finalized') &&
      this.queue.length === 0 &&
      this.pendingBatch === null &&
      this.uploadPromise === null &&
      this.drainPromise === null &&
      this.finalizePromise === null
    );
  }

  private serializeDurableAppend(
    sessionId: string,
    segment: IQueuedBehaviorSegment
  ): void {
    const write = this.durableWritePromise.then(() =>
      this.dependencies.durableStore.append(sessionId, segment)
    );
    this.durableWritePromise = write.catch(error => {
      const failure = normalizeDurableError(error);
      this.accepting = false;
      this.automaticRetryBlocked = true;
      this.uploadState = 'error';
      this.errorCode = failure.code;
      this.publish();
      throw failure;
    });
    void this.durableWritePromise.catch(() => undefined);
  }

  private validateResumedSegments(
    session: ISessionState,
    segments: IQueuedBehaviorSegment[]
  ): void {
    let expected = session.last_contiguous_sequence + 1;
    for (const segment of segments) {
      if (
        segment.session_seq !== expected ||
        segment.event_id !== `${session.session_id}:${expected}`
      ) {
        throw new DurableStorageError(
          'durable_storage_invalid',
          'Durable behavior segments are not a continuous session sequence.'
        );
      }
      expected += 1;
    }
  }

  private publish(): void {
    const snapshot = this.snapshot();
    for (const listener of this.listeners) {
      listener(snapshot);
    }
  }

  private scheduleFlush(): void {
    if (this.flushTimer !== undefined || this.queue.length === 0) {
      return;
    }
    this.flushTimer = window.setTimeout(() => {
      this.flushTimer = undefined;
      void this.flush().catch(() => undefined);
    }, this.dependencies.flushIntervalMs);
  }

  private clearFlushTimer(): void {
    if (this.flushTimer === undefined) {
      return;
    }
    window.clearTimeout(this.flushTimer);
    this.flushTimer = undefined;
  }
}

class ReceiptMismatchError extends Error {
  constructor() {
    super('Upload receipt does not match the pending batch.');
    this.name = 'ReceiptMismatchError';
  }
}

class FinalizeMismatchError extends Error {
  constructor() {
    super('Finalize response does not match the active session.');
    this.name = 'FinalizeMismatchError';
  }
}

function statusFromError(error: unknown): number {
  if (error instanceof ApiError) {
    return error.status;
  }
  if (error instanceof ServerConnection.ResponseError) {
    return error.response.status;
  }
  if (
    typeof error === 'object' &&
    error !== null &&
    typeof (error as { status?: unknown }).status === 'number'
  ) {
    return (error as { status: number }).status;
  }
  return 0;
}

function safeUploadErrorCode(error: unknown): string {
  const status = statusFromError(error);
  switch (status) {
    case 400:
      return 'upload_bad_request';
    case 409:
      return 'upload_conflict';
    case 413:
      return 'upload_too_large';
    case 422:
      return 'upload_invalid';
    case 429:
      return 'upload_rate_limited';
    default:
      if (status >= 500) {
        return 'upload_unavailable';
      }
      if (error instanceof ServerConnection.NetworkError) {
        return 'upload_network';
      }
      return 'upload_failed';
  }
}

function durableErrorCode(error: unknown): string {
  return normalizeDurableError(error).code;
}

function normalizeDurableError(error: unknown): DurableStorageError {
  return error instanceof DurableStorageError
    ? error
    : new DurableStorageError(
        'durable_storage_unavailable',
        'Durable behavior storage is unavailable.'
      );
}

function isRetryableUploadError(error: unknown): boolean {
  const status = statusFromError(error);
  if (status !== 0) {
    return status === 429 || (status >= 500 && status < 600);
  }
  return error instanceof ServerConnection.NetworkError;
}

function safeFinalizeErrorCode(error: unknown): string {
  const status = statusFromError(error);
  if (status === 429) {
    return 'finalize_rate_limited';
  }
  if (status >= 500) {
    return 'finalize_unavailable';
  }
  if (error instanceof ServerConnection.NetworkError) {
    return 'finalize_network';
  }
  return 'finalize_failed';
}
