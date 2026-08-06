import {
  formatPythonRunResult,
  registerPythonFileRunner,
  RUN_CURRENT_PYTHON_FILE_COMMAND
} from '../pythonFileRunner';

function runnerFixture(path: string | null = 'lesson.py') {
  let commandOptions: {
    label: string;
    caption: string;
    isEnabled: () => boolean;
    execute: () => Promise<void>;
  } | null = null;
  const save = jest.fn(async () => undefined);
  const app = {
    commands: {
      addCommand: jest.fn((_id, options) => {
        commandOptions = options;
      })
    },
    contextMenu: {
      addItem: jest.fn()
    },
    shell: {
      currentWidget: path
        ? {
            context: {
              path,
              save,
              model: {
                sharedModel: {
                  getSource: () => 'print("hello")\n'
                }
              }
            }
          }
        : null
    }
  };
  const palette = { addItem: jest.fn() };
  const mainMenu = { runMenu: { addGroup: jest.fn() } };
  const logger = { emit: jest.fn() };
  const monitor = {
    close: jest.fn(),
    getCurrentContext: jest.fn(() =>
      path
        ? {
            document_type: 'python_file' as const,
            file_path: path,
            file_name: path
          }
        : null
    ),
    getCurrentSource: jest.fn(() => 'print("hello")\n')
  };
  const runFile = jest.fn(async () => ({
    status: 'success' as const,
    path: path ?? '',
    exit_code: 0,
    duration_ms: 12,
    stdout: 'hello\n',
    stderr: '',
    timed_out: false
  }));
  const showMessage = jest.fn(async () => undefined);

  registerPythonFileRunner(
    app as never,
    logger as never,
    monitor as never,
    palette,
    mainMenu,
    { runFile, showMessage }
  );

  return {
    app,
    palette,
    mainMenu,
    logger,
    monitor,
    runFile,
    showMessage,
    save,
    get commandOptions() {
      return commandOptions;
    }
  };
}

describe('Python 文件运行入口', () => {
  it('同时注册中文命令面板、运行菜单和文件右键入口', () => {
    const fixture = runnerFixture();

    expect(fixture.app.commands.addCommand).toHaveBeenCalledWith(
      RUN_CURRENT_PYTHON_FILE_COMMAND,
      expect.objectContaining({
        label: '运行当前 Python 文件',
        caption: '使用 Jupyter Server 的 Python 环境运行当前 .py 文件'
      })
    );
    expect(fixture.palette.addItem).toHaveBeenCalledWith({
      command: RUN_CURRENT_PYTHON_FILE_COMMAND,
      category: '编程行为分析'
    });
    expect(fixture.mainMenu.runMenu.addGroup).toHaveBeenCalledWith(
      [{ command: RUN_CURRENT_PYTHON_FILE_COMMAND }],
      expect.any(Number)
    );
    expect(fixture.app.contextMenu.addItem).toHaveBeenCalledWith({
      command: RUN_CURRENT_PYTHON_FILE_COMMAND,
      selector: '.jp-FileEditor',
      rank: 0
    });
  });

  it('保存并运行当前文件，然后显示中文结果', async () => {
    const fixture = runnerFixture('课程/练习.py');

    await fixture.commandOptions?.execute();

    expect(fixture.save).toHaveBeenCalledTimes(1);
    expect(fixture.monitor.close).toHaveBeenCalledTimes(1);
    expect(fixture.runFile).toHaveBeenCalledWith('课程/练习.py');
    expect(fixture.logger.emit).toHaveBeenCalledWith(
      'cell_execution_success',
      expect.any(Object),
      expect.objectContaining({ cell_source: 'print("hello")\n' })
    );
    expect(fixture.showMessage).toHaveBeenCalledWith(
      'Python 运行结果：练习.py',
      expect.stringContaining('退出代码：0')
    );
  });

  it('没有活动 Python 文件时给出中文操作提示', async () => {
    const fixture = runnerFixture(null);

    expect(fixture.commandOptions?.isEnabled()).toBe(false);
    await fixture.commandOptions?.execute();

    expect(fixture.runFile).not.toHaveBeenCalled();
    expect(fixture.showMessage).toHaveBeenCalledWith(
      '运行 Python 文件',
      '请先打开一个 .py 文件，再运行此命令。'
    );
  });
});

describe('Python 运行结果格式化', () => {
  it('用中文展示退出状态、耗时、超时和输出', () => {
    expect(
      formatPythonRunResult({
        status: 'error',
        path: 'lesson.py',
        exit_code: 1,
        duration_ms: 250,
        stdout: '部分输出',
        stderr: '错误详情',
        timed_out: true
      })
    ).toBe(
      [
        '退出代码：1',
        '耗时：250 毫秒',
        '运行超时：是',
        '',
        '标准输出：',
        '部分输出',
        '',
        '错误输出：',
        '错误详情'
      ].join('\n')
    );
  });

  it('明确显示没有输出', () => {
    expect(
      formatPythonRunResult({
        status: 'success',
        path: 'lesson.py',
        exit_code: 0,
        duration_ms: 3,
        stdout: '',
        stderr: '',
        timed_out: false
      })
    ).toContain('（无输出）');
  });
});

