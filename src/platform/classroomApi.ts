import { ServerConnection } from '@jupyterlab/services';

import { IDimensionProfileVersion } from '../models/dimensionProfile';
import { requestAPI } from '../request';

export interface IClassroomRegistration {
  schema_version: 1;
  request_id: string;
  assignment_id: string;
  plan_id: string;
  plan_version: number;
  session_id: string;
  profile: IDimensionProfileVersion;
  scheduled_end_at: string;
  evidence_cutoff_at: string;
  last_sync_at: string;
}

export interface IClassroomSubmission {
  session_id: string;
  status: 'submitted' | 'pending_upload';
  reason: 'student_manual' | 'system_deadline';
  brief_id: string | null;
  revision: number | null;
  remote_status: string | null;
}

export function registerClassroomTicket(
  settings: ServerConnection.ISettings,
  ticket: string,
  pluginInstanceId: string
): Promise<IClassroomRegistration> {
  return requestAPI<IClassroomRegistration>('platform/register', settings, {
    method: 'POST',
    body: JSON.stringify({
      schema_version: 1,
      ticket,
      plugin_instance_id: pluginInstanceId
    }),
    headers: { 'Content-Type': 'application/json' }
  });
}

/** Submit through the authenticated local Jupyter server; no platform token enters the browser. */
export function submitClassroomBrief(
  settings: ServerConnection.ISettings,
  sessionId: string
): Promise<IClassroomSubmission> {
  return requestAPI<IClassroomSubmission>(
    `platform/sessions/${encodeURIComponent(sessionId)}/submit`,
    settings,
    {
      method: 'POST',
      body: JSON.stringify({
        schema_version: 1,
        reason: 'student_manual'
      }),
      headers: { 'Content-Type': 'application/json' }
    }
  );
}
