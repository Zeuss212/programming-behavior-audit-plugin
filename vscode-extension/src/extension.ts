import { spawn } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { join } from 'node:path';

import type * as VSCode from 'vscode';

import { CompatibleAiClient } from './ai/aiClient';
import { FileAiSettingsService } from './ai/aiSettings';
import { DurableCaptureController } from './capture/captureController';
import { TextCollector, type TextCollectorHost } from './capture/textCollector';
import { workspaceIdentity } from './capture/workspaceIdentity';
import { canonicalJson } from './domain/canonicalJson';
import type { JsonObject, JsonValue, PublishedPlan, SessionState } from './domain/types';
import { StableNotebookCollector, type NotebookCollectorHost } from './notebooks/notebookCollector';
import { FilePlanRepository } from './plans/planRepository';
import { applySuggestion, toPublishPlanInput } from './plans/planDraft';
import { PlanDraftStore } from './plans/planDraftStore';
import { FileReportService, FileSessionExporter } from './reports/exporter';
import { VsCodePythonRunner, type PythonTextDocument } from './runners/pythonRunner';
import { FileSessionRepository } from './storage/sessionRepository';
import {
  registerAuditCommands,
  type AuditCommandHost,
  type CommandActions,
} from './commands/registerCommands';
import type { AuditCommandId, SidebarRoute, WebviewMessage } from './ui/protocol';
import {
  AuditSidebarProvider,
  type SidebarUri,
  type SidebarWebview,
} from './ui/sidebarProvider';
import { AuditStatusBar, type StatusBarItemLike } from './ui/statusBar';
import {
  PlanWizardPanel,
  type WizardPanel,
  type WizardUri,
} from './ui/planWizardPanel';
import type { PlanWizardMessage } from './ui/planWizardProtocol';

interface ActiveRuntime {
  readonly capture: DurableCaptureController;
}

export interface TestAuditApi {
  readonly storageRoot: string;
  startSession(): Promise<string>;
  finishSession(): Promise<string>;
  flush(): Promise<void>;
  readBrief(sessionId: string): Promise<unknown>;
  recoverPersistedSession(): Promise<SessionState | undefined>;
}

let activeRuntime: ActiveRuntime | undefined;

function isJsonObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function nonce(): string {
  return randomBytes(18).toString('base64url');
}

class VsCodeWebviewAdapter implements SidebarWebview {
  private sidebarOptions: SidebarWebview['options'] = {};

  public constructor(private readonly webview: VSCode.Webview) {}

  public get cspSource(): string {
    return this.webview.cspSource;
  }

  public get options(): SidebarWebview['options'] {
    return this.sidebarOptions;
  }

  public set options(value: SidebarWebview['options']) {
    this.sidebarOptions = value;
    const localResourceRoots = value.localResourceRoots
      ?.map((uri) => uri.raw)
      .filter((raw): raw is VSCode.Uri => raw !== undefined);
    this.webview.options = {
      ...(value.enableScripts === undefined ? {} : { enableScripts: value.enableScripts }),
      ...(localResourceRoots === undefined ? {} : { localResourceRoots }),
    };
  }

  public get html(): string {
    return this.webview.html;
  }

  public set html(value: string) {
    this.webview.html = value;
  }

  public asWebviewUri(uri: SidebarUri): { readonly text: string } {
    if (uri.raw === undefined) {
      throw new Error('Missing VS Code media URI.');
    }
    return { text: this.webview.asWebviewUri(uri.raw as VSCode.Uri).toString() };
  }

  public onDidReceiveMessage(listener: (value: unknown) => void): VSCode.Disposable {
    return this.webview.onDidReceiveMessage((value: unknown) => listener(value));
  }

  public postMessage(value: unknown): PromiseLike<boolean> {
    return this.webview.postMessage(value);
  }
}

class VsCodePlanWizardPanelAdapter implements WizardPanel {
  public constructor(private readonly panel: VSCode.WebviewPanel) {}

