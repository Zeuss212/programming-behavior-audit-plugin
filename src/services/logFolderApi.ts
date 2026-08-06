import { ServerConnection } from '@jupyterlab/services';

import { ILogFolderOpenResponse } from '../models/logFolder';
import { requestAPI } from '../request';

export function openLogFolder(
  settings: ServerConnection.ISettings
): Promise<ILogFolderOpenResponse> {
  return requestAPI<ILogFolderOpenResponse>('log-folder/open', settings, {
    method: 'POST',
    body: '{}',
    headers: { 'Content-Type': 'application/json' }
  });
}
