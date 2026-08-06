import { ServerConnection } from '@jupyterlab/services';
import { webcrypto } from 'node:crypto';

import {
  BehaviorEventUploader,
  IBehaviorEventUploaderDependencies
} from '../behaviorEventUploader';
import { IBehaviorSegment } from '../behaviorSegments';
import { BehaviorTimelineBuilder } from '../behaviorTimelineBuilder';
import { ApiError } from '../models/apiError';
import {
  ISegmentBatchReceipt,
  ISegmentBatchRequest,
  ISessionFinalizeResponse,
  ISessionStartResponse
} from '../models/session';
import { sha256Json } from '../utils/canonicalJson';

const SESSION_ID = '123e4567-e89b-42d3-a456-426614174000';
const BATCH_ID = '223e4567-e89b-42d3-a456-426614174000';
const JOB_ID = '323e4567-e89b-42d3-a456-426614174000';
const SETTINGS = {} as ServerConnection.ISettings;

const START_RESPONSE: ISessionStartResponse = {
  schema_version: 1,
  request_id: 'request-start',
  session_id: SESSION_ID,
  problem_id: 'synthetic-problem',
  profile_id: '423e4567-e89b-42d3-a456-426614174000',
  profile_version: 1,
  profile_content_hash: 'a'.repeat(64),
  signal_dictionary_version: 'pilot-v1',
  signal_dictionary_hash: 'b'.repeat(64),
  status: 'collecting',
  last_contiguous_sequence: 0
};

const FINALIZE_RESPONSE: ISessionFinalizeResponse = {
  schema_version: 1,
  request_id: 'request-finalize',
  session_id: SESSION_ID,
  status: 'finalized',
  last_contiguous_sequence: 1,
  analysis_job_id: JOB_ID
};

const SEGMENT: IBehaviorSegment = {
  segment_type: 'code_writing',
  started_at: '2026-07-28T10:00:00Z',
  ended_at: '2026-07-28T10:00:01Z',
  duration_ms: 1000,
  document_type: 'notebook_cell',
  notebook_path: 'synthetic.ipynb',
  cell_id: 'cell-synthetic',
  cell_index: 0,
  inserted_char_count: 4,
  cell_source: 'x = 1'
};

function receipt(batch: ISegmentBatchRequest): ISegmentBatchReceipt {
  return {
    schema_version: 1,
    request_id: 'request-receipt',
    session_id: SESSION_ID,
    segment_id: batch.segment_id,
    accepted_count: batch.segments.length,
    last_contiguous_sequence: batch.last_sequence
  };
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function waitForCall(mock: jest.Mock): Promise<void> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (mock.mock.calls.length > 0) {
      return;
    }
    await new Promise(resolve => {
      setTimeout(resolve, 0);
    });
  }
  throw new Error('Synthetic upload was not invoked.');
}

function createHarness(
  overrides: Partial<IBehaviorEventUploaderDependencies> = {}
): {
  uploader: BehaviorEventUploader;
  upload: jest.Mock<
    Promise<ISegmentBatchReceipt>,
    [unknown, string, ISegmentBatchRequest]
  >;
  finalize: jest.Mock<
    Promise<ISessionFinalizeResponse>,
    [unknown, string, number]
  >;
  sleeps: number[];
} {
  const upload = jest.fn(
    async (
      _settings: unknown,
      _sessionId: string,
      batch: ISegmentBatchRequest
    ) => receipt(batch)
  );
  const finalize = jest.fn<
    Promise<ISessionFinalizeResponse>,
    [unknown, string, number]
  >(async () => FINALIZE_RESPONSE);
  const sleeps: number[] = [];
  const uploader = new BehaviorEventUploader(SETTINGS, {
    uploadSegmentBatch: upload,
    finalizeSession: finalize,
    uuid: () => BATCH_ID,
    sleep: async delay => {
      sleeps.push(delay);
    },
    subtle: webcrypto.subtle as SubtleCrypto,
    flushIntervalMs: 60_000,
    ...overrides
  });
  return { uploader, upload, finalize, sleeps };
}

