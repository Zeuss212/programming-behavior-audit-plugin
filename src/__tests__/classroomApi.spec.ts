import { ServerConnection } from '@jupyterlab/services';

import {
  registerClassroomTicket,
  submitClassroomBrief
} from '../platform/classroomApi';
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

it('submits a classroom brief through the authenticated local Jupyter route', async () => {
  await submitClassroomBrief(
    settings,
    '23d7d803-524a-4d9f-b8bd-152a540dba12',
    true
  );

  expect(mockedRequest).toHaveBeenCalledWith(
    'platform/sessions/23d7d803-524a-4d9f-b8bd-152a540dba12/submit',
    settings,
    {
      method: 'POST',
      body: JSON.stringify({
        schema_version: 1,
        reason: 'student_manual',
        request_ai_analysis: true
      }),
      headers: { 'Content-Type': 'application/json' }
    }
  );
  expect(mockedRequest.mock.calls[0]?.[2]?.body).not.toContain(
    'code_snapshots'
  );
});
