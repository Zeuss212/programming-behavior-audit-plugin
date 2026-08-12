import type { ErrorObject, ValidateFunction } from 'ajv';
import Ajv2020 from 'ajv/dist/2020';

import planSuggestionSchema from '../../schemas/ai-plan-suggestion-v1.schema.json';
import sessionAnalysisSchema from '../../schemas/ai-session-analysis-v1.schema.json';
import { AuditError, type AuditErrorCode } from '../domain/errors';
import type { JsonValue } from '../domain/types';
import type { AiRuntimeSettings } from './aiSettings';
import {
  sanitizePlanSuggestionInput,
  sanitizeSessionAnalysisInput,
  type PlanSuggestionInput,
  type SessionAnalysisInput,
} from './sanitize';

export interface PlanSuggestion {
  readonly schema_version: 1;
  readonly knowledge_points: readonly {
    readonly name: string;
    readonly description: string;
    readonly observation_basis: string;
  }[];
  readonly tests: readonly {
    readonly title: string;
    readonly description: string;
    readonly expected_behavior: string;
  }[];
}

export interface SessionAnalysis {
  readonly schema_version: 1;
  readonly summary: string;
  readonly observations: readonly {
    readonly title: string;
    readonly description: string;
    readonly evidence_event_ids: readonly string[];
  }[];
  readonly attention_points: readonly string[];
}

export interface AiClient {
  suggestPlan(input: PlanSuggestionInput): Promise<PlanSuggestion>;
  analyzeSession(input: SessionAnalysisInput): Promise<SessionAnalysis>;
}

export interface AiRuntimeProvider {
  requireRuntime(): Promise<Readonly<AiRuntimeSettings>>;
}

export type FetchLike = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>;

export interface CompatibleAiClientOptions {
  readonly runtime: AiRuntimeProvider;
  readonly fetch: FetchLike;
  readonly sleep?: (milliseconds: number) => Promise<void>;
  readonly nowMs?: () => number;
  readonly suggestionTimeoutMs?: number;
  readonly providerCallTimeoutMs?: number;
  readonly analysisBudgetMs?: number;
}

interface ProviderChoice {
  readonly finishReason: string | undefined;
  readonly content: string;
}

interface ProviderFailureDetail {
  readonly param?: string;
  readonly code?: string;
  readonly type?: string;
  readonly message?: string;
  readonly rejectsResponseFormat: boolean;
}