describe('BehaviorEventUploader session sequencing', () => {
  it('publishes observation progress from enqueued finalized segments', () => {
    const { uploader } = createHarness();
    uploader.start(START_RESPONSE);

    uploader.enqueue({
      ...SEGMENT,
      segment_type: 'code_writing',
      started_at: '2026-07-28T10:00:00.000Z',
      ended_at: '2026-07-28T10:00:10.000Z',
      duration_ms: 1
    });
    uploader.enqueue({
      ...SEGMENT,
      segment_type: 'page_away',
      started_at: '2026-07-28T10:00:04.000Z',
      ended_at: '2026-07-28T10:00:06.000Z',
      duration_ms: 999_999
    });

    expect(uploader.snapshot()).toMatchObject({
      validObservationDurationMs: 8_000,
      pageAwayDurationMs: 2_000,
      observationAnchorAt: '2026-07-28T10:00:10.000Z'
    });
  });

  it('resets observation progress when a new session starts', async () => {
    const { uploader } = createHarness();
    uploader.start(START_RESPONSE);
    uploader.enqueue({
      ...SEGMENT,
      segment_type: 'idle',
      started_at: '2026-07-28T10:00:00.000Z',
      ended_at: '2026-07-28T10:00:05.000Z'
    });
    await uploader.finalize();

    uploader.start(START_RESPONSE);

    expect(uploader.snapshot()).toMatchObject({
      validObservationDurationMs: 0,
      pageAwayDurationMs: 0,
      observationAnchorAt: null
    });
  });

  it('starts with the exact server session and assigns canonical event IDs without mutating the caller', () => {
    const { uploader } = createHarness();
    const caller = {
      ...SEGMENT,
      event_id: 'caller-event',
      session_seq: 99
    };
    const before = { ...caller };

    uploader.start(START_RESPONSE);
    uploader.enqueue(caller);
    uploader.enqueue({ ...SEGMENT, started_at: '2026-07-28T10:00:02Z' });

    expect(caller).toEqual(before);
    expect(uploader.snapshot()).toEqual({
      sessionId: SESSION_ID,
      uploadState: 'collecting',
      eventCount: 2,
      queuedCount: 2,
      lastSequence: 2,
      lastServerSequence: 0,
      validObservationDurationMs: 1000,
      pageAwayDurationMs: 0,
      observationAnchorAt: '2026-07-28T10:00:01Z'
    });
  });

  it('uploads exact monotonic IDs from the server-owned session', async () => {
    const { uploader, upload } = createHarness();
    uploader.start(START_RESPONSE);
    uploader.enqueue({ ...SEGMENT, event_id: 'ignored', session_seq: 44 });
    uploader.enqueue({ ...SEGMENT });

    await uploader.flush();

    const batch = upload.mock.calls[0][2];
    expect(upload.mock.calls[0][1]).toBe(SESSION_ID);
    expect(batch.segments.map(item => item.session_seq)).toEqual([1, 2]);
    expect(batch.segments.map(item => item.event_id)).toEqual([
      `${SESSION_ID}:1`,
      `${SESSION_ID}:2`
    ]);
  });

  it('rejects a new start before the prior session is finalized', () => {
    const { uploader } = createHarness();
    uploader.start(START_RESPONSE);
    expect(() => uploader.start(START_RESPONSE)).toThrow(/active/i);
  });
});