  public get webview() {
    const webview = this.panel.webview;
    return {
      cspSource: webview.cspSource,
      get html(): string {
        return webview.html;
      },
      set html(value: string) {
        webview.html = value;
      },
      asWebviewUri: (uri: WizardUri) => {
        if (uri.raw === undefined) {
          throw new Error('Missing VS Code media URI.');
        }
        return { text: webview.asWebviewUri(uri.raw as VSCode.Uri).toString() };
      },
      onDidReceiveMessage: (listener: (value: unknown) => void) =>
        webview.onDidReceiveMessage(listener),
      postMessage: (value: unknown) => webview.postMessage(value),
    };
  }

  public reveal(): void {
    this.panel.reveal();
  }

  public onDidDispose(listener: () => void): VSCode.Disposable {
    return this.panel.onDidDispose(listener);
  }

  public dispose(): void {
    this.panel.dispose();
  }
}

function textCollectorHost(
  vscode: typeof import('vscode'),
  workspaceRootPath: string,
): TextCollectorHost {
  return {
    workspaceRootPath,
    onDidChangeTextDocument: (listener) =>
      vscode.workspace.onDidChangeTextDocument((event) => listener(event)),
    onDidSaveTextDocument: (listener) =>
      vscode.workspace.onDidSaveTextDocument((document) => listener(document)),
    onDidChangeActiveTextEditor: (listener) =>
      vscode.window.onDidChangeActiveTextEditor((editor) => listener(editor)),
    onDidChangeWindowState: (listener) =>
      vscode.window.onDidChangeWindowState((state) => listener({ focused: state.focused })),
    onDidOpenTerminal: (listener) =>
      vscode.window.onDidOpenTerminal((terminal) => listener(terminal)),
    registerCommand: (name, handler) => vscode.commands.registerCommand(name, handler),
    executeCommand: async (name) => vscode.commands.executeCommand(name),
  };
}

function notebookCollectorHost(
  vscode: typeof import('vscode'),
  workspaceRootPath: string,
): NotebookCollectorHost {
  return {
    workspaceRootPath,
    onDidChangeNotebookDocument: (listener) =>
      vscode.workspace.onDidChangeNotebookDocument((event) => listener(event)),
  };
}

