import { AuditError } from '../domain/errors';

export const AI_API_KEY_SECRET = 'behaviorAudit.ai.apiKey';
export const DEFAULT_AI_BASE_URL = 'https://ark.cn-beijing.volces.com/api/coding/v3';
export const DEFAULT_AI_MODEL = 'glm-5-2-260617';

export interface AiConfiguration {
  get(key: 'baseUrl' | 'model'): string | undefined;
}

export interface SecretStorageLike {
  get(key: string): Promise<string | undefined>;
  store(key: string, value: string): Promise<void>;
  delete(key: string): Promise<void>;
}

export interface AiPublicSettings {
  readonly baseUrl: string;
  readonly model: string;
  readonly hasApiKey: boolean;
}

export interface AiRuntimeSettings {
  readonly baseUrl: URL;
  readonly model: string;
  readonly apiKey: string;
}

export interface AiSettingsService {
  initialize(): Promise<void>;
  getPublic(): Readonly<AiPublicSettings>;
  saveApiKey(value: string): Promise<void>;
  clearApiKey(): Promise<void>;
  requireRuntime(): Promise<Readonly<AiRuntimeSettings>>;
}

function rejectProvider(message: string): AuditError {
  return new AuditError(
    'ai_provider_request_rejected',
    message,
    '请使用 HTTPS 地址；只有 localhost 和 127.0.0.1 可以使用 HTTP。',
  );
}

export function validateProviderUrl(value: string): URL {
  let url: URL;
  try {
    url = new URL(value);
  } catch (error) {
    throw new AuditError(
      'ai_provider_request_rejected',
      'AI 服务地址不是有效 URL。',
      '请检查 AI 基础 URL 后重试。',
      error,
    );
  }
  if (url.username.length > 0 || url.password.length > 0) {
    throw rejectProvider('AI 服务地址不能包含用户名或密码。');
  }
  if (url.hash.length > 0) {
    throw rejectProvider('AI 服务地址不能包含 URL 片段。');
  }
  const localHttp =
    url.protocol === 'http:' && (url.hostname === '127.0.0.1' || url.hostname === 'localhost');
  if (url.protocol !== 'https:' && !localHttp) {
    throw rejectProvider('AI 服务地址协议不安全。');
  }
  return url;
}

export class FileAiSettingsService implements AiSettingsService {
  private hasApiKey = false;

  public constructor(
    private readonly configuration: AiConfiguration,
    private readonly secrets: SecretStorageLike,
  ) {}

  public async initialize(): Promise<void> {
    const value = await this.secrets.get(AI_API_KEY_SECRET);
    this.hasApiKey = value !== undefined && value.trim().length > 0;
  }

  public getPublic(): Readonly<AiPublicSettings> {
    return {
      baseUrl: this.configuration.get('baseUrl')?.trim() || DEFAULT_AI_BASE_URL,
      model: this.configuration.get('model')?.trim() || DEFAULT_AI_MODEL,
      hasApiKey: this.hasApiKey,
    };
  }

  public async saveApiKey(value: string): Promise<void> {
    const trimmed = value.trim();
    if (trimmed.length === 0) {
      throw new AuditError(
        'ai_not_configured',
        'API Key 不能为空。',
        '请输入有效的 API Key，或清除已有密钥。',
      );
    }
    await this.secrets.store(AI_API_KEY_SECRET, trimmed);
    this.hasApiKey = true;
  }

  public async clearApiKey(): Promise<void> {
    await this.secrets.delete(AI_API_KEY_SECRET);
    this.hasApiKey = false;
  }

  public async requireRuntime(): Promise<Readonly<AiRuntimeSettings>> {
    const apiKey = (await this.secrets.get(AI_API_KEY_SECRET))?.trim();
    if (apiKey === undefined || apiKey.length === 0) {
      this.hasApiKey = false;
      throw new AuditError(
        'ai_not_configured',
        '尚未配置可选 AI 服务的 API Key。',
        '请先运行“配置 AI Key”命令，或继续使用不依赖 AI 的功能。',
      );
    }
    const settings = this.getPublic();
    if (settings.model.length === 0) {
      throw rejectProvider('AI 模型名称不能为空。');
    }
    this.hasApiKey = true;
    return {
      baseUrl: validateProviderUrl(settings.baseUrl),
      model: settings.model,
      apiKey,
    };
  }
}
