import { describe, expect, it, vi } from 'vitest';

import {
  AuditSidebarProvider,
  createSidebarHtml,
  type SidebarWebview,
  type SidebarWebviewView,
} from '../ui/sidebarProvider';

describe('AuditSidebarProvider', () => {
  it('renders semantic Chinese teacher/student UI under a nonce CSP without inline handlers', () => {
    const html = createSidebarHtml({
      cspSource: 'vscode-webview://test',
      nonce: 'nonce-123',
      styleUri: 'vscode-resource:/media/sidebar.css',
      scriptUri: 'vscode-resource:/media/sidebar.js',
    });

    expect(html).toContain("default-src 'none'");
    expect(html).toContain("script-src 'nonce-nonce-123'");
    expect(html).toContain('<h1>编程行为分析</h1>');
    expect(html).toContain('<h2>教师端</h2>');
    expect(html).toContain('<h2>学生端</h2>');
    expect(html).toContain('本扩展不具备系统隔离或考试防作弊权限');
    expect(html).toContain('aria-live="polite"');
    expect(html).not.toMatch(/on(?:click|change|submit)=/iu);
  });

  it('limits local resources to the media directory and validates inbound messages', () => {
    const mediaRoot = { path: '/extension/media' };
    const postMessage = vi.fn(() => Promise.resolve(true));
    let messageListener: ((value: unknown) => void) | undefined;
    const webview: SidebarWebview = {
      cspSource: 'vscode-webview://test',
      options: {},
      html: '',
      asWebviewUri: (uri) => ({ text: `vscode-resource:${uri.path}` }),
      onDidReceiveMessage: (listener) => {
        messageListener = listener;
        return { dispose: () => undefined };
      },
      postMessage,
    };
    const handler = vi.fn(() => Promise.resolve());
    const provider = new AuditSidebarProvider(mediaRoot, () => 'fixed-nonce', handler);
    provider.resolveWebviewView({ webview } satisfies SidebarWebviewView);

    expect(webview.options).toEqual({
      enableScripts: true,
      localResourceRoots: [mediaRoot],
    });
    expect(webview.html).toContain('fixed-nonce');
    messageListener?.({ type: 'navigate', route: 'student' });
    expect(handler).toHaveBeenCalledWith({ type: 'navigate', route: 'student' });
  });
});
