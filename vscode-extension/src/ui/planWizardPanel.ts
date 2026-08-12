import { parsePlanWizardMessage, type PlanWizardMessage, type PlanWizardViewState } from './planWizardProtocol';

export interface WizardUri {
  readonly path: string;
  readonly join?: (name: string) => WizardUri;
  readonly raw?: unknown;
}

interface Disposable {
  dispose(): void;
}

interface WizardWebview {
  readonly cspSource: string;
  html: string;
  asWebviewUri(uri: WizardUri): { readonly text: string };
  onDidReceiveMessage(listener: (value: unknown) => void): Disposable;
  postMessage(value: unknown): PromiseLike<boolean>;
}

export interface WizardPanel {
  readonly webview: WizardWebview;
  reveal(): void;
  onDidDispose(listener: () => void): Disposable;
  dispose(): void;
}

export interface PlanWizardHtmlInput {
  readonly cspSource: string;
  readonly nonce: string;
  readonly styleUri: string;
  readonly scriptUri: string;
}

export interface PlanWizardPanelOptions {
  readonly createPanel: () => WizardPanel;
  readonly mediaRoot: WizardUri;
  readonly nonce: () => string;
  readonly onMessage: (message: PlanWizardMessage) => Promise<void>;
}

function childUri(root: WizardUri, name: string): WizardUri {
  return root.join?.(name) ?? { path: `${root.path.replace(/\/$/u, '')}/${name}` };
}

export function createPlanWizardHtml(input: PlanWizardHtmlInput): string {
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${input.cspSource}; script-src 'nonce-${input.nonce}';">
  <link rel="stylesheet" href="${input.styleUri}">
  <title>创建考核方案</title>
</head>
<body>
  <main class="wizard-shell">
    <header class="page-header">
      <p class="eyebrow">教师端</p>
      <h1>创建考核方案</h1>
      <p>分三步完成题目、知识点与发布确认。内容会自动保存为本地草稿。</p>
    </header>
    <ol class="stepper" aria-label="创建步骤">
      <li data-step-indicator="1"><span>1</span>输入题目</li>
      <li data-step-indicator="2"><span>2</span>确认知识点</li>
      <li data-step-indicator="3"><span>3</span>确认并发布</li>
    </ol>

    <section class="step" data-step="1" aria-labelledby="step-1-heading">
      <h2 id="step-1-heading" tabindex="-1">输入题目</h2>
      <p class="helper">可输入完整要求、示例输入输出和注意事项。</p>
      <label for="problem-text">编程题目</label>
      <textarea id="problem-text" class="problem-input" maxlength="20000" placeholder="例如：实现 analyze_scores 函数，需要正确处理空列表、默认参数与异常输入……"></textarea>
      <div class="field-meta"><span id="problem-error" class="error" role="alert"></span><span id="problem-count">0 / 20000</span></div>
    </section>

    <section class="step" data-step="2" aria-labelledby="step-2-heading" hidden>
      <div class="section-heading">
        <div><h2 id="step-2-heading" tabindex="-1">确认知识点</h2><p class="helper">AI 只提供建议，所有字段都可编辑。</p></div>
        <div class="button-row"><button type="button" id="generate-ai" class="secondary">生成 AI 建议</button><button type="button" id="add-kp">手动添加</button></div>
      </div>
      <div id="knowledge-empty" class="empty-state"><strong>还没有知识点</strong><p>可使用 AI 生成初稿，或手动添加。</p></div>
      <div id="knowledge-list" class="card-list"></div>
      <p id="knowledge-error" class="error" role="alert"></p>
    </section>

    <section class="step" data-step="3" aria-labelledby="step-3-heading" hidden>
      <h2 id="step-3-heading" tabindex="-1">确认并发布</h2>
      <p class="helper">发布后才会生成可导出的方案版本。</p>
      <div class="review-grid"><article><h3>题目</h3><pre id="review-problem"></pre></article><article><h3>知识点</h3><div id="review-knowledge"></div></article></div>
      <div id="published-result" class="success" hidden></div>
    </section>

    <p id="wizard-status" class="status" role="status" aria-live="polite"></p>
    <footer class="wizard-actions">
      <button type="button" id="previous" class="secondary" hidden>上一步</button>
      <span class="spacer"></span>
      <button type="button" id="next">下一步</button>
      <button type="button" id="publish" hidden>发布方案</button>
      <button type="button" id="export" hidden>导出方案</button>
    </footer>
  </main>
  <script nonce="${input.nonce}" src="${input.scriptUri}"></script>
</body>
</html>`;
}

export class PlanWizardPanel {
  private panel: WizardPanel | undefined;

  public constructor(private readonly options: PlanWizardPanelOptions) {}

  public show(): void {
    if (this.panel !== undefined) {
      this.panel.reveal();
      return;
    }
    const panel = this.options.createPanel();
    this.panel = panel;
    const styleUri = panel.webview.asWebviewUri(childUri(this.options.mediaRoot, 'plan-wizard.css')).text;
    const scriptUri = panel.webview.asWebviewUri(childUri(this.options.mediaRoot, 'plan-wizard.js')).text;
    panel.webview.html = createPlanWizardHtml({
      cspSource: panel.webview.cspSource,
      nonce: this.options.nonce(),
      styleUri,
      scriptUri,
    });
    panel.webview.onDidReceiveMessage((value) => {
      try {
        void this.options.onMessage(parsePlanWizardMessage(value));
      } catch {
        void panel.webview.postMessage({
          type: 'state',
          value: { busy: false, notice: { kind: 'error', message: '收到无效的方案向导消息。' } },
        });
      }
    });
    panel.onDidDispose(() => {
      this.panel = undefined;
    });
  }

  public async postState(value: PlanWizardViewState | Record<string, unknown>): Promise<boolean> {
    return (await this.panel?.webview.postMessage({ type: 'state', value })) ?? false;
  }

  public dispose(): void {
    this.panel?.dispose();
    this.panel = undefined;
  }
}
