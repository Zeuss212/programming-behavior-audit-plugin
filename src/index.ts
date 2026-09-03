import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { Dialog, ICommandPalette, showDialog } from '@jupyterlab/apputils';
import { IMainMenu } from '@jupyterlab/mainmenu';
import { INotebookTracker } from '@jupyterlab/notebook';
import { IRenderMimeRegistry } from '@jupyterlab/rendermime';
import { ISettingRegistry } from '@jupyterlab/settingregistry';

import {
  getStoredActiveSession,
  startBehaviorCapture
} from './behaviorCapture';
import { PythonFileMonitor } from './pythonFileMonitor';
import {
  IPythonRunResponse,
  registerPythonFileRunner
} from './pythonFileRunner';
import { requestAPI } from './request';
import { registerClassroomTicket } from './platform/classroomApi';
import { getPlatformContext, IPlatformContext } from './platform/contextApi';
import { initializeClassroomUi } from './platform/classroomUiBootstrap';
import { PlatformSessionController } from './platform/platformSessionController';
import {
  bootstrapClassroomTicket,
  hasClassroomTicket
} from './platform/ticketBootstrap';
import { openLogFolder } from './services/logFolderApi';
import {
  downloadSessionLog,
  fetchSessionLogContent
} from './services/sessionLogApi';
import { FirstRunView } from './ui/firstRunView';
import {
  MANAGE_DIMENSION_PROFILES_COMMAND,
  registerGuidedProfileEditorCommand
} from './ui/guidedProfileCommand';
import {
  BehaviorAnalysisSidebar,
  sidebarDependencies
} from './ui/behaviorAnalysisSidebar';
import {
  ISessionLogViewerShell,
  openSessionLogViewer
} from './ui/sessionLogViewer';

interface IHelloResponse {
  data: string;
}

const OPEN_BEHAVIOR_ANALYSIS_COMMAND = 'myextension:open-latest-analysis';
const AUTHORING_FIRST_RUN_STORAGE_KEY = 'myextension:authoring-first-run-shown';

