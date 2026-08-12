import type {
  ChildProcessWithoutNullStreams,
  SpawnOptionsWithoutStdio,
} from 'node:child_process';
import { basename, dirname, extname } from 'node:path';

import type { PythonExtension as PythonExtensionApi } from '@vscode/python-extension';

import type { CaptureController } from '../capture/captureController';
import { AuditError } from '../domain/errors';
import type { PythonRunResult } from '../domain/types';

export const MAX_RUN_OUTPUT_BYTES = 16 * 1024;
export const MAX_RUN_DURATION_MS = 120_000;
export const RUN_TERMINATION_GRACE_MS = 5_000;

export interface PythonTextDocument {
  readonly uri: {
    readonly scheme: string;
    readonly fsPath: string;
  };
  readonly languageId: string;
  readonly isDirty: boolean;
  save(): Promise<boolean>;
}

export type SpawnPython = (
  command: string,
  args: readonly string[],
  options: SpawnOptionsWithoutStdio,
) => ChildProcessWithoutNullStreams;

export interface PythonRunner {
  run(document: PythonTextDocument): Promise<PythonRunResult>;
}

export interface PythonRunnerOptions {
  readonly pythonExtensionAvailable: () => boolean;
  readonly pythonApi: () => Promise<PythonExtensionApi>;
  readonly spawn: SpawnPython;
  readonly controller: Pick<CaptureController, 'current' | 'record'>;
  readonly workspaceTrusted: () => boolean;
  readonly monotonicNow: () => number;
}

interface ProcessOutcome {
  readonly exitCode: number | null;
  readonly signal: NodeJS.Signals | null;
  readonly timedOut: boolean;
  readonly launchError?: unknown;
}

class BoundedOutput {
  private readonly chunks: Uint8Array[] = [];
  private bytes = 0;
  private wasTruncated = false;

  public append(chunk: string | Uint8Array): void {
    const bytes = typeof chunk === 'string' ? Buffer.from(chunk) : Buffer.from(chunk);
    const remaining = MAX_RUN_OUTPUT_BYTES - this.bytes;
    if (remaining <= 0) {
      this.wasTruncated = true;
      return;
    }
    if (bytes.byteLength > remaining) {
      this.chunks.push(bytes.subarray(0, remaining));
      this.bytes += remaining;
      this.wasTruncated = true;
      return;
    }
    this.chunks.push(bytes);
    this.bytes += bytes.byteLength;
  }

  public text(redactions: readonly string[]): string {
    let value = Buffer.concat(this.chunks.map((chunk) => Buffer.from(chunk))).toString('utf8');
    for (const redaction of redactions.filter((item) => item.length > 0)) {
      value = value.split(redaction).join('[path]');
    }
    return value;
  }

  public get truncated(): boolean {
    return this.wasTruncated;
  }
}

export class VsCodePythonRunner implements PythonRunner {
  public constructor(private readonly options: PythonRunnerOptions) {}

