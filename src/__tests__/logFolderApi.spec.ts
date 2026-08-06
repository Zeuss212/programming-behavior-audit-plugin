import { ServerConnection } from '@jupyterlab/services';

import { ILogFolderOpenResponse } from '../models/logFolder';
import { requestAPI } from '../request';
import { openLogFolder } from '../services/logFolderApi';

jest.mock('../request', () => ({
  requestAPI: jest.fn()
}));

const SETTINGS = {} as ServerConnection.ISettings;
const RESPONSE: ILogFolderOpenResponse = {
  schema_version: 1,
  request_id: '123e4567-e89b-42d3-a456-426614174000',
  opened: true,
  platform: 'macos'
};
const mockedRequest = requestAPI as jest.MockedFunction<typeof requestAPI>;

beforeEach(() => {
  mockedRequest.mockReset();
  mockedRequest.mockResolvedValue(RESPONSE);
});

describe('log folder API boundary', () => {
  it('opens the log folder through the fixed POST endpoint', async () => {
    await expect(openLogFolder(SETTINGS)).resolves.toEqual(RESPONSE);

    expect(mockedRequest).toHaveBeenCalledWith('log-folder/open', SETTINGS, {
      method: 'POST',
      body: '{}',
      headers: { 'Content-Type': 'application/json' }
    });
  });
});
