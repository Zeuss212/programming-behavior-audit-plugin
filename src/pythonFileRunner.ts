import type { JupyterFrontEnd } from '@jupyterlab/application';

import type { BehaviorEventLogger } from './events';
import { currentPythonPath, type PythonFileMonitor } from './pythonFileMonitor';

export interface IPythonRunResponse {
  status: 'success' | 'error';
  path: string;
  exit_code: number;
  duration_ms: number;
  stdout: string;
  stderr: string;
  timed_out: boolean;
  message?: string;
}

interface ICommandPaletteLike {
  addItem(options: { command: string; category: string }): unknown;
}

interface IMainMenuLike {
  runMenu: {
    addGroup(items: Array<{ command: string }>, rank?: number): unknown;
  };
}

export interface IPythonFileRunnerDependencies {
  runFile(path: string): Promise<IPythonRunResponse>;
  showMessage(title: string, body: string): Promise<void>;
}

export const RUN_CURRENT_PYTHON_FILE_COMMAND =
  'myextension:run-current-python-file';

export function registerPythonFileRunner(
  app: JupyterFrontEnd,
  logger: BehaviorEventLogger,
  pythonFileMonitor: PythonFileMonitor,
  palette: ICommandPaletteLike | null,
  mainMenu: IMainMenuLike | null,
  dependencies: IPythonFileRunnerDependencies
): void {
  app.commands.addCommand(RUN_CURRENT_PYTHON_FILE_COMMAND, {
    label: '运行当前 Python 文件',
    caption: '使用 Jupyter Server 的 Python 环境运行当前 .py 文件',
    isEnabled: () => currentPythonPath(app) !== null,
    execute: async () => {
      const path = currentPythonPath(app);
      if (!path) {
        await dependencies.showMessage(
          '运行 Python 文件',
          '请先打开一个 .py 文件，再运行此命令。'
        );
        return;
      }

      await saveCurrentWidget(app);
      pythonFileMonitor.close();
      const context = pythonFileMonitor.getCurrentContext() ?? {
        document_type: 'python_file' as const,
        file_path: path,
        file_name: fileName(path)
      };
      const cellSource = pythonFileMonitor.getCurrentSource();
      logger.emit('cell_execution_scheduled', context);

      const result = await dependencies.runFile(path);
      logger.emit(
        result.exit_code === 0
          ? 'cell_execution_success'
          : 'cell_execution_error',
        context,
        {
          cell_source: cellSource,
          error_type:
            result.exit_code === 0 ? undefined : `ExitCode${result.exit_code}`,
          error_message:
            result.exit_code === 0
              ? undefined
              : firstOutputLine(result.stderr) || result.message
        }
      );

      await dependencies.showMessage(
        `Python 运行结果：${fileName(path)}`,
        formatPythonRunResult(result)
      );
    }
  });

  palette?.addItem({
    command: RUN_CURRENT_PYTHON_FILE_COMMAND,
    category: '编程行为分析'
  });
  mainMenu?.runMenu.addGroup(
    [{ command: RUN_CURRENT_PYTHON_FILE_COMMAND }],
    40
  );
  app.contextMenu.addItem({
    command: RUN_CURRENT_PYTHON_FILE_COMMAND,
    selector: '.jp-FileEditor',
    rank: 0
  });
}

export function formatPythonRunResult(result: IPythonRunResponse): string {
  const lines = [
    `退出代码：${result.exit_code}`,
    `耗时：${result.duration_ms} 毫秒`
  ];
  if (result.timed_out) lines.push('运行超时：是');
  if (result.stdout) {
    lines.push('', '标准输出：', truncateOutput(result.stdout));
  }
  if (result.stderr) {
    lines.push('', '错误输出：', truncateOutput(result.stderr));
  }
  if (!result.stdout && !result.stderr) lines.push('', '（无输出）');
  return lines.join('\n');
}

async function saveCurrentWidget(app: JupyterFrontEnd): Promise<void> {
  const context = (
    app.shell.currentWidget as unknown as {
      context?: { save?: () => Promise<void> };
    } | null
  )?.context;
  if (typeof context?.save === 'function') await context.save();
}

function truncateOutput(value: string): string {
  return value.length <= 4000
    ? value
    : `${value.slice(0, 4000)}\n……输出已截断……`;
}

function firstOutputLine(value: string): string | undefined {
  return value
    .split(/\r?\n/)
    .map(line => line.trim())
    .find(line => line.length > 0);
}

function fileName(path: string): string {
  return path.split('/').pop() ?? path;
}
