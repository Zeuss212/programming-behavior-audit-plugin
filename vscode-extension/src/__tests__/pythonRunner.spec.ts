import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import type { ChildProcessWithoutNullStreams, SpawnOptionsWithoutStdio } from 'node:child_process';

import type { PythonExtension as PythonExtensionApi } from '@vscode/python-extension';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AuditEvent, SessionState } from '../domain/types';
import { SESSION_SCHEMA_VERSION } from '../domain/types';
import type { AuditEventInput, CaptureController } from '../capture/captureController';
import {
  MAX_RUN_DURATION_MS,
  MAX_RUN_OUTPUT_BYTES,
  VsCodePythonRunner,
  type PythonTextDocument,
  type SpawnPython,
} from '../runners/pythonRunner';

const EXPECTED_TERMINATION_GRACE_MS = 5_000;

class FakeChildProcess extends EventEmitter {
  public readonly stdout = new PassThrough();
  public readonly stderr = new PassThrough();
  public readonly stdin = new PassThrough();
  public readonly kill = vi.fn(() => true);
}

function collectingState(): SessionState {
  return {
    schema_version: SESSION_SCHEMA_VERSION,
    session_id: 'session-python-test',
    workspace_id: 'workspace-python-test',
    status: 'collecting',
    plan_id: 'plan-python-test',
    plan_version: 1,
    plan_content_sha256: '0'.repeat(64),
    started_at: '2026-08-10T00:00:00.000Z',
    updated_at: '2026-08-10T00:00:00.000Z',
    last_event_seq: 0,
    last_persisted_seq: 0,
  };
}

function controller(recorded: AuditEventInput[]): Pick<CaptureController, 'current' | 'record'> {
  return {
    current: () => collectingState(),
    record: (input) => {
      recorded.push(input);
      return Promise.resolve({
        schema_version: 1,
        event_id: 'session-python-test:1',
        session_id: 'session-python-test',
        session_seq: 1,
        occurred_at: '2026-08-10T00:00:01.000Z',
        monotonic_ms: 1000,
        ...input,
      } satisfies AuditEvent);
    },
  };
}

function pythonApi(executable: string | undefined): PythonExtensionApi {
  return {
    ready: Promise.resolve(),
    environments: {
      getActiveEnvironmentPath: () => ({ id: 'active', path: executable ?? '/missing' }),
      resolveEnvironment: () =>
        Promise.resolve(
          executable === undefined
            ? undefined
            : {
                executable: { uri: { fsPath: executable } },
              },
        ),
    },
  } as unknown as PythonExtensionApi;
}

