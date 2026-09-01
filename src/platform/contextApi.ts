import { ServerConnection } from '@jupyterlab/services';

import { IDimensionProfileVersion } from '../models/dimensionProfile';
import { ISessionState } from '../models/session';
import { requestAPI } from '../request';
import {
  capabilitiesForMode,
  PlatformCapabilities,
  PlatformMode
} from './studentCapabilities';

export interface IClassroomPlatformSession {
  assignment_id: string;
  plan_id: string;
  plan_version: number;
  session_id: string;
  profile: IDimensionProfileVersion;
  scheduled_end_at: string;
  evidence_cutoff_at: string;
  last_sync_at: string;
}

export interface IPlatformContext {
  schema_version: 1;
  request_id: string;
  mode: PlatformMode;
  capabilities: PlatformCapabilities;
  classroom_session: IClassroomPlatformSession | null;
}

export type IPlatformCaptureSession = Omit<
  ISessionState,
  'schema_version' | 'request_id'
>;

export interface IPlatformCaptureBootstrapResponse {
  schema_version: 1;
  request_id: string;
  outcome: 'created' | 'resumed' | 'terminal';
  assignment_id: string;
  plan_id: string;
  plan_version: number;
  session: IPlatformCaptureSession | null;
}

export const LOCAL_PLATFORM_CONTEXT: IPlatformContext = {
  schema_version: 1,
  request_id: 'local',
  mode: 'local',
  capabilities: capabilitiesForMode('local'),
  classroom_session: null
};

export function createUnavailableStudentPlatformContext(): IPlatformContext {
  return {
    schema_version: 1,
    request_id: 'classroom-context-unavailable',
    mode: 'student',
    capabilities: capabilitiesForMode('student'),
    classroom_session: null
  };
}

export function getPlatformContext(
  settings: ServerConnection.ISettings
): Promise<IPlatformContext> {
  return requestAPI<IPlatformContext>('platform/context', settings);
}

export function refreshPlatformContext(
  settings: ServerConnection.ISettings
): Promise<IPlatformContext> {
  return requestAPI<IPlatformContext>('platform/context', settings, {
    method: 'POST',
    body: '',
    headers: { 'Content-Type': 'application/json' }
  });
}

export function bootstrapPlatformCapture(
  settings: ServerConnection.ISettings
): Promise<IPlatformCaptureBootstrapResponse> {
  return requestAPI<IPlatformCaptureBootstrapResponse>(
    'platform/capture/bootstrap',
    settings,
    {
      method: 'POST',
      body: '',
      headers: { 'Content-Type': 'application/json' }
    }
  );
}
