import type { WebviewMessage } from './protocol';
import { parseWebviewMessage } from './protocol';

export interface SidebarUri {
  readonly path: string;
  readonly join?: (name: string) => SidebarUri;
  readonly raw?: unknown;
}

export interface SidebarWebviewUri {
  readonly text: string;
}

export interface SidebarDisposable {
  dispose(): void;
}

export interface SidebarWebview {
  readonly cspSource: string;
  options: {
    readonly enableScripts?: boolean;
    readonly localResourceRoots?: readonly SidebarUri[];
  };
  html: string;
  asWebviewUri(uri: SidebarUri): SidebarWebviewUri;
  onDidReceiveMessage(listener: (value: unknown) => void): SidebarDisposable;
  postMessage(value: unknown): PromiseLike<boolean>;
}

export interface SidebarWebviewView {
  readonly webview: SidebarWebview;
}

export interface SidebarHtmlInput {
  readonly cspSource: string;
  readonly nonce: string;
  readonly styleUri: string;
  readonly scriptUri: string;
}

export function createSidebarHtml(input: SidebarHtmlInput): string {
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${input.cspSource}; style-src ${input.cspSource}; script-src 'nonce-${input.nonce}';">
  <link rel="stylesheet" href="${input.styleUri}">
  <title>编程行为分析</title>
</head>
<body>
  <header class="page-header">
    <h1>编程行为分析</h1>
    <p class="scope-notice">本扩展不具备系统隔离或考试防作弊权限，仅记录明确支持的 VS Code 编辑与运行事件。</p>
  </header>
  <nav class="route-tabs" aria-label="功能角色">
    <button type="button" data-route="teacher" aria-pressed="true">教师端</button>
    <button type="button" data-route="student" aria-pressed="false">学生端</button>
  </nav>
  <main>
    <section id="teacher-route" data-route-panel="teacher">
      <h2>教师端</h2>
      <p>发布可携带考核方案，供学生导入后开始采集。</p>
      <div class="action-list">
        <button type="button" data-command="behaviorAudit.openPlanWizard">创建考核方案</button>
        <button type="button" data-command="behaviorAudit.exportPlan">导出已发布方案</button>
      </div>
    </section>
    <section id="student-route" data-route-panel="student" hidden>
      <h2>学生端</h2>
      <p>导入教师方案，确认范围后开始本地采集。</p>
      <label class="consent-row"><input id="consent" type="checkbox">我已了解采集范围并同意开始</label>
      <label class="consent-row"><input id="auto-analyze" type="checkbox" checked>结束时生成 AI 建议（可选）</label>
      <p class="secondary">AI 未配置或暂不可用时，仍会保留并导出本地课堂简报。</p>
      <div class="action-list">
        <button type="button" data-command="behaviorAudit.importPlan">导入方案</button>
        <button type="button" data-command="behaviorAudit.startCapture">开始监控</button>
        <button type="button" data-command="behaviorAudit.resumeCapture">继续中断会话</button>
        <button class="primary-action" type="button" data-command="behaviorAudit.finishAnalyzeExport">结束、生成简报并导出</button>
        <button class="secondary-action" type="button" data-command="behaviorAudit.finishCapture">仅结束并生成简报</button>
        <button class="secondary-action" type="button" data-command="behaviorAudit.analyzeSession">仅生成 AI 建议</button>
        <button class="secondary-action" type="button" data-command="behaviorAudit.exportSession">仅导出上次会话</button>
      </div>
    </section>
    <section aria-labelledby="status-heading">
      <h2 id="status-heading">当前状态</h2>
      <p id="status" role="status" aria-live="polite">正在读取本地状态…</p>
      <p id="notice" class="secondary"></p>
    </section>
  </main>
  <script nonce="${input.nonce}" src="${input.scriptUri}"></script>
</body>
</html>`;
}

function childUri(root: SidebarUri, name: string): SidebarUri {
  return root.join?.(name) ?? { path: `${root.path.replace(/\/$/u, '')}/${name}` };
}

export class AuditSidebarProvider {
  private webview: SidebarWebview | undefined;

  public constructor(
    private readonly mediaRoot: SidebarUri,
    private readonly nonce: () => string,
    private readonly onMessage: (message: WebviewMessage) => Promise<void>,
  ) {}

  public resolveWebviewView(view: SidebarWebviewView): void {
    const webview = view.webview;
    this.webview = webview;
    webview.options = {
      enableScripts: true,
      localResourceRoots: [this.mediaRoot],
    };
    const styleUri = webview.asWebviewUri(childUri(this.mediaRoot, 'sidebar.css')).text;
    const scriptUri = webview.asWebviewUri(childUri(this.mediaRoot, 'sidebar.js')).text;
    webview.html = createSidebarHtml({
      cspSource: webview.cspSource,
      nonce: this.nonce(),
      styleUri,
      scriptUri,
    });
    webview.onDidReceiveMessage((value) => {
      try {
        const parsed = parseWebviewMessage(value);
        void this.onMessage(parsed);
      } catch {
        void webview.postMessage({ type: 'notice', message: '收到无效的侧边栏消息，请刷新后重试。' });
      }
    });
  }

  public async postState(value: unknown): Promise<boolean> {
    return (await this.webview?.postMessage({ type: 'state', value })) ?? false;
  }
}
