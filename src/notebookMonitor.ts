import {
  INotebookTracker,
  NotebookActions,
  NotebookPanel
} from '@jupyterlab/notebook';

import {
  EditStateMachine,
  ITypingCompletedArgs,
  ITextChangeCount
} from './editState';
import {
  IBehaviorContext,
  BehaviorEventLogger,
  errorTypeFromUnknown,
  errorMessageFromUnknown
} from './events';

type NotebookCell = NonNullable<INotebookTracker['activeCell']>;

type SignalLike<TArgs> = {
  connect: (slot: (sender: unknown, args: TArgs) => void) => void;
};

type NotebookActionsSignals = {
  executionScheduled?: SignalLike<unknown>;
  executionStarted?: SignalLike<unknown>;
  executed?: SignalLike<unknown>;
};

export class NotebookBehaviorMonitor {
  private readonly boundPanels = new WeakSet<NotebookPanel>();
  private readonly boundCells = new WeakSet<NotebookCell>();
  private readonly cellSourceCache = new WeakMap<NotebookCell, string>();
  private currentPanel: NotebookPanel | null = null;
  private currentCell: NotebookCell | null = null;

  constructor(
    private readonly notebookTracker: INotebookTracker,
    private readonly logger: BehaviorEventLogger,
    private readonly editState: EditStateMachine
  ) {}

  start(): void {
    this.notebookTracker.widgetAdded.connect((_tracker, panel) => {
      this.bindPanel(panel);
    });

    this.notebookTracker.currentChanged.connect((_tracker, panel) => {
      this.handleNotebookChanged(panel);
    });

    this.notebookTracker.activeCellChanged.connect((_tracker, cell) => {
      this.handleCellChanged(cell);
    });

    this.notebookTracker.forEach(panel => this.bindPanel(panel));
    this.bindNotebookActionSignals();

    void this.notebookTracker.restored.then(() => {
      this.handleNotebookChanged(this.notebookTracker.currentWidget);
      this.handleCellChanged(this.notebookTracker.activeCell);
    });
  }

  getCurrentContext(): IBehaviorContext {
    return this.getContext(this.currentPanel, this.currentCell);
  }

  emitCodeInputCompleted(args: ITypingCompletedArgs): void {
    const cell = this.findCellForContext(args.context);
    if (!cell || this.getCellType(cell) !== 'code') {
      return;
    }

    const panel = this.findPanelForCell(cell) ?? this.currentPanel;
    const context = this.getContext(panel, cell);
    const cellSource =
      args.cellSource ?? getSharedModelSource(cell.model.sharedModel);

    this.logger.emit('code_input_completed', context, {
      input_started_at: new Date(args.inputStartedAt).toISOString(),
      input_ended_at: new Date(args.inputEndedAt).toISOString(),
      duration_ms: args.durationMs,
      cell_source: cellSource
    });
  }

  private handleNotebookChanged(panel: NotebookPanel | null): void {
    const previousPanel = this.currentPanel;
    const previousContext = this.getContext(previousPanel, this.currentCell);

    if (panel) {
      this.bindPanel(panel);
    }

    const nextCell = panel?.content.activeCell ?? null;
    const nextContext = this.getContext(panel, nextCell);
    const notebookChanged =
      previousContext.notebook_path !== nextContext.notebook_path ||
      previousContext.notebook_id !== nextContext.notebook_id;

    this.editState.close('context_change');
    this.currentPanel = panel;
    this.currentCell = nextCell;

    if (notebookChanged) {
      this.logger.emit('notebook_changed', nextContext, {
        previous_notebook_path: previousContext.notebook_path,
        previous_notebook_id: previousContext.notebook_id,
        next_notebook_path: nextContext.notebook_path,
        next_notebook_id: nextContext.notebook_id
      });
    }
  }

