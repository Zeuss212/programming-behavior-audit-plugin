import { extname, isAbsolute, relative, sep } from 'node:path';

import { sha256Hex } from '../domain/canonicalJson';
import type { DocumentRef } from '../domain/types';
import type { AuditEventInput, CaptureController } from './captureController';

export interface CollectorDisposable {
  dispose(): void;
}

export interface TextDocumentLike {
  readonly uri: {
    readonly scheme: string;
    readonly fsPath: string;
    readonly fragment?: string;
  };
  readonly languageId: string;
  getText(): string;
}

export interface TextDocumentChange {
  readonly document: TextDocumentLike;
  readonly contentChanges: readonly {
    readonly text: string;
    readonly rangeLength: number;
  }[];
}

type EventRegistration<T> = (
  listener: (event: T) => void | Promise<void>,
) => CollectorDisposable;

export interface TextCollectorHost {
  readonly workspaceRootPath: string;
  readonly onDidChangeTextDocument: EventRegistration<TextDocumentChange>;
  readonly onDidSaveTextDocument: EventRegistration<TextDocumentLike>;
  readonly onDidChangeActiveTextEditor: EventRegistration<
    { readonly document: TextDocumentLike } | undefined
  >;
  readonly onDidChangeWindowState: EventRegistration<{ readonly focused: boolean }>;
  readonly onDidOpenTerminal: EventRegistration<unknown>;
  readonly registerCommand: (
    name: string,
    handler: () => Promise<void>,
  ) => CollectorDisposable;
  readonly executeCommand: (name: string) => Promise<unknown>;
  readonly onError?: (error: unknown) => void;
}

function composite(disposables: readonly CollectorDisposable[]): CollectorDisposable {
  return {
    dispose: () => {
      for (const disposable of disposables) {
        disposable.dispose();
      }
    },
  };
}

function lineCount(text: string): number {
  return text.length === 0 ? 0 : text.split(/\r\n|\r|\n/u).length;
}

export class TextCollector {
  private pasteInProgress = false;

  public constructor(private readonly host: TextCollectorHost) {}

  public start(
    controller: Pick<CaptureController, 'record'>,
    registerPasteCommand = true,
  ): CollectorDisposable {
    const dispatch = async (input: AuditEventInput): Promise<void> => {
      try {
        await controller.record(input);
      } catch (error) {
        this.host.onError?.(error);
      }
    };

    const disposables: CollectorDisposable[] = [
      this.host.onDidChangeTextDocument(async (event) => {
        const document = this.documentRef(event.document);
        if (document === undefined || event.contentChanges.length === 0) {
          return;
        }
        const resultSha256 = sha256Hex(event.document.getText());
        if (this.pasteInProgress) {
          await dispatch({
            kind: 'paste_shortcut',
            document,
            payload: {
              inserted_chars: event.contentChanges.reduce(
                (sum, change) => sum + change.text.length,
                0,
              ),
              line_count: event.contentChanges.reduce(
                (sum, change) => sum + lineCount(change.text),
                0,
              ),
              result_sha256: resultSha256,
            },
          });
          return;
        }
        await dispatch({
          kind: 'edit',
          document,
          payload: {
            inserted_chars: event.contentChanges.reduce(
              (sum, change) => sum + change.text.length,
              0,
            ),
            deleted_chars: event.contentChanges.reduce(
              (sum, change) => sum + change.rangeLength,
              0,
            ),
            result_sha256: resultSha256,
          },
        });
      }),
      this.host.onDidSaveTextDocument(async (document) => {
        const ref = this.documentRef(document);
        if (ref !== undefined) {
          await dispatch({
            kind: 'save',
            document: ref,
            payload: { content_sha256: sha256Hex(document.getText()) },
          });
        }
      }),
      this.host.onDidChangeActiveTextEditor(async (editor) => {
        const ref = editor === undefined ? undefined : this.documentRef(editor.document);
        if (ref !== undefined) {
          await dispatch({ kind: 'document_focus', document: ref, payload: {} });
        }
      }),
      this.host.onDidChangeWindowState(async (state) => {
        await dispatch({ kind: 'window_focus', payload: { focused: state.focused } });
      }),
      this.host.onDidOpenTerminal(async () => {
        await dispatch({ kind: 'external_terminal_activity', payload: { occurred: true } });
      }),
    ];
    if (registerPasteCommand) {
      disposables.push(
        this.host.registerCommand('behaviorAudit.pasteAndRecord', () =>
          this.pasteAndRecord(),
        ),
      );
    }
    return composite(disposables);
  }

  public async pasteAndRecord(): Promise<void> {
    this.pasteInProgress = true;
    try {
      await this.host.executeCommand('editor.action.clipboardPasteAction');
    } finally {
      this.pasteInProgress = false;
    }
  }

  private documentRef(document: TextDocumentLike): DocumentRef | undefined {
    const isPythonFile =
      document.uri.scheme === 'file' &&
      document.languageId === 'python' &&
      extname(document.uri.fsPath).toLowerCase() === '.py';
    const isNotebookCell =
      document.uri.scheme === 'vscode-notebook-cell' && document.languageId === 'python';
    if (!isPythonFile && !isNotebookCell) {
      return undefined;
    }

    const relativePath = relative(this.host.workspaceRootPath, document.uri.fsPath);
    if (
      relativePath.length === 0 ||
      isAbsolute(relativePath) ||
      relativePath === '..' ||
      relativePath.startsWith(`..${sep}`)
    ) {
      return undefined;
    }
    const notebookCellId =
      isNotebookCell && document.uri.fragment !== undefined
        ? sha256Hex(document.uri.fragment).slice(0, 16)
        : undefined;
    return {
      relative_uri: relativePath.split(sep).join('/'),
      language_id: document.languageId,
      ...(notebookCellId === undefined ? {} : { notebook_cell_id: notebookCellId }),
    };
  }
}
