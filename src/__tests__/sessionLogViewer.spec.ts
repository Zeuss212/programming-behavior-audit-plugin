import type { IRenderMimeRegistry } from '@jupyterlab/rendermime';
import { Widget } from '@lumino/widgets';

import { ISessionLogFile } from '../services/sessionLogApi';
import { openSessionLogViewer } from '../ui/sessionLogViewer';

const SESSION_ID = '123e4567-e89b-42d3-a456-426614174000';

class TestShell {
  readonly main: Widget[] = [];
  activated = '';

  widgets(_area: 'main'): IterableIterator<Widget> {
    return this.main.values();
  }

  add(widget: Widget, _area: 'main'): void {
    this.main.push(widget);
  }

  activateById(id: string): void {
    this.activated = id;
  }
}

function safeTestRenderMime(): IRenderMimeRegistry {
  const registry = {
    clone: () => registry,
    createRenderer: () => {
      const renderer = new Widget() as Widget & {
        renderModel: (model: {
          data: Record<string, unknown>;
        }) => Promise<void>;
      };
      renderer.renderModel = async model => {
        renderer.node.textContent = String(model.data['text/markdown'] ?? '');
      };
      return renderer;
    },
    createModel: (options: { data: Record<string, unknown> }) => options
  };
  return registry as unknown as IRenderMimeRegistry;
}

function log(overrides: Partial<ISessionLogFile> = {}): ISessionLogFile {
  return {
    kind: 'operation',
    filename: 'operation_log.json',
    label: '操作日志',
    description: '用户输入、删除、粘贴、运行成功/失败及输出。',
    status: 'ready',
    media_type: 'application/json; charset=utf-8',
    size_bytes: 100,
    generated_at: '2026-08-04T00:00:00+00:00',
    error_code: null,
    ...overrides
  };
}

describe('read-only session log viewer', () => {
  afterEach(() => {
    document.body.textContent = '';
  });

  it('opens one stable JSON widget and renders content only as text', async () => {
    const shell = new TestShell();
    let reads = 0;
    const first = await openSessionLogViewer({
      shell,
      rendermime: safeTestRenderMime(),
      sessionId: SESSION_ID,
      log: log(),
      fetchContent: async () => {
        reads += 1;
        return JSON.stringify({ value: '<img src=x onerror=alert(1)>' });
      },
      download: async () => undefined
    });
    const second = await openSessionLogViewer({
      shell,
      rendermime: safeTestRenderMime(),
      sessionId: SESSION_ID,
      log: log(),
      fetchContent: async () => {
        throw new Error('existing widget should be reused');
      },
      download: async () => undefined
    });

    expect(first).toBe(second);
    expect(shell.main).toHaveLength(1);
    expect(shell.activated).toBe(first.id);
    expect(reads).toBe(1);
    expect(first.node.querySelector('pre')?.textContent).toContain(
      '<img src=x onerror=alert(1)>'
    );
    expect(first.node.querySelector('img')).toBeNull();
    expect(first.node.textContent).toContain('operation_log.json');
    expect(first.node.textContent).toContain(SESSION_ID);
    expect(first.node.querySelector('button')?.textContent).toBe('下载');
  });

  it('coalesces concurrent opens for the same session log', async () => {
    const shell = new TestShell();
    let resolveContent!: (value: string) => void;
    const content = new Promise<string>(resolve => {
      resolveContent = resolve;
    });
    const options = {
      shell,
      rendermime: safeTestRenderMime(),
      sessionId: SESSION_ID,
      log: log(),
      fetchContent: jest.fn(() => content),
      download: async () => undefined
    };

    const first = openSessionLogViewer(options);
    const second = openSessionLogViewer(options);
    resolveContent('{"value": 1}');

    expect(await first).toBe(await second);
    expect(options.fetchContent).toHaveBeenCalledTimes(1);
    expect(shell.main).toHaveLength(1);
  });

  it('shows a visible live-region error when viewer download fails', async () => {
    const shell = new TestShell();
    const viewer = await openSessionLogViewer({
      shell,
      rendermime: safeTestRenderMime(),
      sessionId: SESSION_ID,
      log: log({ kind: 'analysis', filename: 'analysis_log.json' }),
      fetchContent: async () => '{"value": 1}',
      download: async () => {
        throw new Error('synthetic failure');
      }
    });

    viewer.node.querySelector<HTMLButtonElement>('button')?.click();
    await Promise.resolve();
    await Promise.resolve();

    const status = viewer.node.querySelector('[role="status"]');
    expect(status?.getAttribute('aria-live')).toBe('polite');
    expect(status?.textContent).toBe('下载失败，请稍后重试。');
  });

  it('renders untrusted Markdown through the Jupyter rendermime registry', async () => {
    const shell = new TestShell();

    const viewer = await openSessionLogViewer({
      shell,
      rendermime: safeTestRenderMime(),
      sessionId: SESSION_ID,
      log: log({
        kind: 'process',
        filename: 'process_log.md',
        label: '过程日志',
        media_type: 'text/markdown; charset=utf-8'
      }),
      fetchContent: async () =>
        '# 过程日志\n<script>window.__unsafe = true</script>',
      download: async () => undefined
    });

    expect(viewer.node.textContent).toContain('过程日志');
    expect(viewer.node.querySelector('script')).toBeNull();
    expect(
      (window as unknown as { __unsafe?: boolean }).__unsafe
    ).toBeUndefined();
  });
});
