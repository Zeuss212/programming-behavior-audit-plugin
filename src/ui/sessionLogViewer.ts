import type { IRenderMimeRegistry } from '@jupyterlab/rendermime';
import { PanelLayout, Widget } from '@lumino/widgets';

import { ISessionLogFile, SessionLogKind } from '../services/sessionLogApi';

const pendingViewers = new Map<string, Promise<SessionLogViewer>>();

export interface ISessionLogViewerShell {
  widgets(area: 'main'): IterableIterator<Widget>;
  add(widget: Widget, area: 'main'): void;
  activateById(id: string): void;
}

export interface IOpenSessionLogViewerOptions {
  shell: ISessionLogViewerShell;
  rendermime: IRenderMimeRegistry;
  sessionId: string;
  log: ISessionLogFile;
  fetchContent: (sessionId: string, kind: SessionLogKind) => Promise<string>;
  download: (
    sessionId: string,
    kind: SessionLogKind,
    filename: ISessionLogFile['filename']
  ) => Promise<void>;
}

export class SessionLogViewer extends Widget {
  private constructor(options: IOpenSessionLogViewerOptions) {
    super();
    this.id = viewerId(options.sessionId, options.log.kind);
    this.title.label = options.log.label;
    this.title.caption = `${options.log.filename} · ${options.sessionId}`;
    this.title.closable = true;
    this.addClass('jp-BehaviorAudit-logViewer');

    const layout = new PanelLayout();
    this.layout = layout;
    layout.addWidget(this.header(options));
  }

  static async create(
    options: IOpenSessionLogViewerOptions,
    content: string
  ): Promise<SessionLogViewer> {
    const viewer = new SessionLogViewer(options);
    const layout = viewer.layout as PanelLayout;
    if (options.log.kind === 'process') {
      const registry = options.rendermime.clone();
      const renderer = registry.createRenderer('text/markdown');
      const model = registry.createModel({
        data: { 'text/markdown': content },
        trusted: false
      });
      await renderer.renderModel(model);
      renderer.addClass('jp-BehaviorAudit-logViewer-content');
      layout.addWidget(renderer);
    } else {
      const body = new Widget();
      body.addClass('jp-BehaviorAudit-logViewer-content');
      const pre = document.createElement('pre');
      let parsed: unknown;
      try {
        parsed = JSON.parse(content) as unknown;
      } catch {
        viewer.dispose();
        throw new Error('日志 JSON 格式无效。');
      }
      pre.textContent = JSON.stringify(parsed, null, 2);
      body.node.appendChild(pre);
      layout.addWidget(body);
    }
    return viewer;
  }

  private header(options: IOpenSessionLogViewerOptions): Widget {
    const header = new Widget();
    header.addClass('jp-BehaviorAudit-logViewer-header');
    const identity = document.createElement('div');
    const filename = document.createElement('strong');
    filename.textContent = options.log.filename;
    const metadata = document.createElement('span');
    metadata.textContent = `会话 ${options.sessionId} · 生成时间 ${
      options.log.generated_at ?? '未知'
    }`;
    identity.append(filename, metadata);

    const download = document.createElement('button');
    const downloadStatus = document.createElement('span');
    downloadStatus.className = 'jp-BehaviorAudit-logViewer-downloadStatus';
    downloadStatus.setAttribute('role', 'status');
    downloadStatus.setAttribute('aria-live', 'polite');
    download.type = 'button';
    download.className = 'jp-BehaviorAudit-button';
    download.textContent = '下载';
    download.addEventListener('click', () => {
      download.disabled = true;
      download.title = '';
      downloadStatus.textContent = '';
      void options
        .download(options.sessionId, options.log.kind, options.log.filename)
        .catch(() => {
          download.title = '下载失败，请稍后重试。';
          downloadStatus.textContent = '下载失败，请稍后重试。';
        })
        .finally(() => {
          download.disabled = false;
        });
    });
    header.node.append(identity, downloadStatus, download);
    return header;
  }
}

export async function openSessionLogViewer(
  options: IOpenSessionLogViewerOptions
): Promise<SessionLogViewer> {
  const id = viewerId(options.sessionId, options.log.kind);
  const existing = findViewer(options.shell, id);
  if (existing) return existing;
  const pending = pendingViewers.get(id);
  if (pending) return pending;
  if (options.log.status !== 'ready') {
    throw new Error('本次日志尚未生成完成。');
  }
  const opening = (async () => {
    const content = await options.fetchContent(
      options.sessionId,
      options.log.kind
    );
    const createdWhileLoading = findViewer(options.shell, id);
    if (createdWhileLoading) return createdWhileLoading;
    const viewer = await SessionLogViewer.create(options, content);
    options.shell.add(viewer, 'main');
    options.shell.activateById(viewer.id);
    return viewer;
  })();
  pendingViewers.set(id, opening);
  try {
    return await opening;
  } finally {
    if (pendingViewers.get(id) === opening) pendingViewers.delete(id);
  }
}

function findViewer(
  shell: ISessionLogViewerShell,
  id: string
): SessionLogViewer | null {
  for (const widget of shell.widgets('main')) {
    if (widget.id === id && widget instanceof SessionLogViewer) {
      shell.activateById(id);
      return widget;
    }
  }
  return null;
}

function viewerId(sessionId: string, kind: SessionLogKind): string {
  return `myextension-session-log-${sessionId}-${kind}`;
}