const plugin: JupyterFrontEndPlugin<void> = {
  id: 'myextension:plugin',
  description: 'A JupyterLab extension.',
  autoStart: true,
  requires: [INotebookTracker, IRenderMimeRegistry],
  optional: [ISettingRegistry, ICommandPalette, IMainMenu],
  activate: (
    app: JupyterFrontEnd,
    notebookTracker: INotebookTracker,
    rendermime: IRenderMimeRegistry,
    settingRegistry: ISettingRegistry | null,
    palette: ICommandPalette | null,
    mainMenu: IMainMenu | null
  ) => {
    console.log('JupyterLab extension myextension is activated!');
    const capture = startBehaviorCapture(
      notebookTracker,
      app.serviceManager.serverSettings
    );
    const logger = capture.logger;
    const pythonFileMonitor = new PythonFileMonitor(app, logger);
    pythonFileMonitor.start();
    registerPythonFileRunner(
      app,
      logger,
      pythonFileMonitor,
      palette,
      mainMenu,
      {
        runFile: path =>
          requestAPI<IPythonRunResponse>(
            'run-python-file',
            app.serviceManager.serverSettings,
            {
              method: 'POST',
              body: JSON.stringify({ path }),
              headers: { 'Content-Type': 'application/json' }
            }
          ),
        showMessage: async (title, body) => {
          await showDialog({
            title,
            body,
            buttons: [Dialog.okButton({ label: '确定' })]
          });
        }
      }
    );

    const initializePlatformUi = (platformContext: IPlatformContext): void => {
      const sidebar = new BehaviorAnalysisSidebar(
        sidebarDependencies(
          app.serviceManager.serverSettings,
          capture,
          {
            openProfileEditor: () => {
              void app.commands.execute(MANAGE_DIMENSION_PROFILES_COMMAND);
            },
            openDataFile: async path => {
              await app.commands.execute('docmanager:open', {
                path,
                factory: 'Editor'
              });
            },
            confirmClearAIKey: async () => {
              const result = await showDialog({
                title: '清除已保存的 API Key？',
                body: '清除后，新的分析需要重新配置 Key。',
                buttons: [
                  Dialog.cancelButton({ label: '取消' }),
                  Dialog.warnButton({ label: '清除' })
                ]
              });
              return result.button.accept;
            },
            getStoredActiveSession,
            openLogFolder,
            openSessionLog: async (sessionId, log) => {
              await openSessionLogViewer({
                shell: app.shell as ISessionLogViewerShell,
                rendermime,
                sessionId,
                log,
                fetchContent: (value, kind) =>
                  fetchSessionLogContent(
                    value,
                    kind,
                    app.serviceManager.serverSettings
                  ),
                download: (value, kind, filename) =>
                  downloadSessionLog(
                    value,
                    kind,
                    filename,
                    app.serviceManager.serverSettings
                  )
              });
            },
            downloadSessionLog: (sessionId, log) =>
              downloadSessionLog(
                sessionId,
                log.kind,
                log.filename,
                app.serviceManager.serverSettings
              )
          },
          platformContext
        )
      );
      app.shell.add(sidebar, 'left', { rank: 500 });
      registerBehaviorAnalysisCommand(app, palette, sidebar);
      if (platformContext.capabilities.canAuthorPlan) {
        registerGuidedProfileEditorCommand(app, palette, () => {
          void sidebar.refreshProfiles();
        });
        showFirstRunView(app);
      } else if (
        platformContext.mode === 'student' &&
        platformContext.capabilities.canCapture &&
        platformContext.classroom_session !== null
      ) {
        void new PlatformSessionController(
          app.serviceManager.serverSettings,
          platformContext,
          capture
        )
          .bootstrap()
          .then(
            result =>
              console.info(`myextension_platform_capture_${result.outcome}`),
            () => console.error('myextension_platform_capture_unavailable')
          );
      }
    };

    if (settingRegistry) {
      settingRegistry.load(plugin.id).then(
        () => console.info('myextension_settings_loaded'),
        () => console.error('myextension_settings_load_failed')
      );
    }
    requestAPI<IHelloResponse>('hello', app.serviceManager.serverSettings).then(
      () => console.info('myextension_server_available'),
      () => console.error('myextension_server_unavailable')
    );
    const classroomTicketObserved = hasClassroomTicket(window.location);
    const initializeAfterClassroomTicket = (): void => {
      void initializeClassroomUi({
        classroomTicketObserved,
        getContext: () => getPlatformContext(app.serviceManager.serverSettings),
        initialize: initializePlatformUi,
        reportUnavailable: () =>
          console.error('myextension_platform_context_unavailable')
      });
    };
    void bootstrapClassroomTicket(
      window.location,
      window.history,
      (ticket, pluginInstanceId) =>
        registerClassroomTicket(
          app.serviceManager.serverSettings,
          ticket,
          pluginInstanceId
        ),
      classroomPluginInstanceId()
    ).then(
      registered => {
        if (registered) {
          console.info('myextension_classroom_registration_succeeded');
        }
        initializeAfterClassroomTicket();
      },
      () => {
        console.error('myextension_classroom_registration_failed');
        initializeAfterClassroomTicket();
      }
    );
  }
};

function classroomPluginInstanceId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return `jupyter-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function registerBehaviorAnalysisCommand(
  app: JupyterFrontEnd,
  palette: ICommandPalette | null,
  sidebar: BehaviorAnalysisSidebar
): void {
  app.commands.addCommand(OPEN_BEHAVIOR_ANALYSIS_COMMAND, {
    label: 'Open Behavior Analysis',
    caption: 'Open the behavior analysis sidebar.',
    execute: () => {
      app.shell.activateById(sidebar.id);
      void sidebar.refreshProfiles();
    }
  });
  palette?.addItem({
    command: OPEN_BEHAVIOR_ANALYSIS_COMMAND,
    category: 'Behavior Audit'
  });
}

function showFirstRunView(app: JupyterFrontEnd): void {
  if (localStorage.getItem(AUTHORING_FIRST_RUN_STORAGE_KEY) === 'true') return;
  const view = new FirstRunView({
    onCreateProfile: () => {
      void app.commands.execute(MANAGE_DIMENSION_PROFILES_COMMAND);
      view.dispose();
    }
  });
  app.shell.add(view, 'main');
  app.shell.activateById(view.id);
  localStorage.setItem(AUTHORING_FIRST_RUN_STORAGE_KEY, 'true');
}

export default plugin;