  private handleCellChanged(cell: NotebookCell | null): void {
    const panel =
      this.findPanelForCell(cell) ?? this.notebookTracker.currentWidget;
    const previousContext = this.getContext(
      this.currentPanel,
      this.currentCell
    );
    const nextContext = this.getContext(panel, cell);
    const changed =
      previousContext.cell_id !== nextContext.cell_id ||
      previousContext.cell_index !== nextContext.cell_index ||
      previousContext.notebook_id !== nextContext.notebook_id;

    this.editState.close('context_change');

    if (cell) {
      this.bindCell(cell);
    }

    this.currentPanel = panel;
    this.currentCell = cell;

    if (changed) {
      this.logger.emit('cell_changed', nextContext, {
        previous_cell_id: previousContext.cell_id,
        previous_cell_index: previousContext.cell_index,
        previous_cell_type: previousContext.cell_type,
        next_cell_id: nextContext.cell_id,
        next_cell_index: nextContext.cell_index,
        next_cell_type: nextContext.cell_type
      });
    }
  }

  private bindPanel(panel: NotebookPanel): void {
    if (this.boundPanels.has(panel)) {
      return;
    }
    this.boundPanels.add(panel);

    for (const cell of panel.content.widgets) {
      this.bindCell(cell);
    }

    panel.sessionContext.statusChanged.connect((_session, status) => {
      this.handleKernelStatus(panel, String(status));
    });

    panel.context.pathChanged.connect(() => {
      const nextContext = this.getContext(panel, panel.content.activeCell);
      this.logger.emit('notebook_changed', nextContext, {
        next_notebook_path: nextContext.notebook_path,
        next_notebook_id: nextContext.notebook_id
      });
    });
  }

  private bindCell(cell: NotebookCell): void {
    if (this.boundCells.has(cell)) {
      return;
    }
    this.boundCells.add(cell);

    // Cache initial source for deleted-content extraction
    const initialSource = getSharedModelSource(cell.model.sharedModel);
    this.cellSourceCache.set(cell, initialSource);

    const sharedModel = cell.model.sharedModel as unknown as {
      changed?: SignalLike<unknown>;
    };

    sharedModel.changed?.connect((_sender, change) => {
      if (this.getCellType(cell) !== 'code') {
        return;
      }

      const counts = countTextChange(change);
      if (counts.inserted <= 0 && counts.deleted <= 0) {
        return;
      }

      // Capture pre-change source for deleted-content extraction
      const preChangeSource =
        this.cellSourceCache.get(cell) ??
        getSharedModelSource(cell.model.sharedModel);

      const cellSourceAfterChange =
        counts.inserted > 0
          ? getSharedModelSource(cell.model.sharedModel)
          : undefined;

      // Always refresh post-change source from the model
      const currentSource = getSharedModelSource(cell.model.sharedModel);

      let deletedContent: string | undefined;
      let deletedIsFullLine = false;
      if (counts.deleted > 0) {
        // Strategy 1: extract from Yjs delta
        deletedContent = extractDeletedTextFromChange(change, preChangeSource);

        // Strategy 2: fallback to simple diff between pre- and post-change source
        if (!deletedContent) {
          deletedContent = extractDeletedTextByDiff(
            preChangeSource,
            currentSource
          );
        }

        // Only report deleted content for full-line deletions.
        // Character-level edits within a line should only show the count.
        if (deletedContent) {
          const pos = findDeletionPosition(preChangeSource, currentSource);
          if (pos >= 0) {
            const startsAtLineStart =
              pos === 0 || preChangeSource[pos - 1] === '\n';
            const endPos = pos + deletedContent.length;
            const endsAtLineEnd =
              endPos === preChangeSource.length ||
              preChangeSource[endPos] === '\n';
            deletedIsFullLine = startsAtLineStart && endsAtLineEnd;
          }
          if (!deletedIsFullLine) {
            deletedContent = undefined;
          }
        }
      }

      // Update cache with post-change source
      this.cellSourceCache.set(cell, currentSource);

      // Heuristic: single insertion of 15+ characters is almost certainly a paste.
      // This catches cases where the native paste event is not captured
      // (e.g., context-menu paste, keyboard shortcut in some environments).
      if (counts.inserted >= 15) {
        this.editState.markPaste(this.getContextForCell(cell));
      }

      this.editState.handleTextChange(
        { ...counts, deletedContent, deletedIsFullLine },
        this.getContextForCell(cell),
        cellSourceAfterChange
      );
    });

    cell.editor?.host.addEventListener('paste', () => {
      if (this.getCellType(cell) !== 'code') {
        return;
      }
      this.editState.markPaste(this.getContextForCell(cell));
    });
  }

