import { ServerConnection } from '@jupyterlab/services';

import {
  IBehaviorCaptureDependencies,
  getStoredActiveSession,
  readActiveSessionId,
  startBehaviorCapture
} from '../behaviorCapture';
import { IBehaviorSegment } from '../behaviorSegments';
import { IProfileReference } from '../models/dimensionProfile';
import {
  ACTIVE_SESSION_STORAGE_KEY,
  ISessionFinalizeResponse,
  ISessionStartResponse,
  ISessionState,
  IUploadSnapshot
} from '../models/session';
import * as sessionApi from '../services/sessionApi';

jest.mock('../notebookMonitor', () => ({
  NotebookBehaviorMonitor: class {}
}));

jest.mock('../pageMonitor', () => ({
  PageStateMonitor: class {}
}));

const SETTINGS = {} as ServerConnection.ISettings;
const SESSION_ID = '123e4567-e89b-42d3-a456-426614174000';
const PROFILE: IProfileReference = {
  problem_id: 'synthetic-problem',
  profile_id: '223e4567-e89b-42d3-a456-426614174000',
  profile_version: 1,
  profile_content_hash: 'a'.repeat(64)
};
const START_RESPONSE: ISessionStartResponse = {
  schema_version: 1,
  request_id: 'request-start',
  session_id: SESSION_ID,
  problem_id: PROFILE.problem_id,
  profile_id: PROFILE.profile_id,
  profile_version: PROFILE.profile_version,
  profile_content_hash: PROFILE.profile_content_hash,
  signal_dictionary_version: 'pilot-v1',
  signal_dictionary_hash: 'b'.repeat(64),
  status: 'collecting',
  last_contiguous_sequence: 0
};
const FINAL_RESPONSE: ISessionFinalizeResponse = {
  schema_version: 1,
  request_id: 'request-finalize',
  session_id: SESSION_ID,
  status: 'finalized',
  last_contiguous_sequence: 0,
  analysis_job_id: '323e4567-e89b-42d3-a456-426614174000'
};
const STORED_COLLECTING_SESSION: ISessionState = {
  schema_version: 1,
  request_id: 'request-stored',
  session_id: SESSION_ID,
  problem_id: PROFILE.problem_id,
  profile_id: PROFILE.profile_id,
  profile_version: PROFILE.profile_version,
  profile_content_hash: PROFILE.profile_content_hash,
  status: 'collecting',
  last_contiguous_sequence: 0,
  received_event_count: 0,
  analysis_job_id: null
};

function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function memoryStorage(initial?: string): Storage {
  const values = new Map<string, string>();
  if (initial !== undefined) {
    values.set(ACTIVE_SESSION_STORAGE_KEY, initial);
  }
  return {
    get length() {
      return values.size;
    },
    clear: jest.fn(() => values.clear()),
    getItem: jest.fn((key: string) => values.get(key) ?? null),
    key: jest.fn((index: number) => Array.from(values.keys())[index] ?? null),
    removeItem: jest.fn((key: string) => {
      values.delete(key);
    }),
    setItem: jest.fn((key: string, value: string) => {
      values.set(key, value);
    })
  };
}

class FakeUploader {
  readonly starts: ISessionStartResponse[] = [];
  readonly resumes: ISessionState[] = [];
  readonly queued: IBehaviorSegment[] = [];
  finalizeResult: Promise<ISessionFinalizeResponse> =
    Promise.resolve(FINAL_RESPONSE);
  private current: IUploadSnapshot = {
    sessionId: null,
    uploadState: 'idle',
    eventCount: 0,
    queuedCount: 0,
    lastSequence: 0,
    lastServerSequence: 0,
    validObservationDurationMs: 0,
    pageAwayDurationMs: 0,
    observationAnchorAt: null
  };

  async start(response: ISessionStartResponse): Promise<void> {
    this.starts.push(response);
    this.current = {
      ...this.current,
      sessionId: response.session_id,
      uploadState: 'collecting'
    };
  }

  async resume(response: ISessionState): Promise<void> {
    this.resumes.push(response);
    this.current = {
      ...this.current,
      sessionId: response.session_id,
      uploadState: 'collecting',
      eventCount: response.received_event_count,
      lastSequence: response.last_contiguous_sequence,
      lastServerSequence: response.last_contiguous_sequence
    };
  }

