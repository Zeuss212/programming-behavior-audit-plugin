import { describe, expect, it } from 'vitest';

import {
  AI_API_KEY_SECRET,
  FileAiSettingsService,
  type AiConfiguration,
  type SecretStorageLike,
} from '../ai/aiSettings';

class MemorySecrets implements SecretStorageLike {
  public readonly values = new Map<string, string>();

  public get(key: string): Promise<string | undefined> {
    return Promise.resolve(this.values.get(key));
  }

  public store(key: string, value: string): Promise<void> {
    this.values.set(key, value);
    return Promise.resolve();
  }

  public delete(key: string): Promise<void> {
    this.values.delete(key);
    return Promise.resolve();
  }
}

function configuration(baseUrl?: string, model?: string): AiConfiguration {
  return {
    get: (key) => {
      if (key === 'baseUrl') {
        return baseUrl;
      }
      if (key === 'model') {
        return model;
      }
      return undefined;
    },
  };
}

describe('FileAiSettingsService', () => {
  it('stores the API key under the exact secret name and never exposes its value', async () => {
    const secrets = new MemorySecrets();
    const service = new FileAiSettingsService(configuration(), secrets);
    await service.initialize();
    expect(service.getPublic().hasApiKey).toBe(false);

    await service.saveApiKey('must-not-appear');
    expect(AI_API_KEY_SECRET).toBe('behaviorAudit.ai.apiKey');
    expect(secrets.values.get(AI_API_KEY_SECRET)).toBe('must-not-appear');
    expect(service.getPublic()).toEqual({
      baseUrl: 'https://ark.cn-beijing.volces.com/api/coding/v3',
      model: 'glm-5-2-260617',
      hasApiKey: true,
    });
    expect(JSON.stringify(service.getPublic())).not.toContain('must-not-appear');

    await service.clearApiKey();
    expect(secrets.values.has(AI_API_KEY_SECRET)).toBe(false);
    expect(service.getPublic().hasApiKey).toBe(false);
  });

  it.each([
    'https://provider.example/v1',
    'http://127.0.0.1:8080/v1',
    'http://localhost:8080/v1',
  ])('accepts a safe provider URL: %s', async (baseUrl) => {
    const secrets = new MemorySecrets();
    secrets.values.set(AI_API_KEY_SECRET, 'secret');
    const service = new FileAiSettingsService(configuration(baseUrl, 'model-a'), secrets);
    await service.initialize();

    await expect(service.requireRuntime()).resolves.toMatchObject({
      model: 'model-a',
      apiKey: 'secret',
    });
  });

  it.each([
    'http://provider.example/v1',
    'https://user:pass@provider.example/v1',
    'https://provider.example/v1#fragment',
    'file:///tmp/provider',
    'ftp://provider.example/v1',
  ])('rejects unsafe provider URL: %s', async (baseUrl) => {
    const secrets = new MemorySecrets();
    secrets.values.set(AI_API_KEY_SECRET, 'secret');
    const service = new FileAiSettingsService(configuration(baseUrl), secrets);
    await service.initialize();

    await expect(service.requireRuntime()).rejects.toMatchObject({
      code: 'ai_provider_request_rejected',
    });
  });

  it('reports missing API key without mutating public configuration', async () => {
    const service = new FileAiSettingsService(configuration(), new MemorySecrets());
    await service.initialize();

    await expect(service.requireRuntime()).rejects.toMatchObject({ code: 'ai_not_configured' });
    expect(service.getPublic().hasApiKey).toBe(false);
  });
});
