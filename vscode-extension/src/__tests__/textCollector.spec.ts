import { describe, expect, it } from 'vitest';

import type { AuditEvent } from '../domain/types';
import {
  TextCollector,
  type CollectorDisposable,
  type TextCollectorHost,
  type TextDocumentChange,
  type TextDocumentLike,
} from '../capture/textCollector';
import type { AuditEventInput, CaptureController } from '../capture/captureController';

type Listener<T> = (event: T) => void | Promise<void>;

class AsyncEmitter<T> {
  private readonly listeners: Listener<T>[] = [];

  public readonly event = (listener: Listener<T>): CollectorDisposable => {
    this.listeners.push(listener);
    return { dispose: () => undefined };
  };

  public async fire(value: T): Promise<void> {
    for (const listener of this.listeners) {
      await listener(value);
    }
  }
}

function fakeEvent(input: AuditEventInput, sequence: number): AuditEvent {
  return {
    schema_version: 1,
    event_id: `session:${String(sequence)}`,
    session_id: 'session',
    session_seq: sequence,
    occurred_at: '2026-08-10T00:00:00.000Z',
    monotonic_ms: sequence,
    ...input,
  };
}

describe('TextCollector', () => {
  it('records Python edits using relative refs, counts, and hashes without source text', async () => {
    const changes = new AsyncEmitter<TextDocumentChange>();
    const saves = new AsyncEmitter<TextDocumentLike>();
    const editors = new AsyncEmitter<{ readonly document: TextDocumentLike } | undefined>();
    const windowStates = new AsyncEmitter<{ readonly focused: boolean }>();
    const terminals = new AsyncEmitter<unknown>();
    const recorded: AuditEventInput[] = [];
    const commands = new Map<string, () => Promise<void>>();
    const host: TextCollectorHost = {
      workspaceRootPath: '/private/course',
      onDidChangeTextDocument: changes.event,
      onDidSaveTextDocument: saves.event,
      onDidChangeActiveTextEditor: editors.event,
      onDidChangeWindowState: windowStates.event,
      onDidOpenTerminal: terminals.event,
      registerCommand: (name, handler) => {
        commands.set(name, handler);
        return { dispose: () => undefined };
      },
      executeCommand: () => Promise.resolve(undefined),
    };
    const controller = {
      record: (input: AuditEventInput) => {
        recorded.push(input);
        return Promise.resolve(fakeEvent(input, recorded.length));
      },
    } as Pick<CaptureController, 'record'>;
    const document: TextDocumentLike = {
      uri: { scheme: 'file', fsPath: '/private/course/main.py' },
      languageId: 'python',
      getText: () => 'x = 1\n',
    };

    const disposable = new TextCollector(host).start(controller);
    await changes.fire({
      document,
      contentChanges: [{ text: 'x = 1\n', rangeLength: 0 }],
    });

    expect(recorded[0]).toMatchObject({
      kind: 'edit',
      document: { relative_uri: 'main.py', language_id: 'python' },
      payload: { inserted_chars: 6, deleted_chars: 0 },
    });
    expect(recorded[0]?.payload).toHaveProperty('result_sha256');
    expect(JSON.stringify(recorded[0])).not.toContain('/private/course');
    expect(JSON.stringify(recorded[0])).not.toContain('x = 1');
    disposable.dispose();
  });

  it('delegates paste without reading or storing clipboard text', async () => {
    const changes = new AsyncEmitter<TextDocumentChange>();
    const saves = new AsyncEmitter<TextDocumentLike>();
    const editors = new AsyncEmitter<{ readonly document: TextDocumentLike } | undefined>();
    const windowStates = new AsyncEmitter<{ readonly focused: boolean }>();
    const terminals = new AsyncEmitter<unknown>();
    const recorded: AuditEventInput[] = [];
    const commands = new Map<string, () => Promise<void>>();
    const document: TextDocumentLike = {
      uri: { scheme: 'file', fsPath: '/private/course/main.py' },
      languageId: 'python',
      getText: () => 'TOP-SECRET\n',
    };
    const host: TextCollectorHost = {
      workspaceRootPath: '/private/course',
      onDidChangeTextDocument: changes.event,
      onDidSaveTextDocument: saves.event,
      onDidChangeActiveTextEditor: editors.event,
      onDidChangeWindowState: windowStates.event,
      onDidOpenTerminal: terminals.event,
      registerCommand: (name, handler) => {
        commands.set(name, handler);
        return { dispose: () => undefined };
      },
      executeCommand: async (name) => {
        expect(name).toBe('editor.action.clipboardPasteAction');
        await changes.fire({
          document,
          contentChanges: [{ text: 'TOP-SECRET\n', rangeLength: 0 }],
        });
      },
    };
    const controller = {
      record: (input: AuditEventInput) => {
        recorded.push(input);
        return Promise.resolve(fakeEvent(input, recorded.length));
      },
    } as Pick<CaptureController, 'record'>;
    new TextCollector(host).start(controller);

    await commands.get('behaviorAudit.pasteAndRecord')?.();

    expect(recorded).toHaveLength(1);
    expect(recorded[0]?.kind).toBe('paste_shortcut');
    expect(Object.keys(recorded[0]?.payload ?? {}).sort()).toEqual([
      'inserted_chars',
      'line_count',
      'result_sha256',
    ]);
    expect(JSON.stringify(recorded[0])).not.toContain('TOP-SECRET');
  });
});
