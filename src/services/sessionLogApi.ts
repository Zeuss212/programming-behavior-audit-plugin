import { URLExt } from '@jupyterlab/coreutils';
import { ServerConnection } from '@jupyterlab/services';

import { ApiError } from '../models/apiError';

export type SessionLogKind = 'operation' | 'process' | 'analysis';
export type SessionLogStatus = 'pending' | 'generating' | 'ready' | 'error';

export interface ISessionLogFile {
  kind: SessionLogKind;
  filename: 'operation_log.json' | 'process_log.md' | 'analysis_log.json';
  label: string;
  description: string;
  status: SessionLogStatus;
  media_type:
    | 'application/json; charset=utf-8'
    | 'text/markdown; charset=utf-8';
  size_bytes: number | null;
  generated_at: string | null;
  error_code: string | null;
}

export interface ISessionLogListResponse {
  schema_version: 1;
  request_id: string;
  session_id: string;
  logs: [ISessionLogFile, ISessionLogFile, ISessionLogFile];
}

const EXPECTED_LOGS = [
  ['operation', 'operation_log.json'],
  ['process', 'process_log.md'],
  ['analysis', 'analysis_log.json']
] as const;

function sessionLogUrl(
  sessionId: string,
  settings: ServerConnection.ISettings,
  ...parts: string[]
): string {
  return URLExt.join(
    settings.baseUrl,
    'myextension',
    'sessions',
    sessionId,
    'logs',
    ...parts
  );
}

export async function listSessionLogs(
  sessionId: string,
  settings: ServerConnection.ISettings
): Promise<ISessionLogListResponse> {
  const response = await ServerConnection.makeRequest(
    sessionLogUrl(sessionId, settings),
    {},
    settings
  );
  const value = await readJsonResponse(response);
  if (!isSessionLogListResponse(value, sessionId)) {
    throw new Error('日志列表响应无效。');
  }
  return value;
}

export async function fetchSessionLogContent(
  sessionId: string,
  kind: SessionLogKind,
  settings: ServerConnection.ISettings
): Promise<string> {
  const response = await ServerConnection.makeRequest(
    sessionLogUrl(sessionId, settings, kind),
    {},
    settings
  );
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.text();
}

export async function downloadSessionLog(
  sessionId: string,
  kind: SessionLogKind,
  filename: ISessionLogFile['filename'],
  settings: ServerConnection.ISettings
): Promise<void> {
  const response = await ServerConnection.makeRequest(
    sessionLogUrl(sessionId, settings, kind, 'download'),
    {},
    settings
  );
  if (!response.ok) {
    throw await responseError(response);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    URL.revokeObjectURL(objectUrl);
  }
}

async function readJsonResponse(response: Response): Promise<unknown> {
  const raw = await response.text();
  let value: unknown;
  try {
    value = raw ? (JSON.parse(raw) as unknown) : undefined;
  } catch {
    value = undefined;
  }
  if (!response.ok) {
    throw apiErrorFromValue(response.status, value);
  }
  return value;
}

async function responseError(response: Response): Promise<Error> {
  let value: unknown;
  try {
    value = JSON.parse(await response.text()) as unknown;
  } catch {
    value = undefined;
  }
  return apiErrorFromValue(response.status, value);
}

function apiErrorFromValue(status: number, value: unknown): ApiError {
  if (isRecord(value)) {
    const code = value.code;
    const message = value.message;
    const retryable = value.retryable;
    if (
      typeof code === 'string' &&
      typeof message === 'string' &&
      typeof retryable === 'boolean'
    ) {
      return new ApiError(status, code, message, retryable, value.details);
    }
  }
  return new ApiError(
    status,
    'session_log_request_failed',
    '本次日志暂时无法读取。',
    status >= 500
  );
}

function isSessionLogListResponse(
  value: unknown,
  expectedSessionId: string
): value is ISessionLogListResponse {
  if (!isRecord(value) || value.session_id !== expectedSessionId) {
    return false;
  }
  if (
    value.schema_version !== 1 ||
    typeof value.request_id !== 'string' ||
    !Array.isArray(value.logs) ||
    value.logs.length !== EXPECTED_LOGS.length
  ) {
    return false;
  }
  return value.logs.every((row, index) => {
    const expected = EXPECTED_LOGS[index];
    return (
      isSessionLogFile(row) &&
      row.kind === expected[0] &&
      row.filename === expected[1]
    );
  });
}

function isSessionLogFile(value: unknown): value is ISessionLogFile {
  if (!isRecord(value)) {
    return false;
  }
  return (
    ['operation', 'process', 'analysis'].includes(String(value.kind)) &&
    ['operation_log.json', 'process_log.md', 'analysis_log.json'].includes(
      String(value.filename)
    ) &&
    typeof value.label === 'string' &&
    typeof value.description === 'string' &&
    ['pending', 'generating', 'ready', 'error'].includes(
      String(value.status)
    ) &&
    [
      'application/json; charset=utf-8',
      'text/markdown; charset=utf-8'
    ].includes(String(value.media_type)) &&
    (value.size_bytes === null ||
      (typeof value.size_bytes === 'number' && value.size_bytes >= 0)) &&
    (value.generated_at === null || typeof value.generated_at === 'string') &&
    (value.error_code === null || typeof value.error_code === 'string')
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
