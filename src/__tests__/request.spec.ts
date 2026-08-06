import { ServerConnection } from '@jupyterlab/services';
import { webcrypto } from 'node:crypto';

import { BehaviorEventUploader } from '../behaviorEventUploader';
import { IBehaviorSegment } from '../behaviorSegments';
import {
  ISessionFinalizeResponse,
  ISessionStartResponse
} from '../models/session';
import { requestAPI } from '../request';

const SETTINGS = {
  baseUrl: 'http://synthetic.invalid/'
} as ServerConnection.ISettings;
const SESSION_ID = '123e4567-e89b-42d3-a456-426614174000';
const BATCH_ID = '223e4567-e89b-42d3-a456-426614174000';

const START_RESPONSE: ISessionStartResponse = {
  schema_version: 1,
  request_id: 'request-start',
  session_id: SESSION_ID,
  problem_id: 'synthetic-problem',
  profile_id: '323e4567-e89b-42d3-a456-426614174000',
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
  analysis_job_id: '423e4567-e89b-42d3-a456-426614174000'
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

function composedUploader(sleeps: number[]): BehaviorEventUploader {
  return new BehaviorEventUploader(SETTINGS, {
    finalizeSession: jest.fn(async () => FINALIZE_RESPONSE),
    uuid: () => BATCH_ID,
    sleep: async delayMs => {
      sleeps.push(delayMs);
    },
    subtle: webcrypto.subtle as SubtleCrypto,
    flushIntervalMs: 60_000
  });
}

describe('requestAPI error identity', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it.each([
    ['bare TypeError', new TypeError('synthetic programming type failure')],
    ['ordinary Error', new Error('synthetic programming failure')]
  ])('preserves the exact %s from makeRequest', async (_label, failure) => {
    const makeRequest = jest
      .spyOn(ServerConnection, 'makeRequest')
      .mockRejectedValue(failure);

    await expect(requestAPI('synthetic-endpoint', SETTINGS)).rejects.toBe(
      failure
    );
    expect(makeRequest).toHaveBeenCalledTimes(1);
  });

  it('preserves the exact Jupyter NetworkError from makeRequest', async () => {
    const failure = new ServerConnection.NetworkError(
      new TypeError('synthetic network failure')
    );
    const makeRequest = jest
      .spyOn(ServerConnection, 'makeRequest')
      .mockRejectedValue(failure);

    await expect(requestAPI('synthetic-endpoint', SETTINGS)).rejects.toBe(
      failure
    );
    expect(makeRequest).toHaveBeenCalledTimes(1);
  });
});

describe('composed request/session/uploader retry boundary', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('makes one physical request and no sleeps for a bare TypeError', async () => {
    const failure = new TypeError('synthetic programming type failure');
    const makeRequest = jest
      .spyOn(ServerConnection, 'makeRequest')
      .mockRejectedValue(failure);
    const sleeps: number[] = [];
    const uploader = composedUploader(sleeps);
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);

    await expect(uploader.flush()).rejects.toBe(failure);
    expect(makeRequest).toHaveBeenCalledTimes(1);
    expect(sleeps).toEqual([]);
  });

  it('retries a real NetworkError with only 1000 and 2000 ms sleeps', async () => {
    const failure = new ServerConnection.NetworkError(
      new TypeError('synthetic network failure')
    );
    const makeRequest = jest
      .spyOn(ServerConnection, 'makeRequest')
      .mockRejectedValue(failure);
    const sleeps: number[] = [];
    const uploader = composedUploader(sleeps);
    uploader.start(START_RESPONSE);
    uploader.enqueue(SEGMENT);

    await expect(uploader.flush()).rejects.toBe(failure);
    expect(makeRequest).toHaveBeenCalledTimes(3);
    expect(sleeps).toEqual([1000, 2000]);
  });
});