  enqueue(segment: IBehaviorSegment): void {
    this.queued.push(segment);
  }

  flush(): Promise<void> {
    return Promise.resolve();
  }

  drain(): Promise<IUploadSnapshot> {
    return Promise.resolve(this.snapshot());
  }

  async finalize(): Promise<ISessionFinalizeResponse> {
    if (
      this.current.sessionId === null ||
      this.current.uploadState === 'idle' ||
      this.current.uploadState === 'finalized'
    ) {
      throw new Error('Finalize requires an active upload session.');
    }
    try {
      const result = await this.finalizeResult;
      this.current = { ...this.current, uploadState: 'finalized' };
      return result;
    } catch (error) {
      this.current = {
        ...this.current,
        uploadState: 'error',
        errorCode: 'synthetic_finalize_failure'
      };
      throw error;
    }
  }

  snapshot(): IUploadSnapshot {
    return { ...this.current };
  }

  subscribe(listener: (snapshot: IUploadSnapshot) => void): () => void {
    listener(this.snapshot());
    return () => undefined;
  }
}

function harness(overrides: Partial<IBehaviorCaptureDependencies> = {}) {
  const uploader = new FakeUploader();
  const storage = memoryStorage();
  const monitorStarts: string[] = [];
  const dependencies: IBehaviorCaptureDependencies = {
    storage,
    nowIso: () => new Date().toISOString(),
    startSession: jest.fn(async () => START_RESPONSE),
    abandonSession: jest.fn(async () => ({
      schema_version: 1 as const,
      request_id: 'request-abandon',
      session_id: SESSION_ID,
      problem_id: PROFILE.problem_id,
      profile_id: PROFILE.profile_id,
      profile_version: 1,
      profile_content_hash: PROFILE.profile_content_hash,
      status: 'abandoned' as const,
      last_contiguous_sequence: 0,
      received_event_count: 0,
      analysis_job_id: null
    })),
    getSession: jest.fn(),
    createUploader: () => uploader,
    createNotebookMonitor: () => ({
      start: () => {
        monitorStarts.push('notebook');
      },
      getCurrentContext: () => ({}),
      emitCodeInputCompleted: () => undefined
    }),
    createPageMonitor: () => ({
      start: () => {
        monitorStarts.push('page');
      }
    }),
    ...overrides
  };
  const controller = startBehaviorCapture(
    {} as Parameters<typeof startBehaviorCapture>[0],
    SETTINGS,
    dependencies
  );
  return { controller, dependencies, uploader, storage, monitorStarts };
}

