import { ServerConnection } from '@jupyterlab/services';

import { IProfileReference } from '../models/dimensionProfile';
import {
  ISegmentBatchReceipt,
  ISegmentBatchRequest,
  ISessionFinalizeResponse,
  ISessionStartResponse,
  ISessionState
} from '../models/session';
import { requestAPI } from '../request';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export function startSession(
  settings: ServerConnection.ISettings,
  profile: IProfileReference
): Promise<ISessionStartResponse> {
  return requestAPI<ISessionStartResponse>('sessions/start', settings, {
    method: 'POST',
    body: JSON.stringify({
      schema_version: 1,
      problem_id: profile.problem_id,
      profile_id: profile.profile_id,
      profile_version: profile.profile_version,
      profile_content_hash: profile.profile_content_hash
    }),
    headers: JSON_HEADERS
  });
}

export function uploadSegmentBatch(
  settings: ServerConnection.ISettings,
  sessionId: string,
  batch: ISegmentBatchRequest
): Promise<ISegmentBatchReceipt> {
  return requestAPI<ISegmentBatchReceipt>(
    `sessions/${encodeURIComponent(sessionId)}/segments`,
    settings,
    {
      method: 'POST',
      body: JSON.stringify(batch),
      headers: JSON_HEADERS
    }
  );
}

export function finalizeSession(
  settings: ServerConnection.ISettings,
  sessionId: string,
  lastSequence: number,
  requestAiAnalysis = false
): Promise<ISessionFinalizeResponse> {
  return requestAPI<ISessionFinalizeResponse>(
    `sessions/${encodeURIComponent(sessionId)}/finalize`,
    settings,
    {
      method: 'POST',
      body: JSON.stringify({
        schema_version: 1,
        last_sequence: lastSequence,
        request_ai_analysis: requestAiAnalysis
      }),
      headers: JSON_HEADERS
    }
  );
}

export function getSession(
  settings: ServerConnection.ISettings,
  sessionId: string
): Promise<ISessionState> {
  return requestAPI<ISessionState>(
    `sessions/${encodeURIComponent(sessionId)}`,
    settings
  );
}

export function abandonSession(
  settings: ServerConnection.ISettings,
  sessionId: string,
  reason: string
): Promise<ISessionState> {
  return requestAPI<ISessionState>(
    `sessions/${encodeURIComponent(sessionId)}/abandon`,
    settings,
    {
      method: 'POST',
      body: JSON.stringify({ reason }),
      headers: JSON_HEADERS
    }
  );
}

export function recoverSession(
  settings: ServerConnection.ISettings,
  sessionId: string,
  actor: string,
  reason: string
): Promise<ISessionState> {
  return requestAPI<ISessionState>(
    `sessions/${encodeURIComponent(sessionId)}/recover`,
    settings,
    {
      method: 'POST',
      body: JSON.stringify({ actor, reason }),
      headers: JSON_HEADERS
    }
  );
}

export function deleteSession(
  settings: ServerConnection.ISettings,
  sessionId: string,
  actor: string,
  reason: string
): Promise<{
  schema_version: 1;
  request_id: string;
  deleted_session_id: string;
}> {
  return requestAPI(`sessions/${encodeURIComponent(sessionId)}`, settings, {
    method: 'DELETE',
    body: JSON.stringify({
      actor,
      reason,
      confirm_session_id: sessionId
    }),
    headers: JSON_HEADERS
  });
}
