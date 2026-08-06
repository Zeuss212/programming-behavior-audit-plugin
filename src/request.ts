import { URLExt } from '@jupyterlab/coreutils';

import { ServerConnection } from '@jupyterlab/services';

import { ApiError } from './models/apiError';

/**
 * Call the server extension
 *
 * @param endPoint API REST end point for the extension
 * @param serverSettings The server settings to use for the request
 * @param init Initial values for the request
 * @returns The response body interpreted as JSON
 */
export async function requestAPI<T>(
  endPoint: string,
  serverSettings: ServerConnection.ISettings,
  init: RequestInit = {}
): Promise<T> {
  // Make request to Jupyter API
  const requestUrl = URLExt.join(
    serverSettings.baseUrl,
    'myextension', // our server extension's API namespace
    endPoint
  );

  const response = await ServerConnection.makeRequest(
    requestUrl,
    init,
    serverSettings
  );

  const rawBody = await response.text();
  let data: unknown = undefined;

  if (rawBody.length > 0) {
    try {
      data = JSON.parse(rawBody) as unknown;
    } catch {
      data = undefined;
    }
  }

  if (!response.ok) {
    if (isApiErrorBody(data)) {
      throw new ApiError(
        response.status,
        data.code,
        data.message,
        data.retryable,
        data.details
      );
    }
    throw new ApiError(
      response.status,
      'http_error',
      '服务器暂时无法处理请求。',
      response.status >= 500
    );
  }

  return data as T;
}

function isApiErrorBody(value: unknown): value is {
  code: string;
  message: string;
  retryable: boolean;
  details?: unknown;
} {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.code === 'string' &&
    typeof candidate.message === 'string' &&
    typeof candidate.retryable === 'boolean'
  );
}