describe('BehaviorEventUploader immutable upload batches', () => {
  it('joins concurrent flush and drain on one in-flight upload', async () => {
    const pending = deferred<ISegmentBatchReceipt>();
    const upload = jest.fn<
      Promise<ISegmentBatchReceipt>,
      [unknown, string, ISegmentBatchRequest]
    >(() => pending.promise);
    const { uploader } = createHarness({ uploadSegmentBatch: upload });
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);

    const flushPromise = uploader.flush();
    const drainPromise = uploader.drain();
    await waitForCall(upload);
    expect(upload).toHaveBeenCalledTimes(1);
    pending.resolve(receipt(upload.mock.calls[0][2]));
    await expect(Promise.all([flushPromise, drainPromise])).resolves.toEqual([
      undefined,
      expect.objectContaining({ queuedCount: 0 })
    ]);
    expect(upload).toHaveBeenCalledTimes(1);
  });

  it('retains the exact pending batch, ID, hash, and content across explicit retry', async () => {
    const sent: ISegmentBatchRequest[] = [];
    let call = 0;
    const { uploader } = createHarness({
      uploadSegmentBatch: jest.fn(
        async (_settings, _sessionId, batch: ISegmentBatchRequest) => {
          sent.push(batch);
          call += 1;
          if (call === 1) {
            throw new ApiError(422, 'invalid', 'private server text', false);
          }
          return receipt(batch);
        }
      )
    });
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);

    await expect(uploader.flush()).rejects.toThrow();
    const canonicalBytes = JSON.stringify(sent[0]);
    expect(uploader.snapshot().queuedCount).toBe(1);

    await expect(uploader.flush()).resolves.toBeUndefined();
    expect(sent).toHaveLength(2);
    expect(sent[1]).toBe(sent[0]);
    expect(JSON.stringify(sent[1])).toBe(canonicalBytes);
    expect(sent[1].segment_id).toBe(BATCH_ID);
    await expect(
      sha256Json(
        {
          first_sequence: sent[1].first_sequence,
          last_sequence: sent[1].last_sequence,
          segments: sent[1].segments
        },
        webcrypto.subtle as SubtleCrypto
      )
    ).resolves.toBe(sent[1].content_hash);
  });

  it.each([
    [
      'session ID',
      (value: ISegmentBatchReceipt) => ({ ...value, session_id: JOB_ID })
    ],
    [
      'segment ID',
      (value: ISegmentBatchReceipt) => ({ ...value, segment_id: JOB_ID })
    ],
    [
      'accepted count',
      (value: ISegmentBatchReceipt) => ({ ...value, accepted_count: 0 })
    ],
    [
      'last sequence',
      (value: ISegmentBatchReceipt) => ({
        ...value,
        last_contiguous_sequence: 0
      })
    ]
  ])('fails closed on a mismatched receipt %s', async (_label, mutate) => {
    const { uploader } = createHarness({
      uploadSegmentBatch: jest.fn(
        async (_settings, _sessionId, batch: ISegmentBatchRequest) =>
          mutate(receipt(batch))
      )
    });
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);

    await expect(uploader.flush()).rejects.toThrow(/receipt/i);
    expect(uploader.snapshot()).toEqual(
      expect.objectContaining({
        queuedCount: 1,
        lastServerSequence: 0,
        uploadState: 'error',
        errorCode: 'receipt_mismatch'
      })
    );
  });
});

