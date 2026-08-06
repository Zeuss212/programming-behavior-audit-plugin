import { ServerConnection } from '@jupyterlab/services';

import {
  downloadSessionLog,
  fetchSessionLogContent,
  listSessionLogs
} from '../services/sessionLogApi';

const SETTINGS = {
  baseUrl: 'https://platform.example/notebook_abc/'
} as ServerConnection.ISettings;
const SESSION_ID = '123e4567-e89b-42d3-a456-426614174000';

const LOGS = [
  {
    kind: 'operation',
    filename: 'operation_log.json',
    label: '操作日志',
    description: '用户输入、删除、粘贴、运行成功/失败及输出。',
    status: 'ready',
    media_type: 'application/json; charset=utf-8',
    size_bytes: 100,
    generated_at: '2026-08-04T00:00:00+00:00',
    error_code: null
  },
  {
    kind: 'process',
    filename: 'process_log.md',
    label: '过程日志',
    description: '按时间顺序整理输入、修改、动作间停顿和运行结果。',
    status: 'ready',
    media_type: 'text/markdown; charset=utf-8',
    size_bytes: 200,
    generated_at: '2026-08-04T00:00:00+00:00',
    error_code: null
  },
  {
    kind: 'analysis',
    filename: 'analysis_log.json',
    label: 'AI 分析日志',
    description: '维度结论、数据质量、行为证据与分析来源。',
    status: 'generating',
    media_type: 'application/json; charset=utf-8',
    size_bytes: null,
    generated_at: null,
    error_code: null
  }
] as const;

describe('session log API', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('uses the Jupyter base URL and preserves the fixed server order', async () => {
    const makeRequest = jest
      .spyOn(ServerConnection, 'makeRequest')
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            schema_version: 1,
            request_id: 'request-1',
            session_id: SESSION_ID,
            logs: LOGS
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      );

    const result = await listSessionLogs(SESSION_ID, SETTINGS);

    expect(result.logs.map(row => row.kind)).toEqual([
      'operation',
      'process',
      'analysis'
    ]);
    expect(makeRequest.mock.calls[0][0]).toBe(
      `https://platform.example/notebook_abc/myextension/sessions/${SESSION_ID}/logs`
    );
  });

  it('rejects a reordered or malformed list instead of binding the wrong row', async () => {
    jest.spyOn(ServerConnection, 'makeRequest').mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: 1,
          request_id: 'request-1',
          session_id: SESSION_ID,
          logs: [LOGS[1], LOGS[0], LOGS[2]]
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    await expect(listSessionLogs(SESSION_ID, SETTINGS)).rejects.toThrow(
      '日志列表响应无效'
    );
  });

  it('fetches raw view content through the dynamic prefix', async () => {
    const makeRequest = jest
      .spyOn(ServerConnection, 'makeRequest')
      .mockResolvedValue(
        new Response('# 过程日志', {
          status: 200,
          headers: { 'Content-Type': 'text/markdown; charset=utf-8' }
        })
      );

    const content = await fetchSessionLogContent(
      SESSION_ID,
      'process',
      SETTINGS
    );

    expect(content).toBe('# 过程日志');
    expect(makeRequest.mock.calls[0][0]).toBe(
      `https://platform.example/notebook_abc/myextension/sessions/${SESSION_ID}/logs/process`
    );
  });

  it('downloads authenticated bytes through a temporary object URL', async () => {
    jest.spyOn(ServerConnection, 'makeRequest').mockResolvedValue(
      new Response('complete-log', {
        status: 200,
        headers: { 'Content-Type': 'application/json; charset=utf-8' }
      })
    );
    const created: Blob[] = [];
    const revoked: string[] = [];
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: (blob: Blob) => {
        created.push(blob);
        return 'blob:session-log';
      }
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: (url: string) => revoked.push(url)
    });
    const clicked: { value: HTMLAnchorElement | null } = { value: null };
    const onClick = (event: Event): void => {
      if (event.target instanceof HTMLAnchorElement) {
        clicked.value = event.target;
        event.preventDefault();
      }
    };
    document.addEventListener('click', onClick);

    try {
      await downloadSessionLog(
        SESSION_ID,
        'operation',
        'operation_log.json',
        SETTINGS
      );
    } finally {
      document.removeEventListener('click', onClick);
    }

    expect(created).toHaveLength(1);
    expect(await created[0].text()).toBe('complete-log');
    expect(clicked.value?.download).toBe('operation_log.json');
    expect(clicked.value?.href).toBe('blob:session-log');
    expect(revoked).toEqual(['blob:session-log']);
  });
});
