import { describe, expect, it, vi } from 'vitest';

import { CompatibleAiClient, type AiRuntimeProvider } from '../ai/aiClient';

const runtime: AiRuntimeProvider = {
  requireRuntime: () =>
    Promise.resolve({
      baseUrl: new URL('https://provider.example/v1'),
      model: 'model-a',
      apiKey: 'must-not-ship-secret',
    }),
};

const planSuggestion = {
  schema_version: 1,
  knowledge_points: [
    {
      name: '边界处理',
      description: '处理空列表输入。',
      observation_basis: '运行空列表用例。',
    },
  ],
  tests: [],
};

const sessionAnalysis = {
  schema_version: 1,
  summary: '本次会话记录到编辑和运行。',
  observations: [],
  attention_points: [],
};

function providerResponse(content: unknown, finishReason = 'stop'): Response {
  return new Response(
    JSON.stringify({
      choices: [{ finish_reason: finishReason, message: { content: JSON.stringify(content) } }],
    }),
    { status: 200, headers: { 'content-type': 'application/json' } },
  );
}

function planInput() {
  return {
    problemText: '实现列表分析函数。',
    workspaceRoot: '/private/tmp/student-workspace',
    codeFragments: [
      {
        absolutePath: '/private/tmp/student-workspace/analyze.py',
        languageId: 'python',
        content: 'print("hello")',
      },
    ],
  };
}

type FetchMock = ReturnType<
  typeof vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>
>;

function requestBody(fetcher: FetchMock, index: number): string {
  const body = fetcher.mock.calls[index]?.[1]?.body;
  if (typeof body !== 'string') {
    throw new Error('Expected a string request body.');
  }
  return body;
}

function requestUrl(fetcher: FetchMock, index: number): string {
  const input = fetcher.mock.calls[index]?.[0];
  if (typeof input === 'string') {
    return input;
  }
  if (input instanceof URL) {
    return input.href;
  }
  if (input instanceof Request) {
    return input.url;
  }
  throw new Error('Expected a request URL.');
}