describe('BehaviorEventUploader bounded retry and lifecycle', () => {
  it.each([400, 409, 413, 422])(
    'does not automatically retry fatal HTTP %s',
    async status => {
      const upload = jest.fn<
        Promise<ISegmentBatchReceipt>,
        [unknown, string, ISegmentBatchRequest]
      >(async () => {
        throw new ApiError(status, 'private-code', 'private text', false);
      });
      const { uploader, sleeps } = createHarness({
        uploadSegmentBatch: upload
      });
      uploader.start(START_RESPONSE);
      uploader.enqueue(SEGMENT);

      await expect(uploader.flush()).rejects.toThrow();
      expect(upload).toHaveBeenCalledTimes(1);
      expect(sleeps).toEqual([]);
      expect(uploader.snapshot().queuedCount).toBe(1);
    }
  );

  it.each([
    ['401', new ApiError(401, 'unauthorized', 'private text', false)],
    ['403', new ApiError(403, 'forbidden', 'private text', false)],
    ['404', new ApiError(404, 'not_found', 'private text', false)],
    ['418', new ApiError(418, 'other_4xx', 'private text', false)],
    ['bare TypeError', new TypeError('synthetic programming type failure')],
    ['programming error', new Error('synthetic programming failure')]
  ])('does not retry %s', async (_label, failure) => {
    const upload = jest.fn<
      Promise<ISegmentBatchReceipt>,
      [unknown, string, ISegmentBatchRequest]
    >(async () => {
      throw failure;
    });
    const { uploader, sleeps } = createHarness({
      uploadSegmentBatch: upload
    });
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);

    await expect(uploader.flush()).rejects.toBe(failure);
    expect(upload).toHaveBeenCalledTimes(1);
    expect(sleeps).toEqual([]);
  });

  it.each([
    [
      'network',
      new ServerConnection.NetworkError(
        new TypeError('synthetic network failure')
      )
    ],
    ['429', new ApiError(429, 'rate_limited', 'private text', true)],
    ['500', new ApiError(500, 'server_error', 'private text', true)]
  ])(
    'retries %s with only 1000 ms and 2000 ms delays',
    async (_label, failure) => {
      const upload = jest.fn<
        Promise<ISegmentBatchReceipt>,
        [unknown, string, ISegmentBatchRequest]
      >(async () => {
        throw failure;
      });
      const { uploader, sleeps } = createHarness({
        uploadSegmentBatch: upload
      });
      uploader.start(START_RESPONSE);
      uploader.enqueue(SEGMENT);

      await expect(uploader.flush()).rejects.toThrow();
      expect(upload).toHaveBeenCalledTimes(3);
      expect(sleeps).toEqual([1000, 2000]);
      expect(upload.mock.calls[1][2]).toBe(upload.mock.calls[0][2]);
      expect(upload.mock.calls[2][2]).toBe(upload.mock.calls[0][2]);
    }
  );

  it('flush never finalizes', async () => {
    const { uploader, finalize } = createHarness();
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);
    await uploader.flush();
    expect(finalize).not.toHaveBeenCalled();
  });

  it('does not resolve drain before the active upload settles', async () => {
    const pending = deferred<ISegmentBatchReceipt>();
    const upload = jest.fn<
      Promise<ISegmentBatchReceipt>,
      [unknown, string, ISegmentBatchRequest]
    >(() => pending.promise);
    const { uploader } = createHarness({ uploadSegmentBatch: upload });
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);

    let settled = false;
    const drain = uploader.drain().then(() => {
      settled = true;
    });
    await waitForCall(upload);
    expect(settled).toBe(false);

    pending.resolve(receipt(upload.mock.calls[0][2]));
    await drain;
    expect(settled).toBe(true);
  });

  it('drains before finalizing with the last assigned sequence', async () => {
    const order: string[] = [];
    const finalize = jest.fn<
      Promise<ISessionFinalizeResponse>,
      [unknown, string, number]
    >(async (_settings, _sessionId, lastSequence) => {
      order.push(`finalize:${lastSequence}`);
      return FINALIZE_RESPONSE;
    });
    const { uploader } = createHarness({
      uploadSegmentBatch: jest.fn(
        async (_settings, _sessionId, batch: ISegmentBatchRequest) => {
          order.push('upload');
          return receipt(batch);
        }
      ),
      finalizeSession: finalize
    });
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);

    await expect(uploader.finalize()).resolves.toEqual(FINALIZE_RESPONSE);
    expect(order).toEqual(['upload', 'finalize:1']);
    expect(finalize).toHaveBeenCalledTimes(1);
    expect(uploader.snapshot()).toEqual(
      expect.objectContaining({
        uploadState: 'finalized',
        queuedCount: 0,
        lastServerSequence: 1
      })
    );
  });

  it('publishes a real draining boundary before the finalize request boundary', async () => {
    const pendingUpload = deferred<ISegmentBatchReceipt>();
    const pendingFinalize = deferred<ISessionFinalizeResponse>();
    const upload = jest.fn<
      Promise<ISegmentBatchReceipt>,
      [unknown, string, ISegmentBatchRequest]
    >(() => pendingUpload.promise);
    const finalize = jest.fn<
      Promise<ISessionFinalizeResponse>,
      [unknown, string, number]
    >(() => pendingFinalize.promise);
    const { uploader } = createHarness({
      uploadSegmentBatch: upload,
      finalizeSession: finalize
    });
    const states: string[] = [];
    uploader.subscribe(value => states.push(value.uploadState));
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);

    const operation = uploader.finalize();
    await waitForCall(upload);
    expect(states).toContain('draining');
    expect(states).not.toContain('finalizing');
    expect(finalize).not.toHaveBeenCalled();
    pendingUpload.resolve(receipt(upload.mock.calls[0][2]));
    await waitForCall(finalize);
    expect(states).toContain('finalizing');
    pendingFinalize.resolve(FINALIZE_RESPONSE);
    await expect(operation).resolves.toEqual(FINALIZE_RESPONSE);
  });

  it('shares concurrent finalize calls as one request', async () => {
    const pending = deferred<ISessionFinalizeResponse>();
    const finalize = jest.fn<
      Promise<ISessionFinalizeResponse>,
      [unknown, string, number]
    >(() => pending.promise);
    const { uploader } = createHarness({ finalizeSession: finalize });
    uploader.start(START_RESPONSE);

    const first = uploader.finalize();
    const second = uploader.finalize();
    expect(first).toBe(second);
    await Promise.resolve();
    expect(finalize).toHaveBeenCalledTimes(1);
    pending.resolve({ ...FINALIZE_RESPONSE, last_contiguous_sequence: 0 });
    await expect(Promise.all([first, second])).resolves.toHaveLength(2);
  });

  it('keeps the session actionable after finalize failure and can recover', async () => {
    const failure = new Error('synthetic finalize failure');
    let attempt = 0;
    const finalize = jest.fn<
      Promise<ISessionFinalizeResponse>,
      [unknown, string, number]
    >(async (_settings, _sessionId, lastSequence) => {
      attempt += 1;
      if (attempt === 1) {
        throw failure;
      }
      return {
        ...FINALIZE_RESPONSE,
        last_contiguous_sequence: lastSequence
      };
    });
    const { uploader } = createHarness({ finalizeSession: finalize });
    uploader.start(START_RESPONSE);

    await expect(uploader.finalize()).rejects.toBe(failure);
    expect(uploader.snapshot()).toEqual(
      expect.objectContaining({
        sessionId: SESSION_ID,
        uploadState: 'error',
        eventCount: 0
      })
    );

    uploader.enqueue(SEGMENT);
    expect(uploader.snapshot().eventCount).toBe(1);
    await expect(uploader.finalize()).resolves.toEqual(
      expect.objectContaining({
        session_id: SESSION_ID,
        last_contiguous_sequence: 1
      })
    );
    expect(finalize).toHaveBeenCalledTimes(2);
  });
});