describe('behavior capture default-off and start transaction', () => {
  it('creates monitors once but remains disabled without a server request', () => {
    const { controller, dependencies, uploader, monitorStarts } = harness();

    expect(monitorStarts).toEqual(['notebook', 'page']);
    expect(controller.isEnabled()).toBe(false);
    expect(controller.snapshot().uploadState).toBe('idle');
    expect(dependencies.startSession).not.toHaveBeenCalled();
    expect(uploader.starts).toEqual([]);
  });

  it('waits for server start before persisting only the session ID and enabling', async () => {
    const pending = deferred<ISessionStartResponse>();
    const order: string[] = [];
    const storage = memoryStorage();
    (storage.setItem as jest.Mock).mockImplementation(
      (key: string, value: string) => {
        order.push(`storage:${key}:${value}`);
      }
    );
    const { controller, uploader } = harness({
      storage,
      startSession: jest.fn(() => {
        order.push('server');
        return pending.promise;
      })
    });

    const starting = controller.start(PROFILE);
    expect(order).toEqual(['server']);
    expect(controller.isEnabled()).toBe(false);
    expect(uploader.starts).toEqual([]);

    pending.resolve(START_RESPONSE);
    await starting;

    expect(uploader.starts).toEqual([START_RESPONSE]);
    expect(order).toEqual([
      'server',
      `storage:${ACTIVE_SESSION_STORAGE_KEY}:${SESSION_ID}`
    ]);
    expect(controller.isEnabled()).toBe(true);
    expect(storage.setItem).toHaveBeenCalledWith(
      ACTIVE_SESSION_STORAGE_KEY,
      SESSION_ID
    );
  });

  it('rejects a noncanonical server session ID before uploader or storage mutation', async () => {
    const invalid = { ...START_RESPONSE, session_id: SESSION_ID.toUpperCase() };
    const { controller, uploader, storage } = harness({
      startSession: jest.fn(async () => invalid)
    });

    await expect(controller.start(PROFILE)).rejects.toThrow(/canonical/i);
    expect(uploader.starts).toEqual([]);
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(controller.isEnabled()).toBe(false);
  });

  it('rejects a concurrent start before making a second server request', async () => {
    const pending = deferred<ISessionStartResponse>();
    const startSession = jest.fn(() => pending.promise);
    const { controller } = harness({ startSession });

    const first = controller.start(PROFILE);
    const second = controller.start(PROFILE);

    await expect(second).rejects.toThrow(/starting|active/i);
    expect(startSession).toHaveBeenCalledTimes(1);
    pending.resolve(START_RESPONSE);
    await first;
  });

  it('orders stop after an exact pending start and finishes disabled', async () => {
    const pending = deferred<ISessionStartResponse>();
    const order: string[] = [];
    const startSession = jest.fn(() => {
      order.push('start-request');
      return pending.promise;
    });
    const { controller, uploader, storage } = harness({ startSession });
    uploader.finalizeResult = Promise.resolve(FINAL_RESPONSE).then(response => {
      order.push('finalize');
      return response;
    });

    const starting = controller.start(PROFILE);
    const stopping = controller.stop().then(response => {
      order.push('stop-complete');
      return response;
    });
    let stopSettled = false;
    void stopping.then(
      () => {
        stopSettled = true;
      },
      () => {
        stopSettled = true;
      }
    );

    await Promise.resolve();
    expect(stopSettled).toBe(false);
    expect(controller.isEnabled()).toBe(false);
    expect(uploader.starts).toEqual([]);

    pending.resolve(START_RESPONSE);
    await expect(Promise.all([starting, stopping])).resolves.toEqual([
      undefined,
      FINAL_RESPONSE
    ]);

    expect(startSession).toHaveBeenCalledTimes(1);
    expect(uploader.starts).toEqual([START_RESPONSE]);
    expect(order).toEqual(['start-request', 'finalize', 'stop-complete']);
    expect(controller.isEnabled()).toBe(false);
    expect(controller.snapshot().uploadState).toBe('finalized');
    expect(storage.removeItem).toHaveBeenCalledWith(ACTIVE_SESSION_STORAGE_KEY);
  });

  it('best-effort abandons the server session when active-ID persistence fails', async () => {
    const storage = memoryStorage();
    const persistenceError = new Error('synthetic storage set failure');
    (storage.setItem as jest.Mock).mockImplementation(() => {
      throw persistenceError;
    });
    (storage.removeItem as jest.Mock).mockImplementation(() => {
      throw new Error('synthetic cleanup failure');
    });
    const abandonSession = jest.fn(async () => {
      throw new Error('synthetic abandon failure');
    });
    const { controller, uploader } = harness({
      storage,
      abandonSession
    });

    await expect(controller.start(PROFILE)).rejects.toBe(persistenceError);
    expect(abandonSession).toHaveBeenCalledWith(
      SETTINGS,
      SESSION_ID,
      'active_session_persistence_failed'
    );
    expect(uploader.starts).toEqual([]);
    expect(controller.isEnabled()).toBe(false);
  });
});

