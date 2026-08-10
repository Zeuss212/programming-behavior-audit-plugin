import { ServerConnection } from '@jupyterlab/services';

import { getClassroomBrief, IClassroomBrief } from '../sessionBriefApi';

const SETTINGS = {
  baseUrl: 'https://platform.example/notebook_demo/'
} as ServerConnection.ISettings;
const SESSION_ID = '123e4567-e89b-42d3-a456-426614174000';

const BRIEF: IClassroomBrief = {
  schema_version: 1,
  request_id: '223e4567-e89b-42d3-a456-426614174000',
  session_id: SESSION_ID,
  status: 'complete',
  data_completeness: 'complete',
  active_duration_ms: 12_000,
  run_summary: '运行 2 次，其中 1 次成功、1 次失败',
  process_highlights: ['记录到 3 个代码编辑片段'],
  attention_message: null,
  generated_at: '2026-08-10T09:40:00+08:00'
};

describe('classroom brief API', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('uses the dynamic authenticated Jupyter base URL', async () => {
    const makeRequest = jest
      .spyOn(ServerConnection, 'makeRequest')
      .mockResolvedValue(
        new Response(JSON.stringify(BRIEF), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        })
      );

    await expect(getClassroomBrief(SETTINGS, SESSION_ID)).resolves.toEqual(
      BRIEF
    );
    expect(makeRequest.mock.calls[0][0]).toBe(
      `https://platform.example/notebook_demo/myextension/sessions/${SESSION_ID}/brief`
    );
  });

  it('rejects malformed content instead of rendering ambiguous fields', async () => {
    jest.spyOn(ServerConnection, 'makeRequest').mockResolvedValue(
      new Response(JSON.stringify({ ...BRIEF, run_summary: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    );

    await expect(getClassroomBrief(SETTINGS, SESSION_ID)).rejects.toThrow(
      /简报响应无效/
    );
  });
});