function document(saveCalls: number[]): PythonTextDocument {
  return {
    uri: { scheme: 'file', fsPath: '/course/main.py' },
    languageId: 'python',
    isDirty: true,
    save: () => {
      saveCalls.push(1);
      return Promise.resolve(true);
    },
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe('VsCodePythonRunner', () => {
  it('saves and runs the selected interpreter without a shell, then records one bounded event', async () => {
    const child = new FakeChildProcess();
    const spawnCalls: Array<{
      readonly command: string;
      readonly args: readonly string[];
      readonly options: SpawnOptionsWithoutStdio;
    }> = [];
    const spawn: SpawnPython = (command, args, options) => {
      spawnCalls.push({ command, args, options });
      queueMicrotask(() => {
        child.stdout.write('ok\n');
        child.stderr.write('/venv/bin/python warning\n');
        child.stdout.end();
        child.stderr.end();
        child.emit('close', 0, null);
      });
      return child as unknown as ChildProcessWithoutNullStreams;
    };
    const recorded: AuditEventInput[] = [];
    const saveCalls: number[] = [];
    let elapsed = 100;
    const runner = new VsCodePythonRunner({
      pythonExtensionAvailable: () => true,
      pythonApi: () => Promise.resolve(pythonApi('/venv/bin/python')),
      spawn,
      controller: controller(recorded),
      workspaceTrusted: () => true,
      monotonicNow: () => {
        elapsed += 25;
        return elapsed;
      },
    });

    const result = await runner.run(document(saveCalls));

    expect(saveCalls).toEqual([1]);
    expect(spawnCalls).toEqual([
      {
        command: '/venv/bin/python',
        args: ['/course/main.py'],
        options: { shell: false, cwd: '/course' },
      },
    ]);
    expect(result).toMatchObject({ exitCode: 0, durationMs: 25, stdout: 'ok\n' });
    expect(recorded).toHaveLength(1);
    expect(recorded[0]?.kind).toBe('python_run');
    expect(JSON.stringify(recorded[0])).not.toContain('/venv/bin/python');
    expect(JSON.stringify(recorded[0])).not.toContain('process.env');
  });

  it('truncates each output stream to 16 KiB and reports truncation', async () => {
    const child = new FakeChildProcess();
    const spawn: SpawnPython = () => {
      queueMicrotask(() => {
        child.stdout.end('x'.repeat(MAX_RUN_OUTPUT_BYTES + 100));
        child.stderr.end('y'.repeat(MAX_RUN_OUTPUT_BYTES + 200));
        child.emit('close', 1, null);
      });
      return child as unknown as ChildProcessWithoutNullStreams;
    };
    const runner = new VsCodePythonRunner({
      pythonExtensionAvailable: () => true,
      pythonApi: () => Promise.resolve(pythonApi('/venv/bin/python')),
      spawn,
      controller: controller([]),
      workspaceTrusted: () => true,
      monotonicNow: (() => {
        let value = 0;
        return () => (value += 1);
      })(),
    });

    const result = await runner.run(document([]));

    expect(Buffer.byteLength(result.stdout)).toBe(MAX_RUN_OUTPUT_BYTES);
    expect(Buffer.byteLength(result.stderr)).toBe(MAX_RUN_OUTPUT_BYTES);
    expect(result.stdoutTruncated).toBe(true);
    expect(result.stderrTruncated).toBe(true);
    expect(result.exitCode).toBe(1);
  });

  it('returns a stable missing-interpreter error before spawning', async () => {
    const spawn = vi.fn<SpawnPython>();
    const runner = new VsCodePythonRunner({
      pythonExtensionAvailable: () => true,
      pythonApi: () => Promise.resolve(pythonApi(undefined)),
      spawn,
      controller: controller([]),
      workspaceTrusted: () => true,
      monotonicNow: () => 0,
    });

    await expect(runner.run(document([]))).rejects.toMatchObject({
      code: 'python_interpreter_missing',
    });
    expect(spawn).not.toHaveBeenCalled();
  });

  it('kills a timed-out process, records timeout evidence, and raises python_run_failed', async () => {
    vi.useFakeTimers();
    const child = new FakeChildProcess();
    child.kill.mockImplementation(() => {
      queueMicrotask(() => child.emit('close', null, 'SIGTERM'));
      return true;
    });
    const recorded: AuditEventInput[] = [];
    const runner = new VsCodePythonRunner({
      pythonExtensionAvailable: () => true,
      pythonApi: () => Promise.resolve(pythonApi('/venv/bin/python')),
      spawn: () => child as unknown as ChildProcessWithoutNullStreams,
      controller: controller(recorded),
      workspaceTrusted: () => true,
      monotonicNow: (() => {
        let value = 0;
        return () => (value += MAX_RUN_DURATION_MS);
      })(),
    });

    const running = runner.run(document([]));
    const rejection = expect(running).rejects.toMatchObject({ code: 'python_run_failed' });
    await vi.advanceTimersByTimeAsync(MAX_RUN_DURATION_MS);

    await rejection;
    expect(child.kill).toHaveBeenCalledWith('SIGTERM');
    expect(recorded[0]?.payload).toMatchObject({ timed_out: true });
  });

  it('stops waiting after the timed-out child ignores graceful termination', async () => {
    vi.useFakeTimers();
    const child = new FakeChildProcess();
    const runner = new VsCodePythonRunner({
      pythonExtensionAvailable: () => true,
      pythonApi: () => Promise.resolve(pythonApi('/venv/bin/python')),
      spawn: () => child as unknown as ChildProcessWithoutNullStreams,
      controller: controller([]),
      workspaceTrusted: () => true,
      monotonicNow: () => 0,
    });
    let rejected = false;
    void runner.run(document([])).catch(() => {
      rejected = true;
    });

    await vi.advanceTimersByTimeAsync(MAX_RUN_DURATION_MS + EXPECTED_TERMINATION_GRACE_MS);

    expect(rejected).toBe(true);
    expect(child.kill).toHaveBeenNthCalledWith(1, 'SIGTERM');
    expect(child.kill).toHaveBeenNthCalledWith(2, 'SIGKILL');
  });
});
