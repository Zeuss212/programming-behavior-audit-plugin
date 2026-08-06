import { ServerConnection } from '@jupyterlab/services';

import {
  IDimensionProfileDraft,
  IDimensionProfileVersion,
  IProfileDraftInput
} from '../models/dimensionProfile';
import { requestAPI } from '../request';

interface IProfileListResponse {
  profiles: IDimensionProfileVersion[];
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export function createProfile(
  settings: ServerConnection.ISettings,
  draft: IProfileDraftInput
): Promise<IDimensionProfileDraft> {
  return requestAPI<IDimensionProfileDraft>('dimension-profiles', settings, {
    method: 'POST',
    body: JSON.stringify(draft),
    headers: JSON_HEADERS
  });
}

export function updateProfileDraft(
  settings: ServerConnection.ISettings,
  profileId: string,
  revision: number,
  draft: IProfileDraftInput
): Promise<IDimensionProfileDraft> {
  return requestAPI<IDimensionProfileDraft>(
    `dimension-profiles/${encodeURIComponent(profileId)}/draft`,
    settings,
    {
      method: 'PUT',
      body: JSON.stringify({ revision, draft }),
      headers: JSON_HEADERS
    }
  );
}

export function publishProfile(
  settings: ServerConnection.ISettings,
  profileId: string
): Promise<IDimensionProfileVersion> {
  return requestAPI<IDimensionProfileVersion>(
    `dimension-profiles/${encodeURIComponent(profileId)}/publish`,
    settings,
    { method: 'POST', body: '{}', headers: JSON_HEADERS }
  );
}

export async function listProfiles(
  settings: ServerConnection.ISettings,
  problemId?: string
): Promise<IDimensionProfileVersion[]> {
  const query =
    problemId === undefined
      ? ''
      : `?problem_id=${encodeURIComponent(problemId)}`;
  const response = await requestAPI<IProfileListResponse>(
    `dimension-profiles${query}`,
    settings
  );
  return response.profiles;
}

export function getProfileVersion(
  settings: ServerConnection.ISettings,
  profileId: string,
  version: number
): Promise<IDimensionProfileVersion> {
  return requestAPI<IDimensionProfileVersion>(
    `dimension-profiles/${encodeURIComponent(
      profileId
    )}/versions/${encodeURIComponent(String(version))}`,
    settings
  );
}