describe('behavior capture stop and unfinished-session recovery', () => {
  it('resumes a stored collecting session without starting a second server session', async () => {
    const storage = memoryStorage(SESSION_ID);
    const { controller, dependencies, uploader } = harness({ storage });

    await controller.resume(STORED_COLLECTING_SESSION);

    expect(dependencies.startSession).not.toHaveBeenCalled();
    expect(uploader.resumes).toEqual([STORED_COLLECTING_SESSION]);
    expect(uploader.starts).toEqual([]);
    expect(controller.isEnabled()).toBe(true);
  });

  it('rejects resume when the server session is no longer collecting', async () => {
    const { controller, uploader } = harness();

    await expect(
      controller.resume({ ...STORED_COLLECTING_SESSION, status: 'finalized' })
    ).rejects.toThrow(/collecting/i);
    expect(uploader.resumes).toEqual([]);
    expect(controller.isEnabled()).toBe(false);
  });

  it('enqueues the trailing foreground idle before finalizing', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-07-30T08:00:00.000Z'));
    try {
      const { controller, uploader } = harness({
        nowIso: () => '2026-07-30T08:00:10.000Z'
      });
      await controller.start(PROFILE);
      controller.logger.emit('cell_changed', {
        document_type: 'notebook_cell',
        notebook_path: 'synthetic.ipynb',
        cell_id: 'cell-1',
        cell_index: 0
      });

      await controller.stop();

      expect(uploader.queued[uploader.queued.length - 1]).toMatchObject({
        segment_type: 'idle',
        started_at: '2026-07-30T08:00:00.000Z',
        ended_at: '2026-07-30T08:00:10.000Z',
        duration_ms: 10_000
      });
    } finally {
      jest.useRealTimers();
    }
  });

  it('preserves enabled capture and storage when finalize fails', async () => {
    const { controller, uploader, storage } = harness();
    await controller.start(PROFILE);
    uploader.finalizeResult = Promise.reject(
      new Error('synthetic finalize failure')
    );

    await expect(controller.stop()).rejects.toThrow(
      'synthetic finalize failure'
    );
    expect(controller.isEnabled()).toBe(true);
    expect(storage.removeItem).not.toHaveBeenCalled();
    expect(controller.snapshot()).toEqual(
      expect.objectContaining({
        sessionId: SESSION_ID,
        uploadState: 'error'
      })
    );
  });

  it('returns finalized success and disables capture even when key removal throws', async () => {
    const storage = memoryStorage();
    (storage.removeItem as jest.Mock).mockImplementation(() => {
      throw new Error('synthetic storage remove failure');
    });
    const { controller } = harness({ storage });
    await controller.start(PROFILE);

    await expect(controller.stop()).resolves.toEqual(FINAL_RESPONSE);
    expect(controller.isEnabled()).toBe(false);
    expect(controller.snapshot().uploadState).toBe('finalized');
  });

  it('treats an invalid stored ID as absent even when cleanup removal throws', () => {
    const storage = memoryStorage('INVALID-SYNTHETIC-ID');
    (storage.removeItem as jest.Mock).mockImplementation(() => {
      throw new Error('synthetic invalid-key cleanup failure');
    });

    expect(readActiveSessionId(storage)).toBeNull();
    expect(storage.removeItem).toHaveBeenCalledWith(ACTIVE_SESSION_STORAGE_KEY);
  });

  it('treats storage read failure as absent', () => {
    const storage = memoryStorage();
    (storage.getItem as jest.Mock).mockImplementation(() => {
      throw new Error('synthetic storage read failure');
    });
    expect(readActiveSessionId(storage)).toBeNull();
  });

  it('looks up a valid unfinished session through getSession without calling startSession', async () => {
    const storage = memoryStorage(SESSION_ID);
    const state = {
      schema_version: 1 as const,
      request_id: 'request-state',
      session_id: SESSION_ID,
      problem_id: PROFILE.problem_id,
      profile_id: PROFILE.profile_id,
      profile_version: 1,
      profile_content_hash: PROFILE.profile_content_hash,
      status: 'collecting' as const,
      last_contiguous_sequence: 0,
      received_event_count: 0,
      analysis_job_id: null
    };
    const getSession = jest.fn(async () => state);
    const startSession = jest
      .spyOn(sessionApi, 'startSession')
      .mockRejectedValue(new Error('lookup must not start capture'));

    await expect(
      getStoredActiveSession(SETTINGS, storage, getSession)
    ).resolves.toEqual(state);
    expect(getSession).toHaveBeenCalledWith(SETTINGS, SESSION_ID);
    expect(startSession).not.toHaveBeenCalled();
    startSession.mockRestore();
  });
});