  private bindNotebookActionSignals(): void {
    const actions = NotebookActions as unknown as NotebookActionsSignals;

    actions.executionScheduled?.connect((_sender, args) => {
      this.handleExecutionScheduled(args);
    });

    actions.executionStarted?.connect((_sender, args) => {
      this.handleExecutionStarted(args);
    });

    actions.executed?.connect((_sender, args) => {
      this.handleExecutionFinished(args);
    });
  }

  private handleExecutionScheduled(args: unknown): void {
    const cell = getCellFromArgs(args);
    const panel = this.getPanelFromExecutionArgs(args, cell);
    const context = this.getContext(panel, cell);

    this.editState.close('execution');
    this.logger.emit('cell_execution_scheduled', context);
  }

  private handleExecutionStarted(args: unknown): void {
    const cell = getCellFromArgs(args);
    const panel = this.getPanelFromExecutionArgs(args, cell);
    this.logger.emit('cell_execution_started', this.getContext(panel, cell));
  }

  private handleExecutionFinished(args: unknown): void {
    const cell = getCellFromArgs(args) ?? this.currentCell;
    const panel =
      this.getPanelFromExecutionArgs(args, cell) ?? this.currentPanel;
    const context = this.getContext(panel, cell);
    const cellSource = cell
      ? getSharedModelSource(cell.model.sharedModel)
      : undefined;

    if (getExecutionSuccess(args)) {
      this.logger.emit('cell_execution_success', context, {
        cell_source: cellSource
      });
      return;
    }

    // 1) Try extracting error from signal args first
    const error = getExecutionError(args);
    let errorType = errorTypeFromUnknown(error);
    let errorMessage = errorMessageFromUnknown(error);

    // 2) If args-based extraction yields insufficient info (e.g. just "Error"),
    //    read directly from cell model outputs — always reliable
    if ((!errorType || (errorType === 'Error' && !errorMessage)) && cell) {
      const cellOutputError = this.extractErrorFromCellOutputs(cell);
      if (cellOutputError) {
        errorType = cellOutputError.errorType;
        errorMessage = cellOutputError.errorMessage;
      }
    }

    this.logger.emit('cell_execution_error', context, {
      error_type: errorType,
      error_message: errorMessage,
      cell_source: cellSource
    });
  }

  /**
   * Read the most recent error output from the cell model.
   * Cell outputs are guaranteed to contain the kernel error after a failed execution.
   */
  private extractErrorFromCellOutputs(
    cell: NotebookCell
  ): { errorType: string; errorMessage: string } | null {
    try {
      const model = cell.model as unknown as {
        outputs?: {
          length: number;
          get: (index: number) => unknown;
        };
      };
      const outputs = model.outputs;
      if (!outputs || typeof outputs.length !== 'number') {
        return null;
      }

      // Search in reverse — the most recent error output is at the end
      for (let i = outputs.length - 1; i >= 0; i--) {
        const output = outputs.get(i);
        if (!output || typeof output !== 'object') {
          continue;
        }
        const out = output as Record<string, unknown>;
        // Handle both output_type (standard) and type (some JupyterLab versions)
        if (out.output_type === 'error' || out.type === 'error') {
          const ename = out.ename;
          const evalue = out.evalue;
          return {
            errorType:
              typeof ename === 'string' && ename.length > 0 ? ename : 'Error',
            errorMessage: typeof evalue === 'string' ? evalue : ''
          };
        }
      }
    } catch {
      // Guard against any access errors on the model
    }
    return null;
  }

  private handleKernelStatus(panel: NotebookPanel, status: string): void {
    const context = this.getContext(panel, panel.content.activeCell);
    const eventMap: Record<
      string,
      'kernel_busy' | 'kernel_idle' | 'kernel_restarting' | 'kernel_dead'
    > = {
      busy: 'kernel_busy',
      idle: 'kernel_idle',
      restarting: 'kernel_restarting',
      dead: 'kernel_dead'
    };
    const eventType = eventMap[status];

    if (!eventType) {
      return;
    }

    this.logger.emit(eventType, context, { kernel_status: status });
  }