export async function activate(
  context: VSCode.ExtensionContext,
): Promise<void | TestAuditApi> {
  const vscode = await import('vscode');
  const workspaceFolders = vscode.workspace.workspaceFolders ?? [];
  const workspaceRoot = workspaceFolders[0]?.uri.fsPath;
  const workspaceId = workspaceIdentity(workspaceFolders.map((folder) => folder.uri.toString()));
  const storageRoot = context.globalStorageUri.fsPath;
  const planRepository = new FilePlanRepository(
    join(storageRoot, 'plans'),
    () => new Date(),
    () => `plan-${randomUUID()}`,
  );
  const sessionRepository = new FileSessionRepository(
    storageRoot,
    () => new Date(),
    () => `session-${randomUUID()}`,
  );
  const capture = new DurableCaptureController({
    repository: sessionRepository,
    workspaceId,
    isTrusted: () => vscode.workspace.isTrusted && workspaceRoot !== undefined,
    now: () => new Date(),
    monotonicNow: () => performance.now(),
    setCollectingContext: async (collecting) =>
      vscode.commands.executeCommand('setContext', 'behaviorAudit.collecting', collecting),
  });
  activeRuntime = { capture };

  const reportService = new FileReportService(sessionRepository, () => new Date());
  const sessionExporter = new FileSessionExporter(
    sessionRepository,
    '0.1.1',
    () => new Date(),
  );
  const aiSettings = new FileAiSettingsService(
    {
      get: (key) => vscode.workspace.getConfiguration('behaviorAudit.ai').get<string>(key),
    },
    {
      get: async (key) => context.secrets.get(key),
      store: async (key, value) => context.secrets.store(key, value),
      delete: async (key) => context.secrets.delete(key),
    },
  );
  await aiSettings.initialize();
  const aiClient = new CompatibleAiClient({ runtime: aiSettings, fetch: globalThis.fetch });
  const planDraftStore = new PlanDraftStore(context.workspaceState, () => new Date());
  const pythonRunner = new VsCodePythonRunner({
    pythonExtensionAvailable: () => vscode.extensions.getExtension('ms-python.python') !== undefined,
    pythonApi: async () => {
      const pythonExtension = await import('@vscode/python-extension');
      return pythonExtension.PythonExtension.api();
    },
    spawn: (command, args, options) => spawn(command, [...args], options),
    controller: capture,
    workspaceTrusted: () => vscode.workspace.isTrusted,
    monotonicNow: () => performance.now(),
  });

  let route: SidebarRoute = 'teacher';
  let consent = false;
  let selectedPlan: PublishedPlan | undefined;
  let interruptedState = await sessionRepository.findActive(workspaceId);
  let lastSessionId: string | undefined;
  const mediaUri = vscode.Uri.joinPath(context.extensionUri, 'media');
  const mediaRoot: SidebarUri = {
    path: mediaUri.path,
    raw: mediaUri,
    join: (name) => {
      const uri = vscode.Uri.joinPath(mediaUri, name);
      return { path: uri.path, raw: uri };
    },
  };
  const wizardMediaRoot: WizardUri = mediaRoot;

  const statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusItem.command = 'behaviorAudit.openStudent';
  const statusBar = new AuditStatusBar(statusItem as StatusBarItemLike, () => new Date());
  context.subscriptions.push(statusBar);

  const refreshPresentation = async (): Promise<void> => {
    const plans = await planRepository.list();
    if (selectedPlan === undefined) {
      selectedPlan = plans[0];
    }
    const session = capture.current() ?? interruptedState;
    statusBar.update(session);
    await sidebar.postState({
      route,
      trusted: vscode.workspace.isTrusted,
      plans: plans.map((plan) => ({
        planId: plan.plan_id,
        version: plan.version,
        problemText: plan.problem_text,
      })),
      ...(selectedPlan === undefined
        ? {}
        : { selectedPlan: { planId: selectedPlan.plan_id, version: selectedPlan.version } }),
      consent,
      ...(session === undefined
        ? {}
        : {
            session: {
              sessionId: session.session_id,
              status: session.status,
              eventCount: session.last_event_seq,
            },
          }),
      ai: { configured: aiSettings.getPublic().hasApiKey },
      ...(workspaceRoot === undefined ? { notice: '请先打开一个本地文件夹工作区。' } : {}),
    });
  };

  const onWebviewMessage = async (message: WebviewMessage): Promise<void> => {
    if (message.type === 'navigate') {
      route = message.route;
      await refreshPresentation();
      return;
    }
    if (message.type === 'setConsent') {
      consent = message.value;
      await refreshPresentation();
      return;
    }
    if (message.type === 'command') {
      await vscode.commands.executeCommand(message.command);
      return;
    }
    await refreshPresentation();
  };

  const sidebar = new AuditSidebarProvider(mediaRoot, nonce, onWebviewMessage);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider('behaviorAudit.sidebar', {
      resolveWebviewView: (view) => {
        sidebar.resolveWebviewView({ webview: new VsCodeWebviewAdapter(view.webview) });
        void refreshPresentation();
      },
    }),
  );

  const showDataView = async (nextRoute: SidebarRoute): Promise<void> => {
    route = nextRoute;
    await vscode.commands.executeCommand('workbench.view.extension.behaviorAudit');
    await refreshPresentation();
  };

  const importPlan = async (): Promise<void> => {
    const selected = await vscode.window.showOpenDialog({
      canSelectMany: false,
      filters: { JSON: ['json'] },
      openLabel: '导入方案',
    });
    const uri = selected?.[0];
    if (uri !== undefined) {
      selectedPlan = await planRepository.import(await vscode.workspace.fs.readFile(uri));
    }
  };

  const exportPlan = async (): Promise<void> => {
    if (selectedPlan === undefined) {
      throw new Error('尚未选择可导出的方案。');
    }
    const uri = await vscode.window.showSaveDialog({
      defaultUri: vscode.Uri.file(`${selectedPlan.plan_id}-v${String(selectedPlan.version)}.json`),
      filters: { JSON: ['json'] },
      saveLabel: '导出方案',
    });
    if (uri !== undefined) {
      await vscode.workspace.fs.writeFile(
        uri,
        await planRepository.export(selectedPlan.plan_id, selectedPlan.version),
      );
    }
  };

  const postWizardError = async (error: unknown): Promise<void> => {
    const message = error instanceof Error ? error.message : '操作失败，请重试。';
    await planWizard.postState({
      draft: planDraftStore.load(),
      aiConfigured: aiSettings.getPublic().hasApiKey,
      busy: false,
      notice: { kind: 'error', message },
    });
  };
  const onPlanWizardMessage = async (message: PlanWizardMessage): Promise<void> => {
    try {
      if (message.type === 'ready') {
        await planWizard.postState({
          draft: planDraftStore.load(),
          aiConfigured: aiSettings.getPublic().hasApiKey,
          busy: false,
        });
        return;
      }
      if (message.type === 'saveDraft') {
        await planDraftStore.save(message.draft);
        return;
      }
      if (message.type === 'requestSuggestion') {
        const draft = { ...planDraftStore.load(), problemText: message.problemText.trim() };
        await planDraftStore.save(draft);
        await planWizard.postState({ draft, busy: true, aiConfigured: true, notice: { kind: 'info', message: 'AI 正在生成建议……' } });
        const suggestion = await aiClient.suggestPlan({
          problemText: draft.problemText,
          workspaceRoot: workspaceRoot ?? '',
          codeFragments: [],
        });
        const suggestedDraft = applySuggestion(draft, suggestion, new Date().toISOString());
        await planDraftStore.save(suggestedDraft);
        await planWizard.postState({
          draft: suggestedDraft,
          aiConfigured: true,
          busy: false,
          notice: { kind: 'info', message: 'AI 建议已生成，请人工复核后继续。' },
        });
        return;
      }
      if (message.type === 'publishDraft') {
        await planDraftStore.save(message.draft);
        selectedPlan = await planRepository.publish(toPublishPlanInput(message.draft));
        await planDraftStore.clear();
        await refreshPresentation();
        await planWizard.postState({
          busy: false,
          published: { planId: selectedPlan.plan_id, version: selectedPlan.version },
          notice: { kind: 'info', message: '方案已发布，可以导出给学生端。' },
        });
        return;
      }
      if (message.type === 'exportPublishedPlan') {
        await exportPlan();
        return;
      }
      if (message.type === 'closeWizard') {
        planWizard.dispose();
      }
    } catch (error) {
      await postWizardError(error);
    }
  };
  const planWizard = new PlanWizardPanel({
    createPanel: () =>
      new VsCodePlanWizardPanelAdapter(
        vscode.window.createWebviewPanel(
          'behaviorAudit.planWizard',
          '创建考核方案',
          vscode.ViewColumn.One,
          {
            enableScripts: true,
            retainContextWhenHidden: true,
            localResourceRoots: [mediaUri],
          },
        ),
      ),
    mediaRoot: wizardMediaRoot,
    nonce,
    onMessage: onPlanWizardMessage,
  });
  context.subscriptions.push({ dispose: () => planWizard.dispose() });

  const openPlanWizard = (): Promise<void> => {
    planWizard.show();
    return Promise.resolve();
  };

  const analyzeSession = async (): Promise<void> => {
    if (lastSessionId === undefined) {
      throw new Error('尚没有可分析的已结束会话。');
    }
    const bytes = await sessionRepository.readArtifact(lastSessionId, 'classroom_brief');
    if (bytes === undefined) {
      throw new Error('请先生成本地课堂简报。');
    }
    const value = JSON.parse(new TextDecoder().decode(bytes)) as unknown;
    if (!isJsonObject(value)) {
      throw new Error('本地课堂简报格式无效。');
    }
    const analysis = await aiClient.analyzeSession({
      sessionId: lastSessionId,
      workspaceRoot: workspaceRoot ?? '',
      brief: value,
      evidence: [],
      codeFragments: [],
    });
    await sessionRepository.writeArtifact(
      lastSessionId,
      'ai_analysis',
      new TextEncoder().encode(`${canonicalJson(analysis as unknown as JsonValue)}\n`),
    );
  };

  const exportSession = async (): Promise<void> => {
    const sessionId = lastSessionId ?? capture.current()?.session_id;
    if (sessionId === undefined) {
      throw new Error('尚没有可导出的会话。');
    }
    const selected = await vscode.window.showOpenDialog({
      canSelectFiles: false,
      canSelectFolders: true,
      canSelectMany: false,
      openLabel: '选择导出位置',
    });
    const uri = selected?.[0];
    if (uri !== undefined) {
      await sessionExporter.exportSession(sessionId, { fsPath: uri.fsPath });
    }
  };

  const actions: CommandActions = {
    'behaviorAudit.openTeacher': async () => showDataView('teacher'),
    'behaviorAudit.openStudent': async () => showDataView('student'),
    'behaviorAudit.openPlanWizard': openPlanWizard,
    'behaviorAudit.publishPlan': openPlanWizard,
    'behaviorAudit.suggestPlan': openPlanWizard,
    'behaviorAudit.importPlan': importPlan,
    'behaviorAudit.exportPlan': exportPlan,
    'behaviorAudit.startCapture': async () => Promise.resolve(),
    'behaviorAudit.resumeCapture': async () => Promise.resolve(),
    'behaviorAudit.finishCapture': async () => Promise.resolve(),
    'behaviorAudit.abandonCapture': async () => Promise.resolve(),
    'behaviorAudit.runPython': async () => {
      const document = vscode.window.activeTextEditor?.document;
      if (document === undefined) {
        throw new Error('请先打开一个 Python 文件。');
      }
      const adapted: PythonTextDocument = {
        uri: { scheme: document.uri.scheme, fsPath: document.uri.fsPath },
        languageId: document.languageId,
        isDirty: document.isDirty,
        save: async () => document.save(),
      };
      await pythonRunner.run(adapted);
    },
    'behaviorAudit.analyzeSession': analyzeSession,
    'behaviorAudit.exportSession': exportSession,
    'behaviorAudit.openDataLocation': async () => {
      await vscode.env.openExternal(context.globalStorageUri);
    },
    'behaviorAudit.configureAiKey': async () => {
      const value = await vscode.window.showInputBox({
        title: '配置可选 AI Key',
        password: true,
        ignoreFocusOut: true,
        prompt: '密钥只保存在 VS Code SecretStorage 中',
      });
      if (value !== undefined) {
        await aiSettings.saveApiKey(value);
      }
    },
    'behaviorAudit.clearAiKey': async () => aiSettings.clearApiKey(),
    'behaviorAudit.pasteAndRecord': async () => textCollector?.pasteAndRecord(),
  };

  const host: AuditCommandHost = {
    registerCommand: (id, handler) =>
      vscode.commands.registerCommand(id, async () => {
        const activeBefore = capture.current()?.session_id;
        await handler();
        if (
          activeBefore !== undefined &&
          (id === 'behaviorAudit.finishCapture' || id === 'behaviorAudit.abandonCapture')
        ) {
          lastSessionId = activeBefore;
        }
        if (id === 'behaviorAudit.resumeCapture') {
          interruptedState = undefined;
        }
        await refreshPresentation();
      }),
    confirm: async (id: AuditCommandId) => {
      const choice = await vscode.window.showWarningMessage(
        `确认执行“${id}”？此操作只影响当前本地扩展数据或可选 AI 请求。`,
        { modal: true },
        '确认',
      );
      return choice === '确认';
    },
    showError: async (message, ...items) =>
      vscode.window.showErrorMessage(message, ...items),
    isWorkspaceTrusted: () => vscode.workspace.isTrusted && workspaceRoot !== undefined,
  };

  context.subscriptions.push(
    ...registerAuditCommands(host, {
      capture,
      reportService,
      selectedPlan: () => selectedPlan,
      hasConsent: () => consent,
      interruptedSessionId: () => interruptedState?.session_id,
      actions,
    }),
  );

  let textCollector: TextCollector | undefined;
  if (workspaceRoot !== undefined) {
    textCollector = new TextCollector(textCollectorHost(vscode, workspaceRoot));
    context.subscriptions.push(textCollector.start(capture, false));
    const notebookCollector = new StableNotebookCollector(
      notebookCollectorHost(vscode, workspaceRoot),
    );
    context.subscriptions.push(notebookCollector.start(capture));
  }

  const statusTimer = setInterval(() => statusBar.update(capture.current() ?? interruptedState), 1000);
  context.subscriptions.push({ dispose: () => clearInterval(statusTimer) });
  await refreshPresentation();

  if (interruptedState?.status === 'interrupted') {
    const choice = await vscode.window.showWarningMessage(
      '检测到上次退出前未结束的监控会话。请选择后续处理。',
      '继续会话',
      '结束并生成部分简报',
    );
    if (choice === '继续会话') {
      await capture.resume(interruptedState.session_id);
      interruptedState = undefined;
    } else if (choice === '结束并生成部分简报') {
      const sessionId = interruptedState.session_id;
      await capture.resume(sessionId);
      const terminal = await capture.finish('partial', 'VS Code 关闭后由用户选择部分结束。');
      lastSessionId = terminal.session_id;
      interruptedState = undefined;
      await reportService.materialize(terminal.session_id);
    }
    await refreshPresentation();
  }

  if (process.env.BEHAVIOR_AUDIT_TEST_MODE === '1') {
    return {
      storageRoot,
      startSession: async () => {
        const testPlan = await planRepository.publish({
          problem_text: 'Extension Host 合成测试：实现空列表边界处理。',
          knowledge_points: [
            {
              knowledge_point_id: 'kp-test-boundary',
              name: '空列表边界',
              description: '处理空列表输入。',
              observation_basis: '记录到编辑、保存以及完成会话。',
            },
          ],
          tests: [],
        });
        selectedPlan = testPlan;
        consent = true;
        return (await capture.start(testPlan, true)).session_id;
      },
      finishSession: async () => {
        const terminal = await capture.finish('completed');
        lastSessionId = terminal.session_id;
        await reportService.materialize(terminal.session_id);
        return terminal.session_id;
      },
      flush: async () => capture.flush(),
      readBrief: async (sessionId) => {
        const bytes = await sessionRepository.readArtifact(sessionId, 'classroom_brief');
        return bytes === undefined
          ? undefined
          : JSON.parse(new TextDecoder().decode(bytes)) as unknown;
      },
      recoverPersistedSession: async () => {
        const restarted = new FileSessionRepository(
          storageRoot,
          () => new Date(),
          () => `unused-${randomUUID()}`,
        );
        return restarted.findActive(workspaceId);
      },
    };
  }
}

export async function deactivate(): Promise<void> {
  try {
    await activeRuntime?.capture.flush();
  } finally {
    activeRuntime = undefined;
  }
}