describe('BehaviorEventUploader capacity and observability', () => {
  it('retains all existing events, rejects overflow, and stops accepting', () => {
    const { uploader } = createHarness({
      queueLimit: 2,
      automaticFlushThreshold: 99
    });
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);
    uploader.enqueue({ ...SEGMENT, started_at: '2026-07-28T10:00:02Z' });
    uploader.enqueue({ ...SEGMENT, started_at: '2026-07-28T10:00:03Z' });
    uploader.enqueue({ ...SEGMENT, started_at: '2026-07-28T10:00:04Z' });

    expect(uploader.snapshot()).toEqual(
      expect.objectContaining({
        eventCount: 2,
        queuedCount: 2,
        lastSequence: 2,
        uploadState: 'error',
        errorCode: 'queue_overflow'
      })
    );
  });

  it('uploads the oldest retained events in original order after overflow', async () => {
    const uploaded: ISegmentBatchRequest[] = [];
    const { uploader } = createHarness({
      queueLimit: 2,
      automaticFlushThreshold: 99,
      uploadSegmentBatch: jest.fn(
        async (_settings, _sessionId, batch: ISegmentBatchRequest) => {
          uploaded.push(batch);
          return receipt(batch);
        }
      )
    });
    uploader.start(START_RESPONSE);
    uploader.enqueue({ ...SEGMENT, cell_source: 'synthetic-oldest-1' });
    uploader.enqueue({ ...SEGMENT, cell_source: 'synthetic-oldest-2' });
    uploader.enqueue({ ...SEGMENT, cell_source: 'synthetic-rejected-3' });

    await uploader.drain();

    expect(uploaded).toHaveLength(1);
    expect(uploaded[0].segments.map(segment => segment.cell_source)).toEqual([
      'synthetic-oldest-1',
      'synthetic-oldest-2'
    ]);
    const drainedSnapshot = uploader.snapshot();
    expect(drainedSnapshot).toEqual(
      expect.objectContaining({
        eventCount: 2,
        queuedCount: 0
      })
    );
    expect(drainedSnapshot.errorCode).toBeUndefined();
    uploader.enqueue({ ...SEGMENT, cell_source: 'synthetic-still-stopped' });
    expect(uploader.snapshot().eventCount).toBe(2);
  });

  it('shares the exact concurrent drain promise', async () => {
    const pending = deferred<ISegmentBatchReceipt>();
    const upload = jest.fn<
      Promise<ISegmentBatchReceipt>,
      [unknown, string, ISegmentBatchRequest]
    >(() => pending.promise);
    const { uploader } = createHarness({ uploadSegmentBatch: upload });
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);

    const first = uploader.drain();
    const second = uploader.drain();
    expect(first).toBe(second);
    await waitForCall(upload);
    pending.resolve(receipt(upload.mock.calls[0][2]));
    await expect(first).resolves.toEqual(
      expect.objectContaining({ queuedCount: 0 })
    );
  });

  it('drains every batch sequentially until a multi-batch queue is empty', async () => {
    const uploaded: ISegmentBatchRequest[] = [];
    const { uploader } = createHarness({
      maxBatchSize: 2,
      automaticFlushThreshold: 99,
      uploadSegmentBatch: jest.fn(
        async (_settings, _sessionId, batch: ISegmentBatchRequest) => {
          uploaded.push(batch);
          return receipt(batch);
        }
      )
    });
    uploader.start(START_RESPONSE);
    for (let index = 0; index < 5; index += 1) {
      uploader.enqueue({
        ...SEGMENT,
        cell_source: `synthetic-batch-${index + 1}`
      });
    }

    await expect(uploader.drain()).resolves.toEqual(
      expect.objectContaining({ queuedCount: 0, lastServerSequence: 5 })
    );
    expect(uploaded.map(batch => batch.segments.length)).toEqual([2, 2, 1]);
    expect(
      uploaded.flatMap(batch =>
        batch.segments.map(segment => segment.session_seq)
      )
    ).toEqual([1, 2, 3, 4, 5]);
  });

  it('publishes meaningful snapshots and unsubscribe stops notifications', async () => {
    const { uploader } = createHarness();
    const states: string[] = [];
    const unsubscribe = uploader.subscribe(snapshot => {
      states.push(`${snapshot.uploadState}:${snapshot.queuedCount}`);
    });

    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);
    await uploader.flush();
    unsubscribe();
    uploader.enqueue(SEGMENT);

    expect(states).toEqual([
      'idle:0',
      'collecting:0',
      'collecting:1',
      'uploading:1',
      'collecting:0'
    ]);
  });
});

