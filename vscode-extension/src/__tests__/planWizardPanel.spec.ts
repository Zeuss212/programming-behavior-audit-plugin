import { describe, expect, it, vi } from 'vitest';

import { createPlanWizardHtml, PlanWizardPanel } from '../ui/planWizardPanel';

describe('plan wizard panel', () => {
  it('renders a CSP-safe accessible three-step authoring shell', () => {
    const html = createPlanWizardHtml({
      cspSource: 'vscode-resource:',
      nonce: 'nonce-1',
      styleUri: 'style.css',
      scriptUri: 'script.js',
    });

    expect(html).toContain('输入题目');
    expect(html).toContain('确认知识点');
    expect(html).toContain('确认并发布');
    expect(html).toContain('<textarea');
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain("script-src 'nonce-nonce-1'");
    expect(html).not.toContain('<script>');
  });

  it('reuses an existing panel and parses incoming messages before dispatch', async () => {
    const reveal = vi.fn();
    const postMessage = vi.fn(() => Promise.resolve(true));
    let listener: ((value: unknown) => void) | undefined;
    const panel = {
      webview: {
        cspSource: 'vscode-resource:',
        html: '',
        asWebviewUri: (uri: { path: string }) => ({ text: `webview:${uri.path}` }),
        onDidReceiveMessage: (next: (value: unknown) => void) => {
          listener = next;
          return { dispose: vi.fn() };
        },
        postMessage,
      },
      reveal,
      onDidDispose: () => ({ dispose: vi.fn() }),
      dispose: vi.fn(),
    };
    const createPanel = vi.fn(() => panel);
    const onMessage = vi.fn(() => Promise.resolve());
    const wizard = new PlanWizardPanel({
      createPanel,
      mediaRoot: { path: '/media', join: (name) => ({ path: `/media/${name}` }) },
      nonce: () => 'nonce-1',
      onMessage,
    });

    wizard.show();
    wizard.show();
    expect(createPanel).toHaveBeenCalledTimes(1);
    expect(reveal).toHaveBeenCalledTimes(1);
    listener?.({ type: 'ready' });
    await Promise.resolve();
    expect(onMessage).toHaveBeenCalledWith({ type: 'ready' });
    await expect(wizard.postState({ busy: false })).resolves.toBe(true);
  });
});