function isUnknownArray(value: unknown): value is readonly unknown[] {
  return Array.isArray(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

const ajv = new Ajv2020({ allErrors: true, strict: true });
const validatePlanSuggestion = ajv.compile<PlanSuggestion>(planSuggestionSchema);
const validateSessionAnalysis = ajv.compile<SessionAnalysis>(sessionAnalysisSchema);
const RETRYABLE_ANALYSIS_CODES: readonly AuditErrorCode[] = [
  'ai_provider_timeout',
  'ai_provider_network_error',
  'ai_provider_rate_limited',
  'ai_provider_unavailable',
];

function validationMessage(errors: readonly ErrorObject[] | null | undefined): string {
  return (errors ?? [])
    .slice(0, 3)
    .map((error) => `${error.instancePath || '/'} ${error.message ?? '格式无效'}`)
    .join('；');
}

function endpoint(baseUrl: URL): URL {
  const url = new URL(baseUrl.toString());
  if (url.pathname.endsWith('/chat/completions')) {
    return url;
  }
  if (!url.pathname.endsWith('/')) {
    url.pathname += '/';
  }
  return new URL('chat/completions', url);
}

function redactProviderText(value: string): string {
  return value
    .replace(/Bearer\s+\S+/giu, 'Bearer [REDACTED]')
    .replace(/\b(?:sk|ark)-[A-Za-z0-9_-]{8,}\b/gu, '[REDACTED]')
    .replace(/(?:[A-Za-z]:\\|\/Users\/|\/home\/)[^\s"']+/gu, '[PATH]')
    .replaceAll('\n', ' ')
    .replaceAll('\r', ' ')
    .replaceAll('\t', ' ')
    .trim()
    .slice(0, 300);
}

function optionalText(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim().length > 0
    ? redactProviderText(value.trim())
    : undefined;
}

function providerFailureDetail(body: string): ProviderFailureDetail {
  let error: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(body.slice(0, 4096)) as unknown;
    if (isRecord(parsed)) {
      error = isRecord(parsed.error) ? parsed.error : parsed;
    }
  } catch {
    error = { message: body.slice(0, 4096) };
  }
  const param = optionalText(error.param);
  const code = optionalText(error.code);
  const type = optionalText(error.type);
  const message = optionalText(error.message);
  const searchable = [param, code, type, message].filter(Boolean).join(' ').toLowerCase();
  return {
    ...(param === undefined ? {} : { param }),
    ...(code === undefined ? {} : { code }),
    ...(type === undefined ? {} : { type }),
    ...(message === undefined ? {} : { message }),
    rejectsResponseFormat:
      searchable.includes('response_format') ||
      searchable.includes('json mode') ||
      searchable.includes('structured output'),
  };
}

function providerError(status: number, detail: ProviderFailureDetail): AuditError {
  if (status === 401 || status === 403) {
    return new AuditError(
      'ai_provider_auth_failed',
      'AI 服务拒绝了身份验证。',
      '请重新配置 API Key 后重试。',
    );
  }
  if (status === 429) {
    return new AuditError(
      'ai_provider_rate_limited',
      'AI 服务当前请求过多。',
      '请稍后重试，核心本地功能不受影响。',
    );
  }
  const reason = [detail.param, detail.code, detail.type, detail.message]
    .filter((value, index, values): value is string => value !== undefined && values.indexOf(value) === index)
    .join('：');
  return new AuditError(
    'ai_provider_unavailable',
    `AI 服务返回 HTTP ${String(status)}${reason.length === 0 ? '。' : `：${reason}`}`,
    '请稍后重试，或继续使用不依赖 AI 的功能。',
  );
}

function nonBlank(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : undefined;
}

function repairPlanSuggestion(value: unknown): unknown {
  if (!isRecord(value) || !Array.isArray(value.knowledge_points) || !Array.isArray(value.tests)) {
    return value;
  }
  return {
    ...value,
    schema_version: 1,
    knowledge_points: value.knowledge_points.map((item, index) => {
      if (!isRecord(item)) {
        return item as unknown;
      }
      const name = nonBlank(item.name) ?? `知识点 ${String(index + 1)}`;
      return {
        name,
        description:
          nonBlank(item.description) ?? `观察与“${name}”相关的代码实现与运行过程。`,
        observation_basis:
          nonBlank(item.observation_basis) ??
          `以“${name}”相关的代码编辑、运行结果或错误修正记录作为观察依据。`,
      };
    }),
  };
}

function parseChoice(value: unknown): ProviderChoice {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw invalidResponse('AI 服务响应不是 JSON 对象。');
  }
  const choices = (value as Record<string, unknown>).choices;
  if (!isUnknownArray(choices) || choices.length === 0) {
    throw invalidResponse('AI 服务响应缺少 choices。');
  }
  const choice = choices[0];
  if (choice === null || typeof choice !== 'object' || Array.isArray(choice)) {
    throw invalidResponse('AI 服务响应中的 choice 无效。');
  }
  const record = choice as Record<string, unknown>;
  const message = record.message;
  if (message === null || typeof message !== 'object' || Array.isArray(message)) {
    throw invalidResponse('AI 服务响应缺少 message。');
  }
  const content = (message as Record<string, unknown>).content;
  if (typeof content !== 'string') {
    throw invalidResponse('AI 服务响应缺少文本内容。');
  }
  return {
    finishReason: typeof record.finish_reason === 'string' ? record.finish_reason : undefined,
    content,
  };
}

function invalidResponse(message: string, cause?: unknown): AuditError {
  return new AuditError(
    'ai_response_invalid',
    message,
    '请重试；若持续失败，请检查服务是否返回兼容的 JSON。',
    cause,
  );
}

export class CompatibleAiClient implements AiClient {
  private readonly sleep: (milliseconds: number) => Promise<void>;
  private readonly nowMs: () => number;
  private readonly suggestionTimeoutMs: number;
  private readonly providerCallTimeoutMs: number;
  private readonly analysisBudgetMs: number;

  public constructor(private readonly options: CompatibleAiClientOptions) {
    this.sleep = options.sleep ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
    this.nowMs = options.nowMs ?? (() => Date.now());
    this.suggestionTimeoutMs = options.suggestionTimeoutMs ?? 60_000;
    this.providerCallTimeoutMs = options.providerCallTimeoutMs ?? 60_000;
    this.analysisBudgetMs = options.analysisBudgetMs ?? 180_000;
  }

  public async suggestPlan(input: PlanSuggestionInput): Promise<PlanSuggestion> {
    const runtime = await this.options.runtime.requireRuntime();
    const sanitized = sanitizePlanSuggestionInput(input);
    for (const maximumTokens of [2048, 4096] as const) {
      try {
        return await this.request(
          runtime,
          sanitized as unknown as JsonValue,
          maximumTokens,
          this.suggestionTimeoutMs,
          validatePlanSuggestion,
          '方案建议',
        );
      } catch (error) {
        if (
          error instanceof AuditError &&
          error.code === 'ai_response_truncated' &&
          maximumTokens === 2048
        ) {
          continue;
        }
        throw error;
      }
    }
    throw new AuditError(
      'ai_response_truncated',
      'AI 方案建议在一次扩容重试后仍不完整。',
      '请缩短题目或代码引用后重试。',
    );
  }

  public async analyzeSession(input: SessionAnalysisInput): Promise<SessionAnalysis> {
    const runtime = await this.options.runtime.requireRuntime();
    const sanitized = sanitizeSessionAnalysisInput(input);
    const deadline = this.nowMs() + this.analysisBudgetMs;
    let lastError: unknown;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      const remaining = deadline - this.nowMs();
      if (remaining <= 0) {
        break;
      }
      try {
        return await this.request(
          runtime,
          sanitized as unknown as JsonValue,
          4096,
          Math.min(this.providerCallTimeoutMs, remaining),
          validateSessionAnalysis,
          '会话分析',
        );
      } catch (error) {
        lastError = error;
        if (
          !(error instanceof AuditError) ||
          !RETRYABLE_ANALYSIS_CODES.includes(error.code) ||
          attempt === 3
        ) {
          throw error;
        }
        if (
          error.code !== 'ai_provider_timeout' &&
          deadline - this.nowMs() > 2000
        ) {
          await this.sleep(2000);
        }
      }
    }
    if (lastError instanceof Error) {
      throw lastError;
    }
    throw new AuditError(
      'ai_provider_timeout',
      'AI 会话分析超过 180 秒总预算。',
      '请稍后重试；本地简报和导出仍可正常使用。',
    );
  }

  private async request<T>(
    runtime: Readonly<AiRuntimeSettings>,
    data: JsonValue,
    maximumTokens: number,
    timeoutMs: number,
    validate: ValidateFunction<T>,
    purpose: string,
  ): Promise<T> {
    let response: Response | undefined;
    for (const includeResponseFormat of [true, false]) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        response = await this.options.fetch(endpoint(runtime.baseUrl), {
          method: 'POST',
          headers: {
            authorization: `Bearer ${runtime.apiKey}`,
            'content-type': 'application/json',
          },
          body: JSON.stringify({
            model: runtime.model,
            max_tokens: maximumTokens,
            ...(includeResponseFormat ? { response_format: { type: 'json_object' } } : {}),
            messages: [
              {
                role: 'system',
                content: `你是课堂编程行为审计助手。生成${purpose}时只能依据引用数据，不评分、不排名、不判断能力或掌握程度。只返回一个有效 JSON 对象，不得包含 Markdown 或额外解释。`,
              },
              { role: 'user', content: JSON.stringify(data) },
            ],
          }),
          signal: controller.signal,
        });
      } catch (error) {
        if (error instanceof Error && error.name === 'AbortError') {
          throw new AuditError(
            'ai_provider_timeout',
            'AI 服务请求超时。',
            '请稍后重试；本地功能不受影响。',
            error,
          );
        }
        throw new AuditError(
          'ai_provider_network_error',
          '无法连接 AI 服务。',
          '请检查网络和基础 URL，或继续使用本地功能。',
          error,
        );
      } finally {
        clearTimeout(timer);
      }
      if (response.ok) {
        break;
      }
      const detail = providerFailureDetail(await response.text());
      if (!(response.status === 400 && includeResponseFormat && detail.rejectsResponseFormat)) {
        throw providerError(response.status, detail);
      }
    }
    if (response === undefined || !response.ok) {
      throw providerError(response?.status ?? 503, {
        rejectsResponseFormat: false,
      });
    }

    let envelope: unknown;
    try {
      envelope = JSON.parse(await response.text()) as unknown;
    } catch (error) {
      throw invalidResponse('AI 服务响应不是有效 JSON。', error);
    }
    const choice = parseChoice(envelope);
    if (choice.finishReason === 'length') {
      throw new AuditError(
        'ai_response_truncated',
        'AI 服务因长度限制返回了不完整结果。',
        '扩展将缩减或扩大一次请求后重试。',
      );
    }
    let value: unknown;
    try {
      value = JSON.parse(choice.content) as unknown;
    } catch (error) {
      throw invalidResponse('AI 返回内容不是有效 JSON。', error);
    }
    const normalized = purpose === '方案建议' ? repairPlanSuggestion(value) : value;
    if (!validate(normalized)) {
      throw invalidResponse(`AI 返回的${purpose}格式无效：${validationMessage(validate.errors)}`);
    }
    return normalized;
  }
}
