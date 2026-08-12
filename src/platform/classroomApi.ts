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
