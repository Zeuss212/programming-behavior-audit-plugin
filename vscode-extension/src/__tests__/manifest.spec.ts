import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

type ExtensionManifest = Record<string, unknown> & {
  capabilities?: {
    untrustedWorkspaces?: { supported?: unknown };
    virtualWorkspaces?: { supported?: unknown };
  };
};

function readManifest(): ExtensionManifest {
  const manifestPath = resolve(process.cwd(), 'package.json');
  return JSON.parse(readFileSync(manifestPath, 'utf8')) as ExtensionManifest;
}

describe('VS Code extension manifest', () => {
  it('locks the desktop extension identity and compatibility contract', () => {
    const manifest = readManifest();

    expect(manifest.name).toBe('behavior-audit-vscode');
    expect(manifest.displayName).toBe('编程行为分析');
    expect(manifest.publisher).toBe('bluedot-ai');
    expect(manifest.version).toBe('0.1.5');
    expect(manifest.engines).toEqual({ vscode: '^1.125.0' });
    expect(manifest.main).toBe('./dist/extension.js');
    expect(manifest.extensionKind).toEqual(['workspace']);
    expect(manifest.extensionPack).toEqual(['ms-python.python']);
    expect(manifest.capabilities?.untrustedWorkspaces?.supported).toBe('limited');
    expect(manifest.capabilities?.virtualWorkspaces?.supported).toBe(false);
    expect(manifest).not.toHaveProperty('browser');
    expect(manifest).not.toHaveProperty('extensionDependencies');
  });

  it('exports the VS Code activation lifecycle', async () => {
    const extension = await import('../extension');

    expect(extension.activate).toBeTypeOf('function');
    expect(extension.deactivate).toBeTypeOf('function');
  });
});