  private getContextForCell(cell: NotebookCell): IBehaviorContext {
    return this.getContext(this.findPanelForCell(cell), cell);
  }

  private getContext(
    panel: NotebookPanel | null | undefined,
    cell: NotebookCell | null | undefined
  ): IBehaviorContext {
    const resolvedPanel = panel ?? this.findPanelForCell(cell ?? null);
    const resolvedCell = cell ?? resolvedPanel?.content.activeCell ?? null;

    return {
      document_type: resolvedCell ? 'notebook_cell' : undefined,
      notebook_path: resolvedPanel?.context.path,
      notebook_id: getNotebookId(resolvedPanel),
      cell_id: getCellId(resolvedCell),
      cell_index: getCellIndex(resolvedPanel, resolvedCell),
      cell_type: this.getCellType(resolvedCell)
    };
  }

  private findCellForContext(context: IBehaviorContext): NotebookCell | null {
    if (!context.cell_id) {
      return this.currentCell;
    }

    let matchedCell: NotebookCell | null = null;
    this.notebookTracker.forEach(panel => {
      if (
        context.notebook_path &&
        panel.context.path !== context.notebook_path
      ) {
        return;
      }

      for (const cell of panel.content.widgets) {
        if (getCellId(cell) === context.cell_id) {
          matchedCell = cell;
          return;
        }
      }
    });

    return matchedCell ?? this.currentCell;
  }

  private findPanelForCell(
    cell: NotebookCell | null | undefined
  ): NotebookPanel | null {
    if (!cell) {
      return this.notebookTracker.currentWidget;
    }

    return (
      this.notebookTracker.find(panel =>
        panel.content.widgets.includes(cell)
      ) ?? this.notebookTracker.currentWidget
    );
  }

  private getPanelFromExecutionArgs(
    args: unknown,
    cell: NotebookCell | null
  ): NotebookPanel | null {
    const notebook = getNotebookFromArgs(args);
    if (notebook) {
      const panel = this.notebookTracker.find(candidate => {
        return candidate.content === notebook;
      });
      if (panel) {
        return panel;
      }
    }
    return this.findPanelForCell(cell);
  }

  private getCellType(
    cell: NotebookCell | null | undefined
  ): string | undefined {
    return cell?.model.type;
  }
}

function countTextChange(change: unknown): ITextChangeCount {
  return countTextChangeInner(change, 0);
}

function countTextChangeInner(
  change: unknown,
  depth: number
): ITextChangeCount {
  if (depth > 5 || !change) {
    return { inserted: 0, deleted: 0 };
  }

  if (Array.isArray(change)) {
    return change.reduce<ITextChangeCount>(
      (counts, item) => addCounts(counts, countDeltaOperation(item, depth + 1)),
      { inserted: 0, deleted: 0 }
    );
  }

  if (typeof change !== 'object') {
    return { inserted: 0, deleted: 0 };
  }

  const record = change as Record<string, unknown>;
  let counts = countDeltaOperation(record, depth + 1);

  for (const key of ['sourceChange', 'delta', 'changes', 'change']) {
    counts = addCounts(counts, countTextChangeInner(record[key], depth + 1));
  }

  return counts;
}

function countDeltaOperation(value: unknown, depth: number): ITextChangeCount {
  if (!value || typeof value !== 'object') {
    return { inserted: 0, deleted: 0 };
  }

  const operation = value as Record<string, unknown>;
  let inserted = 0;
  let deleted = 0;

  if (typeof operation.insert === 'string') {
    inserted += operation.insert.length;
  } else if (Array.isArray(operation.insert)) {
    inserted += operation.insert.length;
  } else if (operation.insert !== undefined) {
    inserted += 1;
  }

  if (typeof operation.delete === 'number') {
    deleted += operation.delete;
  }

  for (const key of ['ops', 'delta']) {
    const nested = countTextChangeInner(operation[key], depth + 1);
    inserted += nested.inserted;
    deleted += nested.deleted;
  }

  return { inserted, deleted };
}

