import { ServerConnection } from '@jupyterlab/services';

import { registerClassroomTicket } from '../platform/classroomApi';
import { requestAPI } from '../request';

jest.mock('../request', () => ({
  requestAPI: jest.fn()
}));

const settings = {} as ServerConnection.ISettings;
const mockedRequest = requestAPI as jest.MockedFunction<typeof requestAPI>;

beforeEach(() => {
  mockedRequest.mockReset();
  mockedRequest.mockResolvedValue({} as never);
});

it('sends the one-time ticket only to the authenticated local Jupyter route', async () => {
  await registerClassroomTicket(
    settings,
    'temporary-ticket',
    'plugin-instance-a'
  );

  expect(mockedRequest).toHaveBeenCalledWith('platform/register', settings, {
    method: 'POST',
    body: JSON.stringify({
      schema_version: 1,
      ticket: 'temporary-ticket',
      plugin_instance_id: 'plugin-instance-a'
    }),
    headers: { 'Content-Type': 'application/json' }
  });
});