describe('timeline duration consistency', () => {
  it('derives edit durations from the timestamps persisted on each segment', () => {
    const segments: IBehaviorSegment[] = [];
    const builder = new BehaviorTimelineBuilder({
      enqueue: segment => {
        segments.push(segment);
      },
      flush: async () => undefined
    });
    const context = {
      document_type: 'notebook_cell' as const,
      notebook_path: 'synthetic.ipynb',
      cell_id: 'cell-1',
      cell_index: 0
    };

    builder.enqueue({
      event_type: 'typing_start',
      occurred_at: '2026-07-30T03:00:00.000Z',
      ...context
    });
    builder.enqueue({
      event_type: 'typing_end',
      occurred_at: '2026-07-30T03:00:00.002Z',
      duration_ms: 1,
      inserted_char_count: 1,
      ...context
    });
    builder.enqueue({
      event_type: 'code_input_completed',
      occurred_at: '2026-07-30T03:00:00.003Z',
      input_ended_at: '2026-07-30T03:00:00.002Z',
      cell_source: 'x',
      ...context
    });
    builder.enqueue({
      event_type: 'deleting_start',
      occurred_at: '2026-07-30T03:00:01.000Z',
      ...context
    });
    builder.enqueue({
      event_type: 'deleting_end',
      occurred_at: '2026-07-30T03:00:01.002Z',
      duration_ms: 1,
      deleted_char_count: 1,
      ...context
    });

    expect(
      segments.map(({ segment_type, duration_ms }) => ({
        segment_type,
        duration_ms
      }))
    ).toEqual([
      { segment_type: 'code_writing', duration_ms: 2 },
      { segment_type: 'code_deletion', duration_ms: 2 }
    ]);
  });
});