function addCounts(
  left: ITextChangeCount,
  right: ITextChangeCount
): ITextChangeCount {
  return {
    inserted: left.inserted + right.inserted,
    deleted: left.deleted + right.deleted
  };
}

function getNotebookId(
  panel: NotebookPanel | null | undefined
): string | undefined {
  if (!panel) {
    return undefined;
  }

  const model = panel.context.model as unknown as {
    sharedModel?: { getMetadata?: (key: string) => unknown };
  };
  const metadataId = model.sharedModel?.getMetadata?.('id');
  if (typeof metadataId === 'string' && metadataId.length > 0) {
    return metadataId;
  }

  return panel.id;
}

function getSharedModelSource(sharedModel: unknown): string {
  if (!sharedModel || typeof sharedModel !== 'object') {
    return '';
  }

  const getSource = (sharedModel as { getSource?: () => unknown }).getSource;
  if (typeof getSource !== 'function') {
    return '';
  }

  const source = getSource.call(sharedModel);
  return typeof source === 'string' ? source : '';
}

function getCellId(cell: NotebookCell | null | undefined): string | undefined {
  const id = (cell?.model as unknown as { id?: unknown } | undefined)?.id;
  if (typeof id === 'string' && id.length > 0) {
    return id;
  }
  return undefined;
}

function getCellIndex(
  panel: NotebookPanel | null | undefined,
  cell: NotebookCell | null | undefined
): number | undefined {
  if (!panel || !cell) {
    return undefined;
  }

  const index = panel.content.widgets.indexOf(cell);
  return index >= 0 ? index : undefined;
}

function getCellFromArgs(args: unknown): NotebookCell | null {
  if (!args || typeof args !== 'object') {
    return null;
  }

  const cell = (args as Record<string, unknown>).cell;
  if (cell && typeof cell === 'object' && 'model' in cell) {
    return cell as NotebookCell;
  }
  return null;
}

function getNotebookFromArgs(args: unknown): unknown | null {
  if (!args || typeof args !== 'object') {
    return null;
  }

  return (args as Record<string, unknown>).notebook ?? null;
}

function getExecutionSuccess(args: unknown): boolean {
  if (!args || typeof args !== 'object') {
    return false;
  }

  const record = args as Record<string, unknown>;

  // Simplified { success: boolean } format
  if (typeof record.success === 'boolean') {
    return record.success;
  }

  // IExecuteReplyMsg format: { header, parent_header, content: { status } }
  const content = record.content;
  if (content && typeof content === 'object') {
    return (content as Record<string, unknown>).status === 'ok';
  }

  // Alternative: reply.content.status
  const reply = record.reply;
  if (reply && typeof reply === 'object') {
    const replyContent = (reply as Record<string, unknown>).content;
    if (replyContent && typeof replyContent === 'object') {
      return (replyContent as Record<string, unknown>).status === 'ok';
    }
  }

  return false;
}

function getExecutionError(args: unknown): unknown {
  if (!args || typeof args !== 'object') {
    return undefined;
  }

  const record = args as Record<string, unknown>;

  // Try args.content — this is the Jupyter kernel reply message format
  // IExecuteReplyMsg: { header, parent_header, content: { status, ename, evalue, traceback } }
  const content = record.content;
  if (content && typeof content === 'object') {
    const contentRec = content as Record<string, unknown>;
    if (contentRec.status === 'error') {
      return content; // { status, ename, evalue, traceback }
    }
  }

  // Try reply.content (alternative nesting)
  const reply = record.reply;
  if (reply && typeof reply === 'object') {
    const replyContent = (reply as Record<string, unknown>).content;
    if (replyContent && typeof replyContent === 'object') {
      const status = (replyContent as Record<string, unknown>).status;
      if (status === 'error') {
        return replyContent;
      }
    }
  }

  // Fallback: error property from simplified { success, error } format
  if (record.error && typeof record.error === 'object') {
    return record.error;
  }

  return record.reply ?? record;
}

/**
 * Walk a Yjs/CodeMirror change delta and extract the text that was deleted.
 * Requires the source string *before* the change was applied.
 */
