import { ServerConnection } from '@jupyterlab/services';

import {
  IPlatformSessionControllerDependencies,
  PlatformSessionController
} from '../platform/platformSessionController';
import { IPlatformContext } from '../platform/contextApi';
import { ISessionState } from '../models/session';

const SETTINGS = {} as ServerConnection.ISettings;
const SESSION_ID = '23d7d803-524a-4d9f-b8bd-152a540dba12';
const ASSIGNMENT_ID = 'd7647a1a-89c3-4c6d-9b5f-7e803918aa9d';

const CONTEXT = {
  schema_version: 1,
  request_id: 'request-context',
  mode: 'student',
  capabilities: {
    canAuthorPlan: false,
    canPublishPlan: false,
    canConfigureAi: false,
    canUseAssessmentAssist: false,
    canCapture: true,
    canSubmit: true
  },
  classroom_session: {
    assignment_id: ASSIGNMENT_ID,
    plan_id: '2b16b5c0-4e58-48f9-9448-9067de005e4a',
    plan_version: 1,
    session_id: SESSION_ID,
    profile: {
      profile_id: '10000000-0000-4000-8000-000000000001',
      version: 1,
      content_hash: 'a'.repeat(64),
      problem_id: 'average-debug'
    },
    scheduled_end_at: '2026-08-12T08:30:00Z',
    evidence_cutoff_at: '2026-08-12T08:45:00Z',
    last_sync_at: '2026-08-12T08:00:00Z'
  }
} as unknown as IPlatformContext;

const SESSION: ISessionState = {
  schema_version: 1,
  request_id: 'request-bootstrap',
  session_id: SESSION_ID,
  problem_id: 'average-debug',
  profile_id: '10000000-0000-4000-8000-000000000001',
  profile_version: 1,
  profile_content_hash: 'a'.repeat(64),
  status: 'collecting',
  last_contiguous_sequence: 37,
  received_event_count: 37,
  analysis_job_id: null
};

function storage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: jest.fn(() => values.clear()),
    getItem: jest.fn((key: string) => values.get(key) ?? null),
    key: jest.fn((index: number) => Array.from(values.keys())[index] ?? null),
    removeItem: jest.fn((key: string) => values.delete(key)),
    setItem: jest.fn((key: string, value: string) => values.set(key, value))
  };
}

function dependencies(
  outcome: 'created' | 'resumed' | 'terminal'
): IPlatformSessionControllerDependencies {
  return {
    storage: storage(),
    bootstrap: jest.fn(async () => ({
      schema_version: 1 as const,
      request_id: 'request-bootstrap',
      outcome,
      assignment_id: ASSIGNMENT_ID,
      plan_id: CONTEXT.classroom_session!.plan_id,
      plan_version: 1,
      session: outcome === 'terminal' ? null : SESSION
    }))
  };
}

describe('PlatformSessionController', () => {
  it('restores the fixed classroom session and continues after sequence 37', async () => {
    const capture = { resume: jest.fn(async () => undefined) };
    const deps = dependencies('resumed');

    const result = await new PlatformSessionController(
      SETTINGS,
      CONTEXT,
      capture,
      deps
    ).bootstrap();

    expect(result.outcome).toBe('resumed');
    expect(capture.resume).toHaveBeenCalledWith(SESSION);
    expect(deps.storage.getItem('myextension:active-session')).toBe(SESSION_ID);
  });

  it('does not resume or persist a session once evidence cutoff has passed', async () => {
    const capture = { resume: jest.fn(async () => undefined) };
    const deps = dependencies('terminal');

    const result = await new PlatformSessionController(
      SETTINGS,
      CONTEXT,
      capture,
      deps
    ).bootstrap();

    expect(result.outcome).toBe('terminal');
    expect(capture.resume).not.toHaveBeenCalled();
    expect(deps.storage.getItem('myextension:active-session')).toBeNull();
  });
});