describe('插件激活的本次日志接线', () => {
  it('wires the authenticated viewer and download while retaining advanced folder diagnostics', async () => {
    jest.resetModules();
    const capture = {
      logger: {},
      snapshot: jest.fn(() => ({ sessionId: null })),
      subscribe: jest.fn(() => () => undefined),
      isEnabled: jest.fn(() => false)
    };
    const startBehaviorCapture = jest.fn(() => capture);
    const getStoredActiveSession = jest.fn(async () => null);
    const openLogFolder = jest.fn(async () => ({
      schema_version: 1 as const,
      request_id: '10000000-0000-4000-8000-000000000001',
      opened: true as const,
      platform: 'macos' as const
    }));
    const requestAPI = jest.fn(async () => ({}));
    const fetchSessionLogContent = jest.fn(async () => '{"events":[]}');
    const downloadSessionLog = jest.fn(async () => undefined);
    const openSessionLogViewer = jest.fn(async () => undefined);
    const sidebarDependencies = jest.fn(
      (_settings, _capture, actions) => actions
    );
    const sidebar = jest.fn().mockImplementation(() => ({
      id: 'synthetic-behavior-sidebar',
      refreshProfiles: jest.fn()
    }));
    jest.doMock('../behaviorCapture', () => ({
      startBehaviorCapture,
      getStoredActiveSession
    }));
    jest.doMock('../pythonFileMonitor', () => ({
      PythonFileMonitor: class {
        start = jest.fn();
      }
    }));
    jest.doMock('../request', () => ({ requestAPI }));
    jest.doMock('../services/logFolderApi', () => ({ openLogFolder }));
    jest.doMock('../services/sessionLogApi', () => ({
      fetchSessionLogContent,
      downloadSessionLog
    }));
    jest.doMock('../ui/sessionLogViewer', () => ({
      openSessionLogViewer
    }));
    jest.doMock('../ui/behaviorAnalysisSidebar', () => ({
      BehaviorAnalysisSidebar: sidebar,
      sidebarDependencies
    }));
    jest.doMock('../ui/guidedProfileCommand', () => ({
      MANAGE_DIMENSION_PROFILES_COMMAND: 'myextension:manage-profiles',
      registerGuidedProfileEditorCommand: jest.fn()
    }));
    jest.doMock('../ui/firstRunView', () => ({
      FirstRunView: jest.fn()
    }));
    jest.doMock('@jupyterlab/apputils', () => ({
      Dialog: {
        okButton: jest.fn(),
        cancelButton: jest.fn(),
        warnButton: jest.fn()
      },
      showDialog: jest.fn()
    }));
    jest.doMock('@jupyterlab/mainmenu', () => ({}));
    jest.doMock('@jupyterlab/notebook', () => ({}));
    const renderMimeToken = {};
    jest.doMock('@jupyterlab/rendermime', () => ({
      IRenderMimeRegistry: renderMimeToken
    }));
    jest.doMock('@jupyterlab/settingregistry', () => ({}));

    const commands = new Map<string, { execute?: (args: object) => unknown }>();
    const settings = {};
    const app = {
      commands: {
        addCommand: jest.fn((id, options) => commands.set(id, options)),
        execute: jest.fn((id, args = {}) => {
          const command = commands.get(id);
          return Promise.resolve(command?.execute?.(args));
        })
      },
      contextMenu: { addItem: jest.fn() },
      shell: {
        add: jest.fn(),
        activateById: jest.fn(),
        currentWidget: null
      },
      serviceManager: { serverSettings: settings }
    };
    localStorage.setItem('myextension:authoring-first-run-shown', 'true');
    const rendermime = { clone: jest.fn() };

    const { default: plugin } = await import('../index');
    plugin.activate(
      app as never,
      {} as never,
      rendermime as never,
      null,
      null,
      null
    );
    const actions = sidebarDependencies.mock.calls[0][2] as {
      openLogFolder: (settings: object) => Promise<unknown>;
      openDataFile: (path: string) => Promise<void>;
      openSessionLog: (sessionId: string, log: object) => Promise<void>;
      downloadSessionLog: (sessionId: string, log: object) => Promise<void>;
    };
    const log = {
      kind: 'operation',
      filename: 'operation_log.json',
      label: '操作日志',
      status: 'ready'
    };

    expect(commands.has('myextension:open-session-log')).toBe(false);
    await actions.openLogFolder(settings);
    await actions.openDataFile('sessions/synthetic/training_record.json');
    await actions.openSessionLog('synthetic-session', log);
    await actions.downloadSessionLog('synthetic-session', log);

    expect(startBehaviorCapture).toHaveBeenCalledTimes(1);
    expect(openLogFolder).toHaveBeenCalledTimes(1);
    expect(openLogFolder).toHaveBeenCalledWith(settings);
    expect(app.commands.execute).toHaveBeenCalledWith('docmanager:open', {
      path: 'sessions/synthetic/training_record.json',
      factory: 'Editor'
    });
    expect(openSessionLogViewer).toHaveBeenCalledWith(
      expect.objectContaining({
        shell: app.shell,
        rendermime,
        sessionId: 'synthetic-session',
        log
      })
    );
    expect(downloadSessionLog).toHaveBeenCalledWith(
      'synthetic-session',
      'operation',
      'operation_log.json',
      settings
    );
  });
});