describe('shared idle threshold', () => {
  it('emits the trailing idle interval when observation closes', () => {
    const segments: IBehaviorSegment[] = [];
    const builder = new BehaviorTimelineBuilder({
      enqueue: segment => {
        segments.push(segment);
      },
      flush: async () => undefined
    });
    const context = {
      document_type: 'notebook_cell' as const,
      notebook_path: 'synthetic.ipynb',
      cell_id: 'cell-1',
      cell_index: 0
    };
    builder.enqueue({
      event_type: 'cell_changed',
      occurred_at: '2026-07-30T08:00:00.000Z',
      ...context
    });

    builder.closeObservation('2026-07-30T08:00:10.000Z', context);

    expect(segments[segments.length - 1]).toMatchObject({
      segment_type: 'idle',
      started_at: '2026-07-30T08:00:00.000Z',
      ended_at: '2026-07-30T08:00:10.000Z',
      duration_ms: 10_000
    });
  });

  it('does not emit a trailing idle shorter than the shared threshold', () => {
    const segments: IBehaviorSegment[] = [];
    const builder = new BehaviorTimelineBuilder({
      enqueue: segment => {
        segments.push(segment);
      },
      flush: async () => undefined
    });
    builder.enqueue({
      event_type: 'cell_changed',
      occurred_at: '2026-07-30T08:00:00.000Z',
      next_cell_index: 0
    });

    builder.closeObservation('2026-07-30T08:00:01.999Z', {});

    expect(
      segments.filter(segment => segment.segment_type === 'idle')
    ).toHaveLength(0);
  });

  it('emits no idle segment at 1,999 ms and one at 2,000 ms', () => {
    function idleCount(gapMs: number): number {
      const segments: IBehaviorSegment[] = [];
      const builder = new BehaviorTimelineBuilder({
        enqueue: segment => {
          segments.push(segment);
        },
        flush: async () => undefined
      });
      builder.enqueue({
        event_type: 'cell_changed',
        occurred_at: '2026-07-28T10:00:00.000Z',
        next_cell_index: 0
      });
      builder.enqueue({
        event_type: 'cell_changed',
        occurred_at: new Date(
          Date.parse('2026-07-28T10:00:00.000Z') + gapMs
        ).toISOString(),
        previous_cell_index: 0,
        next_cell_index: 1
      });
      return segments.filter(segment => segment.segment_type === 'idle').length;
    }

    expect(idleCount(1999)).toBe(0);
    expect(idleCount(2000)).toBe(1);
  });
});