describe('CompatibleAiClient', () => {
  it('returns validated suggestions and keeps secrets, absolute paths, and environment data out of the body', async () => {
    const fetcher = vi.fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>(
      () => Promise.resolve(providerResponse(planSuggestion)),
    );
    const client = new CompatibleAiClient({ runtime, fetch: fetcher });

    await expect(client.suggestPlan(planInput())).resolves.toEqual(planSuggestion);
    const body = requestBody(fetcher, 0);
    expect(body).not.toContain('must-not-ship-secret');
    expect(body).not.toContain('/private/tmp/student-workspace');
    expect(body).not.toContain('ARK_API_KEY');
    expect(requestUrl(fetcher, 0)).toBe(
      'https://provider.example/v1/chat/completions',
    );
  });

  it.each([
    [401, 'ai_provider_auth_failed'],
    [403, 'ai_provider_auth_failed'],
    [429, 'ai_provider_rate_limited'],
    [503, 'ai_provider_unavailable'],
  ])('maps provider HTTP %i to %s', async (status, code) => {
    const client = new CompatibleAiClient({
      runtime,
      fetch: () => Promise.resolve(new Response('error', { status })),
    });

    await expect(client.suggestPlan(planInput())).rejects.toMatchObject({ code });
  });

  it('shows a redacted provider 400 reason without exposing secrets or paths', async () => {
    const client = new CompatibleAiClient({
      runtime,
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: 'invalid_request_error',
                param: 'max_tokens',
                message: 'bad token sk-secret-value /Users/student/private.py',
              },
            }),
            { status: 400 },
          ),
        ),
    });

    const error = await client.suggestPlan(planInput()).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(Error);
    expect(error).toMatchObject({ code: 'ai_provider_unavailable' });
    expect((error as Error).message).toContain('max_tokens');
    expect((error as Error).message).not.toContain('sk-secret-value');
    expect((error as Error).message).not.toContain('/Users/student/private.py');
  });

  it('retries once without response_format only when the provider rejects that field', async () => {
    const fetcher = vi
      .fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: {
              param: 'response_format',
              message: 'response_format is unsupported',
            },
          }),
          { status: 400 },
        ),
      )
      .mockResolvedValueOnce(providerResponse(planSuggestion));
    const client = new CompatibleAiClient({ runtime, fetch: fetcher });

    await expect(client.suggestPlan(planInput())).resolves.toEqual(planSuggestion);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(requestBody(fetcher, 0)).toContain('"response_format"');
    expect(requestBody(fetcher, 1)).not.toContain('"response_format"');
  });

  it('does not retry an unrelated HTTP 400', async () => {
    const fetcher = vi
      .fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ error: { param: 'model', message: 'model is unavailable' } }),
          { status: 400 },
        ),
      );
    const client = new CompatibleAiClient({ runtime, fetch: fetcher });

    await expect(client.suggestPlan(planInput())).rejects.toMatchObject({
      code: 'ai_provider_unavailable',
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('repairs blank knowledge point text but still rejects invalid containers', async () => {
    const repairClient = new CompatibleAiClient({
      runtime,
      fetch: () =>
        Promise.resolve(
          providerResponse({
            schema_version: 1,
            knowledge_points: [{ name: '边界处理', description: ' ', observation_basis: '' }],
            tests: [],
          }),
        ),
    });
    const repaired = await repairClient.suggestPlan(planInput());
    expect(repaired.knowledge_points[0]?.name).toBe('边界处理');
    expect(repaired.knowledge_points[0]?.description).toContain('边界处理');
    expect(repaired.knowledge_points[0]?.observation_basis).toContain('观察依据');

    const invalidClient = new CompatibleAiClient({
      runtime,
      fetch: () =>
        Promise.resolve(providerResponse({ schema_version: 1, knowledge_points: {}, tests: [] })),
    });
    await expect(invalidClient.suggestPlan(planInput())).rejects.toMatchObject({
      code: 'ai_response_invalid',
    });
  });

  it('maps abort timeout and network failures to stable error codes', async () => {
    const timeoutClient = new CompatibleAiClient({
      runtime,
      suggestionTimeoutMs: 5,
      fetch: (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'));
          });
        }),
    });
    await expect(timeoutClient.suggestPlan(planInput())).rejects.toMatchObject({
      code: 'ai_provider_timeout',
    });

    const networkClient = new CompatibleAiClient({
      runtime,
      fetch: () => Promise.reject(new TypeError('network unavailable')),
    });
    await expect(networkClient.suggestPlan(planInput())).rejects.toMatchObject({
      code: 'ai_provider_network_error',
    });
  });

  it('rejects invalid JSON and performs one length-truncation recovery', async () => {
    const invalidClient = new CompatibleAiClient({
      runtime,
      fetch: () =>
        Promise.resolve(
          new Response(
            JSON.stringify({ choices: [{ finish_reason: 'stop', message: { content: '{bad' } }] }),
            { status: 200 },
          ),
        ),
    });
    await expect(invalidClient.suggestPlan(planInput())).rejects.toMatchObject({
      code: 'ai_response_invalid',
    });

    const fetcher = vi
      .fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(providerResponse({}, 'length'))
      .mockResolvedValueOnce(providerResponse(planSuggestion));
    const recoveryClient = new CompatibleAiClient({ runtime, fetch: fetcher });
    await expect(recoveryClient.suggestPlan(planInput())).resolves.toEqual(planSuggestion);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(requestBody(fetcher, 0)).toContain('"max_tokens":2048');
    expect(requestBody(fetcher, 1)).toContain('"max_tokens":4096');
  });

  it('retries session analysis within the shared budget and validates the result', async () => {
    const fetcher = vi
      .fn<(input: string | URL | Request, init?: RequestInit) => Promise<Response>>()
      .mockRejectedValueOnce(new TypeError('network'))
      .mockResolvedValueOnce(providerResponse(sessionAnalysis));
    const sleep = vi.fn(() => Promise.resolve());
    let currentTime = 0;
    const client = new CompatibleAiClient({
      runtime,
      fetch: fetcher,
      sleep,
      nowMs: () => {
        currentTime += 10;
        return currentTime;
      },
      analysisBudgetMs: 180_000,
    });

    await expect(
      client.analyzeSession({
        sessionId: 'session-ai-001',
        workspaceRoot: '/private/tmp/student-workspace',
        brief: {
          session_result: { status: 'completed' },
          attention_point: null,
          teacher_evaluation: { overall_grade: 'B' },
        },
        evidence: [],
        codeFragments: [],
      }),
    ).resolves.toEqual(sessionAnalysis);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledWith(2000);
    expect(requestBody(fetcher, 1)).toContain('\\"teacher_evaluation\\":{\\"overall_grade\\":\\"B\\"}');
    expect(requestBody(fetcher, 1)).toContain('不得重算、替换或用 AI 结论覆盖');
  });
});
