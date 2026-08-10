import { isAbsolute, relative, sep } from 'node:path';

import { canonicalJson, sha256Hex } from '../domain/canonicalJson';
import type { DocumentRef } from '../domain/types';
import type { AuditEventInput, CaptureController } from '../capture/captureController';

export const MAX_NOTEBOOK_OUTPUT_METADATA_BYTES = 4 * 1024;

export interface NotebookCollectorDisposable {
  dispose(): void;
}

export interface NotebookCellOutputItemLike {
  readonly data: Uint8Array;
  readonly mime: string;
}

export interface NotebookCellOutputLike {
  readonly items: readonly NotebookCellOutputItemLike[];
}

export interface NotebookCellLike {
  readonly index: number;
  readonly document: {
    readonly languageId: string;
    getText(): string;
  };
  readonly outputs: readonly NotebookCellOutputLike[];
}

export interface NotebookExecutionSummaryLike {
  readonly executionOrder?: number;
  readonly success?: boolean;
  readonly timing?: {
    readonly startTime: number;
    readonly endTime: number;
  };
}

export interface NotebookDocumentChangeLike {
  readonly notebook: {
    readonly uri: {
      readonly scheme: string;
      readonly fsPath: string;
    };
  };
  readonly contentChanges: readonly {
    readonly addedCells: readonly { readonly index: number }[];
    readonly removedCells: readonly { readonly index: number }[];
  }[];
  readonly cellChanges: readonly {
    readonly cell: NotebookCellLike;
    readonly document:
      | {
          readonly languageId: string;
          getText(): string;
        }
      | undefined;
    readonly outputs: readonly NotebookCellOutputLike[] | undefined;
    readonly executionSummary: NotebookExecutionSummaryLike | undefined;
  }[];
}

export interface NotebookCollectorHost {
  readonly workspaceRootPath: string;
  readonly onDidChangeNotebookDocument: (
    listener: (event: NotebookDocumentChangeLike) => void | Promise<void>,
  ) => NotebookCollectorDisposable;
  readonly onError?: (error: unknown) => void;
}

export class StableNotebookCollector {
  private readonly latestFingerprintByCell = new Map<string, string>();

  public constructor(private readonly host: NotebookCollectorHost) {}

  public start(
    controller: Pick<CaptureController, 'current' | 'record'>,
  ): NotebookCollectorDisposable {
    this.latestFingerprintByCell.clear();
    return this.host.onDidChangeNotebookDocument(async (event) => {
      const session = controller.current();
      const notebookRef = this.notebookRef(event);
      if (session?.status !== 'collecting' || notebookRef === undefined) {
        return;
      }
      const dispatch = async (input: AuditEventInput): Promise<void> => {
        try {
          await controller.record(input);
        } catch (error) {
          this.host.onError?.(error);
        }
      };

      const addedCells = event.contentChanges.reduce(
        (sum, change) => sum + change.addedCells.length,
        0,
      );
      const removedCells = event.contentChanges.reduce(
        (sum, change) => sum + change.removedCells.length,
        0,
      );
      if (addedCells > 0 || removedCells > 0) {
        await dispatch({
          kind: 'notebook_edit',
          document: notebookRef,
          payload: { added_cells: addedCells, removed_cells: removedCells },
        });
      }

      const notebookHash = sha256Hex(
        `${event.notebook.uri.scheme}:${event.notebook.uri.fsPath}`,
      );
      for (const change of event.cellChanges) {
        const summary = change.executionSummary;
        if (summary === undefined) {
          continue;
        }
        const fingerprint = sha256Hex(
          canonicalJson({
            notebook_hash: notebookHash,
            cell_index: change.cell.index,
            execution_order: summary.executionOrder ?? null,
            success: summary.success ?? null,
            start_time: summary.timing?.startTime ?? null,
            end_time: summary.timing?.endTime ?? null,
          }),
        );
        const cellKey = `${session.session_id}:${notebookHash}:${String(change.cell.index)}`;
        if (this.latestFingerprintByCell.get(cellKey) === fingerprint) {
          continue;
        }
        this.latestFingerprintByCell.set(cellKey, fingerprint);

        const outputMetadata = this.outputMetadata(change.outputs ?? change.cell.outputs);
        const duration =
          summary.timing !== undefined &&
          summary.timing.endTime >= summary.timing.startTime
            ? summary.timing.endTime - summary.timing.startTime
            : null;
        const success = summary.success ?? null;
        await dispatch({
          kind: 'notebook_run',
          document: {
            ...notebookRef,
            language_id: change.cell.document.languageId,
            notebook_cell_id: String(change.cell.index),
          },
          payload: {
            cell_index: change.cell.index,
            execution_order: summary.executionOrder ?? null,
            success,
            outcome: success === null ? 'unknown' : success ? 'success' : 'failure',
            duration_ms: duration,
            source_sha256: sha256Hex(change.cell.document.getText()),
            output_item_count: outputMetadata.itemCount,
            output_bytes: outputMetadata.bytes,
            output_metadata_truncated: outputMetadata.truncated,
          },
        });
      }
    });
  }

  private notebookRef(event: NotebookDocumentChangeLike): DocumentRef | undefined {
    if (event.notebook.uri.scheme !== 'file') {
      return undefined;
    }
    const relativePath = relative(
      this.host.workspaceRootPath,
      event.notebook.uri.fsPath,
    );
    if (
      relativePath.length === 0 ||
      isAbsolute(relativePath) ||
      relativePath === '..' ||
      relativePath.startsWith(`..${sep}`)
    ) {
      return undefined;
    }
    return {
      relative_uri: relativePath.split(sep).join('/'),
      language_id: 'notebook',
    };
  }

  private outputMetadata(outputs: readonly NotebookCellOutputLike[]): {
    readonly itemCount: number;
    readonly bytes: number;
    readonly truncated: boolean;
  } {
    let itemCount = 0;
    let actualBytes = 0;
    for (const output of outputs) {
      for (const item of output.items) {
        itemCount += 1;
        actualBytes += item.data.byteLength;
      }
    }
    return {
      itemCount,
      bytes: Math.min(actualBytes, MAX_NOTEBOOK_OUTPUT_METADATA_BYTES),
      truncated: actualBytes > MAX_NOTEBOOK_OUTPUT_METADATA_BYTES,
    };
  }
}
