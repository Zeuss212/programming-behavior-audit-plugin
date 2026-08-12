import { describe, expect, it } from 'vitest';

import type { AuditEvent } from '../domain/types';
import type { AuditEventInput, CaptureController } from '../capture/captureController';
import {
  MAX_NOTEBOOK_OUTPUT_METADATA_BYTES,
  StableNotebookCollector,
  type NotebookCollectorDisposable,
  type NotebookDocumentChangeLike,
} from '../notebooks/notebookCollector';

type Listener = (event: NotebookDocumentChangeLike) => void | Promise<void>;

class NotebookEmitter {
  private readonly listeners: Listener[] = [];

  public readonly event = (listener: Listener): NotebookCollectorDisposable => {
    this.listeners.push(listener);
    return { dispose: () => undefined };
  };

  public async fire(event: NotebookDocumentChangeLike): Promise<void> {
    for (const listener of this.listeners) {
      await listener(event);
    }
  }
}

function controller(recorded: AuditEventInput[]): Pick<CaptureController, 'current' | 'record'> {
  return {
    current: () => ({
      schema_version: 1,
      session_id: 'session-notebook-test',
      workspace_id: 'workspace-notebook-test',
      status: 'collecting',
      plan_id: 'plan-notebook-test',
      plan_version: 1,
      plan_content_sha256: '0'.repeat(64),
      started_at: '2026-08-10T00:00:00.000Z',
      updated_at: '2026-08-10T00:00:00.000Z',
      last_event_seq: recorded.length,
      last_persisted_seq: recorded.length,
    }),
    record: (input) => {
      recorded.push(input);
      return Promise.resolve({
        schema_version: 1,
        event_id: `session-notebook-test:${String(recorded.length)}`,
        session_id: 'session-notebook-test',
        session_seq: recorded.length,
        occurred_at: '2026-08-10T00:00:00.000Z',
        monotonic_ms: recorded.length,
        ...input,
      } satisfies AuditEvent);
    },
  };
}

function executionChange(
  success: boolean | undefined,
  outputBytes = 100,
): NotebookDocumentChangeLike {
  return {
    notebook: { uri: { scheme: 'file', fsPath: '/course/demo.ipynb' } },
    contentChanges: [],
    cellChanges: [
      {
        cell: {
          index: 2,
          document: { languageId: 'python', getText: () => 'print(1)' },
          outputs: [
            {
              items: [{ data: new Uint8Array(outputBytes), mime: 'text/plain' }],
            },
          ],
        },
        document: undefined,
        outputs: undefined,
        executionSummary: {
          executionOrder: 3,
          ...(success === undefined ? {} : { success }),
          timing: { startTime: 1_000, endTime: 1_250 },
        },
      },
    ],
  };
}

describe('StableNotebookCollector', () => {
  it('records stable failed execution evidence without output bodies', async () => {
    const emitter = new NotebookEmitter();
    const recorded: AuditEventInput[] = [];
    new StableNotebookCollector({
      workspaceRootPath: '/course',
      onDidChangeNotebookDocument: emitter.event,
    }).start(controller(recorded));

    await emitter.fire(executionChange(false));

    expect(recorded).toHaveLength(1);
    expect(recorded[0]).toMatchObject({
      kind: 'notebook_run',
      document: {
        relative_uri: 'demo.ipynb',
        language_id: 'python',
        notebook_cell_id: '2',
      },
      payload: {
        cell_index: 2,
        execution_order: 3,
        success: false,
        outcome: 'failure',
        duration_ms: 250,
        output_item_count: 1,
        output_bytes: 100,
      },
    });
    expect(recorded[0]?.payload).toHaveProperty('source_sha256');
    expect(JSON.stringify(recorded[0])).not.toContain('print(1)');
  });

  it('deduplicates an identical summary and maps absent success to unknown', async () => {
    const emitter = new NotebookEmitter();
    const recorded: AuditEventInput[] = [];
    new StableNotebookCollector({
      workspaceRootPath: '/course',
      onDidChangeNotebookDocument: emitter.event,
    }).start(controller(recorded));
    const change = executionChange(undefined);

    await emitter.fire(change);
    await emitter.fire(change);

    expect(recorded).toHaveLength(1);
    expect(recorded[0]?.payload).toMatchObject({ success: null, outcome: 'unknown' });
  });

  it('caps output byte metadata and never copies output data', async () => {
    const emitter = new NotebookEmitter();
    const recorded: AuditEventInput[] = [];
    new StableNotebookCollector({
      workspaceRootPath: '/course',
      onDidChangeNotebookDocument: emitter.event,
    }).start(controller(recorded));

    await emitter.fire(executionChange(true, MAX_NOTEBOOK_OUTPUT_METADATA_BYTES + 500));

    expect(recorded[0]?.payload).toMatchObject({
      output_bytes: MAX_NOTEBOOK_OUTPUT_METADATA_BYTES,
      output_metadata_truncated: true,
    });
  });

  it('records structural cell counts but ignores detailed cell text changes', async () => {
    const emitter = new NotebookEmitter();
    const recorded: AuditEventInput[] = [];
    new StableNotebookCollector({
      workspaceRootPath: '/course',
      onDidChangeNotebookDocument: emitter.event,
    }).start(controller(recorded));

    await emitter.fire({
      notebook: { uri: { scheme: 'file', fsPath: '/course/demo.ipynb' } },
      contentChanges: [{ addedCells: [{ index: 1 }], removedCells: [{ index: 0 }] }],
      cellChanges: [
        {
          cell: {
            index: 1,
            document: { languageId: 'python', getText: () => 'changed' },
            outputs: [],
          },
          document: { languageId: 'python', getText: () => 'changed' },
          outputs: undefined,
          executionSummary: undefined,
        },
      ],
    });

    expect(recorded).toHaveLength(1);
    expect(recorded[0]).toMatchObject({
      kind: 'notebook_edit',
      payload: { added_cells: 1, removed_cells: 1 },
    });
  });
});