function extractDeletedTextFromChange(
  change: unknown,
  preChangeSource: string
): string | undefined {
  if (!change || preChangeSource === undefined || preChangeSource === null) {
    return undefined;
  }

  const ctx = { pos: 0, deletedParts: '' as string, source: preChangeSource };
  walkChangeForDeletion(change, ctx, 0);
  return ctx.deletedParts || undefined;
}

/**
 * Fallback: extract deleted text by diffing pre- and post-change source strings.
 * Compares common prefix and suffix to isolate the removed portion.
 *
 * This is most reliable for contiguous line-level deletions (where `\n`
 * provides a unique boundary). For character-level deletion, caller should
 * check for `\n` presence and only report the count instead.
 */
function extractDeletedTextByDiff(
  preChangeSource: string,
  postChangeSource: string
): string | undefined {
  const preLen = preChangeSource.length;
  const postLen = postChangeSource.length;

  if (preLen <= postLen) {
    return undefined; // No deletion detected
  }

  // Find common prefix
  let prefixLen = 0;
  const minLen = Math.min(preLen, postLen);
  while (
    prefixLen < minLen &&
    preChangeSource[prefixLen] === postChangeSource[prefixLen]
  ) {
    prefixLen++;
  }

  // Find common suffix in the remaining portions (after the prefix)
  const preRestLen = preLen - prefixLen;
  const postRestLen = postLen - prefixLen;
  let suffixLen = 0;
  while (
    suffixLen < preRestLen &&
    suffixLen < postRestLen &&
    preChangeSource[preLen - 1 - suffixLen] ===
      postChangeSource[postLen - 1 - suffixLen]
  ) {
    suffixLen++;
  }

  // The deleted portion is between the common prefix and common suffix
  const deletedStart = prefixLen;
  const deletedEnd = preLen - suffixLen;

  if (deletedEnd > deletedStart) {
    return preChangeSource.slice(deletedStart, deletedEnd);
  }

  return undefined;
}

/**
 * Find the start position of the deletion in the pre-change source by
 * locating the first character that differs between pre and post.
 */
function findDeletionPosition(
  preChangeSource: string,
  postChangeSource: string
): number {
  const preLen = preChangeSource.length;
  const postLen = postChangeSource.length;
  if (preLen <= postLen) {
    return -1;
  }

  let i = 0;
  while (i < postLen && preChangeSource[i] === postChangeSource[i]) {
    i++;
  }
  return i;
}

function walkChangeForDeletion(
  value: unknown,
  ctx: { pos: number; deletedParts: string; source: string },
  depth: number
): void {
  if (depth > 5 || !value) {
    return;
  }

  if (Array.isArray(value)) {
    for (const item of value) {
      walkDeltaOpForDeletion(item, ctx, depth + 1);
    }
    return;
  }

  if (typeof value !== 'object') {
    return;
  }

  const record = value as Record<string, unknown>;

  // Also check the operation itself (for direct { retain/insert/delete } objects)
  walkDeltaOpForDeletion(value, ctx, depth + 1);

  // Try common nested keys
  for (const key of ['sourceChange', 'delta', 'changes', 'change']) {
    walkChangeForDeletion(record[key], ctx, depth + 1);
  }
}

function walkDeltaOpForDeletion(
  value: unknown,
  ctx: { pos: number; deletedParts: string; source: string },
  depth: number
): void {
  if (!value || typeof value !== 'object') {
    return;
  }

  const op = value as Record<string, unknown>;

  if (typeof op.retain === 'number') {
    ctx.pos += op.retain;
  } else if (typeof op.delete === 'number') {
    const len = op.delete;
    if (ctx.pos + len <= ctx.source.length) {
      ctx.deletedParts += ctx.source.slice(ctx.pos, ctx.pos + len);
    }
    ctx.pos += len;
  }
  // insert doesn't advance position in the original source

  // Recurse into nested operation lists
  if (Array.isArray(op.ops)) {
    for (const item of op.ops) {
      walkDeltaOpForDeletion(item, ctx, depth + 1);
    }
  }
  if (Array.isArray(op.delta)) {
    for (const item of op.delta) {
      walkDeltaOpForDeletion(item, ctx, depth + 1);
    }
  }
}
