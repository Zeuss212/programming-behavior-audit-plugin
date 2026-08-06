import { ServerConnection } from '@jupyterlab/services';

import { IProfileReference } from '../models/dimensionProfile';
import { ISegmentBatchRequest } from '../models/session';
import { requestAPI } from '../request';
import {
  abandonSession,
  deleteSession,
  finalizeSession,
  getSession,
  recoverSession,
  startSession,
  uploadSegmentBatch
} from '../services/sessionApi';
import { getAnalysisJob, retryAnalysisJob } from '../services/analysisApi';

jest.mock('../request', () => ({
  requestAPI: jest.fn()
}));

const SETTINGS = {} as ServerConnection.ISettings;
const RESOURCE_ID = 'synthetic/id with space';
const ENCODED_ID = 'synthetic%2Fid%20with%20space';
const PROFILE: IProfileReference = {
  problem_id: 'synthetic-problem',
  profile_id: '123e4567-e89b-42d3-a456-426614174000',
  profile_version: 3,
  profile_content_hash: 'a'.repeat(64)
};
const BATCH: ISegmentBatchRequest = {
  schema_version: 1,
  segment_id: '223e4567-e89b-42d3-a456-426614174000',
  first_sequence: 1,
  last_sequence: 1,
  content_hash: 'b'.repeat(64),
  segments: [
    {
      session_seq: 1,
      event_id: 'synthetic-session:1',
      segment_type: 'idle',
      started_at: '2026-07-28T10:00:00Z',
      ended_at: '2026-07-28T10:00:02Z',
      duration_ms: 2000
    }
  ]
};
const mockedRequest = requestAPI as jest.MockedFunction<typeof requestAPI>;

beforeEach(() => {
  mockedRequest.mockReset();
  mockedRequest.mockResolvedValue({} as never);
});

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' }
  };
}

describe('session API closed requests', () => {
  it('sends the exact start body', async () => {
    await startSession(SETTINGS, PROFILE);
    expect(mockedRequest).toHaveBeenCalledWith(
      'sessions/start',
      SETTINGS,
      jsonInit('POST', {
        schema_version: 1,
        problem_id: PROFILE.problem_id,
        profile_id: PROFILE.profile_id,
        profile_version: PROFILE.profile_version,
        profile_content_hash: PROFILE.profile_content_hash
      })
    );
  });

  it('encodes session IDs and sends the exact segment batch', async () => {
    await uploadSegmentBatch(SETTINGS, RESOURCE_ID, BATCH);
    expect(mockedRequest).toHaveBeenCalledWith(
      `sessions/${ENCODED_ID}/segments`,
      SETTINGS,
      jsonInit('POST', BATCH)
    );
  });

  it('encodes session IDs and sends the closed finalize body', async () => {
    await finalizeSession(SETTINGS, RESOURCE_ID, 7);
    expect(mockedRequest).toHaveBeenCalledWith(
      `sessions/${ENCODED_ID}/finalize`,
      SETTINGS,
      jsonInit('POST', { schema_version: 1, last_sequence: 7 })
    );
  });

  it('encodes session IDs for state reads without inventing a body', async () => {
    await getSession(SETTINGS, RESOURCE_ID);
    expect(mockedRequest).toHaveBeenCalledWith(
      `sessions/${ENCODED_ID}`,
      SETTINGS
    );
  });

  it('sends the closed abandon body', async () => {
    await abandonSession(SETTINGS, RESOURCE_ID, 'synthetic-abandon');
    expect(mockedRequest).toHaveBeenCalledWith(
      `sessions/${ENCODED_ID}/abandon`,
      SETTINGS,
      jsonInit('POST', { reason: 'synthetic-abandon' })
    );
  });

  it('sends the closed recover body', async () => {
    await recoverSession(
      SETTINGS,
      RESOURCE_ID,
      'synthetic-actor',
      'synthetic-recover'
    );
    expect(mockedRequest).toHaveBeenCalledWith(
      `sessions/${ENCODED_ID}/recover`,
      SETTINGS,
      jsonInit('POST', {
        actor: 'synthetic-actor',
        reason: 'synthetic-recover'
      })
    );
  });

  it('sends a DELETE with exact confirmation body', async () => {
    await deleteSession(
      SETTINGS,
      RESOURCE_ID,
      'synthetic-actor',
      'synthetic-delete'
    );
    expect(mockedRequest).toHaveBeenCalledWith(
      `sessions/${ENCODED_ID}`,
      SETTINGS,
      jsonInit('DELETE', {
        actor: 'synthetic-actor',
        reason: 'synthetic-delete',
        confirm_session_id: RESOURCE_ID
      })
    );
  });
});

describe('analysis job API closed requests', () => {
  it('encodes job IDs for reads without inventing a body', async () => {
    await getAnalysisJob(SETTINGS, RESOURCE_ID);
    expect(mockedRequest).toHaveBeenCalledWith(
      `analysis-jobs/${ENCODED_ID}`,
      SETTINGS
    );
  });

  it('encodes job IDs and sends the exact retry body', async () => {
    await retryAnalysisJob(SETTINGS, RESOURCE_ID, 'synthetic-retry');
    expect(mockedRequest).toHaveBeenCalledWith(
      `analysis-jobs/${ENCODED_ID}/retry`,
      SETTINGS,
      jsonInit('POST', { reason: 'synthetic-retry' })
    );
  });
});
