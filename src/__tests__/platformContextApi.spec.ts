import { ServerConnection } from '@jupyterlab/services';

import {
  bootstrapPlatformCapture,
  getPlatformContext,
  refreshPlatformContext
} from '../platform/contextApi';
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

it('reads the server-authoritative platform context before choosing the UI mode', async () => {
  await getPlatformContext(settings);

  expect(mockedRequest).toHaveBeenCalledWith('platform/context', settings);
});

it('refreshes a classroom context through the local server without browser credentials', async () => {
  await refreshPlatformContext(settings);

  expect(mockedRequest).toHaveBeenCalledWith('platform/context', settings, {
    method: 'POST',
    body: '',
    headers: { 'Content-Type': 'application/json' }
  });
});

it('bootstraps capture through the local server without browser credentials', async () => {
  await bootstrapPlatformCapture(settings);

  expect(mockedRequest).toHaveBeenCalledWith(
    'platform/capture/bootstrap',
    settings,
    {
      method: 'POST',
      body: '',
      headers: { 'Content-Type': 'application/json' }
    }
  );
});