  public async run(document: PythonTextDocument): Promise<PythonRunResult> {
    this.assertCanRun(document);
    if (document.isDirty && !(await document.save())) {
      throw new AuditError(
        'python_run_failed',
        '当前 Python 文件保存失败，未执行代码。',
        '请手工保存文件后重试。',
      );
    }
    if (!this.options.pythonExtensionAvailable()) {
      throw this.interpreterMissing();
    }

    let executable: string | undefined;
    try {
      const python = await this.options.pythonApi();
      await python.ready;
      const active = python.environments.getActiveEnvironmentPath(document.uri as never);
      const resolved = await python.environments.resolveEnvironment(active);
      executable = resolved?.executable.uri?.fsPath;
    } catch (error) {
      throw new AuditError(
        'python_interpreter_missing',
        '无法读取 Microsoft Python 扩展选择的解释器。',
        '请在 VS Code 中重新选择 Python 解释器。',
        error,
      );
    }
    if (executable === undefined || executable.length === 0) {
      throw this.interpreterMissing();
    }

    const workingDirectory = dirname(document.uri.fsPath);
    const startedAt = this.options.monotonicNow();
    let child: ChildProcessWithoutNullStreams;
    try {
      child = this.options.spawn(executable, [document.uri.fsPath], {
        shell: false,
        cwd: workingDirectory,
      });
    } catch (error) {
      throw new AuditError(
        'python_run_failed',
        'Python 进程启动失败。',
        '请检查解释器和文件权限后重试。',
        error,
      );
    }

    const stdout = new BoundedOutput();
    const stderr = new BoundedOutput();
    child.stdout.on('data', (chunk: unknown) => {
      if (typeof chunk === 'string' || chunk instanceof Uint8Array) {
        stdout.append(chunk);
      }
    });
    child.stderr.on('data', (chunk: unknown) => {
      if (typeof chunk === 'string' || chunk instanceof Uint8Array) {
        stderr.append(chunk);
      }
    });

    const outcome = await this.waitForProcess(child);
    const durationMs = Math.max(0, this.options.monotonicNow() - startedAt);
    const redactions = [executable, document.uri.fsPath, workingDirectory];
    const result: PythonRunResult = {
      exitCode: outcome.exitCode,
      signal: outcome.signal,
      durationMs,
      stdout: stdout.text(redactions),
      stderr: stderr.text(redactions),
      stdoutTruncated: stdout.truncated,
      stderrTruncated: stderr.truncated,
    };

    await this.options.controller.record({
      kind: 'python_run',
      payload: {
        file_name: basename(document.uri.fsPath),
        exit_code: result.exitCode,
        signal: result.signal,
        duration_ms: result.durationMs,
        stdout: result.stdout,
        stderr: result.stderr,
        stdout_truncated: result.stdoutTruncated,
        stderr_truncated: result.stderrTruncated,
        timed_out: outcome.timedOut,
        launch_failed: outcome.launchError !== undefined,
      },
    });

    if (outcome.timedOut) {
      throw new AuditError(
        'python_run_failed',
        'Python 运行超过 120 秒，已请求终止。',
        '请缩小输入或检查程序中的长时间循环后重试。',
      );
    }
    if (outcome.launchError !== undefined) {
      throw new AuditError(
        'python_run_failed',
        'Python 运行过程中发生进程错误。',
        '请检查解释器状态后重试。',
        outcome.launchError,
      );
    }
    return result;
  }

  private assertCanRun(document: PythonTextDocument): void {
    if (!this.options.workspaceTrusted()) {
      throw new AuditError(
        'workspace_untrusted',
        '未受信工作区不能运行代码。',
        '请确认工作区来源并在 VS Code 中设为受信。',
      );
    }
    if (this.options.controller.current()?.status !== 'collecting') {
      throw new AuditError(
        'session_conflict',
        '只有正在采集的会话可以运行并记录 Python。',
        '请先开始或恢复采集会话。',
      );
    }
    if (
      document.uri.scheme !== 'file' ||
      document.languageId !== 'python' ||
      extname(document.uri.fsPath).toLowerCase() !== '.py'
    ) {
      throw new AuditError(
        'python_run_failed',
        '当前文档不是受支持的本地 Python 文件。',
        '请打开一个 .py 文件后重试。',
      );
    }
  }

  private waitForProcess(child: ChildProcessWithoutNullStreams): Promise<ProcessOutcome> {
    return new Promise((resolve) => {
      let settled = false;
      let timedOut = false;
      let forceTimer: NodeJS.Timeout | undefined;
      const timer = setTimeout(() => {
        timedOut = true;
        child.kill('SIGTERM');
        forceTimer = setTimeout(() => {
          child.kill('SIGKILL');
          finish({ exitCode: null, signal: 'SIGKILL', timedOut: true });
        }, RUN_TERMINATION_GRACE_MS);
      }, MAX_RUN_DURATION_MS);
      const finish = (outcome: ProcessOutcome): void => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timer);
        if (forceTimer !== undefined) {
          clearTimeout(forceTimer);
        }
        resolve(outcome);
      };
      child.once('error', (error) => {
        finish({ exitCode: null, signal: null, timedOut, launchError: error });
      });
      child.once('close', (exitCode, signal) => {
        finish({ exitCode, signal, timedOut });
      });
    });
  }

  private interpreterMissing(): AuditError {
    return new AuditError(
      'python_interpreter_missing',
      '没有可用的 Microsoft Python 扩展解释器。',
      '请安装 Python 扩展并选择解释器后重试。',
    );
  }
}
